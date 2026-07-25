"""Infrastructure tests for the Self-Healing Agent stack.

Validates:
- EventBridge Rule with correct event pattern and at least one target
- Secrets Manager secret exists
- IAM permissions: agent role has NO secretsmanager:GetSecretValue
- IAM permissions: gateway role HAS secretsmanager:GetSecretValue
- IAM permissions: agent role has bedrock:InvokeModel, logs, tags
- Bridge Lambda exists with correct environment
- No ECS/EC2/ECR resources
- Observability wiring in reuse mode doesn't duplicate Metric Filters
"""
import pytest
import aws_cdk as cdk
from aws_cdk import assertions

from infra.stacks.agent_stack import AgentStack


@pytest.fixture
def template():
    """Synthesize the AgentStack and return a Template for assertions."""
    app = cdk.App()

    # Provide mock alarm ARNs and names (simulating what StatelessStack exposes)
    stack = AgentStack(
        app,
        "TestAgentStack",
        alarm_arns=[
            "arn:aws:cloudwatch:eu-west-1:123456789012:alarm:TestAlarmCreate",
            "arn:aws:cloudwatch:eu-west-1:123456789012:alarm:TestAlarmGet",
        ],
        alarm_names=["TestAlarmCreate", "TestAlarmGet"],
        log_group_names=[
            "/aws/lambda/test-fn-create",
            "/aws/lambda/test-fn-get",
        ],
        env=cdk.Environment(region="eu-west-1", account="123456789012"),
    )

    return assertions.Template.from_stack(stack)


class TestEventBridgeRule:
    """Tests for the EventBridge Rule."""

    def test_rule_exists(self, template):
        template.resource_count_is("AWS::Events::Rule", 1)

    def test_rule_has_correct_source(self, template):
        template.has_resource_properties(
            "AWS::Events::Rule",
            {
                "EventPattern": assertions.Match.object_like(
                    {
                        "source": ["aws.cloudwatch"],
                        "detail-type": ["CloudWatch Alarm State Change"],
                    }
                )
            },
        )

    def test_rule_filters_alarm_state(self, template):
        template.has_resource_properties(
            "AWS::Events::Rule",
            {
                "EventPattern": assertions.Match.object_like(
                    {
                        "detail": assertions.Match.object_like(
                            {"state": {"value": ["ALARM"]}}
                        )
                    }
                )
            },
        )

    def test_rule_has_at_least_one_target(self, template):
        """EventBridge Rule must have at least one target configured."""
        resources = template.find_resources("AWS::Events::Rule")
        for logical_id, resource in resources.items():
            targets_list = resource.get("Properties", {}).get("Targets", [])
            assert len(targets_list) >= 1, (
                f"EventBridge Rule {logical_id} has no targets configured"
            )


class TestSecretsManager:
    """Tests for Secrets Manager resources."""

    def test_secret_exists(self, template):
        template.resource_count_is("AWS::SecretsManager::Secret", 1)

    def test_secret_has_description(self, template):
        template.has_resource_properties(
            "AWS::SecretsManager::Secret",
            {
                "Description": assertions.Match.string_like_regexp(
                    "GitHub.*Personal Access Token"
                ),
            },
        )


class TestIAMPermissions:
    """Tests for IAM role permissions."""

    def test_agent_role_has_no_secrets_access(self, template):
        """The agent runtime role must NOT have secretsmanager:GetSecretValue."""
        # Find all IAM policies
        policies = template.find_resources("AWS::IAM::Policy")

        for logical_id, policy in policies.items():
            statements = (
                policy.get("Properties", {})
                .get("PolicyDocument", {})
                .get("Statement", [])
            )
            for statement in statements:
                actions = statement.get("Action", [])
                if isinstance(actions, str):
                    actions = [actions]
                # If this policy has GetSecretValue, it must be the Gateway role not the Agent role
                if "secretsmanager:GetSecretValue" in actions:
                    # Verify it's attached to the Gateway role, not AgentRuntime role
                    roles = policy.get("Properties", {}).get("Roles", [])
                    for role_ref in roles:
                        if isinstance(role_ref, dict) and "Ref" in role_ref:
                            assert "Gateway" in role_ref["Ref"] or "gateway" in role_ref["Ref"].lower(), (
                                f"secretsmanager:GetSecretValue found on non-Gateway role: {role_ref['Ref']}"
                            )

    def test_gateway_role_has_secrets_access(self, template):
        """The gateway role MUST have secretsmanager:GetSecretValue."""
        policies = template.find_resources("AWS::IAM::Policy")

        found_secret_access = False
        for logical_id, policy in policies.items():
            statements = (
                policy.get("Properties", {})
                .get("PolicyDocument", {})
                .get("Statement", [])
            )
            for statement in statements:
                actions = statement.get("Action", [])
                if isinstance(actions, str):
                    actions = [actions]
                if "secretsmanager:GetSecretValue" in actions:
                    # Verify it's attached to a Gateway role
                    roles = policy.get("Properties", {}).get("Roles", [])
                    for role_ref in roles:
                        if isinstance(role_ref, dict) and "Ref" in role_ref:
                            if "Gateway" in role_ref["Ref"] or "gateway" in role_ref["Ref"].lower():
                                found_secret_access = True

        assert found_secret_access, (
            "Gateway role must have secretsmanager:GetSecretValue permission"
        )

    def test_agent_role_has_bedrock_invoke(self, template):
        """The agent runtime role must have bedrock:InvokeModel."""
        template.has_resource_properties(
            "AWS::IAM::Policy",
            {
                "PolicyDocument": assertions.Match.object_like(
                    {
                        "Statement": assertions.Match.array_with(
                            [
                                assertions.Match.object_like(
                                    {
                                        "Action": assertions.Match.array_with(
                                            ["bedrock:InvokeModel"]
                                        ),
                                        "Effect": "Allow",
                                    }
                                )
                            ]
                        )
                    }
                )
            },
        )

    def test_agent_role_has_logs_access(self, template):
        """The agent runtime role must have logs:FilterLogEvents."""
        template.has_resource_properties(
            "AWS::IAM::Policy",
            {
                "PolicyDocument": assertions.Match.object_like(
                    {
                        "Statement": assertions.Match.array_with(
                            [
                                assertions.Match.object_like(
                                    {
                                        "Action": assertions.Match.array_with(
                                            ["logs:FilterLogEvents"]
                                        ),
                                        "Effect": "Allow",
                                    }
                                )
                            ]
                        )
                    }
                )
            },
        )

    def test_agent_role_has_lambda_tags_access(self, template):
        """The agent runtime role must have lambda:ListTags."""
        template.has_resource_properties(
            "AWS::IAM::Policy",
            {
                "PolicyDocument": assertions.Match.object_like(
                    {
                        "Statement": assertions.Match.array_with(
                            [
                                assertions.Match.object_like(
                                    {
                                        "Action": assertions.Match.array_with(
                                            ["lambda:ListTags"]
                                        ),
                                        "Effect": "Allow",
                                    }
                                )
                            ]
                        )
                    }
                )
            },
        )


