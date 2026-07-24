"""
seeded/se1_create_no_title_validation.py — SE-1: Create sin validación de título.

SEEDED ERROR: Este handler omite intencionalmente la validación del campo
``title``.  Acepta títulos vacíos o que contengan sólo espacios en blanco,
almacenando cadenas inválidas en DynamoDB.

Objetivo del comportamiento correcto violado: Requisito 1.2 — el sistema debe
rechazar títulos vacíos o de sólo espacios con 400 INVALID_TITLE.

Cómo disparo el error:
  POST /tasks  con body  {"title": "   "}   (solo espacios en blanco)
  POST /tasks  con body  {"title": ""}      (cadena vacía)

Excepción / comportamiento observado:
  Ninguna excepción — el handler responde 201 con un título vacío/blank
  guardado en DynamoDB.  El error se detecta al verificar los datos almacenados.
  No produce un log ERROR: por sí solo; para la demo, la falta de validación
  se combina con SE-8 o SE-9 para hacer el defecto detectable por CloudWatch.

NOTA: Este archivo es código de DEMOSTRACIÓN deliberadamente defectuoso.
      Nunca debe desplegarse como handler de producción.
"""

from __future__ import annotations

import json
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
    """Handler sembrado SE-1: acepta títulos vacíos/whitespace sin rechazarlos.

    La diferencia con el handler correcto es que se omite la llamada a
    ``Payload_Validator.validate_title()``, por lo que cualquier cadena
    (incluida la vacía o de sólo espacios) pasa directamente a DynamoDB.
    """
    try:
        body = Payload_Validator.parse_json_body(event)

        if "title" not in body:
            raise ValidationError(
                "MISSING_FIELD",
                "El campo 'title' es obligatorio para crear una tarea.",
            )

        # BUG SE-1: se usa el valor crudo sin llamar a validate_title().
        # Esto permite títulos vacíos ("") o de sólo espacios ("   ").
        title = body["title"]  # ← sin validación ni strip

        completed = False
        if "completed" in body:
            completed = Payload_Validator.validate_completed(body["completed"])

        now = datetime.now(timezone.utc)
        timestamp = (
            now.strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{now.microsecond // 1000:03d}"
            + "+00:00"
        )

        task = {
            "task_id": str(uuid.uuid4()),
            "title": title,
            "completed": completed,
            "created_at": timestamp,
            "updated_at": timestamp,
        }

        TaskRepository.create(task)
        return success_response(201, task)

    except ValidationError as exc:
        logger.error("ERROR: validacion fallida en se1_create: %s", exc.message)
        return error_response(400, exc.code, exc.message)

    except botocore.exceptions.ClientError:
        logger.error("ERROR: ClientError en se1_create", exc_info=True)
        return error_response(500, "DDB_ERROR", "Error al acceder a la base de datos.")

    except botocore.exceptions.ParamValidationError:
        logger.error("ERROR: ParamValidationError en se1_create", exc_info=True)
        return error_response(500, "DDB_PARAM_ERROR", "Error de parametros en la base de datos.")

    except Exception:
        logger.error("ERROR: fallo no controlado en se1_create", exc_info=True)
        return error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")
