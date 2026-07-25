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
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from strands.tools.mcp.mcp_client import StreamableHTTPTransport

from .branch_naming import build_fix_branch_name
from .config import resolve_model_id
from .logs_client import derive_function_name, derive_log_group, get_latest_stack_trace
from .pr_body import build_pr_description, build_pr_title
from .repo_tag import InvalidRepoTagError, parse_repo_tag

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

# System prompt for the agent's LLM
SYSTEM_PROMPT = """You are a Self-Healing Agent that fixes Python Lambda function errors.

When given an error stack trace and the source code that caused it, you must:
1. Identify the root cause of the error
2. Generate a complete fixed version of the file that:
   - Adds proper try-except blocks with specific exception handling
   - Validates input parameters before use
   - Uses logging.error(..., exc_info=True) for error reporting (never print())
   - Preserves the existing business logic (CRUD operations on DynamoDB)
   - Follows Python best practices for defensive programming

Return ONLY the complete fixed file content, no explanations or markdown fences."""


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
        bedrock_model = BedrockModel(model_id=model_id, temperature=0.3)

        # Connect to GitHub MCP via AgentCore Gateway
        transport = StreamableHTTPTransport(GATEWAY_MCP_URL)
        mcp_client = MCPClient(transport)

        # Create the agent with MCP tools
        agent = Agent(
            model=bedrock_model,
            tools=[mcp_client],
            system_prompt=SYSTEM_PROMPT,
        )

        # Compose the prompt for the agent
        prompt = (
            f"A Lambda function '{function_name}' in repository '{repo_full}' "
            f"has thrown an error. Here is the stack trace from CloudWatch Logs:\n\n"
            f"```\n{stack_trace}\n```\n\n"
            f"Please:\n"
            f"1. Use the get_file_contents tool to read the source file that caused the error "
            f"(look at the file paths in the stack trace, they'll be under 'services/crud_api/')\n"
            f"2. Analyze the error and generate a fixed version of the file\n"
            f"3. Create a new branch named '{branch_name}' from 'main'\n"
            f"4. Write the fixed file to the new branch\n"
            f"5. Create a Pull Request with title '{build_pr_title(function_name)}'\n\n"
            f"The PR description should be:\n"
            f"{build_pr_description(function_name, stack_trace[:500])}\n\n"
            f"IMPORTANT: Never merge or approve the PR. Only create it for human review."
        )

        # Execute the agent
        logger.info("Invoking agent with model=%s", model_id)
        response = agent(prompt)
        logger.info("Agent completed successfully")

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
    from bedrock_agentcore.runtime import RuntimeApp

    app = RuntimeApp()

    @app.entrypoint
    def invoke(event: dict) -> dict:
        """AgentCore Runtime entrypoint — handles /invocations POST."""
        logger.info("AgentCore Runtime invocation received")
        if isinstance(event, str):
            event = json.loads(event)
        return handle_event(event)

except ImportError:
    # Fallback for local development/testing without bedrock-agentcore installed
    logger.warning(
        "bedrock-agentcore not available — AgentCore Runtime entrypoint not registered. "
        "Use handle_event() directly for testing."
    )
    app = None
