"""
handlers/update_task.py — Update Task Lambda handler.

PUT /tasks/{task_id}

Validates the task_id path parameter and the body (at least one of title /
completed must be present). Builds an UpdateExpression containing only the
supplied attributes plus a refreshed updated_at. Uses a ConditionExpression
so that updating a non-existent Task raises NotFoundError (404) instead of
silently creating a new record.

Error hierarchy:
  1. ValidationError  → 400
  2. NotFoundError    → 404
  3. ClientError      → 500
  4. ParamValidationError → 500
  5. Exception (net)  → 500

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8
"""

from __future__ import annotations

from datetime import datetime, timezone

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
    """Lambda entry point for PUT /tasks/{task_id}.

    Args:
        event:   Lambda proxy integration event.
        context: Lambda context object (unused).

    Returns:
        Lambda proxy response dict.
    """
    try:
        # --- 1. Validate path parameter ------------------------------------ #
        task_id = Payload_Validator.validate_task_id(event)

        # --- 2. Parse and validate body ------------------------------------ #
        body = Payload_Validator.parse_json_body(event)

        # At least one updatable field must be present
        updatable_fields = {"title", "completed"}
        present = updatable_fields & body.keys()
        if not present:
            raise ValidationError(
                "MISSING_FIELD",
                "El body debe contener al menos uno de los campos: 'title', 'completed'.",
            )

        # --- 3. Build attrs dict with validated values --------------------- #
        attrs: dict = {}

        if "title" in body:
            attrs["title"] = Payload_Validator.validate_title(body["title"])

        if "completed" in body:
            attrs["completed"] = Payload_Validator.validate_completed(body["completed"])

        # Always refresh updated_at
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}" + "+00:00"
        attrs["updated_at"] = timestamp

        # --- 4. Persist update --------------------------------------------- #
        updated_task = TaskRepository.update(task_id, attrs)

        # --- 5. Respond 200 ------------------------------------------------ #
        return success_response(200, updated_task)

    except ValidationError as exc:
        logger.error("ERROR: validacion fallida en update_task: %s", exc.message)
        return error_response(400, exc.code, exc.message)

    except TaskRepository.NotFoundError:
        return error_response(404, "RESOURCE_NOT_FOUND", "La tarea solicitada no existe.")

    except botocore.exceptions.ClientError:
        logger.error("ERROR: ClientError en update_task", exc_info=True)
        return error_response(500, "DDB_ERROR", "Error al acceder a la base de datos.")

    except botocore.exceptions.ParamValidationError:
        logger.error("ERROR: ParamValidationError en update_task", exc_info=True)
        return error_response(500, "DDB_PARAM_ERROR", "Error de parametros en la base de datos.")

    except Exception:
        logger.error("ERROR: fallo no controlado en update_task", exc_info=True)
        return error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")
