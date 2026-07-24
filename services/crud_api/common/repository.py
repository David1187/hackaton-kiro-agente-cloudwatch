"""
repository.py — Persistence layer (TaskRepository) over Amazon DynamoDB.

Module-level boto3 objects are initialised lazily on first use so that:
  1. Tests using @mock_aws / mock_aws can patch botocore before boto3.resource()
     is called (avoiding credential-resolution issues at import time).
  2. Warm Lambda invocations still reuse the same connection objects because
     the initialisation happens once per container, not once per invocation.

The TABLE_NAME environment variable is mandatory at first use.  The CDK stack
injects it as a Lambda environment variable.

Error contract:
  - ConditionalCheckFailedException  → raises NotFoundError (caller returns 404)
  - Any other ClientError            → logs ERROR: + propagates (caller returns 500)
  - ParamValidationError             → logs ERROR: + propagates (caller returns 500)
  - Unexpected Exception             → logs ERROR: + propagates (caller returns 500)
"""

from __future__ import annotations

import os
from typing import Any

import boto3
import botocore.exceptions

from .logging_config import configure_logger

logger = configure_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level DynamoDB resource — lazily initialised on first access.
# Using a mutable dict lets tests (and module reload) replace the references
# without shadowing the module globals.
# ---------------------------------------------------------------------------
_state: dict[str, Any] = {
    "dynamodb": None,
    "table": None,
}


def _get_table():
    """Return the module-level DynamoDB Table, initialising it on first call."""
    if _state["table"] is None:
        _state["dynamodb"] = boto3.resource("dynamodb")
        _state["table"] = _state["dynamodb"].Table(os.environ["TABLE_NAME"])
    return _state["table"]


