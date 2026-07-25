"""Fix branch naming utilities for the Self-Healing Agent."""
import re
from datetime import datetime, timezone


# Characters invalid in git ref names (man git-check-ref-format)
_INVALID_REF_CHARS = re.compile(r"[~^:?*\[\]\\@{}\s]+")
_CONSECUTIVE_DASHES = re.compile(r"-{2,}")
_LEADING_TRAILING_DASH = re.compile(r"^-+|-+$")


def _sanitize_for_git_ref(value: str) -> str:
    """Sanitize a string to be safe for use in a git branch name.

    Replaces invalid characters with '-', collapses consecutive dashes,
    and strips leading/trailing dashes.
    """
    # Replace invalid characters with dash
    sanitized = _INVALID_REF_CHARS.sub("-", value)
    # Also replace dots that could cause issues (e.g., '..' or trailing '.')
    sanitized = sanitized.replace("..", "-").rstrip(".")
    # Collapse consecutive dashes
    sanitized = _CONSECUTIVE_DASHES.sub("-", sanitized)
    # Strip leading/trailing dashes
    sanitized = _LEADING_TRAILING_DASH.sub("", sanitized)
    return sanitized


def build_fix_branch_name(lambda_name: str, timestamp: datetime) -> str:
    """Build the fix branch name following the pattern fix/auto-heal-{lambda}-{timestamp}.

    Args:
        lambda_name: Name of the Lambda function that triggered the error.
        timestamp: When the error was detected. Will be converted to UTC.

    Returns:
        A git-safe branch name like 'fix/auto-heal-my-function-20250115T103045Z'.
    """
    # Ensure timestamp is in UTC
    if timestamp.tzinfo is None:
        utc_ts = timestamp.replace(tzinfo=timezone.utc)
    else:
        utc_ts = timestamp.astimezone(timezone.utc)

    ts_str = utc_ts.strftime("%Y%m%dT%H%M%SZ")
    sanitized_name = _sanitize_for_git_ref(lambda_name)

    # Ensure we have something after sanitization
    if not sanitized_name:
        sanitized_name = "unknown"

    return f"fix/auto-heal-{sanitized_name}-{ts_str}"
