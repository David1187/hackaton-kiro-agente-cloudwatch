"""
services/crud_api/common — Shared layer for all CRUD Lambda handlers.

Exports:
  DecimalEncoder     — json.JSONEncoder subclass for DynamoDB Decimal values.
  success_response   — Build a standard 2xx Lambda proxy response.
  error_response     — Build a standard 4xx/5xx Lambda proxy response.
  configure_logger   — Return a Logger whose error records start with "ERROR:".
  Payload_Validator  — Static validation helpers for request inputs.
  ValidationError    — Raised by Payload_Validator on invalid input.
  TaskRepository     — DynamoDB persistence layer; raises NotFoundError on 404.
"""

from .encoding import DecimalEncoder
from .logging_config import configure_logger
from .repository import TaskRepository
from .responses import error_response, success_response
from .validation import Payload_Validator, ValidationError

__all__ = [
    "DecimalEncoder",
    "configure_logger",
    "TaskRepository",
    "success_response",
    "error_response",
    "Payload_Validator",
    "ValidationError",
]
