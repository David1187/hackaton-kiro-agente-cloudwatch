"""AgentCore Runtime construct — deploys the Self-Healing Agent.

Uses a Custom Resource with a Python Lambda Provider because the
bedrock-agentcore-control SDK is only available in boto3 (Python), not in the
JavaScript SDK that AwsCustomResource uses internally.

The agent is deployed via direct code deployment (zip in S3, ARM64),
without Docker/ECR, per architecture-guide.md section 2 and 3.

Key behavior: CreateAgentRuntime automatically creates V1 + DEFAULT endpoint,
so no separate create_agent_runtime_endpoint call is needed.
"""
import aws_cdk as cdk
from aws_cdk import (
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_s3_assets as s3_assets,
    custom_resources as cr,
    CustomResource,
    Duration,
)
from constructs import Construct


# Default model ID (configurable via environment variable MODEL_ID at runtime)
DEFAULT_MODEL_ID = "qwen.qwen3-coder-30b-a3b-instruct"


# --- Custom Resource Lambda handler code for AgentCore Runtime management ---
RUNTIME_HANDLER_CODE = """\
import boto3
import json
import logging
import time

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def on_event(event, context):
    \"\"\"Handle CloudFormation custom resource events for AgentCore Runtime.\"\"\"
    logger.info("Received event: %s", json.dumps(event, default=str))
    request_type = event["RequestType"]
    props = event["ResourceProperties"]

    if request_type == "Create":
        return on_create(props)
    elif request_type == "Update":
        return on_update(event, props)
    elif request_type == "Delete":
        return on_delete(event, props)
    else:
        raise ValueError(f"Unexpected RequestType: {request_type}")


def on_create(props):
    \"\"\"Create AgentCore Runtime (auto-creates V1 + DEFAULT endpoint).\"\"\"
    client = boto3.client("bedrock-agentcore-control")

    # Build the artifact configuration for direct code deployment
    artifact_config = {
        "codeConfiguration": {
            "code": {
                "s3": {
                    "bucket": props["S3Bucket"],
                    "prefix": props["S3Key"],
                },
            },
            "runtime": "PYTHON_3_13",
            "entryPoint": ["agent.py"],
        },
    }

    # Build environment variables for the runtime
    env_vars = {}
    if props.get("ModelId"):
        env_vars["MODEL_ID"] = props["ModelId"]
    if props.get("GatewayMcpUrl"):
        env_vars["GATEWAY_MCP_URL"] = props["GatewayMcpUrl"]

    create_params = {
        "agentRuntimeName": props["AgentRuntimeName"],
        "description": "Self-Healing Agent for auto-remediation of Lambda errors",
        "roleArn": props["RoleArn"],
        "agentRuntimeArtifact": artifact_config,
        "networkConfiguration": {"networkMode": "PUBLIC"},
    }

    if env_vars:
        create_params["environmentVariables"] = env_vars

    resp = client.create_agent_runtime(**create_params)

    runtime_id = resp["agentRuntimeId"]
    runtime_arn = resp.get("agentRuntimeArn", "")
    logger.info("Created runtime: %s (arn=%s, status=%s)", runtime_id, runtime_arn, resp.get("status"))

    # Wait for runtime to become READY
    _wait_for_runtime_ready(client, runtime_id)

    # The DEFAULT endpoint is auto-created. Get its ARN.
    endpoint_arn = f"{runtime_arn}/endpoint/DEFAULT"

    return {
        "PhysicalResourceId": runtime_id,
        "Data": {
            "AgentRuntimeId": runtime_id,
            "AgentRuntimeArn": runtime_arn,
            "EndpointArn": endpoint_arn,
        },
    }


def on_update(event, props):
    \"\"\"Update the runtime with new code/config.\"\"\"
    client = boto3.client("bedrock-agentcore-control")
    runtime_id = event["PhysicalResourceId"]

    artifact_config = {
        "codeConfiguration": {
            "code": {
                "s3": {
                    "bucket": props["S3Bucket"],
                    "prefix": props["S3Key"],
                },
            },
            "runtime": "PYTHON_3_13",
            "entryPoint": ["agent.py"],
        },
    }

    env_vars = {}
    if props.get("ModelId"):
        env_vars["MODEL_ID"] = props["ModelId"]
    if props.get("GatewayMcpUrl"):
        env_vars["GATEWAY_MCP_URL"] = props["GatewayMcpUrl"]

    update_params = {
        "agentRuntimeId": runtime_id,
        "agentRuntimeArtifact": artifact_config,
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "roleArn": props["RoleArn"],
    }

    if env_vars:
        update_params["environmentVariables"] = env_vars

    try:
        resp = client.update_agent_runtime(**update_params)
        logger.info("Updated runtime: %s (status=%s)", runtime_id, resp.get("status"))
    except Exception as e:
        logger.warning("Failed to update runtime (non-fatal): %s", e)

    runtime_arn = resp.get("agentRuntimeArn", "")
    endpoint_arn = f"{runtime_arn}/endpoint/DEFAULT"

    return {
        "PhysicalResourceId": runtime_id,
        "Data": {
            "AgentRuntimeId": runtime_id,
            "AgentRuntimeArn": runtime_arn,
            "EndpointArn": endpoint_arn,
        },
    }


def on_delete(event, props):
    \"\"\"Delete the AgentCore Runtime (also deletes its endpoints).\"\"\"
    client = boto3.client("bedrock-agentcore-control")
    runtime_id = event["PhysicalResourceId"]

    try:
        client.delete_agent_runtime(agentRuntimeId=runtime_id)
        logger.info("Deleted runtime: %s", runtime_id)
    except Exception as e:
        logger.warning("Failed to delete runtime (non-fatal): %s", e)

    return {"PhysicalResourceId": runtime_id}


def _wait_for_runtime_ready(client, runtime_id, max_attempts=60, delay=10):
    \"\"\"Poll until runtime status is READY or timeout.\"\"\"
    for attempt in range(max_attempts):
        resp = client.get_agent_runtime(agentRuntimeId=runtime_id)
        status = resp.get("status", "UNKNOWN")
        logger.info("Runtime %s status: %s (attempt %d/%d)", runtime_id, status, attempt + 1, max_attempts)
        if status == "READY":
            return
        if status in ("CREATE_FAILED", "FAILED"):
            failure_reason = resp.get("failureReason", "Unknown")
            raise RuntimeError(f"Runtime creation failed: {status} - {failure_reason}")
        time.sleep(delay)
    raise RuntimeError(f"Runtime {runtime_id} did not become READY within {max_attempts * delay}s")
"""


