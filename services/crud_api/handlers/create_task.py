"""
handlers/create_task.py — Create Task Lambda handler.

POST /tasks

Parses and validates the request body, generates a new task_id (UUID v4),
sets created_at / updated_at to the same ISO 8601 UTC instant with
milliseconds, defaults completed to false, persists via TaskRepository, and
responds 201 with the full Task item.

Error hierarchy (try-except order):
  1. ValidationError  → 400
  2. NotFoundError    → 404 (should not occur on create, kept for safety)
  3. ClientError      → 500
  4. ParamValidationError → 500
  5. Exception (net)  → 500

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7
"""

from __future__ import annotations

import uuid
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
    """Lambda entry point for POST /tasks.

    Args:
        event:   Lambda proxy integration event.
        context: Lambda context object (unused).

    Returns:
        Lambda proxy response dict.
    """
    try:
        # --- 1. Parse and validate body ------------------------------------ #
        body = Payload_Validator.parse_json_body(event)

        # title is required for creation
        if "title" not in body:
            raise ValidationError(
                "MISSING_FIELD",
                "El campo 'title' es obligatorio para crear una tarea.",
            )
        title = Payload_Validator.validate_title(body["title"])

        # completed is optional on creation; default is False
        if "completed" in body:
            completed = Payload_Validator.validate_completed(body["completed"])
        else:
            completed = False

        # --- 2. Build Task item -------------------------------------------- #
        now = datetime.now(timezone.utc)
        # ISO 8601 with milliseconds and UTC offset, e.g. "2026-07-24T23:00:00.123+00:00"
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}" + "+00:00"

        task = {
            "task_id": str(uuid.uuid4()),
            "title": title,
            "completed": completed,
            "created_at": timestamp,
            "updated_at": timestamp,
        }

        # --- 3. Persist ---------------------------------------------------- #
        TaskRepository.create(task)

        # --- 4. Respond 201 ------------------------------------------------ #
        return success_response(201, task)

    except ValidationError as exc:
        logger.error("ERROR: validacion fallida en create_task: %s", exc.message)
        return error_response(400, exc.code, exc.message)

    except TaskRepository.NotFoundError:
        # Should not occur during create, but included as safety net
        logger.error("ERROR: NotFoundError inesperado en create_task", exc_info=True)
        return error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")

    except botocore.exceptions.ClientError:
        logger.error("ERROR: ClientError en create_task", exc_info=True)
        return error_response(500, "DDB_ERROR", "Error al acceder a la base de datos.")

    except botocore.exceptions.ParamValidationError:
        logger.error("ERROR: ParamValidationError en create_task", exc_info=True)
        return error_response(500, "DDB_PARAM_ERROR", "Error de parametros en la base de datos.")

    except Exception:
        logger.error("ERROR: fallo no controlado en create_task", exc_info=True)
        return error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")
