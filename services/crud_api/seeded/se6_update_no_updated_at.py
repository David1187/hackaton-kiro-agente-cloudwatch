"""
seeded/se6_update_no_updated_at.py — SE-6: Update sin actualizar updated_at.

SEEDED ERROR: Este handler actualiza ``title`` y/o ``completed`` pero nunca
refresca el campo ``updated_at``.  El timestamp de última modificación queda
congelado en el valor de ``created_at``, haciendo imposible detectar cuándo
fue el último cambio real del item.

Objetivo del comportamiento correcto violado: Requisito 4.7 — el sistema debe
refrescar ``updated_at`` con la hora UTC actual en cada operación de update.

Cómo disparo el error:
  1. Crear una tarea: POST /tasks {"title": "Original"}
  2. Esperar 1 segundo.
  3. PUT /tasks/{task_id} {"title": "Modificado"}
  4. GET /tasks/{task_id} → updated_at sigue siendo igual a created_at.

Excepción / comportamiento observado:
  No se lanza excepción.  La respuesta es 200 con los datos actualizados, pero
  el campo ``updated_at`` permanece con el valor original.  El agente detecta
  la anomalía al comparar la especificación con el código fuente.

NOTA: Este archivo es código de DEMOSTRACIÓN deliberadamente defectuoso.
      Nunca debe desplegarse como handler de producción.
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
    """Handler sembrado SE-6: actualiza atributos pero omite updated_at.

    El campo ``updated_at`` nunca se incluye en ``attrs``, por lo que DynamoDB
    no lo toca y permanece con el valor original de creación.
    """
    try:
        task_id = Payload_Validator.validate_task_id(event)
        body = Payload_Validator.parse_json_body(event)

        updatable_fields = {"title", "completed"}
        present = updatable_fields & body.keys()
        if not present:
            raise ValidationError(
                "MISSING_FIELD",
                "El body debe contener al menos uno de los campos: 'title', 'completed'.",
            )

        attrs: dict = {}

        if "title" in body:
            attrs["title"] = Payload_Validator.validate_title(body["title"])

        if "completed" in body:
            attrs["completed"] = Payload_Validator.validate_completed(body["completed"])

        # BUG SE-6: se omite completamente la línea que actualiza updated_at.
        # attrs nunca contiene "updated_at", así que DynamoDB no lo modifica.

        updated_task = TaskRepository.update(task_id, attrs)
        return success_response(200, updated_task)

    except ValidationError as exc:
        logger.error("ERROR: validacion fallida en se6_update: %s", exc.message)
        return error_response(400, exc.code, exc.message)

    except TaskRepository.NotFoundError:
        return error_response(404, "RESOURCE_NOT_FOUND", "La tarea solicitada no existe.")

    except botocore.exceptions.ClientError:
        logger.error("ERROR: ClientError en se6_update", exc_info=True)
        return error_response(500, "DDB_ERROR", "Error al acceder a la base de datos.")

    except botocore.exceptions.ParamValidationError:
        logger.error("ERROR: ParamValidationError en se6_update", exc_info=True)
        return error_response(500, "DDB_PARAM_ERROR", "Error de parametros en la base de datos.")

    except Exception:
        logger.error("ERROR: fallo no controlado en se6_update", exc_info=True)
        return error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")
