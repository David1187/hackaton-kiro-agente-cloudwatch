"""AgentCore Gateway construct — routes MCP calls to GitHub with PAT injection.

Uses CfnResource (L1) as escape hatch because CDK does not yet provide L2
constructs for Amazon Bedrock AgentCore Gateway (as of aws-cdk-lib 2.200.0).
This is documented per iac-standards.md section 1.

The Gateway:
- Points to the GitHub MCP remote endpoint
- Injects the GitHub PAT from Secrets Manager in transit
- Never exposes the PAT to the agent runtime

Per architecture-guide.md section 2.2:
- The PAT is stored in Secrets Manager (provisioned here, value loaded manually)
- Only the Gateway role has secretsmanager:GetSecretValue
- The agent runtime role does NOT have access to the secret
"""
import aws_cdk as cdk
from aws_cdk import (
    aws_iam as iam,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

# GitHub MCP remote endpoint (official)
GITHUB_MCP_ENDPOINT = "https://api.githubcopilot.com/mcp/"


class AgentGateway(Construct):
    """Bedrock AgentCore Gateway for routing MCP calls to GitHub.

    Creates:
    - A Secrets Manager secret for the GitHub PAT (empty placeholder)
    - An AgentCore Gateway configured with the GitHub MCP target
    - IAM permissions: only the Gateway role can read the secret

    Attributes:
        secret: The Secrets Manager secret holding the GitHub PAT.
        gateway_mcp_url: The MCP URL that the agent should use to reach GitHub tools.
        gateway_role: The IAM role used by the gateway.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
    ) -> None:
        """Initialize the AgentCore Gateway construct.

        Args:
            scope: CDK scope.
            construct_id: Construct ID.
        """
        super().__init__(scope, construct_id)

        # --- GitHub PAT Secret (empty placeholder, value loaded manually post-deploy) ---
        self.secret = secretsmanager.Secret(
            self,
            "GitHubPATSecret",
            description=(
                "GitHub Personal Access Token for the Self-Healing Agent. "
                "Value must be set manually after deployment via AWS Console or CLI: "
                "aws secretsmanager put-secret-value --secret-id <arn> --secret-string <pat>"
            ),
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # --- Gateway IAM Role ---
        self.gateway_role = iam.Role(
            self,
            "GatewayRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description="Role for AgentCore Gateway — reads GitHub PAT from Secrets Manager",
        )

        # Grant the gateway role read access to the PAT secret
        self.secret.grant_read(self.gateway_role)

        # --- AgentCore Gateway (CfnResource — no L2 available) ---
        # Note: Using CfnResource as escape hatch per iac-standards.md section 1.
        # When CDK adds L2 for AgentCore Gateway, this should be migrated.
        self._gateway = cdk.CfnResource(
            self,
            "AgentCoreGateway",
            type="AWS::BedrockAgentCore::Gateway",
            properties={
                "Name": "self-healing-github-gateway",
                "Description": "Routes MCP calls to GitHub MCP remote with PAT injection",
                "RoleArn": self.gateway_role.role_arn,
                "ProtocolConfiguration": {
                    "Mcp": {
                        "SupportedVersions": ["2025-03-26"],
                    }
                },
            },
        )
        self._gateway.apply_removal_policy(cdk.RemovalPolicy.DESTROY)

        # --- Gateway Target: GitHub MCP ---
        self._gateway_target = cdk.CfnResource(
            self,
            "GitHubMCPTarget",
            type="AWS::BedrockAgentCore::GatewayTarget",
            properties={
                "GatewayIdentifier": self._gateway.ref,
                "Name": "github-mcp",
                "Description": "GitHub MCP remote endpoint with PAT credential",
                "TargetConfiguration": {
                    "McpTarget": {
                        "McpEndpointUri": GITHUB_MCP_ENDPOINT,
                    }
                },
                "CredentialProviderConfigurations": [
                    {
                        "CredentialProvider": {
                            "ApiKeyCredentialProvider": {
                                "ProviderArn": self.secret.secret_arn,
                                "CredentialPrefix": "Bearer ",
                                "HttpScheme": "HTTPS",
                                "HeaderName": "Authorization",
                            }
                        }
                    }
                ],
            },
        )
        self._gateway_target.apply_removal_policy(cdk.RemovalPolicy.DESTROY)
        self._gateway_target.add_dependency(self._gateway)

    @property
    def gateway_id(self) -> str:
        """ID of the AgentCore Gateway."""
        return self._gateway.ref

    @property
    def gateway_arn(self) -> str:
        """ARN of the AgentCore Gateway."""
        return self._gateway.get_att("GatewayArn").to_string()

    @property
    def gateway_mcp_url(self) -> str:
        """MCP URL for the agent to use when connecting to the Gateway.

        Note: The actual URL is constructed from the gateway ID and region.
        This returns a CloudFormation expression that resolves at deploy time.
        """
        return cdk.Fn.sub(
            "https://gateway.agentcore.${AWS::Region}.amazonaws.com/${GatewayId}/mcp",
            {"GatewayId": self._gateway.ref},
        )
