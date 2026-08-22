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

    # Step 5: Enable service-provided (vended) logs for the Gateway.
    # AgentCore does NOT create a CloudWatch log group for gateway resources
    # automatically (unlike Runtime, which does). Without this, errors like
    # AccessDeniedException on the outbound credential fetch are invisible
    # outside exceptionLevel=DEBUG probing of individual tool calls.
    log_delivery_data = _ensure_gateway_log_delivery(gateway_id, gateway_arn)

    # Physical resource ID is the gateway ID (used in Delete)
    return {
        "PhysicalResourceId": gateway_id,
        "Data": {
            "GatewayId": gateway_id,
            "GatewayArn": gateway_arn,
            "GatewayUrl": gateway_url,
            "TargetId": target_id,
            "CredentialProviderArn": cred_provider_arn,
            **log_delivery_data,
        },
    }


def _ensure_gateway_log_delivery(gateway_id, gateway_arn):
    \"\"\"Idempotently create/verify the log group + delivery pipeline for a Gateway.

    Safe to call on every Create AND every Update: each underlying call
    (create_log_group, put_delivery_source, put_delivery_destination) is
    either a no-op-on-exists or an upsert. create_delivery is the one
    exception — it errors if a delivery between the same source/destination
    already exists, so that failure is caught and treated as "already set up".

    This must run on Update too (not just Create) because CloudFormation
    only invokes on_create for a resource once; once the Gateway physical
    resource already exists, every subsequent `cdk deploy` calls on_update,
    and log delivery must still be verified/created there for gateways that
    existed before this logging feature was added.
    \"\"\"
    log_group_name = f"/aws/vendedlogs/bedrock-agentcore/gateway/APPLICATION_LOGS/{gateway_id}"
    logs_client = boto3.client("logs")
    try:
        logs_client.create_log_group(logGroupName=log_group_name)
        logger.info("Created log group: %s", log_group_name)
    except logs_client.exceptions.ResourceAlreadyExistsException:
        logger.info("Log group already exists: %s", log_group_name)

    log_group_arn = (
        f"arn:aws:logs:{boto3.session.Session().region_name}:"
        f"{boto3.client('sts').get_caller_identity()['Account']}:log-group:{log_group_name}"
    )

    delivery_source_name = f"{gateway_id}-logs-source"
    delivery_destination_name = f"{gateway_id}-logs-destination"

    # put_delivery_source and put_delivery_destination are upserts (PUT
    # semantics) — safe to call every time.
    logs_client.put_delivery_source(
        name=delivery_source_name,
        logType="APPLICATION_LOGS",
        resourceArn=gateway_arn,
    )
    dest_resp = logs_client.put_delivery_destination(
        name=delivery_destination_name,
        deliveryDestinationType="CWL",
        deliveryDestinationConfiguration={"destinationResourceArn": log_group_arn},
    )
    delivery_destination_arn = dest_resp["deliveryDestination"]["arn"]

    # create_delivery is NOT an upsert: calling it again for a source that
    # already has a delivery to this destination raises an error. Treat
    # that as "already configured" rather than failing the whole resource.
    delivery_id = None
    try:
        delivery_resp = logs_client.create_delivery(
            deliverySourceName=delivery_source_name,
            deliveryDestinationArn=delivery_destination_arn,
        )
        delivery_id = delivery_resp["delivery"]["id"]
        logger.info(
            "Enabled gateway log delivery: source=%s destination=%s delivery=%s log_group=%s",
            delivery_source_name,
            delivery_destination_name,
            delivery_id,
            log_group_name,
        )
    except Exception as e:
        logger.info(
            "create_delivery skipped for source=%s (likely already exists): %s",
            delivery_source_name,
            e,
        )
        try:
            existing = logs_client.describe_deliveries().get("deliveries", [])
            for d in existing:
                if d.get("deliverySourceName") == delivery_source_name:
                    delivery_id = d["id"]
                    break
        except Exception as lookup_err:
            logger.warning("Could not look up existing delivery id: %s", lookup_err)

    return {
        "LogGroupName": log_group_name,
        "DeliveryId": delivery_id or "",
        "DeliverySourceName": delivery_source_name,
        "DeliveryDestinationName": delivery_destination_name,
    }