class TaskRepository:
    """Data-access object for Task items in DynamoDB.

    All DynamoDB I/O is encapsulated here; no handler should import boto3
    directly.
    """

    class NotFoundError(Exception):
        """Raised when a conditional check fails (item does not exist)."""

    # ------------------------------------------------------------------ #
    # Write operations                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def create(task: dict[str, Any]) -> str:
        """Persist a new Task item.

        Args:
            task: Dict representing the full Task (task_id, title, completed,
                  created_at, updated_at).

        Returns:
            The ``task_id`` of the newly created item.

        Raises:
            NotFoundError: Should not occur on create, but included for
                           completeness in case of unexpected conditional expr.
            botocore.exceptions.ClientError: On any other DynamoDB error.
        """
        table = _get_table()
        try:
            table.put_item(Item=task)
        except botocore.exceptions.ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                raise TaskRepository.NotFoundError(
                    f"ConditionalCheckFailedException en create para task_id={task.get('task_id')}"
                ) from exc
            logger.error("ERROR: fallo al crear item en DynamoDB", exc_info=True)
            raise
        except botocore.exceptions.ParamValidationError:
            logger.error("ERROR: parametros invalidos en create de DynamoDB", exc_info=True)
            raise
        except Exception:
            logger.error("ERROR: fallo inesperado en create de TaskRepository", exc_info=True)
            raise

        return task["task_id"]

    @staticmethod
    def update(task_id: str, attrs: dict[str, Any]) -> dict[str, Any]:
        """Update an existing Task item.

        Only the attributes present in ``attrs`` are updated.  ``updated_at``
        must be included by the caller.  Uses ``ConditionExpression
        attribute_exists(task_id)`` so that updating a non-existent item raises
        NotFoundError instead of silently creating a new record.

        Args:
            task_id: PK of the Task to update.
            attrs: Dict of attribute names → new values.  Must contain at
                   least one attribute (validation is the caller's responsibility).

        Returns:
            The updated Task item (ALL_NEW return values from DynamoDB).

        Raises:
            NotFoundError: if the item does not exist.
            botocore.exceptions.ClientError: on any other DynamoDB error.
        """
        table = _get_table()

        # Build UpdateExpression dynamically from attrs
        set_clauses = []
        expr_names: dict[str, str] = {}
        expr_values: dict[str, Any] = {}

        for idx, (key, val) in enumerate(attrs.items()):
            placeholder_name = f"#attr{idx}"
            placeholder_val = f":val{idx}"
            set_clauses.append(f"{placeholder_name} = {placeholder_val}")
            expr_names[placeholder_name] = key
            expr_values[placeholder_val] = val

        update_expr = "SET " + ", ".join(set_clauses)

        try:
            response = table.update_item(
                Key={"task_id": task_id},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values,
                ConditionExpression="attribute_exists(task_id)",
                ReturnValues="ALL_NEW",
            )
        except botocore.exceptions.ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                raise TaskRepository.NotFoundError(
                    f"Task no encontrada: task_id={task_id}"
                ) from exc
            logger.error("ERROR: fallo al actualizar item en DynamoDB", exc_info=True)
            raise
        except botocore.exceptions.ParamValidationError:
            logger.error("ERROR: parametros invalidos en update de DynamoDB", exc_info=True)
            raise
        except Exception:
            logger.error("ERROR: fallo inesperado en update de TaskRepository", exc_info=True)
            raise

        return response["Attributes"]

    @staticmethod
    def delete(task_id: str) -> str:
        """Delete a Task item.

        Uses ``ConditionExpression attribute_exists(task_id)`` so that
        deleting a non-existent item raises NotFoundError.

        Args:
            task_id: PK of the Task to delete.

        Returns:
            The deleted ``task_id``.

        Raises:
            NotFoundError: if the item does not exist.
            botocore.exceptions.ClientError: on any other DynamoDB error.
        """
        table = _get_table()
        try:
            table.delete_item(
                Key={"task_id": task_id},
                ConditionExpression="attribute_exists(task_id)",
            )
        except botocore.exceptions.ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                raise TaskRepository.NotFoundError(
                    f"Task no encontrada: task_id={task_id}"
                ) from exc
            logger.error("ERROR: fallo al eliminar item en DynamoDB", exc_info=True)
            raise
        except botocore.exceptions.ParamValidationError:
            logger.error("ERROR: parametros invalidos en delete de DynamoDB", exc_info=True)
            raise
        except Exception:
            logger.error("ERROR: fallo inesperado en delete de TaskRepository", exc_info=True)
            raise

        return task_id

    # ------------------------------------------------------------------ #
    # Read operations                                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def get(task_id: str) -> dict[str, Any]:
        """Retrieve a single Task item by its PK.

        Args:
            task_id: PK of the Task.

        Returns:
            The Task item dict (with DynamoDB Decimal types intact; caller
            must serialise with DecimalEncoder).

        Raises:
            NotFoundError: if the item does not exist (no ``Item`` key in
                           the response).
            botocore.exceptions.ClientError: on any other DynamoDB error.
        """
        table = _get_table()
        try:
            response = table.get_item(Key={"task_id": task_id})
        except botocore.exceptions.ClientError:
            logger.error("ERROR: fallo al obtener item de DynamoDB", exc_info=True)
            raise
        except botocore.exceptions.ParamValidationError:
            logger.error("ERROR: parametros invalidos en get de DynamoDB", exc_info=True)
            raise
        except Exception:
            logger.error("ERROR: fallo inesperado en get de TaskRepository", exc_info=True)
            raise

        if "Item" not in response:
            raise TaskRepository.NotFoundError(f"Task no encontrada: task_id={task_id}")

        return response["Item"]

    @staticmethod
    def list(limit: int = 1000) -> list[dict[str, Any]]:
        """Scan the table and return up to ``limit`` Task items.

        Args:
            limit: Maximum number of items to return (default 1000).

        Returns:
            List of Task item dicts (may be empty).

        Raises:
            botocore.exceptions.ClientError: on DynamoDB errors.
        """
        table = _get_table()
        try:
            response = table.scan(Limit=limit)
        except botocore.exceptions.ClientError:
            logger.error("ERROR: fallo al listar items de DynamoDB", exc_info=True)
            raise
        except botocore.exceptions.ParamValidationError:
            logger.error("ERROR: parametros invalidos en list de DynamoDB", exc_info=True)
            raise
        except Exception:
            logger.error("ERROR: fallo inesperado en list de TaskRepository", exc_info=True)
            raise

        return response.get("Items", [])
