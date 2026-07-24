"""CDK infrastructure tests for the Todo CRUD API.

Validates:
  - DynamoDB table configuration (PK, billing mode, deletion policy)
  - 5 Lambda functions with correct tags and environment
  - API Gateway configuration with Usage Plan and API Key
  - Observability resources (Metric Filters, Alarms)
"""
import sys
from pathlib import Path

import pytest

# Add project root to path so infra package is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import aws_cdk as cdk
from aws_cdk import assertions

from infra.stacks.stateful_stack import StatefulStack
from infra.stacks.stateless_stack import StatelessStack


@pytest.fixture(scope="module")
def stateful_template():
    app = cdk.App()
    stack = StatefulStack(app, "TestStatefulStack", env=cdk.Environment(region="eu-west-1"))
    return assertions.Template.from_stack(stack)


@pytest.fixture(scope="module")
def stateless_template():
    app = cdk.App()
    stateful = StatefulStack(app, "TestStatefulForStateless", env=cdk.Environment(region="eu-west-1"))
    stack = StatelessStack(
        app,
        "TestStatelessStack",
        table=stateful.table,
        env=cdk.Environment(region="eu-west-1"),
    )
    return assertions.Template.from_stack(stack)


# -----------------------------------------------------------------------
# 7.2 — DynamoDB Table
# -----------------------------------------------------------------------
class TestDynamoDBTable:
    """Verify DynamoDB table configuration."""

    def test_table_has_task_id_partition_key(self, stateful_template):
        """Table has partition key 'task_id' of type String."""
        stateful_template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {
                "KeySchema": [
                    {"AttributeName": "task_id", "KeyType": "HASH"}
                ],
                "AttributeDefinitions": [
                    {"AttributeName": "task_id", "AttributeType": "S"}
                ],
            },
        )

    def test_table_uses_pay_per_request(self, stateful_template):
        """Table uses on-demand billing (PAY_PER_REQUEST)."""
        stateful_template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {"BillingMode": "PAY_PER_REQUEST"},
        )

    def test_table_deletion_policy_is_delete(self, stateful_template):
        """Table has DeletionPolicy: Delete (RemovalPolicy.DESTROY)."""
        stateful_template.has_resource(
            "AWS::DynamoDB::Table",
            {"DeletionPolicy": "Delete"},
        )


# -----------------------------------------------------------------------
# 7.3 — Lambda Functions
# -----------------------------------------------------------------------
class TestLambdaFunctions:
    """Verify Lambda function configuration."""

    def test_five_lambda_functions_exist(self, stateless_template):
        """Exactly 5 Lambda functions are created."""
        resources = stateless_template.find_resources("AWS::Lambda::Function")
        assert len(resources) == 5, f"Expected 5 Lambda functions, got {len(resources)}"

    def test_lambda_runtime_is_python313(self, stateless_template):
        """All Lambdas use Python 3.13 runtime."""
        resources = stateless_template.find_resources(
            "AWS::Lambda::Function",
            {"Properties": {"Runtime": "python3.13"}},
        )
        assert len(resources) == 5

    def test_lambda_architecture_is_arm64(self, stateless_template):
        """All Lambdas use arm64 architecture."""
        resources = stateless_template.find_resources(
            "AWS::Lambda::Function",
            {"Properties": {"Architectures": ["arm64"]}},
        )
        assert len(resources) == 5

    def test_lambda_has_table_name_env_var(self, stateless_template):
        """All Lambdas have TABLE_NAME environment variable."""
        resources = stateless_template.find_resources("AWS::Lambda::Function")
        for logical_id, resource in resources.items():
            env_vars = resource["Properties"]["Environment"]["Variables"]
            assert "TABLE_NAME" in env_vars, (
                f"Lambda {logical_id} missing TABLE_NAME env var"
            )

    def test_lambda_github_repo_tag(self, stateless_template):
        """All Lambdas have github-repo tag with correct value."""
        resources = stateless_template.find_resources("AWS::Lambda::Function")
        for logical_id, resource in resources.items():
            tags = resource["Properties"].get("Tags", [])
            github_tag = [t for t in tags if t["Key"] == "github-repo"]
            assert len(github_tag) == 1, (
                f"Lambda {logical_id} missing github-repo tag"
            )
            assert github_tag[0]["Value"] == "David1187/hackaton-kiro-agente-cloudwatch"


