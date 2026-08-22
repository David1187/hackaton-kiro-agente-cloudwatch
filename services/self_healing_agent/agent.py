"""Self-Healing Agent entrypoint for Bedrock AgentCore Runtime.

Exposes the AgentCore Runtime contract (/invocations POST, /ping GET)
using the bedrock-agentcore SDK decorator. Implements the full ReAct cycle:

1. Derive Log Group from EventBridge alarm event
2. Query CloudWatch Logs for the latest stack trace
3. Read github-repo tag from the affected Lambda
4. Read the buggy source file via GitHub MCP
5. Generate fix using LLM (Bedrock)
6. Create branch, write fix, open PR — never merge/approve
"""
import json
import logging
import os
from datetime import datetime, timezone

import boto3
import botocore.exceptions
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

# Dual-mode imports: support both package import (from .module) and direct
# script execution (from module). This allows entryPoint=['python', 'agent.py']
# while keeping tests that import via 'from services.self_healing_agent.x' working.
try:
    from .branch_naming import build_fix_branch_name
except ImportError:
    from branch_naming import build_fix_branch_name

try:
    from .config import resolve_model_id
except ImportError:
    from config import resolve_model_id

try:
    from .logs_client import derive_function_name, derive_log_group, get_latest_stack_trace
except ImportError:
    from logs_client import derive_function_name, derive_log_group, get_latest_stack_trace

try:
    from .pr_body import build_pr_description, build_pr_title
except ImportError:
    from pr_body import build_pr_description, build_pr_title

try:
    from .repo_tag import InvalidRepoTagError, parse_repo_tag
except ImportError:
    from repo_tag import InvalidRepoTagError, parse_repo_tag

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Lazy-initialized module-level boto3 client — reused across invocations (warm starts)
# Uses a getter to avoid resolving credentials at import time (breaks tests).
_lambda_client = None


def _get_lambda_client():
    """Return the module-level Lambda client, creating it on first use."""
    global _lambda_client
    if _lambda_client is None:
        _lambda_client = boto3.client("lambda")
    return _lambda_client

# AgentCore Gateway URL for GitHub MCP (injected via environment)
GATEWAY_MCP_URL = os.environ.get("GATEWAY_MCP_URL", "")

# AWS region where the AgentCore Gateway is deployed (AgentCore Runtime sets
# AWS_REGION automatically; falls back to AWS_DEFAULT_REGION for local/test runs)
AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "")

# System prompt for the agent's LLM
SYSTEM_PROMPT = """You are a Self-Healing Agent. You fix Python Lambda errors by creating a Pull Request on GitHub.

CRITICAL RULES:
- You MUST use tools to perform actions. NEVER output code as plain text.
- You MUST complete the steps below IN ORDER, exactly once each. Do NOT repeat any step.
- Do NOT call github-mcp___get_me or github-mcp___get_commit — they are unnecessary.
- Do NOT loop or re-read files you have already read.
- After creating the Pull Request, STOP immediately. Your job is done.

EXACT STEPS (execute once each, in this order):

Step 1: Read the broken file.
  Tool: github-mcp___get_file_contents
  Params: owner, repo, path (provided in the prompt)

Step 2: Analyze the error and generate the COMPLETE fixed file content in memory.
  The fix must:
  - Add try-except with specific exceptions (botocore.exceptions.ClientError, ParamValidationError) before a generic except Exception
  - Validate input parameters before use
  - Use logging.error(..., exc_info=True) — never print()
  - Preserve existing DynamoDB CRUD logic

Step 3: Create a branch from main.
  Tool: github-mcp___create_branch
  Params: owner, repo, branch (provided in the prompt), from_ref="main"

Step 4: Write the fixed file to the new branch.
  Tool: github-mcp___create_or_update_file_contents
  Params: owner, repo, path (same file), branch (the new branch), message="fix: auto-heal <function_name>", content=<the complete fixed file>

Step 5: Open a Pull Request.
  Tool: github-mcp___create_pull_request
  Params: owner, repo, title (provided), body (provided), head=<branch>, base="main"

After Step 5, STOP. Do NOT merge or approve the PR. Do NOT repeat any step."""


