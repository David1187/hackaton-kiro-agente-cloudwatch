"""
seeded/se2_create_hardcoded_timestamp.py — SE-2: Create con timestamp hardcoded.

SEEDED ERROR: Este handler usa un timestamp fijo (hardcoded) en lugar de
generar dinámicamente la hora UTC actual con milisegundos en formato ISO 8601.

Objetivo del comportamiento correcto violado: Requisito 1.5 — created_at y
updated_at deben ser el instante UTC real en formato ISO 8601 con milisegundos.

Cómo disparo el error:
  POST /tasks  con body  {"title": "Mi tarea"}
  Cualquier creación válida reproducirá el defecto: todos los items tendrán
  el mismo timestamp hardcodeado "1970-01-01T00:00:00.000+00:00".

Excepción / comportamiento observado:
  No se lanza excepción.  La respuesta es 201 pero el campo created_at y
  updated_at siempre contienen "1970-01-01T00:00:00.000+00:00".
  El agente detectará la anomalía al comparar el código con la especificación.

NOTA: Este archivo es código de DEMOSTRACIÓN deliberadamente defectuoso.
      Nunca debe desplegarse como handler de producción.
"""

from __future__ import annotations

import uuid

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

# BUG SE-2: timestamp hardcodeado — nunca refleja la hora real de creación.
_HARDCODED_TIMESTAMP = "1970-01-01T00:00:00.000+00:00"


def handler(event: dict, context: object) -> dict:
    """Handler sembrado SE-2: usa timestamp fijo en lugar de datetime.now().

    Todos los items creados tendrán created_at = updated_at =
    "1970-01-01T00:00:00.000+00:00", independientemente de cuándo se invoque.
    """
    try:
        body = Payload_Validator.parse_json_body(event)

        if "title" not in body:
            raise ValidationError(
                "MISSING_FIELD",
                "El campo 'title' es obligatorio para crear una tarea.",
            )
        title = Payload_Validator.validate_title(body["title"])

        completed = False
        if "completed" in body:
            completed = Payload_Validator.validate_completed(body["completed"])

        # BUG SE-2: timestamp fijo en lugar de datetime.now(timezone.utc)
        task = {
            "task_id": str(uuid.uuid4()),
            "title": title,
            "completed": completed,
            "created_at": _HARDCODED_TIMESTAMP,   # ← hardcoded
            "updated_at": _HARDCODED_TIMESTAMP,   # ← hardcoded
        }

        TaskRepository.create(task)
        return success_response(201, task)

    except ValidationError as exc:
        logger.error("ERROR: validacion fallida en se2_create: %s", exc.message)
        return error_response(400, exc.code, exc.message)

    except botocore.exceptions.ClientError:
        logger.error("ERROR: ClientError en se2_create", exc_info=True)
        return error_response(500, "DDB_ERROR", "Error al acceder a la base de datos.")

    except botocore.exceptions.ParamValidationError:
        logger.error("ERROR: ParamValidationError en se2_create", exc_info=True)
        return error_response(500, "DDB_PARAM_ERROR", "Error de parametros en la base de datos.")

    except Exception:
        logger.error("ERROR: fallo no controlado en se2_create", exc_info=True)
        return error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")
