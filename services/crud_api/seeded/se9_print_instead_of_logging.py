"""
seeded/se9_print_instead_of_logging.py — SE-9: Logging vía print() en lugar de logging.

SEEDED ERROR: Este handler usa ``print()`` para reportar errores en lugar del
módulo estándar ``logging``.  Los mensajes de error emitidos con print() no
contienen el prefijo ``ERROR:`` que requiere el CloudWatch Metric Filter, por
lo que los errores de este handler son invisibles para el sistema de alertas.

Objetivo del comportamiento correcto violado: Requisito 9 (estándar de logging)
y Requisito 3 de ``architecture-guide.md`` — todo fallo debe registrarse con
``logging.error(..., exc_info=True)`` para que el Metric Filter ``ERROR:``
lo detecte.

Cómo disparo el error:
  GET /tasks/{task_id} con un task_id que no existe en DynamoDB.
  El handler imprime el error con print() pero CloudWatch Metric Filter
  no lo cuenta porque el prefijo ``ERROR:`` está ausente.

Excepción / comportamiento observado:
  No se lanza excepción al cliente.  El handler devuelve 404 o 500, pero
  los mensajes de error se emiten con print() y aparecen en CloudWatch Logs
  sin el prefijo ``ERROR:``.  El Metric Filter no se activa y el Agente de
  auto-reparación nunca recibe la alerta → los errores pasan desapercibidos.

NOTA: Este archivo es código de DEMOSTRACIÓN deliberadamente defectuoso.
      Nunca debe desplegarse como handler de producción.
"""

from __future__ import annotations

import botocore.exceptions

from common import (
    TaskRepository,
    Payload_Validator,
    ValidationError,
    error_response,
    success_response,
)

# BUG SE-9: no se inicializa ningún logger — se usa print() en su lugar.
# Nótese la ausencia de:   logger = configure_logger(__name__)


def handler(event: dict, context: object) -> dict:
    """Handler sembrado SE-9: usa print() en lugar de logging.error().

    Los errores se emiten en stdout con print(), lo que significa que
    CloudWatch Logs los recibe pero sin el prefijo ``ERROR:``.
    El Metric Filter ``ERROR:`` no los cuenta → no se dispara la alarma
    → el Agente de auto-reparación permanece inactivo aunque haya errores.
    """
    try:
        task_id = Payload_Validator.validate_task_id(event)
        task = TaskRepository.get(task_id)
        return success_response(200, task)

    except ValidationError as exc:
        # BUG SE-9: print() en lugar de logger.error("ERROR: ...")
        print(f"Validation error in se9_get: {exc.code} - {exc.message}")
        return error_response(400, exc.code, exc.message)

    except TaskRepository.NotFoundError:
        # BUG SE-9: print() en lugar de logger.error("ERROR: ...")
        print(f"Task not found in se9_get: task_id={event.get('pathParameters', {}).get('task_id')}")
        return error_response(404, "RESOURCE_NOT_FOUND", "La tarea solicitada no existe.")

    except botocore.exceptions.ClientError as exc:
        # BUG SE-9: print() — no activa el Metric Filter
        print(f"ClientError in se9_get: {exc}")
        return error_response(500, "DDB_ERROR", "Error al acceder a la base de datos.")

    except botocore.exceptions.ParamValidationError as exc:
        # BUG SE-9: print() — no activa el Metric Filter
        print(f"ParamValidationError in se9_get: {exc}")
        return error_response(500, "DDB_PARAM_ERROR", "Error de parametros en la base de datos.")

    except Exception as exc:
        # BUG SE-9: print() — no activa el Metric Filter
        print(f"Unhandled exception in se9_get: {exc}")
        return error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")
