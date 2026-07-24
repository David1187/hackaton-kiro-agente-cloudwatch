"""
responses.py — Standard HTTP response helpers for Lambda proxy integration.

All CRUD handlers MUST use these helpers to guarantee a consistent envelope:
  {statusCode, headers: {Content-Type: application/json}, body: <json string>}

Success body: the payload dict/list directly serialised with DecimalEncoder.
Error body:   {"error": {"code": UPPER_SNAKE_CASE, "message": <str>}}
              Never includes stack traces, table names, ARNs, or internal details.
"""

import json

from .encoding import DecimalEncoder

_CONTENT_TYPE_JSON = {"Content-Type": "application/json"}


def success_response(status_code: int, payload: object) -> dict:
    """Return a Lambda proxy response for a successful operation.

    Args:
        status_code: HTTP status code (e.g. 200, 201).
        payload: Any JSON-serialisable object.  Decimal values are handled by
                 DecimalEncoder so DynamoDB items can be passed directly.

    Returns:
        Lambda proxy response dict with statusCode, headers, and body.
    """
    return {
        "statusCode": status_code,
        "headers": _CONTENT_TYPE_JSON,
        "body": json.dumps(payload, cls=DecimalEncoder),
    }


def error_response(status_code: int, code: str, message: str) -> dict:
    """Return a Lambda proxy response for an error condition.

    Args:
        status_code: HTTP status code (e.g. 400, 404, 500).
        code: Machine-readable error code in UPPER_SNAKE_CASE
              (e.g. "RESOURCE_NOT_FOUND", "INVALID_JSON").
        message: Human-readable description for debugging.
                 MUST NOT contain stack traces, table names, ARNs, or any
                 internal implementation details.

    Returns:
        Lambda proxy response dict with statusCode, headers, and body.
    """
    return {
        "statusCode": status_code,
        "headers": _CONTENT_TYPE_JSON,
        "body": json.dumps({"error": {"code": code, "message": message}}),
    }