class TestBridgeLambda:
    """Tests for the bridge Lambda function."""

    def test_bridge_lambda_exists(self, template):
        """A bridge Lambda function exists for EventBridge → AgentCore."""
        resources = template.find_resources(
            "AWS::Lambda::Function",
            {"Properties": {"Description": assertions.Match.string_like_regexp("Bridge.*EventBridge")}},
        )
        assert len(resources) >= 1, "Expected at least one bridge Lambda function"

    def test_bridge_lambda_has_agent_runtime_env(self, template):
        """Bridge Lambda has AGENT_RUNTIME_ID environment variable."""
        resources = template.find_resources("AWS::Lambda::Function")
        found_bridge = False
        for logical_id, resource in resources.items():
            env_vars = resource.get("Properties", {}).get("Environment", {}).get("Variables", {})
            if "AGENT_RUNTIME_ID" in env_vars:
                found_bridge = True
                assert "AGENT_RUNTIME_ENDPOINT_ID" in env_vars, (
                    "Bridge Lambda missing AGENT_RUNTIME_ENDPOINT_ID env var"
                )
        assert found_bridge, "No Lambda with AGENT_RUNTIME_ID env var found"


class TestNoContainers:
    """Verify no ECS/EC2/ECR resources exist (architecture constraint)."""

    def test_no_ecs_resources(self, template):
        template.resource_count_is("AWS::ECS::Cluster", 0)
        template.resource_count_is("AWS::ECS::Service", 0)
        template.resource_count_is("AWS::ECS::TaskDefinition", 0)

    def test_no_ec2_instances(self, template):
        template.resource_count_is("AWS::EC2::Instance", 0)

    def test_no_ecr_repositories(self, template):
        template.resource_count_is("AWS::ECR::Repository", 0)


class TestObservabilityReuse:
    """Tests that reuse mode doesn't create new Metric Filters/Alarms."""

    def test_no_new_metric_filters_in_reuse_mode(self, template):
        """In reuse mode, no new Metric Filters should be created."""
        template.resource_count_is("AWS::Logs::MetricFilter", 0)

    def test_no_new_alarms_in_reuse_mode(self, template):
        """In reuse mode, no new CloudWatch Alarms should be created."""
        template.resource_count_is("AWS::CloudWatch::Alarm", 0)


class TestObservabilityCreate:
    """Tests that create mode DOES create Metric Filters/Alarms."""

    @pytest.fixture
    def create_template(self):
        """Synthesize with create_observability=true context."""
        app = cdk.App(context={"create_observability": "true"})

        stack = AgentStack(
            app,
            "TestAgentStackCreate",
            alarm_arns=[],
            alarm_names=[],
            log_group_names=[
                "/aws/lambda/test-fn-create",
                "/aws/lambda/test-fn-get",
            ],
            env=cdk.Environment(region="eu-west-1", account="123456789012"),
        )

        return assertions.Template.from_stack(stack)

    def test_creates_metric_filters(self, create_template):
        """In create mode, Metric Filters should be created."""
        create_template.resource_count_is("AWS::Logs::MetricFilter", 2)

    def test_creates_alarms(self, create_template):
        """In create mode, CloudWatch Alarms should be created."""
        create_template.resource_count_is("AWS::CloudWatch::Alarm", 2)
