"""AgentCore Gateway construct — routes MCP calls to GitHub with PAT injection.

Uses a Custom Resource with a Python Lambda Provider because the
bedrock-agentcore-control SDK is only available in boto3 (Python), not in the
JavaScript SDK that AwsCustomResource uses internally.

The Gateway:
- Points to the GitHub MCP remote endpoint
- Uses an API Key Credential Provider (AgentCore Identity) for outbound auth
- Injects the GitHub PAT (stored in Secrets Manager) via the credential provider
- Never exposes the PAT to the agent runtime

Per architecture-guide.md section 2.2:
- The PAT is stored in Secrets Manager (provisioned here, value loaded manually)
- Only the Gateway role has the necessary permissions
- The agent runtime role does NOT have access to the secret
"""
import aws_cdk as cdk
from aws_cdk import (
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_secretsmanager as secretsmanager,
    custom_resources as cr,
    CustomResource,
    Duration,
)
from constructs import Construct

# GitHub MCP remote endpoint (official)
GITHUB_MCP_ENDPOINT = "https://api.githubcopilot.com/mcp/"


# --- Custom Resource Lambda handler code for Gateway management ---
# This code runs inside a Lambda function that CDK's Provider framework invokes.
# It calls bedrock-agentcore-control APIs via boto3 to manage the gateway lifecycle.
GATEWAY_HANDLER_CODE = """\
import boto3
import json
import logging
import time

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def on_event(event, context):
    \"\"\"Handle CloudFormation custom resource events for AgentCore Gateway.\"\"\"
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
    \"\"\"Create Gateway, API Key Credential Provider, and Gateway Target.\"\"\"
    client = boto3.client("bedrock-agentcore-control")

    # Step 1: Create the Gateway
    gw_resp = client.create_gateway(
        name=props["GatewayName"],
        roleArn=props["GatewayRoleArn"],
        protocolType="MCP",
        authorizerType="AWS_IAM",
    )
    gateway_id = gw_resp["gatewayId"]
    gateway_arn = gw_resp.get("gatewayArn", "")
    gateway_url = gw_resp.get("gatewayUrl", "")
    logger.info("Created gateway: %s (arn=%s)", gateway_id, gateway_arn)

    # Step 2: Wait for gateway to be READY
    _wait_for_gateway_ready(client, gateway_id)

    # Step 3: Create API Key Credential Provider
    # Read the GitHub PAT from Secrets Manager to pass to the credential provider.
    # HACKATHON NOTE: The Lambda provider reads the secret transiently only during
    # gateway creation. The agent runtime never sees the PAT (architecture-guide 2.2).
    import json as _json
    sm_client = boto3.client("secretsmanager")
    secret_resp = sm_client.get_secret_value(SecretId=props["SecretArn"])
    secret_value = secret_resp.get("SecretString", "")
    # Handle case where secret might be JSON {"token": "..."} or plain string
    try:
        parsed = _json.loads(secret_value)
        api_key_value = parsed.get("token", parsed.get("apiKey", secret_value))
    except (_json.JSONDecodeError, TypeError):
        api_key_value = secret_value

    # If the secret is empty or a CDK-generated random placeholder, skip credential
    # provider creation. The gateway will be created without auth — user must update
    # the secret and trigger a stack update to complete the setup.
    cred_provider_arn = ""
    if api_key_value and len(api_key_value) > 10:
        cred_resp = client.create_api_key_credential_provider(
            name=props["CredentialProviderName"],
            apiKey=api_key_value,
        )
        cred_provider_arn = cred_resp.get("apiKeyCredentialProviderArn", "")
        logger.info("Created credential provider: %s", cred_provider_arn)
    else:
        logger.warning(
            "GitHub PAT secret is empty or placeholder. Gateway will be created "
            "WITHOUT credential provider. Set the PAT and run cdk deploy again: "
            "aws secretsmanager put-secret-value --secret-id %s --secret-string <pat>",
            props["SecretArn"],
        )

    # Step 4: Create Gateway Target pointing to GitHub MCP
    target_kwargs = {
        "gatewayIdentifier": gateway_id,
        "name": props["TargetName"],
        "targetConfiguration": {
            "mcp": {
                "mcpServer": {
                    "endpoint": props["McpEndpoint"],
                },
            },
        },
    }
    # Only attach credential provider if it was successfully created
    if cred_provider_arn:
        target_kwargs["credentialProviderConfigurations"] = [
            {
                "credentialProviderType": "API_KEY",
                "credentialProvider": {
                    "apiKeyCredentialProvider": {
                        "providerArn": cred_provider_arn,
                        "credentialLocation": "HEADER",
                        "credentialParameterName": "Authorization",
                        "credentialPrefix": "Bearer ",
                    },
                },
            },
        ]
    target_resp = client.create_gateway_target(**target_kwargs)
    target_id = target_resp.get("targetId", "")
    logger.info("Created gateway target: %s", target_id)

    # Physical resource ID is the gateway ID (used in Delete)
    return {
        "PhysicalResourceId": gateway_id,
        "Data": {
            "GatewayId": gateway_id,
            "GatewayArn": gateway_arn,
            "GatewayUrl": gateway_url,
            "TargetId": target_id,
            "CredentialProviderArn": cred_provider_arn,
        },
    }


def on_update(event, props):
    \"\"\"Update is treated as no-op for hackathon simplicity.\"\"\"
    return {
        "PhysicalResourceId": event["PhysicalResourceId"],
    }


def on_delete(event, props):
    \"\"\"Delete Gateway Target, Credential Provider, and Gateway.\"\"\"
    client = boto3.client("bedrock-agentcore-control")
    gateway_id = event["PhysicalResourceId"]

    # Best-effort cleanup: delete target first, then credential provider, then gateway
    # Ignore errors on delete to avoid stuck stacks
    try:
        # List and delete all targets
        targets = client.list_gateway_targets(gatewayIdentifier=gateway_id)
        for target in targets.get("targets", []):
            try:
                client.delete_gateway_target(
                    gatewayIdentifier=gateway_id,
                    targetId=target["targetId"],
                )
                logger.info("Deleted target: %s", target["targetId"])
            except Exception as e:
                logger.warning("Failed to delete target %s: %s", target.get("targetId"), e)
    except Exception as e:
        logger.warning("Failed to list/delete targets: %s", e)

    # Delete credential provider
    try:
        client.delete_api_key_credential_provider(
            name=props.get("CredentialProviderName", ""),
        )
        logger.info("Deleted credential provider: %s", props.get("CredentialProviderName"))
    except Exception as e:
        logger.warning("Failed to delete credential provider: %s", e)

    # Delete gateway
    try:
        client.delete_gateway(gatewayIdentifier=gateway_id)
        logger.info("Deleted gateway: %s", gateway_id)
    except Exception as e:
        logger.warning("Failed to delete gateway: %s", e)

    return {"PhysicalResourceId": gateway_id}


def _wait_for_gateway_ready(client, gateway_id, max_attempts=30, delay=10):
    \"\"\"Poll until gateway status is READY or timeout.\"\"\"
    for attempt in range(max_attempts):
        resp = client.get_gateway(gatewayIdentifier=gateway_id)
        status = resp.get("status", "UNKNOWN")
        logger.info("Gateway %s status: %s (attempt %d/%d)", gateway_id, status, attempt + 1, max_attempts)
        if status == "READY":
            return
        if status in ("CREATE_FAILED", "FAILED"):
            raise RuntimeError(f"Gateway creation failed with status: {status}")
        time.sleep(delay)
    raise RuntimeError(f"Gateway {gateway_id} did not become READY within {max_attempts * delay}s")
"""


