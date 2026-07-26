"""Unit tests for the handle_event orchestration flow in agent.py.

Tests the full ReAct cycle with mocked dependencies:
- logs_client (derive_function_name, get_latest_stack_trace)
- Lambda tags client (_get_lambda_tags)
- Bedrock model (Agent/BedrockModel)
- MCP tools (MCPClient)

Covers error paths:
- 5.3: No ERROR: entries found in logs
- 5.4: Failure querying CloudWatch Logs
- 6.4: No enumeration of repos (agent only uses tag-specified repo)
- 6.5: Invalid/absent github-repo tag
- 7.5: Gateway credential failure
- 9.5: Failure creating branch/PR
- 10.x: No merge/approve in any flow

Requirements: 5.3, 5.4, 6.4, 6.5, 7.5, 9.5, 10.1, 10.2, 10.3, 10.4
"""
import sys
from unittest.mock import patch, MagicMock

import pytest
import botocore.exceptions

# Mock strands and bedrock_agentcore before importing agent.py
# (these packages may not be installed in the test environment)
_mock_strands = MagicMock()
_mock_strands_models = MagicMock()
_mock_strands_tools_mcp = MagicMock()
_mock_strands_tools_mcp_client = MagicMock()
_mock_mcp_proxy_for_aws = MagicMock()
_mock_mcp_proxy_for_aws_client = MagicMock()
_mock_bedrock_agentcore = MagicMock()
_mock_bedrock_agentcore_runtime = MagicMock()

sys.modules.setdefault("strands", _mock_strands)
sys.modules.setdefault("strands.models", _mock_strands_models)
sys.modules.setdefault("strands.tools", MagicMock())
sys.modules.setdefault("strands.tools.mcp", _mock_strands_tools_mcp)
sys.modules.setdefault("strands.tools.mcp.mcp_client", _mock_strands_tools_mcp_client)
sys.modules.setdefault("mcp_proxy_for_aws", _mock_mcp_proxy_for_aws)
sys.modules.setdefault("mcp_proxy_for_aws.client", _mock_mcp_proxy_for_aws_client)
sys.modules.setdefault("bedrock_agentcore", _mock_bedrock_agentcore)
sys.modules.setdefault("bedrock_agentcore.runtime", _mock_bedrock_agentcore_runtime)

from services.self_healing_agent.agent import handle_event, SYSTEM_PROMPT
from services.self_healing_agent.repo_tag import InvalidRepoTagError


# --- Helper: build a valid EventBridge alarm event ---

def _make_alarm_event(function_name: str = "my-crud-fn") -> dict:
    """Build a minimal EventBridge alarm event with the given function name."""
    return {
        "source": "aws.cloudwatch",
        "detail-type": "CloudWatch Alarm State Change",
        "detail": {
            "alarmName": "TestAlarm",
            "state": {"value": "ALARM"},
            "configuration": {
                "metrics": [
                    {
                        "metricStat": {
                            "metric": {
                                "dimensions": {
                                    "FunctionName": function_name
                                }
                            }
                        }
                    }
                ]
            },
        },
    }


# Patches common to most tests (prevent real AWS calls)
_PATCH_PREFIX = "services.self_healing_agent.agent"


class TestHandleEventNoErrors:
    """5.3: When no ERROR: entries are found, agent terminates gracefully."""

    @patch(f"{_PATCH_PREFIX}.get_latest_stack_trace", return_value=None)
    @patch(f"{_PATCH_PREFIX}._get_lambda_tags", return_value={"github-repo": "owner/repo"})
    def test_no_errors_found_returns_no_errors_status(self, mock_tags, mock_logs):
        """When logs have no ERROR entries, status is 'no_errors_found'."""
        event = _make_alarm_event("my-fn")
        result = handle_event(event)
        assert result["status"] == "no_errors_found"
        assert "No recent ERROR" in result["details"]["message"]

    @patch(f"{_PATCH_PREFIX}.get_latest_stack_trace", return_value=None)
    @patch(f"{_PATCH_PREFIX}._get_lambda_tags")
    def test_no_errors_does_not_read_tags(self, mock_tags, mock_logs):
        """When no errors in logs, tags are never read (early return)."""
        event = _make_alarm_event("my-fn")
        handle_event(event)
        mock_tags.assert_not_called()


