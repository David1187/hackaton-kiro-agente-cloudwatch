"""CloudWatch Logs client for retrieving error stack traces."""
import logging

import boto3
import botocore.exceptions

logger = logging.getLogger(__name__)

# Lazy-initialized module-level client — reused across invocations (warm starts)
# Uses a getter to avoid resolving credentials at import time (breaks tests).
_logs_client = None


def _get_logs_client():
    """Return the module-level CloudWatch Logs client, creating it on first use."""
    global _logs_client
    if _logs_client is None:
        _logs_client = boto3.client("logs")
    return _logs_client


def derive_log_group(function_name: str) -> str:
    """Derive the CloudWatch Log Group name from a Lambda function name.

    Args:
        function_name: The Lambda function name.

    Returns:
        The log group path in format '/aws/lambda/{function_name}'.
    """
    return f"/aws/lambda/{function_name}"


def derive_function_name(event: dict) -> str:
    """Extract the Lambda function name from an EventBridge alarm event.

    The EventBridge event for CloudWatch Alarm State Change contains the alarm
    name in detail.alarmName. The alarm naming convention from StatelessStack is
    '{StackName}{AlarmLogicalId}' but the metric dimensions contain the function
    name directly.

    We extract from detail.configuration.metrics[0].metricStat.metric.dimensions.FunctionName
    or fall back to parsing the alarm name.

    Args:
        event: The EventBridge event payload.

    Returns:
        The Lambda function name.

    Raises:
        ValueError: If the function name cannot be derived from the event.
    """
    try:
        # Try to get from metric dimensions (most reliable)
        detail = event.get("detail", {})

        # Path: detail.configuration.metrics[0].metricStat.metric.dimensions.FunctionName
        configuration = detail.get("configuration", {})
        metrics = configuration.get("metrics", [])
        if metrics:
            metric_stat = metrics[0].get("metricStat", {})
            metric = metric_stat.get("metric", {})
            dimensions = metric.get("dimensions", {})
            function_name = dimensions.get("FunctionName")
            if function_name:
                return function_name

        # Fallback: try to parse from alarm name or namespace
        # The alarm description contains the function context
        alarm_name = detail.get("alarmName", "")
        if alarm_name:
            # Our alarms follow pattern: {StackId}Alarm{Operation}...
            # but the most reliable is the metric namespace + dimensions
            pass

        # Last resort: look in the state change reason which may reference the log group
        state = detail.get("state", {})
        reason = state.get("reason", "")
        if "/aws/lambda/" in reason:
            # Extract function name from log group reference
            parts = reason.split("/aws/lambda/")
            if len(parts) > 1:
                return parts[1].split(" ")[0].split('"')[0].strip()

    except (KeyError, IndexError, TypeError):
        pass

    raise ValueError(
        f"Cannot derive Lambda function name from EventBridge event. "
        f"Event keys: {list(event.get('detail', {}).keys())}"
    )


def get_latest_stack_trace(log_group: str, logs_client=None) -> str | None:
    """Retrieve the most recent ERROR log entry from a CloudWatch Log Group.

    Uses FilterLogEvents with pattern 'ERROR:' to find the latest error
    and its stack trace.

    Args:
        log_group: The CloudWatch Log Group name.
        logs_client: Optional boto3 logs client (for testing/injection).

    Returns:
        The error message/stack trace string, or None if no errors found.
    """
    client = logs_client or _get_logs_client()

    try:
        response = client.filter_log_events(
            logGroupName=log_group,
            filterPattern='"ERROR:"',
            limit=5,
            interleaved=True,
        )

        events = response.get("events", [])
        if not events:
            return None

        # Get the most recent event (last by timestamp)
        events_sorted = sorted(events, key=lambda e: e.get("timestamp", 0), reverse=True)
        latest = events_sorted[0]
        message = latest.get("message", "").strip()

        # If there are consecutive events from the same invocation (multi-line stack trace),
        # try to aggregate them
        if len(events_sorted) > 1:
            latest_ts = latest.get("timestamp", 0)
            # Collect events within 1 second of the latest (likely same invocation)
            related = [
                e.get("message", "").strip()
                for e in events_sorted
                if abs(e.get("timestamp", 0) - latest_ts) < 1000
            ]
            if related:
                message = "\n".join(reversed(related))

        return message if message else None

    except botocore.exceptions.ClientError as error:
        logger.error(
            "ERROR: failed to query CloudWatch Logs for log group '%s': %s",
            log_group,
            error.response["Error"]["Code"],
            exc_info=True,
        )
        raise
    except botocore.exceptions.ParamValidationError as error:
        logger.error(
            "ERROR: invalid parameters for CloudWatch Logs query on '%s'",
            log_group,
            exc_info=True,
        )
        raise
    except Exception:
        logger.error(
            "ERROR: unexpected failure querying CloudWatch Logs for '%s'",
            log_group,
            exc_info=True,
        )
        raise
