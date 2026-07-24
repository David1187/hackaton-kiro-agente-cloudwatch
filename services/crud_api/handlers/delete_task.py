"""
handlers/delete_task.py — Delete Task Lambda handler.

DELETE /tasks/{task_id}

Validates the task_id path parameter (non-empty), deletes the item with a
ConditionExpression (attribute_exists) so that deleting a non-existent Task
returns 404 instead of silently succeeding, and responds 200 with a
confirmation payload that includes the deleted task_id.

Error hierarchy:
  1. ValidationError  → 400
  2. NotFoundError    → 404
  3. ClientError      → 500
  4. ParamValidationError → 500
  5. Exception (net)  → 500

Requirements: 5.1, 5.2, 5.3, 5.4
"""

from __future__ import annotations

import botocore.exceptions

from common import (
    TaskRepository,
    Payload_Validator,
    ValidationError,
    configure_logger,
    error_response,
    success_response,
)

logger = configure_logger(__name__)


def handler(event: dict, context: object) -> dict:
    """Lambda entry point for DELETE /tasks/{task_id}.

    Args:
        event:   Lambda proxy integration event.
        context: Lambda context object (unused).

    Returns:
        Lambda proxy response dict.
    """
    try:
        # --- 1. Validate path parameter ------------------------------------ #
        task_id = Payload_Validator.validate_task_id(event)

        # --- 2. Delete (conditional on existence) -------------------------- #
        TaskRepository.delete(task_id)

        # --- 3. Respond 200 with confirmation ------------------------------ #
        return success_response(200, {"deleted": True, "task_id": task_id})

    except ValidationError as exc:
        logger.error("ERROR: validacion fallida en delete_task: %s", exc.message)
        return error_response(400, exc.code, exc.message)

    except TaskRepository.NotFoundError:
        return error_response(404, "RESOURCE_NOT_FOUND", "La tarea solicitada no existe.")

    except botocore.exceptions.ClientError:
        logger.error("ERROR: ClientError en delete_task", exc_info=True)
        return error_response(500, "DDB_ERROR", "Error al acceder a la base de datos.")

    except botocore.exceptions.ParamValidationError:
        logger.error("ERROR: ParamValidationError en delete_task", exc_info=True)
        return error_response(500, "DDB_PARAM_ERROR", "Error de parametros en la base de datos.")

    except Exception:
        logger.error("ERROR: fallo no controlado en delete_task", exc_info=True)
        return error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")