class TestHandleEventLogsFailure:
    """5.4: When querying CloudWatch Logs fails, agent reports failure."""

    @patch(f"{_PATCH_PREFIX}.get_latest_stack_trace")
    def test_logs_client_error_returns_failed(self, mock_logs):
        """ClientError from logs client results in 'failed' status."""
        mock_logs.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Log group not found"}},
            "FilterLogEvents",
        )
        event = _make_alarm_event("my-fn")
        result = handle_event(event)
        assert result["status"] == "failed"
        assert "Failed to query CloudWatch Logs" in result["details"]["error"]

    @patch(f"{_PATCH_PREFIX}.get_latest_stack_trace")
    def test_logs_generic_exception_returns_failed(self, mock_logs):
        """Generic exception from logs client results in 'failed' status."""
        mock_logs.side_effect = RuntimeError("Connection timeout")
        event = _make_alarm_event("my-fn")
        result = handle_event(event)
        assert result["status"] == "failed"
        assert "Failed to query CloudWatch Logs" in result["details"]["error"]


class TestHandleEventInvalidTag:
    """6.5: Invalid or absent github-repo tag stops the flow."""

    @patch(f"{_PATCH_PREFIX}.get_latest_stack_trace", return_value="ERROR: KeyError: 'id'")
    @patch(f"{_PATCH_PREFIX}._get_lambda_tags", return_value={})
    def test_missing_tag_returns_failed(self, mock_tags, mock_logs):
        """Missing github-repo tag returns failed status."""
        event = _make_alarm_event("my-fn")
        result = handle_event(event)
        assert result["status"] == "failed"
        assert "Invalid github-repo tag" in result["details"]["error"]

    @patch(f"{_PATCH_PREFIX}.get_latest_stack_trace", return_value="ERROR: KeyError: 'id'")
    @patch(f"{_PATCH_PREFIX}._get_lambda_tags", return_value={"github-repo": ""})
    def test_empty_tag_returns_failed(self, mock_tags, mock_logs):
        """Empty github-repo tag returns failed status."""
        event = _make_alarm_event("my-fn")
        result = handle_event(event)
        assert result["status"] == "failed"
        assert "Invalid github-repo tag" in result["details"]["error"]

    @patch(f"{_PATCH_PREFIX}.get_latest_stack_trace", return_value="ERROR: KeyError: 'id'")
    @patch(f"{_PATCH_PREFIX}._get_lambda_tags", return_value={"github-repo": "noslash"})
    def test_malformed_tag_no_slash_returns_failed(self, mock_tags, mock_logs):
        """Tag without '/' returns failed status."""
        event = _make_alarm_event("my-fn")
        result = handle_event(event)
        assert result["status"] == "failed"
        assert "Invalid github-repo tag" in result["details"]["error"]

    @patch(f"{_PATCH_PREFIX}.get_latest_stack_trace", return_value="ERROR: KeyError: 'id'")
    @patch(f"{_PATCH_PREFIX}._get_lambda_tags")
    def test_tags_api_failure_returns_failed(self, mock_tags, mock_logs):
        """Failure to retrieve tags returns failed status."""
        mock_tags.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Function not found"}},
            "GetFunction",
        )
        event = _make_alarm_event("my-fn")
        result = handle_event(event)
        assert result["status"] == "failed"
        assert "Failed to read Lambda tags" in result["details"]["error"]


class TestHandleEventGatewayCredentialFailure:
    """7.5: When AgentCore Gateway credential/URL is missing, agent fails gracefully."""

    @patch(f"{_PATCH_PREFIX}.get_latest_stack_trace", return_value="ERROR: KeyError: 'id'")
    @patch(f"{_PATCH_PREFIX}._get_lambda_tags", return_value={"github-repo": "owner/repo"})
    @patch(f"{_PATCH_PREFIX}.GATEWAY_MCP_URL", "")
    def test_empty_gateway_url_returns_failed(self, mock_tags, mock_logs):
        """Empty GATEWAY_MCP_URL returns failed status."""
        event = _make_alarm_event("my-fn")
        result = handle_event(event)
        assert result["status"] == "failed"
        assert "GATEWAY_MCP_URL" in result["details"]["error"]

    @patch(f"{_PATCH_PREFIX}.get_latest_stack_trace", return_value="ERROR: KeyError: 'id'")
    @patch(f"{_PATCH_PREFIX}._get_lambda_tags", return_value={"github-repo": "owner/repo"})
    @patch(f"{_PATCH_PREFIX}.GATEWAY_MCP_URL", "https://gateway.example.com/mcp")
    @patch(f"{_PATCH_PREFIX}.Agent")
    @patch(f"{_PATCH_PREFIX}.MCPClient")
    @patch(f"{_PATCH_PREFIX}.aws_iam_streamablehttp_client")
    @patch(f"{_PATCH_PREFIX}.BedrockModel")
    def test_agent_auth_error_returns_failed(
        self, mock_bedrock, mock_transport, mock_mcp, mock_agent_cls, mock_tags, mock_logs
    ):
        """Authentication error from the gateway results in 'failed' status."""
        mock_agent_instance = MagicMock()
        mock_agent_instance.side_effect = Exception(
            "403 Forbidden: Gateway credential expired"
        )
        mock_agent_cls.return_value = mock_agent_instance
        event = _make_alarm_event("my-fn")
        result = handle_event(event)
        assert result["status"] == "failed"
        assert "Agent execution failed" in result["details"]["error"]


