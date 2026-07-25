"""
handlers/get_task.py — Get Task Lambda handler (INTENCIONALMENTE DEFECTUOSO).

⚠️  CÓDIGO DELIBERADAMENTE DEFECTUOSO PARA DEMO DEL AGENTE DE AUTO-REPARACIÓN ⚠️

Este handler contiene los siguientes errores sembrados de forma PERMANENTE:
  • SE-3: Accede directamente a event["pathParameters"]["task_id"] sin comprobar
          existencia, provocando KeyError cuando pathParameters está ausente o
          task_id no está presente. La excepción cae en el except Exception
          genérico → 500 + log "ERROR:" → activa Metric Filter.
  • SE-9 (REDUCIDO): Usa print() SOLO en el except NotFoundError (el 404 sigue
          invisible para las alarmas). Los demás except usan logger.error() con
          prefijo "ERROR:" → SÍ disparan el Metric Filter.
  • SE-12: Lee el query param 'fields' y lo pasa como ProjectionExpression a
           get_item directo. Con ?fields=status (palabra reservada de DynamoDB),
           lanza ClientError.
  • SE-13: INCONDICIONAL. Accede a task['completed_at'] (atributo inexistente)
           al construir la respuesta → KeyError → except Exception → 500 SIEMPRE.

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
    TaskRepository,
    configure_logger,
    error_response,
    success_response,
)

logger = configure_logger(__name__)

# Acceso directo a DynamoDB para SE-12 (bypass de TaskRepository para
# poder pasar ProjectionExpression sin validar).
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
    """Lambda entry point for GET /tasks/{task_id}.

    ⚠️  HANDLER INTENCIONALMENTE DEFECTUOSO — contiene SE-3, SE-9, SE-12, SE-13.
    Ver DEMO_ERRORS.md para payloads de disparo.

    Args:
        event:   Lambda proxy integration event.
        context: Lambda context object (unused).

    Returns:
        Lambda proxy response dict.
    """
    try:
        # [SE-3] BUG INTENCIONAL: acceso directo a pathParameters["task_id"]
        # sin comprobar existencia de pathParameters ni de la clave task_id.
        # Si pathParameters no existe o task_id no está → KeyError.
        # Comportamiento correcto: usar Payload_Validator.validate_task_id(event)
        # que devuelve 400 INVALID_TASK_ID en vez de propagar KeyError.
        task_id = event["pathParameters"]["task_id"]

        # [SE-12] BUG INTENCIONAL: lee el query param 'fields' sin validar y lo
        # pasa como ProjectionExpression a get_item directo (bypass de TaskRepository).
        # Con ?fields=status, 'status' es palabra reservada de DynamoDB →
        # ClientError: Invalid ProjectionExpression.
        # Si 'fields' no viene, usa TaskRepository.get(task_id) como hasta ahora.
        # Comportamiento correcto: no aceptar ProjectionExpression del cliente,
        # o usar ExpressionAttributeNames para escapar palabras reservadas.
        query_params = event.get("queryStringParameters") or {}
        fields = query_params.get("fields")

        if fields:
            table = _get_table()
            response = table.get_item(
                Key={"task_id": task_id},
                ProjectionExpression=fields,
            )
            if "Item" not in response:
                raise TaskRepository.NotFoundError(f"Task no encontrada: task_id={task_id}")
            task = response["Item"]
        else:
            task = TaskRepository.get(task_id)

        # [SE-13] BUG INTENCIONAL (INCONDICIONAL): accede a task['completed_at'],
        # atributo que NUNCA existe en el item (el campo se llama 'completed',
        # no 'completed_at'). Provoca KeyError → except Exception → 500 SIEMPRE.
        # Comportamiento correcto: usar task.get('completed_at') o simplemente
        # devolver el item completo sin acceder a campos individuales.
        _ = task["completed_at"]

        return success_response(200, task)

    except TaskRepository.NotFoundError:
        # [SE-9] BUG INTENCIONAL (REDUCIDO): usa print() SOLO aquí en NotFoundError.
        # El 404 sigue INVISIBLE para las alarmas (no emite "ERROR:").
        # Comportamiento correcto: logger.error("ERROR: task no encontrada", exc_info=True)
        print(f"Task not found in get_task: task_id={event.get('pathParameters', {}).get('task_id')}")
        return error_response(404, "RESOURCE_NOT_FOUND", "La tarea solicitada no existe.")

    except botocore.exceptions.ClientError:
        # [SE-9 REDUCIDO]: los demás except ahora SÍ usan logger.error()
        logger.error("ERROR: ClientError en get_task", exc_info=True)
        return error_response(500, "DDB_ERROR", "Error al acceder a la base de datos.")

    except botocore.exceptions.ParamValidationError:
        logger.error("ERROR: ParamValidationError en get_task", exc_info=True)
        return error_response(500, "DDB_PARAM_ERROR", "Error de parametros en la base de datos.")

    except Exception:
        # [SE-3] El KeyError de pathParameters["task_id"] aterriza aquí.
        # [SE-13] El KeyError de task["completed_at"] también aterriza aquí.
        # [SE-9 REDUCIDO]: ahora SÍ usa logger.error() → SÍ dispara Metric Filter.
        logger.error("ERROR: fallo no controlado en get_task", exc_info=True)
        return error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")