# Option B: system prompt for PURE code generation (no tool use).
# The LLM only rewrites the file; Python orchestrates the MCP calls
# deterministically. Nova/qwen produce malformed toolUse blocks with the
# GitHub MCP schemas, so we never let the model emit tool calls.
CODE_FIX_SYSTEM_PROMPT = """You are an expert Python engineer that fixes AWS Lambda handlers.

You will receive an error stack trace and the current content of a Python file.
Your job is to return the COMPLETE corrected content of that file.

Rules for the fix:
- Wrap DynamoDB/boto3 I/O in try-except, catching botocore.exceptions.ClientError
  and botocore.exceptions.ParamValidationError BEFORE a generic except Exception.
- Validate input parameters (event keys, body attributes) before using them, to
  avoid uncontrolled KeyError/TypeError.
- Use logging.error(..., exc_info=True) for errors — never print().
- Preserve the existing DynamoDB CRUD logic and the handler's HTTP response format.
- Keep all imports the file needs.

Output ONLY the raw corrected Python source code for the whole file.
Do NOT add markdown code fences, explanations, or any commentary."""


def _get_lambda_tags(function_name: str) -> dict:
    """Retrieve tags for a Lambda function.

    Args:
        function_name: The Lambda function name.

    Returns:
        Dictionary of tag key-value pairs.

    Raises:
        Exception: If the API call fails.
    """
    try:
        # We need the function ARN for list_tags
        client = _get_lambda_client()
        fn_response = client.get_function(FunctionName=function_name)
        function_arn = fn_response["Configuration"]["FunctionArn"]
        tags_response = client.list_tags(Resource=function_arn)
        return tags_response.get("Tags", {})
    except botocore.exceptions.ClientError as error:
        logger.error(
            "ERROR: failed to get tags for Lambda '%s': %s",
            function_name,
            error.response["Error"]["Code"],
            exc_info=True,
        )
        raise
    except Exception:
        logger.error(
            "ERROR: unexpected failure getting tags for Lambda '%s'",
            function_name,
            exc_info=True,
        )
        raise