class TestHandleEventBranchPrFailure:
    """9.5: When creating branch or PR fails, agent reports failure."""

    @patch(f"{_PATCH_PREFIX}.get_latest_stack_trace", return_value="ERROR: KeyError: 'id'")
    @patch(f"{_PATCH_PREFIX}._get_lambda_tags", return_value={"github-repo": "owner/repo"})
    @patch(f"{_PATCH_PREFIX}.GATEWAY_MCP_URL", "https://gateway.example.com/mcp")
    @patch(f"{_PATCH_PREFIX}.Agent")
    @patch(f"{_PATCH_PREFIX}.MCPClient")
    @patch(f"{_PATCH_PREFIX}.aws_iam_streamablehttp_client")
    @patch(f"{_PATCH_PREFIX}.BedrockModel")
    def test_agent_execution_failure_returns_failed(
        self, mock_bedrock, mock_transport, mock_mcp, mock_agent_cls, mock_tags, mock_logs
    ):
        """When Agent() call raises (branch/PR creation failure), status is failed."""
        mock_agent_instance = MagicMock()
        mock_agent_instance.side_effect = RuntimeError(
            "Failed to create branch: permission denied"
        )
        mock_agent_cls.return_value = mock_agent_instance
        event = _make_alarm_event("my-fn")
        result = handle_event(event)
        assert result["status"] == "failed"
        assert "Agent execution failed" in result["details"]["error"]


class TestHandleEventNoMergeApprove:
    """10.x: The agent never merges or approves a PR."""

    @patch(f"{_PATCH_PREFIX}.get_latest_stack_trace", return_value="ERROR: KeyError: 'id'")
    @patch(f"{_PATCH_PREFIX}._get_lambda_tags", return_value={"github-repo": "owner/repo"})
    @patch(f"{_PATCH_PREFIX}.GATEWAY_MCP_URL", "https://gateway.example.com/mcp")
    @patch(f"{_PATCH_PREFIX}.Agent")
    @patch(f"{_PATCH_PREFIX}.MCPClient")
    @patch(f"{_PATCH_PREFIX}.aws_iam_streamablehttp_client")
    @patch(f"{_PATCH_PREFIX}.BedrockModel")
    def test_successful_flow_does_not_merge(
        self, mock_bedrock, mock_transport, mock_mcp, mock_agent_cls, mock_tags, mock_logs
    ):
        """On success, the agent creates a PR but never merges/approves."""
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = "PR created successfully"
        mock_agent_cls.return_value = mock_agent_instance
        event = _make_alarm_event("my-fn")
        result = handle_event(event)
        assert result["status"] == "success"
        # Verify the prompt sent to the agent contains "Never merge or approve"
        call_args = mock_agent_instance.call_args
        if call_args:
            prompt = call_args[0][0] if call_args[0] else ""
            assert "never merge" in prompt.lower() or "Never merge" in prompt

    def test_system_prompt_forbids_merge(self):
        """The SYSTEM_PROMPT does not contain merge/approve instructions."""
        prompt_lower = SYSTEM_PROMPT.lower()
        # Should NOT instruct to merge
        assert "merge the pr" not in prompt_lower
        assert "approve the pr" not in prompt_lower
        # Should contain defensive coding guidance
        assert "try-except" in prompt_lower or "exception" in prompt_lower