# -----------------------------------------------------------------------
# 7.4 — API Gateway
# -----------------------------------------------------------------------
class TestApiGateway:
    """Verify API Gateway configuration."""

    def test_rest_api_exists(self, stateless_template):
        """A REST API resource exists."""
        stateless_template.resource_count_is("AWS::ApiGateway::RestApi", 1)

    def test_usage_plan_exists(self, stateless_template):
        """A Usage Plan exists with correct throttle and quota settings."""
        stateless_template.has_resource_properties(
            "AWS::ApiGateway::UsagePlan",
            {
                "Throttle": {
                    "RateLimit": 100,
                    "BurstLimit": 200,
                },
                "Quota": {
                    "Limit": 10000,
                    "Period": "DAY",
                },
            },
        )

    def test_api_key_exists(self, stateless_template):
        """An API Key resource exists."""
        stateless_template.resource_count_is("AWS::ApiGateway::ApiKey", 1)

    def test_api_key_source_is_header(self, stateless_template):
        """API uses HEADER as API key source via OpenAPI extension."""
        resources = stateless_template.find_resources("AWS::ApiGateway::RestApi")
        for _logical_id, resource in resources.items():
            body = resource["Properties"].get("Body", {})
            api_key_source = body.get("x-amazon-apigateway-api-key-source")
            assert api_key_source == "HEADER", (
                f"Expected x-amazon-apigateway-api-key-source: HEADER, got: {api_key_source}"
            )

    def test_openapi_body_has_five_operations(self, stateless_template):
        """The OpenAPI spec embedded in the RestApi defines 5 operations."""
        resources = stateless_template.find_resources("AWS::ApiGateway::RestApi")
        for _logical_id, resource in resources.items():
            body = resource["Properties"].get("Body", {})
            paths = body.get("paths", {})
            # /tasks has POST and GET, /tasks/{task_id} has GET, PUT, DELETE
            operations = []
            for path, methods in paths.items():
                for method in methods:
                    if method in ("get", "post", "put", "delete", "patch"):
                        operations.append(f"{method.upper()} {path}")
            assert len(operations) == 5, (
                f"Expected 5 operations, found {len(operations)}: {operations}"
            )

    def test_all_operations_require_api_key(self, stateless_template):
        """All operations in the OpenAPI spec have security requiring api_key."""
        resources = stateless_template.find_resources("AWS::ApiGateway::RestApi")
        for _logical_id, resource in resources.items():
            body = resource["Properties"].get("Body", {})
            paths = body.get("paths", {})
            for path, methods in paths.items():
                for method, config in methods.items():
                    if method in ("get", "post", "put", "delete"):
                        security = config.get("security", [])
                        has_api_key = any("api_key" in s for s in security)
                        assert has_api_key, (
                            f"{method.upper()} {path} missing api_key security"
                        )


# -----------------------------------------------------------------------
# 7.5 — Observability
# -----------------------------------------------------------------------
class TestObservability:
    """Verify observability configuration."""

    def test_five_metric_filters_exist(self, stateless_template):
        """Five Metric Filters are created (one per Lambda)."""
        stateless_template.resource_count_is("AWS::Logs::MetricFilter", 5)

    def test_metric_filter_pattern_is_error(self, stateless_template):
        """All Metric Filters use 'ERROR:' as the filter pattern."""
        resources = stateless_template.find_resources("AWS::Logs::MetricFilter")
        for logical_id, resource in resources.items():
            pattern = resource["Properties"]["FilterPattern"]
            # CDK renders literal patterns with quotes
            assert "ERROR:" in pattern or '"ERROR:"' in pattern, (
                f"MetricFilter {logical_id} has unexpected pattern: {pattern}"
            )

    def test_five_alarms_exist(self, stateless_template):
        """Five CloudWatch Alarms are created (one per Lambda)."""
        stateless_template.resource_count_is("AWS::CloudWatch::Alarm", 5)

    def test_alarm_threshold_is_one(self, stateless_template):
        """All alarms have threshold >= 1."""
        resources = stateless_template.find_resources("AWS::CloudWatch::Alarm")
        for logical_id, resource in resources.items():
            threshold = resource["Properties"]["Threshold"]
            assert threshold == 1, (
                f"Alarm {logical_id} has threshold {threshold}, expected 1"
            )

    def test_five_log_groups_exist(self, stateless_template):
        """At least 5 Log Groups are created (one per Lambda, plus CDK provider)."""
        resources = stateless_template.find_resources("AWS::Logs::LogGroup")
        assert len(resources) >= 5, f"Expected at least 5 Log Groups, got {len(resources)}"

    def test_log_group_retention_is_one_week(self, stateless_template):
        """Application Log Groups (non-provider) have 7-day retention."""
        resources = stateless_template.find_resources("AWS::Logs::LogGroup")
        app_log_groups = {
            lid: r for lid, r in resources.items()
            if r["Properties"].get("RetentionInDays") == 7
        }
        assert len(app_log_groups) == 5, (
            f"Expected 5 log groups with 7-day retention, got {len(app_log_groups)}"
        )
