"""
seeded/se4_list_no_limit.py — SE-4: List sin límite en el scan de DynamoDB.

SEEDED ERROR: Este handler invoca ``table.scan()`` sin el parámetro ``Limit``,
lo que provoca un scan ilimitado sobre la tabla entera.  En tablas grandes
esto puede agotar la memoria Lambda o superar el timeout, causando un error
de ejecución registrado con el prefijo ERROR: en CloudWatch Logs.

Objetivo del comportamiento correcto violado: Requisito 3.2 — el sistema debe
limitar el scan a 1000 elementos para prevenir timeouts y gastos excesivos.

Cómo disparo el error reproduciblemente (en la demo):
  1. Poblar la tabla con más de 1000 items.
  2. GET /tasks — el scan ilimitado devuelve todos los items y puede superar
     el límite de memoria o tiempo de Lambda.

  Para la demo con datos limitados, el defecto más observable es invocar el
  handler directamente con moto y verificar que no se pasa Limit al scan
  (el handler expone el bug por inspección de código).

  El error ERROR: se registra si el scan supera el timeout de Lambda (también
  detectable via moto comprobando que el call no incluye Limit).

Excepción / comportamiento observable:
  Con tabla grande: ``botocore.exceptions.ClientError`` (ReadThrottled) o
  timeout de Lambda → registra ``ERROR:`` activando el Metric Filter.
  Con tabla pequeña: responde 200 pero devuelve todos los items sin límite.

NOTA: Este archivo es código de DEMOSTRACIÓN deliberadamente defectuoso.
      Nunca debe desplegarse como handler de producción.
"""

from __future__ import annotations

import boto3
import botocore.exceptions
import os

from common import (
    configure_logger,
    error_response,
    success_response,
)

logger = configure_logger(__name__)

# BUG SE-4: cliente DynamoDB propio sin límite — no usa TaskRepository.list()
# para poder omitir el parámetro Limit directamente.
_dynamodb = None
_table = None


def _get_table():
    global _dynamodb, _table
    if _table is None:
        _dynamodb = boto3.resource("dynamodb")
        _table = _dynamodb.Table(os.environ["TABLE_NAME"])
    return _table


def handler(event: dict, context: object) -> dict:
    """Handler sembrado SE-4: scan sin Limit — riesgo real de timeout/OOM.

    Omite el parámetro ``Limit`` en el scan de DynamoDB, devolviendo todos
    los items de la tabla sin restricción de cantidad.
    """
    try:
        table = _get_table()
        # BUG SE-4: scan sin Limit — puede devolver millones de registros
        response = table.scan()  # ← sin Limit=1000
        tasks = response.get("Items", [])

        return success_response(200, {"tasks": tasks, "count": len(tasks)})

    except botocore.exceptions.ClientError:
        logger.error("ERROR: ClientError en se4_list (scan sin limite)", exc_info=True)
        return error_response(500, "DDB_ERROR", "Error al acceder a la base de datos.")

    except botocore.exceptions.ParamValidationError:
        logger.error("ERROR: ParamValidationError en se4_list", exc_info=True)
        return error_response(500, "DDB_PARAM_ERROR", "Error de parametros en la base de datos.")

    except Exception:
        logger.error("ERROR: fallo no controlado en se4_list", exc_info=True)
        return error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")