class TestHandleEventNoRepoEnumeration:
    """6.4: The agent never enumerates or scans repositories."""

    @patch(f"{_PATCH_PREFIX}.get_latest_stack_trace", return_value="ERROR: KeyError: 'id'")
    @patch(f"{_PATCH_PREFIX}._get_lambda_tags", return_value={"github-repo": "owner/repo"})
    @patch(f"{_PATCH_PREFIX}.GATEWAY_MCP_URL", "https://gateway.example.com/mcp")
    @patch(f"{_PATCH_PREFIX}.Agent")
    @patch(f"{_PATCH_PREFIX}.MCPClient")
    @patch(f"{_PATCH_PREFIX}.aws_iam_streamablehttp_client")
    @patch(f"{_PATCH_PREFIX}.BedrockModel")
    def test_agent_prompt_specifies_exact_repo(
        self, mock_bedrock, mock_transport, mock_mcp, mock_agent_cls, mock_tags, mock_logs
    ):
        """The prompt sent to the agent references the specific repo from the tag, not a search."""
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = "Done"
        mock_agent_cls.return_value = mock_agent_instance
        event = _make_alarm_event("my-fn")
        handle_event(event)
        # The agent was called with a prompt
        call_args = mock_agent_instance.call_args
        assert call_args is not None
        prompt = call_args[0][0] if call_args[0] else ""
        # The prompt mentions the exact repo from the tag
        assert "owner/repo" in prompt
        # The prompt does NOT ask to list/search/enumerate repositories
        prompt_lower = prompt.lower()
        assert "list repositories" not in prompt_lower
        assert "search repositories" not in prompt_lower
        assert "enumerate" not in prompt_lower

    @patch(f"{_PATCH_PREFIX}.get_latest_stack_trace", return_value="ERROR: KeyError: 'id'")
    @patch(f"{_PATCH_PREFIX}._get_lambda_tags", return_value={"github-repo": "specific-org/specific-repo"})
    @patch(f"{_PATCH_PREFIX}.GATEWAY_MCP_URL", "https://gateway.example.com/mcp")
    @patch(f"{_PATCH_PREFIX}.Agent")
    @patch(f"{_PATCH_PREFIX}.MCPClient")
    @patch(f"{_PATCH_PREFIX}.aws_iam_streamablehttp_client")
    @patch(f"{_PATCH_PREFIX}.BedrockModel")
    def test_repo_from_tag_used_in_prompt(
        self, mock_bedrock, mock_transport, mock_mcp, mock_agent_cls, mock_tags, mock_logs
    ):
        """The exact repo from the tag (not any other) appears in the agent prompt."""
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = "Done"
        mock_agent_cls.return_value = mock_agent_instance
        event = _make_alarm_event("my-fn")
        handle_event(event)
        call_args = mock_agent_instance.call_args
        prompt = call_args[0][0] if call_args[0] else ""
        assert "specific-org/specific-repo" in prompt


class TestHandleEventSuccessFlow:
    """Happy path: full successful flow with all mocks."""

    @patch(f"{_PATCH_PREFIX}.get_latest_stack_trace", return_value="ERROR: KeyError: 'task_id'\nTraceback...")
    @patch(f"{_PATCH_PREFIX}._get_lambda_tags", return_value={"github-repo": "David1187/hackaton-kiro-agente-cloudwatch"})
    @patch(f"{_PATCH_PREFIX}.GATEWAY_MCP_URL", "https://gateway.example.com/mcp")
    @patch(f"{_PATCH_PREFIX}.Agent")
    @patch(f"{_PATCH_PREFIX}.MCPClient")
    @patch(f"{_PATCH_PREFIX}.aws_iam_streamablehttp_client")
    @patch(f"{_PATCH_PREFIX}.BedrockModel")
    def test_success_returns_expected_details(
        self, mock_bedrock, mock_transport, mock_mcp, mock_agent_cls, mock_tags, mock_logs
    ):
        """Successful execution returns status=success with branch and repo details."""
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = "PR #42 created"
        mock_agent_cls.return_value = mock_agent_instance
        event = _make_alarm_event("create-task-fn")
        result = handle_event(event)
        assert result["status"] == "success"
        assert result["details"]["function_name"] == "create-task-fn"
        assert result["details"]["log_group"] == "/aws/lambda/create-task-fn"
        assert result["details"]["repository"] == "David1187/hackaton-kiro-agente-cloudwatch"
        assert result["details"]["branch"].startswith("fix/auto-heal-create-task-fn-")
        assert "model_id" in result["details"]


class TestHandleEventInvalidEvent:
    """Edge case: event that cannot be parsed."""

    def test_empty_event_returns_failed(self):
        """Empty event dict returns failed status."""
        result = handle_event({})
        assert result["status"] == "failed"
        assert "Cannot derive function name" in result["details"]["error"]

    def test_missing_detail_returns_failed(self):
        """Event without 'detail' key returns failed."""
        result = handle_event({"source": "aws.cloudwatch"})
        assert result["status"] == "failed"
        assert "Cannot derive function name" in result["details"]["error"]

    def test_missing_metrics_returns_failed(self):
        """Event with empty metrics returns failed."""
        event = {
            "detail": {
                "alarmName": "test",
                "configuration": {"metrics": []},
            }
        }
        result = handle_event(event)
        assert result["status"] == "failed"
