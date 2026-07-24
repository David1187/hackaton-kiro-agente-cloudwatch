"""
seeded/se7_delete_no_condition.py — SE-7: Delete sin ConditionExpression.

SEEDED ERROR: Este handler elimina el item sin usar ``ConditionExpression
attribute_exists(task_id)``.  DynamoDB responde con éxito (200) incluso cuando
el item no existe, en lugar de devolver 404.

Objetivo del comportamiento correcto violado: Requisito 5.3 — el sistema debe
devolver 404 RESOURCE_NOT_FOUND al intentar eliminar una tarea inexistente.

Cómo disparo el error:
  DELETE /tasks/tarea-que-no-existe

Excepción / comportamiento observado:
  No se lanza excepción.  El handler responde 200 con
  ``{"deleted": true, "task_id": "tarea-que-no-existe"}`` aunque el item no
  existiera.  Es un silent success que enmascara el 404 esperado.
  El agente detecta la anomalía comparando el comportamiento con la spec.

NOTA: Este archivo es código de DEMOSTRACIÓN deliberadamente defectuoso.
      Nunca debe desplegarse como handler de producción.
"""

from __future__ import annotations

import boto3
import botocore.exceptions
import os

from common import (
    Payload_Validator,
    ValidationError,
    configure_logger,
    error_response,
    success_response,
)

logger = configure_logger(__name__)

_dynamodb = None
_table = None


def _get_table():
    global _dynamodb, _table
    if _table is None:
        _dynamodb = boto3.resource("dynamodb")
        _table = _dynamodb.Table(os.environ["TABLE_NAME"])
    return _table


def handler(event: dict, context: object) -> dict:
    """Handler sembrado SE-7: delete sin ConditionExpression.

    DynamoDB acepta el delete sin comprobar si el item existe, devolviendo
    siempre éxito aunque el task_id no esté en la tabla.
    """
    try:
        task_id = Payload_Validator.validate_task_id(event)

        table = _get_table()
        # BUG SE-7: se omite ConditionExpression="attribute_exists(task_id)"
        # DynamoDB no lanza error aunque el item no exista.
        table.delete_item(Key={"task_id": task_id})  # ← sin ConditionExpression

        return success_response(200, {"deleted": True, "task_id": task_id})

    except ValidationError as exc:
        logger.error("ERROR: validacion fallida en se7_delete: %s", exc.message)
        return error_response(400, exc.code, exc.message)

    except botocore.exceptions.ClientError:
        logger.error("ERROR: ClientError en se7_delete", exc_info=True)
        return error_response(500, "DDB_ERROR", "Error al acceder a la base de datos.")

    except botocore.exceptions.ParamValidationError:
        logger.error("ERROR: ParamValidationError en se7_delete", exc_info=True)
        return error_response(500, "DDB_PARAM_ERROR", "Error de parametros en la base de datos.")

    except Exception:
        logger.error("ERROR: fallo no controlado en se7_delete", exc_info=True)
        return error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")
