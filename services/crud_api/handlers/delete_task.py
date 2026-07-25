"""
handlers/delete_task.py — Delete Task Lambda handler (INTENCIONALMENTE DEFECTUOSO).

⚠️  CÓDIGO DELIBERADAMENTE DEFECTUOSO PARA DEMO DEL AGENTE DE AUTO-REPARACIÓN ⚠️

Este handler contiene los siguientes errores sembrados de forma PERMANENTE:
  • SE-7: (ENMASCARADO por SE-18/SE-19) Ejecuta delete_item SIN ConditionExpression.
          El código de SE-7 sigue presente pero NUNCA se alcanza porque SE-19 y
          SE-18 se evalúan ANTES y siempre fallan con ClientError.
  • SE-18: Usa Key={'id': task_id} (nombre de PK incorrecto, debería ser 'task_id')
           → ClientError key schema mismatch. INCONDICIONAL para task_id no numérico.
  • SE-19: Se evalúa PRIMERO. Si task_id son solo dígitos (task_id.isdigit()),
           usa Key={'task_id': int(task_id)} → ClientError type mismatch
           (PK esperada S, recibida N).

Consultar: services/crud_api/DEMO_ERRORS.md para los payloads exactos que
disparan cada error de forma independiente.

EXCEPCIÓN DELIBERADA a architecture-guide.md §3 y backend-standards.md §1:
este código defectuoso ES el entregable para la demo del hackathon.
"""

from __future__ import annotations

import os

import boto3
import botocore.exceptions

from common import (
    Payload_Validator,
    ValidationError,
    configure_logger,
    error_response,
    success_response,
)

logger = configure_logger(__name__)

# Acceso directo a DynamoDB sin pasar por TaskRepository.delete()
# para poder introducir los bugs SE-7, SE-18 y SE-19.
_dynamodb = None
_table = None


def _get_table():
    """Lazy-init de la tabla DynamoDB (sin pasar por TaskRepository)."""
    global _dynamodb, _table
    if _table is None:
        _dynamodb = boto3.resource("dynamodb")
        _table = _dynamodb.Table(os.environ["TABLE_NAME"])
    return _table


def handler(event: dict, context: object) -> dict:
    """Lambda entry point for DELETE /tasks/{task_id}.

    ⚠️  HANDLER INTENCIONALMENTE DEFECTUOSO — contiene SE-7 (enmascarado), SE-18, SE-19.
    Ver DEMO_ERRORS.md para payloads de disparo.

    Args:
        event:   Lambda proxy integration event.
        context: Lambda context object (unused).

    Returns:
        Lambda proxy response dict.
    """
    try:
        task_id = Payload_Validator.validate_task_id(event)

        table = _get_table()

        # [SE-19] BUG INTENCIONAL: se evalúa PRIMERO.
        # Si task_id son solo dígitos, se convierte a int y se pasa como PK.
        # DynamoDB espera tipo S (string) para la PK 'task_id' pero recibe N →
        # ClientError: One or more parameter values were invalid: Type mismatch.
        # Comportamiento correcto: SIEMPRE usar Key={"task_id": task_id} (string).
        if task_id.isdigit():
            table.delete_item(Key={"task_id": int(task_id)})
        else:
            # [SE-18] BUG INTENCIONAL: usa 'id' como nombre de clave en vez de 'task_id'.
            # DynamoDB lanza ClientError porque 'id' no coincide con el key schema
            # de la tabla (que usa 'task_id' como partition key).
            # INCONDICIONAL para cualquier task_id que no sea solo dígitos.
            # Comportamiento correcto: Key={"task_id": task_id}
            table.delete_item(Key={"id": task_id})

        # [SE-7] BUG INTENCIONAL (ENMASCARADO): delete_item SIN ConditionExpression.
        # Este código NUNCA se alcanza porque SE-19 y SE-18 siempre producen
        # ClientError antes de llegar aquí. El bug original era:
        #   table.delete_item(Key={"task_id": task_id})
        # sin ConditionExpression="attribute_exists(task_id)", lo que hacía que
        # DynamoDB no lanzara error aunque el item no existiera → responde 200
        # en vez de 404.
        # Comportamiento correcto: usar TaskRepository.delete(task_id) que
        # incluye ConditionExpression y lanza NotFoundError (→ 404).

        return success_response(200, {"deleted": True, "task_id": task_id})

    except ValidationError as exc:
        logger.error("ERROR: validacion fallida en delete_task: %s", exc.message)
        return error_response(400, exc.code, exc.message)

    except botocore.exceptions.ClientError:
        logger.error("ERROR: ClientError en delete_task", exc_info=True)
        return error_response(500, "DDB_ERROR", "Error al acceder a la base de datos.")

    except botocore.exceptions.ParamValidationError:
        logger.error("ERROR: ParamValidationError en delete_task", exc_info=True)
        return error_response(500, "DDB_PARAM_ERROR", "Error de parametros en la base de datos.")

    except Exception:
        logger.error("ERROR: fallo no controlado en delete_task", exc_info=True)
        return error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")
