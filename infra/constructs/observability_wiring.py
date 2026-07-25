"""Observability wiring construct — idempotent Metric Filter + Alarm handling.

In 'reuse' mode (default): accepts existing alarm ARNs from the StatelessStack
(which already creates Metric Filters and Alarms for the CRUD Lambdas).

In 'create' mode (CDK context 'create_observability=true'): creates its own
Metric Filter and Alarm. This mode exists only as a fallback if the agent stack
is deployed independently of the CRUD stack.

Decision is made at synth time via Python conditional (not CfnCondition),
per iac-standards.md section 5.
"""
import aws_cdk as cdk
from aws_cdk import (
    aws_cloudwatch as cloudwatch,
    aws_logs as logs,
)
from constructs import Construct


class ObservabilityWiring(Construct):
    """Idempotent observability wiring for the Self-Healing Agent.

    Attributes:
        alarm_arns: List of CloudWatch Alarm ARNs to monitor via EventBridge.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        existing_alarm_arns: list[str] | None = None,
        log_group_names: list[str] | None = None,
    ) -> None:
        """Initialize observability wiring.

        Args:
            scope: CDK scope.
            construct_id: Construct ID.
            existing_alarm_arns: Alarm ARNs from the StatelessStack (reuse mode).
            log_group_names: Log group names for the CRUD Lambdas (used in create mode).
        """
        super().__init__(scope, construct_id)

        self._alarm_arns: list[str] = []

        # Decision at synth time: reuse existing or create new
        create_observability = self.node.try_get_context("create_observability")

        if create_observability == "true" and log_group_names:
            # Create mode: build Metric Filters + Alarms from scratch
            for i, log_group_name in enumerate(log_group_names):
                log_group = logs.LogGroup.from_log_group_name(
                    self, f"LogGroup{i}", log_group_name
                )

                metric_filter = logs.MetricFilter(
                    self,
                    f"MetricFilter{i}",
                    log_group=log_group,
                    filter_pattern=logs.FilterPattern.literal('"ERROR:"'),
                    metric_namespace="SelfHealingAgent/Errors",
                    metric_name=f"ErrorCount{i}",
                    metric_value="1",
                    default_value=0,
                )

                metric = metric_filter.metric(
                    statistic="Sum",
                    period=cdk.Duration.minutes(1),
                )

                alarm = cloudwatch.Alarm(
                    self,
                    f"Alarm{i}",
                    metric=metric,
                    threshold=1,
                    evaluation_periods=1,
                    comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                    treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
                    alarm_description=f"Error detected in Lambda (log group: {log_group_name})",
                )

                self._alarm_arns.append(alarm.alarm_arn)
        elif existing_alarm_arns:
            # Reuse mode (default): just reference existing alarm ARNs
            self._alarm_arns = list(existing_alarm_arns)
        else:
            raise ValueError(
                "ObservabilityWiring requires either existing_alarm_arns (reuse mode) "
                "or log_group_names with create_observability=true context flag."
            )

    @property
    def alarm_arns(self) -> list[str]:
        """CloudWatch Alarm ARNs that the EventBridge Rule should monitor."""
        return self._alarm_arns