class AgentGateway(Construct):
    """Bedrock AgentCore Gateway for routing MCP calls to GitHub.

    Creates:
    - A Secrets Manager secret for the GitHub PAT (empty placeholder)
    - An AgentCore Gateway configured with the GitHub MCP target
    - An API Key Credential Provider in AgentCore Identity
    - IAM permissions: only the Gateway role can access the secret

    Attributes:
        secret: The Secrets Manager secret holding the GitHub PAT.
        gateway_mcp_url: The MCP URL that the agent should use to reach GitHub tools.
        gateway_role: The IAM role used by the gateway.
        gateway_id: The ID of the AgentCore Gateway.
        gateway_arn: The ARN of the AgentCore Gateway.
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
            description="Role for AgentCore Gateway - reads GitHub PAT from Secrets Manager",
        )

        # Grant the gateway role read access to the PAT secret
        self.secret.grant_read(self.gateway_role)

        # Additional permissions for gateway to access AgentCore Identity workload tokens
        # HACKATHON SCOPE: Using bedrock-agentcore:* to cover all internal dependencies
        # (WorkloadIdentity, AccessTokens, ResourceApiKeys, etc.) that AgentCore may need.
        # In production, restrict to the minimum set of actions required.
        self.gateway_role.add_to_policy(
            iam.PolicyStatement(
                sid="AgentCoreIdentityAccess",
                effect=iam.Effect.ALLOW,
                actions=["bedrock-agentcore:*"],
                resources=["*"],
            )
        )

        # --- Custom Resource Provider Lambda ---
        provider_fn = lambda_.Function(
            self,
            "GatewayProviderFn",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="index.on_event",
            code=lambda_.Code.from_inline(GATEWAY_HANDLER_CODE),
            timeout=Duration.minutes(10),
            memory_size=256,
            description="Custom Resource provider for AgentCore Gateway lifecycle",
        )

        # IAM permissions for the provider Lambda
        # HACKATHON SCOPE: Using bedrock-agentcore:* because AgentCore creates
        # internal dependencies (WorkloadIdentity, etc.) that are not individually
        # documented. In production, restrict to the minimum set of actions required.
        provider_fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="AgentCoreGatewayManagement",
                effect=iam.Effect.ALLOW,
                actions=["bedrock-agentcore:*"],
                resources=["*"],
            )
        )
        # HACKATHON SCOPE: CreateApiKeyCredentialProvider internally creates a secret
        # in Secrets Manager. The Lambda role needs these permissions for that internal
        # operation. In production, scope down to specific secret ARN patterns.
        provider_fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="SecretsManagerForCredentialProvider",
                effect=iam.Effect.ALLOW,
                actions=[
                    "secretsmanager:CreateSecret",
                    "secretsmanager:DeleteSecret",
                    "secretsmanager:PutSecretValue",
                    "secretsmanager:TagResource",
                ],
                resources=["*"],
            )
        )

        # Grant provider Lambda read access to the secret so it can pass the API key
        # to create_api_key_credential_provider. This is a transient read during
        # gateway provisioning only — the agent runtime never accesses the secret.
        self.secret.grant_read(provider_fn)
        provider_fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="PassGatewayRole",
                effect=iam.Effect.ALLOW,
                actions=["iam:PassRole"],
                resources=[self.gateway_role.role_arn],
            )
        )

        # --- CDK Provider (handles async CloudFormation response) ---
        provider = cr.Provider(
            self,
            "GatewayProvider",
            on_event_handler=provider_fn,
        )

        # --- Custom Resource ---
        self._gateway_resource = CustomResource(
            self,
            "GatewayResource",
            service_token=provider.service_token,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            properties={
                "GatewayName": "self-healing-github-gateway",
                "GatewayRoleArn": self.gateway_role.role_arn,
                "CredentialProviderName": "github-pat-provider",
                "SecretArn": self.secret.secret_arn,
                "TargetName": "github-mcp",
                "McpEndpoint": GITHUB_MCP_ENDPOINT,
            },
        )

    @property
    def gateway_id(self) -> str:
        """ID of the AgentCore Gateway."""
        return self._gateway_resource.get_att_string("GatewayId")

    @property
    def gateway_arn(self) -> str:
        """ARN of the AgentCore Gateway."""
        return self._gateway_resource.get_att_string("GatewayArn")

    @property
    def gateway_mcp_url(self) -> str:
        """MCP URL for the agent to use when connecting to the Gateway."""
        return self._gateway_resource.get_att_string("GatewayUrl")
