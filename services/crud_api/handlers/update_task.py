"""
handlers/update_task.py — Update Task Lambda handler (INTENCIONALMENTE DEFECTUOSO).

⚠️  CÓDIGO DELIBERADAMENTE DEFECTUOSO PARA DEMO DEL AGENTE DE AUTO-REPARACIÓN ⚠️

Este handler contiene los siguientes errores sembrados de forma PERMANENTE:
  • SE-5: No valida que el body contenga al menos un campo actualizable
          (title o completed). Un body vacío {} pasa al repositorio, que
          construye un UpdateExpression vacío → ParamValidationError de DynamoDB
          → 500 + log "ERROR:" → activa Metric Filter.
  • SE-6: Nunca refresca el campo updated_at. Después de un update exitoso,
          updated_at permanece con el valor original de creación.
  • SE-16: Validación cruzada por copy-paste: para 'completed' se llama a
           Payload_Validator.validate_title(body['completed']) en vez de
           validate_completed. Con {"completed":true} lanza ValidationError
           INVALID_TYPE → 400 + log "ERROR:" → SÍ dispara la alarma.
  • SE-17: Si el body trae 'priority', se añade a attrs sin convertir a Decimal.
           Con valor float → TypeError en update_item.

Consultar: services/crud_api/DEMO_ERRORS.md para los payloads exactos que
disparan cada error de forma independiente.

EXCEPCIÓN DELIBERADA a architecture-guide.md §3 y backend-standards.md §1:
este código defectuoso ES el entregable para la demo del hackathon.
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
    """Lambda entry point for PUT /tasks/{task_id}.

    ⚠️  HANDLER INTENCIONALMENTE DEFECTUOSO — contiene SE-5, SE-6, SE-16, SE-17.
    Ver DEMO_ERRORS.md para payloads de disparo.

    Args:
        event:   Lambda proxy integration event.
        context: Lambda context object (unused).

    Returns:
        Lambda proxy response dict.
    """
    try:
        task_id = Payload_Validator.validate_task_id(event)
        body = Payload_Validator.parse_json_body(event)

        # [SE-5] BUG INTENCIONAL: NO se valida que el body contenga al menos
        # un campo actualizable ("title" o "completed").
        # Con body={}, attrs queda vacío → el repositorio construye
        # "SET" sin cláusulas → DynamoDB lanza ParamValidationError.
        # Comportamiento correcto:
        #   updatable_fields = {"title", "completed"}
        #   present = updatable_fields & body.keys()
        #   if not present:
        #       raise ValidationError("MISSING_FIELD", "...")

        attrs: dict = {}

        if "title" in body:
            attrs["title"] = Payload_Validator.validate_title(body["title"])

        if "completed" in body:
            # [SE-16] BUG INTENCIONAL: validación cruzada por copy-paste.
            # Se llama a validate_title() en vez de validate_completed().
            # validate_title() espera un string → con {"completed":true} (bool)
            # lanza ValidationError(INVALID_TYPE, "El campo 'title' debe ser una
            # cadena de texto (string).").
            # Consecuencia: 'completed' ya no se puede actualizar nunca.
            # Comportamiento correcto: Payload_Validator.validate_completed(body["completed"])
            attrs["completed"] = Payload_Validator.validate_title(body["completed"])

        # [SE-17] BUG INTENCIONAL: si el body trae 'priority', se añade a attrs
        # TAL CUAL sin convertir a Decimal.
        # Con {"priority": 2.5} → TypeError en update_item porque DynamoDB no
        # soporta float nativo de Python — requiere Decimal.
        # Comportamiento correcto: from decimal import Decimal
        #   if "priority" in body:
        #       attrs["priority"] = Decimal(str(body["priority"]))
        if "priority" in body:
            attrs["priority"] = body["priority"]

        # [SE-6] BUG INTENCIONAL: NO se añade updated_at a attrs.
        # El timestamp de última modificación queda congelado en el valor
        # original de created_at.
        # Comportamiento correcto:
        #   from datetime import datetime, timezone
        #   now = datetime.now(timezone.utc)
        #   timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}" + "+00:00"
        #   attrs["updated_at"] = timestamp

        updated_task = TaskRepository.update(task_id, attrs)
        return success_response(200, updated_task)

    except ValidationError as exc:
        logger.error("ERROR: validacion fallida en update_task: %s", exc.message)
        return error_response(400, exc.code, exc.message)

    except TaskRepository.NotFoundError:
        return error_response(404, "RESOURCE_NOT_FOUND", "La tarea solicitada no existe.")

    except botocore.exceptions.ClientError:
        logger.error("ERROR: ClientError en update_task", exc_info=True)
        return error_response(500, "DDB_ERROR", "Error al acceder a la base de datos.")

    except botocore.exceptions.ParamValidationError:
        logger.error("ERROR: ParamValidationError en update_task", exc_info=True)
        return error_response(500, "DDB_PARAM_ERROR", "Error de parametros en la base de datos.")

    except Exception:
        logger.error("ERROR: fallo no controlado en update_task", exc_info=True)
        return error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")
