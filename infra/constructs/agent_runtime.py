"""AgentCore Runtime construct — deploys the Self-Healing Agent.

Uses CfnResource (L1) as escape hatch because CDK does not yet provide L2
constructs for Amazon Bedrock AgentCore Runtime (as of aws-cdk-lib 2.200.0).
This is documented per iac-standards.md section 1: L1 allowed when L2 is
not available.

The agent is deployed via direct code deployment (zip in S3, ARM64),
without Docker/ECR, per architecture-guide.md section 2 and 3.
"""
import aws_cdk as cdk
from aws_cdk import (
    aws_iam as iam,
    aws_s3_assets as s3_assets,
)
from constructs import Construct


# Default model ID (configurable via environment variable MODEL_ID at runtime)
DEFAULT_MODEL_ID = "qwen.qwen3-coder-30b-a3b-instruct"


class AgentRuntime(Construct):
    """Bedrock AgentCore Runtime for the Self-Healing Agent.

    Deploys the agent code via direct code deployment (.zip on S3, ARM64).
    Creates an IAM role with minimum privilege for the agent's operations.

    Attributes:
        runtime_role: The IAM role used by the agent runtime.
        runtime_arn: The ARN of the AgentCore Runtime (for EventBridge target).
        runtime_endpoint_arn: The endpoint ARN for invocations.
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
                    f"arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:*:{cdk.Aws.ACCOUNT_ID}:inference-profile/*",
                ],
            )
        )

        # Permission: Read CloudWatch Logs (scoped to Lambda log groups)
        # If specific ARNs are provided, use them; otherwise restrict to project Lambdas only
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
                resources=["*"],  # ListTags requires the function ARN which is dynamic
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

        # --- AgentCore Runtime (CfnResource — no L2 available) ---
        # Note: Using CfnResource as escape hatch per iac-standards.md section 1.
        # When CDK adds L2 for AgentCore, this should be migrated.
        self._runtime = cdk.CfnResource(
            self,
            "AgentCoreRuntime",
            type="AWS::BedrockAgentCore::AgentRuntime",
            properties={
                "AgentRuntimeName": "self-healing-agent",
                "Description": "Self-Healing Agent for auto-remediation of Lambda errors",
                "RoleArn": self.runtime_role.role_arn,
                "AgentRuntimeArtifact": {
                    "S3Artifact": {
                        "S3Uri": self.code_asset.s3_object_url,
                    }
                },
                "EnvironmentVariables": {
                    "MODEL_ID": model_id,
                    "GATEWAY_MCP_URL": gateway_mcp_url,
                },
                "NetworkConfiguration": {
                    "NetworkMode": "PUBLIC",
                },
            },
        )
        self._runtime.apply_removal_policy(cdk.RemovalPolicy.DESTROY)

        # Grant the AgentCore service read access to the S3 asset
        self.code_asset.grant_read(self.runtime_role)

        # --- Endpoint for invocations ---
        self._endpoint = cdk.CfnResource(
            self,
            "AgentCoreEndpoint",
            type="AWS::BedrockAgentCore::AgentRuntimeEndpoint",
            properties={
                "AgentRuntimeId": self._runtime.ref,
                "Name": "self-healing-agent-endpoint",
                "Description": "Invocation endpoint for the Self-Healing Agent",
            },
        )
        self._endpoint.apply_removal_policy(cdk.RemovalPolicy.DESTROY)
        self._endpoint.add_dependency(self._runtime)

    @property
    def runtime_arn(self) -> str:
        """ARN of the AgentCore Runtime."""
        return self._runtime.get_att("AgentRuntimeArn").to_string()

    @property
    def runtime_id(self) -> str:
        """ID of the AgentCore Runtime."""
        return self._runtime.ref

    @property
    def endpoint_arn(self) -> str:
        """ARN of the AgentCore Runtime Endpoint."""
        return self._endpoint.get_att("AgentRuntimeEndpointArn").to_string()