def handle_event(event: dict) -> dict:
    """Handle an EventBridge alarm event — full ReAct cycle.

    This is the core orchestration function that:
    1. Derives the Log Group from the alarm event
    2. Queries CloudWatch Logs for the stack trace
    3. Reads the github-repo tag from the affected Lambda
    4. Connects to GitHub MCP via AgentCore Gateway
    5. Reads the source file, generates a fix with the LLM
    6. Creates a branch and opens a PR

    Args:
        event: The EventBridge event payload.

    Returns:
        A result dict with status and details.
    """
    timestamp = datetime.now(tz=timezone.utc)
    result = {"status": "failed", "details": {}}

    # --- Step 1: Derive function name and log group ---
    try:
        function_name = derive_function_name(event)
        log_group = derive_log_group(function_name)
        logger.info("Derived function_name=%s, log_group=%s", function_name, log_group)
        result["details"]["function_name"] = function_name
        result["details"]["log_group"] = log_group
    except (ValueError, KeyError) as exc:
        logger.error(
            "ERROR: cannot derive function name from event: %s", exc, exc_info=True
        )
        result["details"]["error"] = f"Cannot derive function name: {exc}"
        return result

    # --- Step 2: Get the latest stack trace ---
    try:
        stack_trace = get_latest_stack_trace(log_group)
        if not stack_trace:
            logger.warning("No ERROR entries found in log group '%s'", log_group)
            result["status"] = "no_errors_found"
            result["details"]["message"] = "No recent ERROR entries in CloudWatch Logs"
            return result
        logger.info("Retrieved stack trace (%d chars)", len(stack_trace))
        result["details"]["stack_trace_length"] = len(stack_trace)
    except Exception as exc:
        logger.error(
            "ERROR: failed to retrieve stack trace from '%s': %s",
            log_group,
            exc,
            exc_info=True,
        )
        result["details"]["error"] = f"Failed to query CloudWatch Logs: {exc}"
        return result

    # --- Step 3: Read github-repo tag ---
    try:
        tags = _get_lambda_tags(function_name)
        repo_ref = parse_repo_tag(tags)
        repo_full = str(repo_ref)
        logger.info("Resolved repository: %s", repo_full)
        result["details"]["repository"] = repo_full
    except InvalidRepoTagError as exc:
        logger.error(
            "ERROR: invalid github-repo tag on Lambda '%s': %s",
            function_name,
            exc,
            exc_info=True,
        )
        result["details"]["error"] = f"Invalid github-repo tag: {exc}"
        return result
    except Exception as exc:
        logger.error(
            "ERROR: failed to read tags from Lambda '%s': %s",
            function_name,
            exc,
            exc_info=True,
        )
        result["details"]["error"] = f"Failed to read Lambda tags: {exc}"
        return result

    # --- Step 4-6: Connect to MCP, read code, generate fix, open PR ---
    if not GATEWAY_MCP_URL:
        logger.error("ERROR: GATEWAY_MCP_URL environment variable is not set")
        result["details"]["error"] = "GATEWAY_MCP_URL not configured"
        return result

    try:
        # Build the fix branch name
        branch_name = build_fix_branch_name(function_name, timestamp)
        result["details"]["branch"] = branch_name

        # Initialize the LLM model
        model_id = resolve_model_id(os.environ)
        bedrock_model = BedrockModel(model_id=model_id, temperature=0, streaming=False)

        # Connect to GitHub MCP via AgentCore Gateway. The Gateway enforces
        # authorizerType=AWS_IAM (see infra/constructs/agent_gateway.py), so
        # requests must be SigV4-signed with the runtime's execution role
        # credentials — aws_iam_streamablehttp_client handles that signing.
        #
        # The MCPClient MUST be used as a context manager so it connects to
        # the Gateway and discovers the available tools. After connecting, we
        # call list_tools_sync() to get the MCPAgentTool instances and pass
        # them directly to the Agent — passing the MCPClient object itself
        # does NOT work because it is neither an AgentTool nor an iterable
        # of tools (strands registry rejects it as "unrecognized tool
        # specification").
        mcp_client = MCPClient(
            lambda: aws_iam_streamablehttp_client(
                endpoint=GATEWAY_MCP_URL,
                aws_region=AWS_REGION,
                aws_service="bedrock-agentcore",
            )
        )

        with mcp_client:
            # Discover available GitHub MCP tools via the Gateway
            mcp_tools = mcp_client.list_tools_sync()
            logger.info(
                "Discovered %d MCP tools from Gateway", len(mcp_tools)
            )

            # Option B: call the MCP tools deterministically from Python (below)
            # instead of letting the model emit toolUse blocks. Nova/qwen produce
            # malformed toolUse sequences with the GitHub MCP schemas
            # ("invalid sequence as part of ToolUse"). The LLM is used ONLY to
            # generate the fixed file content as plain text.
            available_tool_names = {
                getattr(t, "tool_name", getattr(t, "name", "")) for t in mcp_tools
            }
            logger.info("Available MCP tools: %s", sorted(available_tool_names))

            def _resolve_tool(*candidates: str) -> str:
                """Resolve an available MCP tool name, tolerating naming variations."""
                for cand in candidates:
                    if cand in available_tool_names:
                        return cand
                # Fallback: partial match on the bare tool name (after the ___ prefix)
                for tool_name in available_tool_names:
                    if any(cand.split("___")[-1] in tool_name for cand in candidates):
                        return tool_name
                raise RuntimeError(f"Required MCP tool not found among {sorted(available_tool_names)}: {candidates}")

            tool_get_file = _resolve_tool("github-mcp___get_file_contents")
            tool_create_branch = _resolve_tool("github-mcp___create_branch")
            tool_write_file = _resolve_tool(
                "github-mcp___create_or_update_file_contents",
                "github-mcp___create_or_update_file",
            )
            tool_create_pr = _resolve_tool("github-mcp___create_pull_request")

            # LLM agent WITHOUT tools — pure text generation of the corrected file.
            codegen_agent = Agent(
                model=bedrock_model,
                system_prompt=CODE_FIX_SYSTEM_PROMPT,
            )

            # --- Parse the file path from the stack trace ---
            # Lambda runtime paths look like /var/task/handlers/update_task.py
            # or /var/task/common/repository.py (no services/crud_api/ prefix).
            # Full repo paths (services/crud_api/...) are unlikely but handled.
            file_path = None
            also_read_common = False  # Flag if common/ module is involved

            # First pass: check if common/repository.py is anywhere in the trace
            if "common/repository.py" in stack_trace:
                also_read_common = True

            # Second pass: find the handler file path
            for trace_line in stack_trace.splitlines():
                trace_line_stripped = trace_line.strip()
                if 'File "' not in trace_line_stripped:
                    continue

                # Extract path between quotes
                start = trace_line_stripped.index('File "') + len('File "')
                end = trace_line_stripped.index('"', start)
                raw_path = trace_line_stripped[start:end]

                # Case 1: Full repo path (services/crud_api/handlers/...)
                if "services/crud_api/handlers/" in raw_path:
                    svc_idx = raw_path.find("services/crud_api/handlers/")
                    file_path = raw_path[svc_idx:]
                    break

                # Case 2: Lambda runtime path (handlers/xxx.py without prefix)
                if "handlers/" in raw_path and raw_path.endswith(".py"):
                    handler_idx = raw_path.find("handlers/")
                    relative_path = raw_path[handler_idx:]
                    file_path = f"services/crud_api/{relative_path}"
                    break

            if not file_path:
                # Fallback: map from Lambda function name pattern to handler file
                fn_upper = function_name.upper()
                if "FNUPDATE" in fn_upper or "UPDATE" in fn_upper:
                    file_path = "services/crud_api/handlers/update_task.py"
                elif "FNCREATE" in fn_upper or "CREATE" in fn_upper:
                    file_path = "services/crud_api/handlers/create_task.py"
                elif "FNGET" in fn_upper or "GET" in fn_upper:
                    file_path = "services/crud_api/handlers/get_task.py"
                elif "FNDELETE" in fn_upper or "DELETE" in fn_upper:
                    file_path = "services/crud_api/handlers/delete_task.py"
                elif "FNLIST" in fn_upper or "LIST" in fn_upper:
                    file_path = "services/crud_api/handlers/list_tasks.py"
                else:
                    file_path = "services/crud_api/handlers/update_task.py"
                logger.warning(
                    "Could not parse file path from stack trace, using fallback: %s",
                    file_path,
                )

            result["details"]["file_path"] = file_path

            pr_title = build_pr_title(function_name)
            pr_body = build_pr_description(function_name, stack_trace[:500])

            OWNER = "David1187"
            REPO = "hackaton-kiro-agente-cloudwatch"

            def _mcp_call(tool_name: str, arguments: dict, retries: int = 4) -> tuple:
                """Call an MCP tool and return (status, combined_text, content_blocks).

                content_blocks is the raw list of MCP content items (may
                include "resource" blocks in addition to "text" blocks — the
                GitHub MCP server returns file contents as a "resource" block
                alongside a "text" confirmation message like "successfully
                downloaded text file (SHA: <sha>)", not as plain JSON text;
                see github/github-mcp-server#607). Callers that need the
                actual file content/sha must inspect content_blocks
                themselves rather than relying solely on combined_text.

                Retries on transient Gateway/MCP errors ("internal error",
                "retry later", rate limiting), which the GitHub MCP surfaces
                intermittently, with exponential backoff.
                """
                import time as _time

                status, text, content_blocks = None, "", []
                for attempt in range(retries):
                    res = mcp_client.call_tool_sync(
                        tool_use_id=f"{tool_name}-{int(timestamp.timestamp())}-{attempt}",
                        name=tool_name,
                        arguments=arguments,
                    )
                    if isinstance(res, dict):
                        status = res.get("status")
                        content_blocks = res.get("content") or []
                    else:
                        status = getattr(res, "status", None)
                        content_blocks = getattr(res, "content", None) or []
                    text = "\n".join(
                        b.get("text", "")
                        for b in content_blocks
                        if isinstance(b, dict) and "text" in b
                    )
                    if status == "success":
                        return status, text, content_blocks

                    logger.warning(
                        "MCP tool '%s' non-success (status=%s). args=%s text=%s",
                        tool_name,
                        status,
                        json.dumps(arguments, default=str)[:500],
                        text[:500],
                    )

                    lowered = text.lower()
                    transient = (
                        "internal error" in lowered
                        or "retry later" in lowered
                        or "rate limit" in lowered
                        or "timeout" in lowered
                        or "timed out" in lowered
                    )
                    if not transient or attempt == retries - 1:
                        return status, text, content_blocks
                    backoff = 2 ** attempt
                    logger.warning(
                        "MCP tool '%s' transient error (attempt %d/%d), retrying in %ds: %s",
                        tool_name,
                        attempt + 1,
                        retries,
                        backoff,
                        text[:200],
                    )
                    _time.sleep(backoff)
                return status, text, content_blocks

            def _extract_file_contents(text: str, content_blocks: list) -> tuple:
                """Extract (source_text, sha) from a get_file_contents response.

                Handles both known response shapes:
                1. Legacy/alternate: a single JSON text block like
                   {"sha": "...", "content": "<base64>", "encoding": "base64"}.
                2. Current github-mcp-server behavior: a "text" confirmation
                   block ("successfully downloaded text file (SHA: <sha>)")
                   plus a separate "resource" block carrying the actual file
                   text/blob (see github/github-mcp-server#607 — the "resource"
                   content type is not auto-unwrapped into text by the MCP
                   client, so it must be read from content_blocks directly).
                """
                import base64 as _b64
                import re as _re

                source_text = text
                sha = None

                # Shape 1: single JSON block with sha/content/encoding.
                try:
                    meta = json.loads(text)
                    if isinstance(meta, dict):
                        sha = meta.get("sha")
                        if meta.get("content"):
                            if meta.get("encoding", "base64") == "base64":
                                source_text = _b64.b64decode(meta["content"]).decode(
                                    "utf-8", "replace"
                                )
                            else:
                                source_text = meta["content"]
                        return source_text, sha
                except (ValueError, TypeError):
                    pass  # Not a plain JSON blob — fall through to shape 2.

                # Shape 2: "resource" content block(s) carry the real file
                # content; the "text" block is just a confirmation message
                # that may embed "(SHA: <sha>)".
                for block in content_blocks:
                    if not isinstance(block, dict):
                        continue
                    resource = block.get("resource")
                    if isinstance(resource, dict):
                        if resource.get("text") is not None:
                            source_text = resource["text"]
                        elif resource.get("blob"):
                            try:
                                source_text = _b64.b64decode(resource["blob"]).decode(
                                    "utf-8", "replace"
                                )
                            except Exception:
                                pass
                        if resource.get("sha"):
                            sha = resource["sha"]

                if not sha:
                    match = _re.search(r"\(SHA:\s*([0-9a-fA-F]{7,40})\)", text)
                    if match:
                        sha = match.group(1)

                return source_text, sha

            # --- Step 1: Read the buggy file via MCP (deterministic) ---
            status, file_text, file_blocks = _mcp_call(
                tool_get_file, {"owner": OWNER, "repo": REPO, "path": file_path}
            )
            if status != "success":
                raise RuntimeError(f"get_file_contents failed: {file_text[:500]}")

            current_source, file_sha = _extract_file_contents(file_text, file_blocks)

            # Optionally read the shared repository module for extra context.
            common_context = ""
            if also_read_common:
                c_status, c_text, c_blocks = _mcp_call(
                    tool_get_file,
                    {
                        "owner": OWNER,
                        "repo": REPO,
                        "path": "services/crud_api/common/repository.py",
                    },
                )
                if c_status == "success":
                    common_source, _common_sha = _extract_file_contents(c_text, c_blocks)
                    common_context = (
                        "\n\n=== SHARED MODULE (services/crud_api/common/repository.py, "
                        "for context only — apply the fix in the handler file) ===\n"
                        f"{common_source}"
                    )

            # --- Step 2: Generate the COMPLETE fixed file (LLM, text only) ---
            codegen_prompt = (
                "Fix the bug in the following Python AWS Lambda handler.\n\n"
                f"=== ERROR STACK TRACE ===\n{stack_trace}\n\n"
                f"=== CURRENT FILE ({file_path}) ===\n{current_source}"
                f"{common_context}\n\n"
                "Return ONLY the complete corrected content of the handler file "
                f"({file_path}). No markdown fences, no commentary."
            )
            logger.info("Generating fix with model=%s (no tools)", model_id)
            llm_response = codegen_agent(codegen_prompt)
            fixed_source = str(llm_response).strip()

            # Strip markdown fences if the model added them despite instructions.
            if fixed_source.startswith("```"):
                fence_lines = fixed_source.splitlines()
                if fence_lines and fence_lines[0].startswith("```"):
                    fence_lines = fence_lines[1:]
                if fence_lines and fence_lines[-1].strip() == "```":
                    fence_lines = fence_lines[:-1]
                fixed_source = "\n".join(fence_lines).strip()

            if not fixed_source:
                raise RuntimeError("LLM produced an empty fixed source file")

            # --- Step 3: Create the fix branch from main (deterministic) ---
            status, branch_out, _branch_blocks = _mcp_call(
                tool_create_branch,
                {"owner": OWNER, "repo": REPO, "branch": branch_name, "from_branch": "main"},
            )
            if status != "success":
                # Non-fatal: the branch may already exist from a previous run.
                logger.warning("create_branch status=%s: %s", status, branch_out[:300])

            # --- Step 4: Write the fixed file to the branch ---
            write_args = {
                "owner": OWNER,
                "repo": REPO,
                "path": file_path,
                "branch": branch_name,
                "message": f"fix: auto-heal {function_name}",
                "content": fixed_source,
            }
            if file_sha:
                write_args["sha"] = file_sha
            status, write_out, _write_blocks = _mcp_call(tool_write_file, write_args)
            if status != "success":
                raise RuntimeError(f"create_or_update_file failed: {write_out[:500]}")

            # --- Step 5: Open the Pull Request (never merge) ---
            status, pr_out, _pr_blocks = _mcp_call(
                tool_create_pr,
                {
                    "owner": OWNER,
                    "repo": REPO,
                    "title": pr_title,
                    "body": pr_body,
                    "head": branch_name,
                    "base": "main",
                },
            )
            if status != "success":
                raise RuntimeError(f"create_pull_request failed: {pr_out[:500]}")

            logger.info("Pull request opened successfully (branch=%s)", branch_name)
            response = pr_out

        result["status"] = "success"
        result["details"]["model_id"] = model_id
        result["details"]["agent_response"] = str(response)[:1000]

    except Exception as exc:
        logger.error(
            "ERROR: agent execution failed for Lambda '%s': %s",
            function_name,
            exc,
            exc_info=True,
        )
        result["details"]["error"] = f"Agent execution failed: {exc}"
        return result

    return result


