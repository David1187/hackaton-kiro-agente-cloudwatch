"""
handlers/create_task.py — Create Task Lambda handler (INTENCIONALMENTE DEFECTUOSO).

⚠️  CÓDIGO DELIBERADAMENTE DEFECTUOSO PARA DEMO DEL AGENTE DE AUTO-REPARACIÓN ⚠️

Este handler contiene los siguientes errores sembrados de forma PERMANENTE:
  • SE-1: No valida que el campo 'title' sea no vacío / no solo espacios.
  • SE-2: Usa un timestamp hardcoded ("1970-01-01T00:00:00.000+00:00") en vez
          de datetime.now(timezone.utc).
  • SE-8 (TRANSFORMADO): Manejo de errores incompleto — captura ClientError,
          ParamValidationError y Exception con logger.error(), pero NO captura
          ValidationError, que sigue propagándose al runtime (502 en API Gateway).
  • SE-10: Si el body trae 'priority', se copia al item sin convertir a Decimal.
           Con valor float, boto3 lanza TypeError ('Float types are not supported').
  • SE-11: Si el body trae 'task_id', se usa tal cual sin validar tipo ni generar
           uuid4. Con valor numérico, DynamoDB lanza ClientError por type mismatch.

Consultar: services/crud_api/DEMO_ERRORS.md para los payloads exactos que
disparan cada error de forma independiente.

EXCEPCIÓN DELIBERADA a architecture-guide.md §3 y backend-standards.md §1:
este código defectuoso ES el entregable para la demo del hackathon.
"""

from __future__ import annotations

import json
import uuid

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

# [SE-2] BUG INTENCIONAL: timestamp hardcoded — nunca refleja la hora real.
# Comportamiento correcto: usar datetime.now(timezone.utc) para generar un
# timestamp ISO 8601 dinámico con milisegundos.
_HARDCODED_TIMESTAMP = "1970-01-01T00:00:00.000+00:00"


def handler(event: dict, context: object) -> dict:
    """Lambda entry point for POST /tasks.

    ⚠️  HANDLER INTENCIONALMENTE DEFECTUOSO — contiene SE-1, SE-2, SE-8, SE-10, SE-11.
    Ver DEMO_ERRORS.md para payloads de disparo.

    Args:
        event:   Lambda proxy integration event.
        context: Lambda context object (unused).

    Returns:
        Lambda proxy response dict (o excepción no capturada para ValidationError).
    """
    # [SE-8] BUG INTENCIONAL (TRANSFORMADO): manejo de errores incompleto.
    # Se capturan ClientError, ParamValidationError y Exception genérica,
    # PERO NO se captura ValidationError → sigue propagándose al runtime
    # de Lambda → 502 en API Gateway.
    # Comportamiento correcto: capturar también ValidationError y devolver
    # error_response(400, exc.code, exc.message).
    try:
        body = Payload_Validator.parse_json_body(event)

        if "title" not in body:
            raise ValidationError(
                "MISSING_FIELD",
                "El campo 'title' es obligatorio para crear una tarea.",
            )

        # [SE-1] BUG INTENCIONAL: se usa el valor crudo sin validar.
        # No se llama a Payload_Validator.validate_title(), por lo que se aceptan
        # títulos vacíos ("") o de solo espacios ("   ") y se almacenan en DynamoDB.
        # Comportamiento correcto: title = Payload_Validator.validate_title(body["title"])
        title = body["title"]

        completed = False
        if "completed" in body:
            completed = Payload_Validator.validate_completed(body["completed"])

        # [SE-11] BUG INTENCIONAL: si el body trae 'task_id', se usa directamente
        # sin generar uuid4 y sin validar tipo.
        # Con {"title":"x","task_id":123} DynamoDB lanza ClientError porque la PK
        # espera tipo S (string) pero recibe N (number).
        # Comportamiento correcto: SIEMPRE generar task_id = str(uuid.uuid4()),
        # ignorar cualquier task_id proporcionado por el cliente.
        if "task_id" in body:
            task_id = body["task_id"]
        else:
            task_id = str(uuid.uuid4())

        # [SE-2] BUG INTENCIONAL: usa la constante _HARDCODED_TIMESTAMP en vez de
        # generar dinámicamente con datetime.now(timezone.utc).
        # Todos los items tendrán created_at = updated_at = "1970-01-01T00:00:00.000+00:00".
        task = {
            "task_id": task_id,
            "title": title,
            "completed": completed,
            "created_at": _HARDCODED_TIMESTAMP,
            "updated_at": _HARDCODED_TIMESTAMP,
        }

        # [SE-10] BUG INTENCIONAL: si el body trae 'priority', se copia al item
        # TAL CUAL sin convertir a Decimal.
        # Con {"title":"x","priority":3.5} boto3 lanza TypeError porque DynamoDB
        # no soporta float nativo de Python — requiere Decimal.
        # Comportamiento correcto: from decimal import Decimal
        #   if "priority" in body:
        #       task["priority"] = Decimal(str(body["priority"]))
        if "priority" in body:
            task["priority"] = body["priority"]

        TaskRepository.create(task)

        return success_response(201, task)

    except ValidationError:
        # [SE-8] BUG INTENCIONAL: ValidationError NO se captura para devolver 400.
        # Se re-lanza al runtime de Lambda → 502 en API Gateway.
        # Comportamiento correcto: return error_response(400, exc.code, exc.message)
        raise

    except botocore.exceptions.ClientError:
        logger.error("ERROR: ClientError en create_task", exc_info=True)
        return error_response(500, "DDB_ERROR", "Error al acceder a la base de datos.")

    except botocore.exceptions.ParamValidationError:
        logger.error("ERROR: ParamValidationError en create_task", exc_info=True)
        return error_response(500, "DDB_PARAM_ERROR", "Error de parametros en la base de datos.")

    except Exception:
        logger.error("ERROR: fallo no controlado en create_task", exc_info=True)
        return error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")