def on_update(event, props):
    \"\"\"Sync the API Key Credential Provider with the current secret value.

    The credential provider stores its own copy of the API key at creation
    time (create_api_key_credential_provider); it does NOT re-read Secrets
    Manager on its own. If the GitHub PAT is rotated in Secrets Manager
    after the gateway/provider already exist, every MCP call keeps using
    the stale key unless this update explicitly pushes the new value via
    update_api_key_credential_provider. Without this, GitHub silently
    rejects the outdated PAT and the Gateway surfaces it as a generic
    'An internal error occurred. Please retry later.' on every tool call.
    \"\"\"
    client = boto3.client("bedrock-agentcore-control")
    sm_client = boto3.client("secretsmanager")

    secret_resp = sm_client.get_secret_value(SecretId=props["SecretArn"])
    secret_value = secret_resp.get("SecretString", "")
    try:
        parsed = json.loads(secret_value)
        api_key_value = parsed.get("token", parsed.get("apiKey", secret_value))
    except (json.JSONDecodeError, TypeError):
        api_key_value = secret_value

    if api_key_value and len(api_key_value) > 10:
        try:
            client.update_api_key_credential_provider(
                name=props["CredentialProviderName"],
                apiKey=api_key_value,
            )
            logger.info(
                "Synced credential provider '%s' with current secret value",
                props["CredentialProviderName"],
            )
        except Exception as e:
            logger.warning(
                "Failed to update credential provider '%s' (non-fatal): %s",
                props["CredentialProviderName"],
                e,
            )
    else:
        logger.warning(
            "GitHub PAT secret is empty or placeholder; skipping credential "
            "provider update."
        )

    # Ensure gateway log delivery exists. This must run on Update too, not
    # just Create, because CloudFormation only invokes on_create once per
    # physical resource — Gateways created before this logging feature was
    # added would otherwise never get their log group/delivery set up.
    gateway_id = event["PhysicalResourceId"]
    log_delivery_data = {}
    try:
        gw_resp = client.get_gateway(gatewayIdentifier=gateway_id)
        gateway_arn = gw_resp.get("gatewayArn", "")
        if gateway_arn:
            log_delivery_data = _ensure_gateway_log_delivery(gateway_id, gateway_arn)
    except Exception as e:
        logger.warning("Failed to ensure gateway log delivery on update: %s", e)

    return {
        "PhysicalResourceId": event["PhysicalResourceId"],
        "Data": log_delivery_data,
    }


def on_delete(event, props):
    \"\"\"Delete Gateway Target, Credential Provider, Log Delivery, and Gateway.\"\"\"
    client = boto3.client("bedrock-agentcore-control")
    gateway_id = event["PhysicalResourceId"]

    # Delete log delivery resources first (delivery -> delivery source ->
    # delivery destination), per AWS guidance on removing vended log delivery
    # when the log-generating resource is deleted.
    logs_client = boto3.client("logs")
    delivery_source_name = f"{gateway_id}-logs-source"
    delivery_destination_name = f"{gateway_id}-logs-destination"
    try:
        deliveries = logs_client.describe_deliveries().get("deliveries", [])
        for d in deliveries:
            if d.get("deliverySourceName") == delivery_source_name:
                try:
                    logs_client.delete_delivery(id=d["id"])
                    logger.info("Deleted delivery: %s", d["id"])
                except Exception as e:
                    logger.warning("Failed to delete delivery %s: %s", d.get("id"), e)
    except Exception as e:
        logger.warning("Failed to list/delete deliveries: %s", e)
    try:
        logs_client.delete_delivery_source(name=delivery_source_name)
        logger.info("Deleted delivery source: %s", delivery_source_name)
    except Exception as e:
        logger.warning("Failed to delete delivery source: %s", e)
    try:
        logs_client.delete_delivery_destination(name=delivery_destination_name)
        logger.info("Deleted delivery destination: %s", delivery_destination_name)
    except Exception as e:
        logger.warning("Failed to delete delivery destination: %s", e)

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

        # Permission to read the INTERNAL secret that AgentCore Identity creates
        # when create_api_key_credential_provider runs (distinct from self.secret,
        # which only holds our copy of the PAT). At tools/call time, the Gateway
        # assumes this role and must call secretsmanager:GetSecretValue on that
        # internal secret to inject the credential into the outbound request to
        # the MCP target — without this, every tools/call fails with
        # AccessDeniedException even though tools/list (metadata only) still
        # works and the gateway/target both report status=READY.
        self.gateway_role.add_to_policy(
            iam.PolicyStatement(
                sid="ReadAgentCoreIdentityApiKeySecret",
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    f"arn:aws:secretsmanager:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:"
                    "secret:bedrock-agentcore-identity!default/apikey/*"
                ],
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

        # Permissions for the provider Lambda to enable vended log delivery
        # for the Gateway (log group + delivery source/destination/delivery).
        # AgentCore does not create these automatically for Gateway resources.
        provider_fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="ConfigureGatewayLogDelivery",
                effect=iam.Effect.ALLOW,
                actions=[
                    "logs:CreateLogGroup",
                    "logs:PutDeliverySource",
                    "logs:PutDeliveryDestination",
                    "logs:CreateDelivery",
                    "logs:DeleteDelivery",
                    "logs:DeleteDeliverySource",
                    "logs:DeleteDeliveryDestination",
                    "logs:DescribeDeliveries",
                    "logs:DescribeDeliverySources",
                    "logs:DescribeDeliveryDestinations",
                    "logs:PutResourcePolicy",
                    "logs:DescribeResourcePolicies",
                    "logs:DescribeLogGroups",
                ],
                resources=["*"],
            )
        )
        provider_fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="ResolveAccountIdForLogGroupArn",
                effect=iam.Effect.ALLOW,
                actions=["sts:GetCallerIdentity"],
                resources=["*"],
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

    @property
    def gateway_log_group_name(self) -> str:
        """CloudWatch Logs group name receiving the Gateway's vended APPLICATION_LOGS.

        AgentCore does not create this automatically for Gateway resources
        (unlike Runtime, which gets a log group by default). See
        architecture-guide.md / observability notes for why this is needed:
        without it, outbound-auth errors (e.g. AccessDeniedException on the
        credential provider's internal secret) are invisible except via
        exceptionLevel=DEBUG probing of individual tool calls.
        """
        return self._gateway_resource.get_att_string("LogGroupName")
