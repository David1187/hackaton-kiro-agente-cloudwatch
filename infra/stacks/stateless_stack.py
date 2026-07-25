"""StatelessStack — Lambdas, API Gateway, and observability for the Todo CRUD API.

Contains:
  - 5 Lambda functions (one per CRUD operation)
  - API Gateway REST API defined via OpenAPI template (api/openapi.yaml)
  - Usage Plan + API Key
  - Observability: Log Groups, Metric Filters, CloudWatch Alarms
"""
import os
from pathlib import Path

import yaml
import aws_cdk as cdk
from aws_cdk import (
    aws_apigateway as apigateway,
    aws_cloudwatch as cloudwatch,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
)
from constructs import Construct

GITHUB_REPO_TAG = "David1187/hackaton-kiro-agente-cloudwatch"

# Project root directory (two levels up from this file's directory)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Path to services/crud_api (Lambda code)
SERVICES_DIR = str(PROJECT_ROOT / "services" / "crud_api")

# Path to OpenAPI template
OPENAPI_TEMPLATE_PATH = PROJECT_ROOT / "api" / "openapi.yaml"


class StatelessStack(cdk.Stack):
    """Stack containing stateless resources (Lambdas, API GW, observability)."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        table: dynamodb.ITable,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ---------------------------------------------------------------
        # 7.3 — Lambda functions
        # ---------------------------------------------------------------
        lambda_config = {
            "create": {
                "handler": "handlers.create_task.handler",
                "description": "Create a new Task",
            },
            "get": {
                "handler": "handlers.get_task.handler",
                "description": "Get a Task by task_id",
            },
            "list": {
                "handler": "handlers.list_tasks.handler",
                "description": "List all Tasks",
            },
            "update": {
                "handler": "handlers.update_task.handler",
                "description": "Update an existing Task",
            },
            "delete": {
                "handler": "handlers.delete_task.handler",
                "description": "Delete a Task by task_id",
            },
        }

        lambdas: dict[str, lambda_.Function] = {}

        for name, config in lambda_config.items():
            fn = lambda_.Function(
                self,
                f"Fn{name.capitalize()}",
                runtime=lambda_.Runtime.PYTHON_3_13,
                architecture=lambda_.Architecture.ARM_64,
                handler=config["handler"],
                code=lambda_.Code.from_asset(
                    SERVICES_DIR,
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
                ),
                description=config["description"],
                environment={"TABLE_NAME": table.table_name},
                timeout=cdk.Duration.seconds(10),
                memory_size=256,
            )
            # Tag: github-repo (functional, per Lambda)
            cdk.Tags.of(fn).add("github-repo", GITHUB_REPO_TAG)
            lambdas[name] = fn

        # ---------------------------------------------------------------
        # IAM: minimum privilege per operation via grant_*()
        # ---------------------------------------------------------------
        table.grant_write_data(lambdas["create"])
        table.grant_write_data(lambdas["update"])
        table.grant_write_data(lambdas["delete"])
        table.grant_read_data(lambdas["get"])
        table.grant_read_data(lambdas["list"])

        # ---------------------------------------------------------------
        # 7.5 — Observability: Log Group, Metric Filter, Alarm per Lambda
        # ---------------------------------------------------------------
        self._alarm_arns: list[str] = []
        self._alarm_names: list[str] = []
        self._log_group_names: list[str] = []

        for name, fn in lambdas.items():
            log_group = logs.LogGroup(
                self,
                f"LogGroup{name.capitalize()}",
                log_group_name=f"/aws/lambda/{fn.function_name}",
                retention=logs.RetentionDays.ONE_WEEK,
                removal_policy=cdk.RemovalPolicy.DESTROY,
            )
            self._log_group_names.append(f"/aws/lambda/{fn.function_name}")

            metric_filter = logs.MetricFilter(
                self,
                f"MetricFilter{name.capitalize()}",
                log_group=log_group,
                filter_pattern=logs.FilterPattern.literal('"ERROR:"'),
                metric_namespace="TodoCrudApi/Errors",
                metric_name=f"{name.capitalize()}ErrorCount",
                metric_value="1",
                default_value=0,
            )

            metric = metric_filter.metric(
                statistic="Sum",
                period=cdk.Duration.minutes(1),
            )

            alarm = cloudwatch.Alarm(
                self,
                f"Alarm{name.capitalize()}",
                metric=metric,
                threshold=1,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                alarm_description=f"Error detected in {name} Lambda handler",
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            self._alarm_arns.append(alarm.alarm_arn)
            self._alarm_names.append(alarm.alarm_name)

        # ---------------------------------------------------------------
        # 7.4 — API Gateway REST API (OpenAPI with CDK token substitution)
        # ---------------------------------------------------------------
        api_definition = self._build_openapi_definition(lambdas)

        api = apigateway.SpecRestApi(
            self,
            "TodoApi",
            api_definition=apigateway.ApiDefinition.from_inline(api_definition),
            rest_api_name="TodoCrudApi",
            deploy_options=apigateway.StageOptions(stage_name="prod"),
        )

        # Usage Plan + API Key
        plan = api.add_usage_plan(
            "UsagePlan",
            name="TodoCrudUsagePlan",
            throttle=apigateway.ThrottleSettings(
                rate_limit=100,
                burst_limit=200,
            ),
            quota=apigateway.QuotaSettings(
                limit=10000,
                period=apigateway.Period.DAY,
            ),
        )

        api_key = api.add_api_key("ApiKey", api_key_name="TodoCrudApiKey")
        plan.add_api_key(api_key)
        plan.add_api_stage(stage=api.deployment_stage)

        # Grant API Gateway permission to invoke each Lambda
        for fn in lambdas.values():
            fn.grant_invoke(iam.ServicePrincipal("apigateway.amazonaws.com"))

        # ---------------------------------------------------------------
        # Outputs
        # ---------------------------------------------------------------
        cdk.CfnOutput(self, "ApiUrl", value=api.url or "")
        cdk.CfnOutput(self, "ApiKeyId", value=api_key.key_id)

    def _build_openapi_definition(
        self, lambdas: dict[str, lambda_.Function]
    ) -> dict:
        """Load api/openapi.yaml, substitute ARN placeholders with CDK tokens.

        The YAML file uses ${CreateFunctionArn}, ${GetFunctionArn}, etc. as
        placeholders in the integration URIs. These are replaced with proper
        Fn::Sub expressions that CloudFormation resolves at deploy time.

        We use ApiDefinition.from_inline() (not from_asset) because from_asset
        cannot resolve CDK/CloudFormation tokens embedded in the file.
        The api/openapi.yaml remains the source of truth for the API contract;
        this method only injects the dynamic ARN references.
        """

        def _make_integration_uri(fn: lambda_.Function) -> str:
            """Build Lambda invoke URI using Fn::Sub for ARN resolution."""
            return cdk.Fn.sub(
                "arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${FnArn}/invocations",
                {"FnArn": fn.function_arn},
            )

        # Read the OpenAPI template
        with open(OPENAPI_TEMPLATE_PATH, "r", encoding="utf-8") as f:
            spec = yaml.safe_load(f)

        # Map placeholder names to integration URIs
        placeholder_map = {
            "${CreateFunctionArn}": _make_integration_uri(lambdas["create"]),
            "${GetFunctionArn}": _make_integration_uri(lambdas["get"]),
            "${ListFunctionArn}": _make_integration_uri(lambdas["list"]),
            "${UpdateFunctionArn}": _make_integration_uri(lambdas["update"]),
            "${DeleteFunctionArn}": _make_integration_uri(lambdas["delete"]),
        }

        # Walk the spec dict and replace placeholder strings with CDK tokens
        def _substitute(obj):
            if isinstance(obj, str):
                if obj in placeholder_map:
                    return placeholder_map[obj]
                return obj
            elif isinstance(obj, dict):
                return {k: _substitute(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [_substitute(item) for item in obj]
            return obj

        return _substitute(spec)

    @property
    def alarm_arns(self) -> list[str]:
        """CloudWatch Alarm ARNs for the CRUD Lambda error alarms."""
        return self._alarm_arns

    @property
    def alarm_names(self) -> list[str]:
        """CloudWatch Alarm names for EventBridge pattern matching."""
        return self._alarm_names

    @property
    def log_group_names(self) -> list[str]:
        """Log Group names for the CRUD Lambdas."""
        return self._log_group_names