class AgentRuntime(Construct):
    """Bedrock AgentCore Runtime for the Self-Healing Agent.

    Deploys the agent code via direct code deployment (.zip on S3, ARM64).
    Creates an IAM role with minimum privilege for the agent's operations.

    Attributes:
        runtime_role: The IAM role used by the agent runtime.
        runtime_arn: The ARN of the AgentCore Runtime (for EventBridge target).
        runtime_id: The ID of the AgentCore Runtime.
        endpoint_arn: The endpoint ARN for invocations.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        agent_code_path: str,
        gateway_mcp_url: str = "",
        model_id: str = DEFAULT_MODEL_ID,
        log_group_arns: list[str] | None = None,
    ) -> None:
        """Initialize the AgentCore Runtime construct.

        Args:
            scope: CDK scope.
            construct_id: Construct ID.
            agent_code_path: Path to the agent source directory (services/self_healing_agent/).
            gateway_mcp_url: URL of the AgentCore Gateway MCP endpoint.
            model_id: Bedrock model ID (default, overridable at runtime via MODEL_ID env var).
            log_group_arns: ARNs of log groups the agent can read (for IAM scoping).
        """
        super().__init__(scope, construct_id)

        # --- IAM Role with minimum privilege ---
        self.runtime_role = iam.Role(
            self,
            "AgentRuntimeRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description="Role for Self-Healing Agent on Bedrock AgentCore Runtime",
        )

        # Permission: Invoke Bedrock model
        self.runtime_role.add_to_policy(
            iam.PolicyStatement(
                sid="InvokeBedrock",
                effect=iam.Effect.ALLOW,
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:*:{cdk.Aws.ACCOUNT_ID}:inference-profile/*",
                ],
            )
        )

        # Permission: Read CloudWatch Logs (scoped to Lambda log groups)
        log_resources = log_group_arns if log_group_arns else [
            f"arn:aws:logs:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:log-group:/aws/lambda/TodoCrud*:*"
        ]
        self.runtime_role.add_to_policy(
            iam.PolicyStatement(
                sid="ReadCloudWatchLogs",
                effect=iam.Effect.ALLOW,
                actions=[
                    "logs:FilterLogEvents",
                    "logs:GetLogEvents",
                    "logs:DescribeLogStreams",
                ],
                resources=log_resources,
            )
        )

        # Permission: Read Lambda tags (for github-repo resolution)
        self.runtime_role.add_to_policy(
            iam.PolicyStatement(
                sid="ReadLambdaTags",
                effect=iam.Effect.ALLOW,
                actions=[
                    "lambda:GetFunction",
                    "lambda:ListTags",
                    "tag:GetResources",
                ],
                resources=["*"],
            )
        )

        # NOTE: NO secretsmanager:GetSecretValue — the agent never reads the PAT directly.

        # --- Upload agent code as S3 asset ---
        self.code_asset = s3_assets.Asset(
            self,
            "AgentCodeAsset",
            path=agent_code_path,
            exclude=[
                "tests",
                "tests/**",
                ".pytest_cache",
                ".pytest_cache/**",
                ".hypothesis",
                ".hypothesis/**",
                "__pycache__",
                "__pycache__/**",
                "**/__pycache__",
                "**/__pycache__/**",
                "requirements-dev.txt",
            ],
        )

        # Grant the AgentCore service read access to the S3 asset
        self.code_asset.grant_read(self.runtime_role)

        # --- Custom Resource Provider Lambda ---
        provider_fn = lambda_.Function(
            self,
            "RuntimeProviderFn",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="index.on_event",
            code=lambda_.Code.from_inline(RUNTIME_HANDLER_CODE),
            timeout=Duration.minutes(14),
            memory_size=256,
            description="Custom Resource provider for AgentCore Runtime lifecycle",
        )

        # IAM permissions for the provider Lambda
        # Hackathon scope: broad bedrock-agentcore:* permission. The specific
        # actions (CreateAgentRuntime, UpdateAgentRuntime, etc.) were insufficient
        # because the service may call internal sub-actions not individually
        # documented. Replace with scoped actions for production use.
        provider_fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="AgentCoreRuntimeManagement",
                effect=iam.Effect.ALLOW,
                actions=["bedrock-agentcore:*"],
                resources=["*"],
            )
        )
        provider_fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="PassRuntimeRole",
                effect=iam.Effect.ALLOW,
                actions=["iam:PassRole"],
                resources=[self.runtime_role.role_arn],
            )
        )
        # CreateAgentRuntime requires creating a service-linked role for AgentCore
        provider_fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="CreateServiceLinkedRole",
                effect=iam.Effect.ALLOW,
                actions=["iam:CreateServiceLinkedRole"],
                resources=["*"],
            )
        )
        # The provider Lambda needs S3 read access to verify the code asset exists
        self.code_asset.grant_read(provider_fn)

        # --- CDK Provider (handles async CloudFormation response) ---
        provider = cr.Provider(
            self,
            "RuntimeProvider",
            on_event_handler=provider_fn,
        )

        # --- Custom Resource ---
        self._runtime_resource = CustomResource(
            self,
            "RuntimeResource",
            service_token=provider.service_token,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            properties={
                "AgentRuntimeName": "self_healing_agent",
                "RoleArn": self.runtime_role.role_arn,
                "S3Bucket": self.code_asset.s3_bucket_name,
                "S3Key": self.code_asset.s3_object_key,
                "ModelId": model_id,
                "GatewayMcpUrl": gateway_mcp_url,
            },
        )

    @property
    def runtime_arn(self) -> str:
        """ARN of the AgentCore Runtime."""
        return self._runtime_resource.get_att_string("AgentRuntimeArn")

    @property
    def runtime_id(self) -> str:
        """ID of the AgentCore Runtime."""
        return self._runtime_resource.get_att_string("AgentRuntimeId")

    @property
    def endpoint_arn(self) -> str:
        """ARN of the AgentCore Runtime Endpoint (DEFAULT)."""
        return self._runtime_resource.get_att_string("EndpointArn")