# --- AgentCore Runtime Entrypoint ---

try:
    from bedrock_agentcore import BedrockAgentCoreApp

    app = BedrockAgentCoreApp()

    @app.entrypoint
    def invoke(payload: dict) -> dict:
        """AgentCore Runtime entrypoint — handles /invocations POST.

        AgentCore Runtime passes the full HTTP request body as `payload`
        with NO automatic unwrapping. The bridge Lambda sends
        {"input": {"prompt": json.dumps(eventbridge_event)}} as that body,
        so `payload` arrives here as {"input": {"prompt": "<json string>"}}.
        The actual EventBridge event is JSON-encoded inside
        payload["input"]["prompt"] and must be parsed out before being
        handed to handle_event().
        """
        logger.info("AgentCore Runtime invocation received")
        if isinstance(payload, str):
            payload = json.loads(payload)

        prompt = None
        if isinstance(payload, dict):
            input_field = payload.get("input")
            if isinstance(input_field, dict):
                prompt = input_field.get("prompt")
            elif "prompt" in payload:
                prompt = payload.get("prompt")

        if prompt is not None:
            event = json.loads(prompt) if isinstance(prompt, str) else prompt
        else:
            event = payload

        return handle_event(event)

except ImportError:
    # Fallback for local development/testing without bedrock-agentcore installed
    logger.warning(
        "bedrock-agentcore not available — AgentCore Runtime entrypoint not registered. "
        "Use handle_event() directly for testing."
    )
    app = None


if __name__ == "__main__":
    if app is not None:
        app.run()
    else:
        # Fallback: if bedrock-agentcore is not installed, print usage info
        print(
            "bedrock-agentcore SDK not installed. "
            "Install it to run the AgentCore Runtime server."
        )
