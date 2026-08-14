"""AgentStack — Self-Healing Agent infrastructure (stateless).

Assembles:
  - ObservabilityWiring (reuses alarms from StatelessStack)
  - AgentGateway (Secrets Manager + Gateway to GitHub MCP)
  - AgentRuntime (direct code deployment on AgentCore)
  - Bridge Lambda (EventBridge → invokes AgentCore Runtime via SDK)
  - EventBridge Rule (Alarm State Change → Bridge Lambda)

This is a stateless stack: it can be destroyed and redeployed without
affecting the CRUD infrastructure or DynamoDB data.
"""
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as lambda_,
)
from constructs import Construct

from ..constructs.agent_gateway import AgentGateway
from ..constructs.agent_runtime import AgentRuntime
from ..constructs.observability_wiring import ObservabilityWiring

# Path to agent service code
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
AGENT_CODE_PATH = str(PROJECT_ROOT / "services" / "self_healing_agent")

# Inline code for the bridge Lambda that invokes AgentCore Runtime via SDK
BRIDGE_LAMBDA_CODE = """\
\"\"\"Bridge Lambda: receives EventBridge event, invokes AgentCore Runtime.\"\"\"
import json
import logging
import os

import boto3
import botocore.exceptions

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

client = boto3.client("bedrock-agentcore")

AGENT_RUNTIME_ARN = os.environ["AGENT_RUNTIME_ARN"]
AGENT_RUNTIME_ENDPOINT_ID = os.environ["AGENT_RUNTIME_ENDPOINT_ID"]


def handler(event, context):
    \"\"\"Forward EventBridge alarm event to AgentCore Runtime.\"\"\"
    logger.info("Received EventBridge event: %s", json.dumps(event))
    try:
        payload = json.dumps({"input": {"prompt": json.dumps(event)}})
        runtimeSessionId = context.aws_request_id + "0" * (33 - len(context.aws_request_id))
        response = client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            runtimeSessionId=runtimeSessionId,
            payload=payload,
            qualifier=AGENT_RUNTIME_ENDPOINT_ID,
        )
        logger.info("Runtime SessionId AgentCore: %s",runtimeSessionId)
        logger.info("AgentCore invocation successful")
        return {"statusCode": 200, "body": "Agent invoked"}
    except botocore.exceptions.ClientError as exc:
        logger.error("ERROR: Failed to invoke AgentCore Runtime", exc_info=True)
        return {"statusCode": 500, "body": str(exc)}
    except Exception:
        logger.error("ERROR: Unexpected error invoking AgentCore Runtime", exc_info=True)
        return {"statusCode": 500, "body": "Internal error"}
"""


class AgentStack(cdk.Stack):
    """Stack containing the Self-Healing Agent infrastructure."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        alarm_arns: list[str],
        alarm_names: list[str],
        log_group_names: list[str] | None = None,
        function_names: list[str] | None = None,
        **kwargs,
    ) -> None:
        """Initialize the Agent stack.

        Args:
            scope: CDK scope.
            construct_id: Construct ID.
            alarm_arns: CloudWatch Alarm ARNs from the StatelessStack.
            alarm_names: CloudWatch Alarm names (for EventBridge pattern matching).
            log_group_names: Log group names for CRUD Lambdas (optional, for create mode).
            function_names: Lambda function names, index-aligned with alarm_names.
                The alarm metric has no dimensions, so the EventBridge event
                never carries the function name directly — it must be
                resolved from a synth-time alarmName -> functionName map.
            **kwargs: Additional stack kwargs (env, etc.).
        """
        super().__init__(scope, construct_id, termination_protection=False, **kwargs)

        # --- Build alarmName -> functionName map (synth-time, tokens OK) ---
        # The Metric Filter behind each alarm has no dimensions, so the
        # EventBridge "Alarm State Change" event never contains the Lambda
        # function name. This map lets the agent resolve it from the one
        # piece of stable identifying data the event does carry: alarmName.
        alarm_function_map = dict(zip(alarm_names, function_names or []))
        alarm_function_map_json = self.to_json_string(alarm_function_map)

        # --- Observability Wiring (reuse existing alarms from StatelessStack) ---
        observability = ObservabilityWiring(
            self,
            "ObservabilityWiring",
            existing_alarm_arns=alarm_arns,
            log_group_names=log_group_names,
        )

        # --- AgentCore Gateway (GitHub MCP + PAT Secret) ---
        gateway = AgentGateway(self, "AgentGateway")

        # --- Convert log group names to ARNs for IAM scoping ---
        log_group_arns = [
            f"arn:aws:logs:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:log-group:{name}:*"
            for name in (log_group_names or [])
        ]

        # --- AgentCore Runtime (agent code + IAM) ---
        runtime = AgentRuntime(
            self,
            "AgentRuntime",
            agent_code_path=AGENT_CODE_PATH,
            gateway_mcp_url=gateway.gateway_mcp_url,
            gateway_arn=gateway.gateway_arn,
            log_group_arns=log_group_arns,
            alarm_function_map_json=alarm_function_map_json,
        )

        # --- Bridge Lambda: EventBridge → AgentCore Runtime ---
        bridge_fn = lambda_.Function(
            self,
            "AgentBridgeLambda",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="index.handler",
            code=lambda_.Code.from_inline(BRIDGE_LAMBDA_CODE),
            timeout=cdk.Duration.seconds(60),
            memory_size=128,
            description="Bridge: forwards EventBridge alarm events to AgentCore Runtime",
            environment={
                "AGENT_RUNTIME_ID": runtime.runtime_id,
                "AGENT_RUNTIME_ENDPOINT_ID": "DEFAULT",
                "AGENT_RUNTIME_ARN": runtime.runtime_arn,
            },
        )

        # Grant the bridge Lambda permission to invoke the AgentCore Runtime
        bridge_fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="InvokeAgentRuntime",
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:InvokeAgentRuntime",
                ],
                resources=["*"],
            )
        )

        # --- EventBridge Rule: Alarm State Change → Bridge Lambda ---
        event_pattern = events.EventPattern(
            source=["aws.cloudwatch"],
            detail_type=["CloudWatch Alarm State Change"],
            detail={
                "state": {"value": ["ALARM"]},
                "alarmName": alarm_names,
            },
        )

        rule = events.Rule(
            self,
            "AlarmStateChangeRule",
            description="Triggers Self-Healing Agent when a CRUD Lambda alarm fires",
            event_pattern=event_pattern,
        )

        # Add bridge Lambda as the target
        rule.add_target(targets.LambdaFunction(bridge_fn))

        # --- Outputs ---
        cdk.CfnOutput(
            self,
            "AgentRuntimeArn",
            value=runtime.runtime_arn,
            description="ARN of the Self-Healing Agent Runtime",
        )
        cdk.CfnOutput(
            self,
            "GatewayId",
            value=gateway.gateway_id,
            description="ID of the AgentCore Gateway",
        )
        cdk.CfnOutput(
            self,
            "GitHubPATSecretArn",
            value=gateway.secret.secret_arn,
            description="ARN of the GitHub PAT secret (set value manually post-deploy)",
        )
