"""
seeded/se3_get_missing_task_id.py — SE-3: Get sin manejo de task_id ausente.

SEEDED ERROR: Este handler accede directamente a
``event["pathParameters"]["task_id"]`` sin comprobar si pathParameters existe
ni si task_id está presente, lo que provoca un ``KeyError`` sin capturar
cuando la ruta no lleva el parámetro.

Objetivo del comportamiento correcto violado: Requisito 2.2 — el sistema debe
devolver 400 INVALID_TASK_ID cuando task_id está ausente, en lugar de propagar
un KeyError no controlado.

Cómo disparo el error:
  Invocar el handler con un evento sin pathParameters:
    event = {"httpMethod": "GET", "path": "/tasks"}
  O con pathParameters vacío:
    event = {"pathParameters": {}}

Excepción / comportamiento observado:
  ``KeyError: 'task_id'``  (o ``KeyError: 'pathParameters'`` si se omite
  completamente el campo).  La excepción no está capturada, por lo que Lambda
  la propaga y el runtime la registra como ERROR en CloudWatch Logs con el
  prefijo ``ERROR:`` garantizado por el logger del módulo ``repository``.

  En la práctica, como el bloque except Exception sí captura el KeyError,
  el handler devuelve 500 y registra:
    ERROR: fallo no controlado en se3_get  (con traceback completo)
  lo que activa el CloudWatch Metric Filter.

NOTA: Este archivo es código de DEMOSTRACIÓN deliberadamente defectuoso.
      Nunca debe desplegarse como handler de producción.
"""

from __future__ import annotations

import botocore.exceptions

from common import (
    TaskRepository,
    configure_logger,
    error_response,
    success_response,
)

logger = configure_logger(__name__)


def handler(event: dict, context: object) -> dict:
    """Handler sembrado SE-3: acceso directo a pathParameters sin guardia.

    Lanza KeyError cuando pathParameters está ausente o task_id no está
    en pathParameters.  El except Exception genérico lo captura y devuelve
    500 con log ERROR: activando el Metric Filter.
    """
    try:
        # BUG SE-3: acceso directo sin comprobar existencia de pathParameters
        # ni de la clave task_id.  Lanza KeyError si alguno falta.
        task_id = event["pathParameters"]["task_id"]  # ← sin validación

        task = TaskRepository.get(task_id)
        return success_response(200, task)

    except TaskRepository.NotFoundError:
        return error_response(404, "RESOURCE_NOT_FOUND", "La tarea solicitada no existe.")

    except botocore.exceptions.ClientError:
        logger.error("ERROR: ClientError en se3_get", exc_info=True)
        return error_response(500, "DDB_ERROR", "Error al acceder a la base de datos.")

    except botocore.exceptions.ParamValidationError:
        logger.error("ERROR: ParamValidationError en se3_get", exc_info=True)
        return error_response(500, "DDB_PARAM_ERROR", "Error de parametros en la base de datos.")

    except Exception:
        # KeyError de SE-3 aterriza aquí → produce log ERROR: + respuesta 500
        logger.error("ERROR: fallo no controlado en se3_get", exc_info=True)
        return error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")
