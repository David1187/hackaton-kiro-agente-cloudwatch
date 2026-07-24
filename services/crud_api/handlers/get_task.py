"""
handlers/get_task.py — Get Task Lambda handler.

GET /tasks/{task_id}

Validates the task_id path parameter, retrieves the Task from DynamoDB, and
responds 200 with the serialised item. Returns 404 if the Task does not exist.

Error hierarchy:
  1. ValidationError  → 400
  2. NotFoundError    → 404
  3. ClientError      → 500
  4. ParamValidationError → 500
  5. Exception (net)  → 500

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5
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
    """Lambda entry point for GET /tasks/{task_id}.

    Args:
        event:   Lambda proxy integration event.
        context: Lambda context object (unused).

    Returns:
        Lambda proxy response dict.
    """
    try:
        # --- 1. Validate path parameter ------------------------------------ #
        task_id = Payload_Validator.validate_task_id(event)

        # --- 2. Retrieve from DynamoDB ------------------------------------- #
        task = TaskRepository.get(task_id)

        # --- 3. Respond 200 ------------------------------------------------ #
        return success_response(200, task)

    except ValidationError as exc:
        logger.error("ERROR: validacion fallida en get_task: %s", exc.message)
        return error_response(400, exc.code, exc.message)

    except TaskRepository.NotFoundError:
        return error_response(404, "RESOURCE_NOT_FOUND", "La tarea solicitada no existe.")

    except botocore.exceptions.ClientError:
        logger.error("ERROR: ClientError en get_task", exc_info=True)
        return error_response(500, "DDB_ERROR", "Error al acceder a la base de datos.")

    except botocore.exceptions.ParamValidationError:
        logger.error("ERROR: ParamValidationError en get_task", exc_info=True)
        return error_response(500, "DDB_PARAM_ERROR", "Error de parametros en la base de datos.")

    except Exception:
        logger.error("ERROR: fallo no controlado en get_task", exc_info=True)
        return error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")
