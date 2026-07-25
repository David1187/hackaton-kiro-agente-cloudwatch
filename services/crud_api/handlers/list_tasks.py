"""
handlers/list_tasks.py — List Tasks Lambda handler (INTENCIONALMENTE DEFECTUOSO).

⚠️  CÓDIGO DELIBERADAMENTE DEFECTUOSO PARA DEMO DEL AGENTE DE AUTO-REPARACIÓN ⚠️

Este handler contiene los siguientes errores sembrados de forma PERMANENTE:
  • SE-4: Ejecuta table.scan() SIN el parámetro Limit cuando no hay query params,
          devolviendo TODOS los items de la tabla sin restricción.
  • SE-14: Si viene el query param 'limit', se pasa a scan(Limit=<valor>) sin
           castear a int → ParamValidationError para CUALQUIER valor de limit.
  • SE-15: Si viene el query param 'next', se pasa a scan(ExclusiveStartKey=<string>)
           sin decodificar a dict → ParamValidationError.

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
    configure_logger,
    error_response,
    success_response,
)

logger = configure_logger(__name__)

# [SE-4] BUG INTENCIONAL: se crea un acceso directo a DynamoDB sin usar
# TaskRepository.list(limit=1000) para poder omitir el parámetro Limit.
# Comportamiento correcto: usar TaskRepository.list(limit=1000) que aplica
# Limit en el scan.
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
    """Lambda entry point for GET /tasks.

    ⚠️  HANDLER INTENCIONALMENTE DEFECTUOSO — contiene SE-4, SE-14, SE-15.
    Ver DEMO_ERRORS.md para payloads de disparo.

    Args:
        event:   Lambda proxy integration event (no parameters required).
        context: Lambda context object (unused).

    Returns:
        Lambda proxy response dict.
    """
    try:
        table = _get_table()
        query_params = event.get("queryStringParameters") or {}

        scan_kwargs = {}

        # [SE-14] BUG INTENCIONAL: si viene 'limit', se pasa TAL CUAL (string)
        # sin castear a int. DynamoDB espera un entero para Limit →
        # ParamValidationError para CUALQUIER valor.
        # Comportamiento correcto: scan_kwargs["Limit"] = int(query_params["limit"])
        if "limit" in query_params:
            scan_kwargs["Limit"] = query_params["limit"]

        # [SE-15] BUG INTENCIONAL: si viene 'next', se pasa como string directamente
        # a ExclusiveStartKey. DynamoDB espera un dict con la clave primaria →
        # ParamValidationError.
        # Comportamiento correcto: decodificar el token (ej. base64 → json → dict)
        #   import json, base64
        #   scan_kwargs["ExclusiveStartKey"] = json.loads(base64.b64decode(query_params["next"]))
        if "next" in query_params:
            scan_kwargs["ExclusiveStartKey"] = query_params["next"]

        # [SE-4] BUG INTENCIONAL: scan SIN Limit (cuando no se pasa 'limit').
        # Devuelve todos los items de la tabla sin restricción de cantidad.
        # Con tabla grande: posible timeout, OOM, o ClientError(ReadThrottled).
        # Comportamiento correcto: table.scan(Limit=1000) o usar
        # TaskRepository.list(limit=1000).
        response = table.scan(**scan_kwargs)
        tasks = response.get("Items", [])

        return success_response(200, {"tasks": tasks, "count": len(tasks)})

    except botocore.exceptions.ClientError:
        logger.error("ERROR: ClientError en list_tasks (scan sin limite)", exc_info=True)
        return error_response(500, "DDB_ERROR", "Error al acceder a la base de datos.")

    except botocore.exceptions.ParamValidationError:
        logger.error("ERROR: ParamValidationError en list_tasks", exc_info=True)
        return error_response(500, "DDB_PARAM_ERROR", "Error de parametros en la base de datos.")

    except Exception:
        logger.error("ERROR: fallo no controlado en list_tasks", exc_info=True)
        return error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")
