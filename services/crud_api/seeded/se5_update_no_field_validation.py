"""
seeded/se5_update_no_field_validation.py — SE-5: Update sin validación de campos presentes.

SEEDED ERROR: Este handler no verifica que el body contenga al menos uno de
los campos actualizables (``title`` o ``completed``).  Un body vacío ``{}``
pasa directamente a la capa de repositorio, que intenta construir un
UpdateExpression vacío y lanza un error de DynamoDB.

Objetivo del comportamiento correcto violado: Requisito 4.3 — el sistema debe
rechazar con 400 MISSING_FIELD si el body no contiene ningún campo actualizable.

Cómo disparo el error:
  PUT /tasks/{task_id}  con body  {}   (body JSON vacío)
  La capa de repositorio intenta construir "SET" con cero cláusulas y DynamoDB
  devuelve un ParamValidationError o ValidationException.

Excepción / comportamiento observado:
  ``botocore.exceptions.ParamValidationError`` (o ``ClientError`` con
  ValidationException) — capturado por el except correspondiente y registrado
  como ``ERROR: ParamValidationError en se5_update`` activando el Metric Filter.

NOTA: Este archivo es código de DEMOSTRACIÓN deliberadamente defectuoso.
      Nunca debe desplegarse como handler de producción.
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
    """Handler sembrado SE-5: omite la validación de campos presentes en el body.

    Un body vacío ``{}`` produce un UpdateExpression vacío → DynamoDB lanza
    ParamValidationError, que se registra con prefijo ERROR: activando el
    Metric Filter.
    """
    try:
        task_id = Payload_Validator.validate_task_id(event)
        body = Payload_Validator.parse_json_body(event)

        # BUG SE-5: se omite la comprobación de que body contenga al menos
        # uno de {"title", "completed"}.  El handler sigue con body vacío.

        attrs: dict = {}

        if "title" in body:
            attrs["title"] = Payload_Validator.validate_title(body["title"])

        if "completed" in body:
            attrs["completed"] = Payload_Validator.validate_completed(body["completed"])

        # Always refresh updated_at — pero si attrs sólo tiene updated_at y
        # no hubo campos reales, el update es no-op semánticamente; pero con
        # body completamente vacío ({}) attrs queda vacío aquí y luego
        # updated_at se añade, así que el UPDATE sí ejecuta pero con sólo
        # updated_at.  Para el SE-5 puro (sin updated_at) eliminamos también
        # la línea de updated_at para que el set_clauses quede completamente
        # vacío cuando el body es {}.
        #
        # BUG SE-5 puro: no añadimos updated_at si attrs está vacío,
        # provocando que el repositorio reciba un dict vacío y DynamoDB falle.
        if attrs:
            now = datetime.now(timezone.utc)
            timestamp = (
                now.strftime("%Y-%m-%dT%H:%M:%S.")
                + f"{now.microsecond // 1000:03d}"
                + "+00:00"
            )
            attrs["updated_at"] = timestamp

        # Con body={} llegaremos aquí con attrs={} → repositorio construye
        # "SET" vacío → ParamValidationError de DynamoDB
        updated_task = TaskRepository.update(task_id, attrs)
        return success_response(200, updated_task)

    except ValidationError as exc:
        logger.error("ERROR: validacion fallida en se5_update: %s", exc.message)
        return error_response(400, exc.code, exc.message)

    except TaskRepository.NotFoundError:
        return error_response(404, "RESOURCE_NOT_FOUND", "La tarea solicitada no existe.")

    except botocore.exceptions.ClientError:
        logger.error("ERROR: ClientError en se5_update", exc_info=True)
        return error_response(500, "DDB_ERROR", "Error al acceder a la base de datos.")

    except botocore.exceptions.ParamValidationError:
        logger.error("ERROR: ParamValidationError en se5_update", exc_info=True)
        return error_response(500, "DDB_PARAM_ERROR", "Error de parametros en la base de datos.")

    except Exception:
        logger.error("ERROR: fallo no controlado en se5_update", exc_info=True)
        return error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")
