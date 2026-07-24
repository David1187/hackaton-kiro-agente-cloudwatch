"""
seeded/se8_no_try_except.py — SE-8: Handler sin try-except.

SEEDED ERROR: Este handler no tiene ningún bloque try-except.  Cualquier
excepción de DynamoDB (ClientError, ParamValidationError) o de validación
se propaga sin capturar hasta el runtime de Lambda, que la registra en
CloudWatch Logs con el formato de error nativo del runtime.

El runtime de Lambda Python registra las excepciones no capturadas con el
prefijo "ERROR" (mayúscula, sin dos puntos), lo que activa el Metric Filter
si el patrón es `ERROR` sin los dos puntos; sin embargo, el patrón estándar
del proyecto es `ERROR:` con dos puntos, que proviene del logger propio.

Para garantizar que el Metric Filter `ERROR:` se activa, el módulo-level
logger del repositorio ya emite `ERROR:` antes de re-lanzar las excepciones.
Adicionalmente, el runtime de Lambda emite una línea que comienza con "ERROR"
(sin ":") al final del log, pero eso no coincide con el patrón `ERROR:`.

Desde el punto de vista del agente, el defecto observable es que la Lambda
no devuelve una respuesta JSON con statusCode sino que el runtime devuelve
un error de función ({"errorMessage": ..., "errorType": ...}) que API Gateway
convierte en un 502.

Objetivo del comportamiento correcto violado: Requisito 8 (genérico) — todo
handler debe capturar excepciones y devolver respuestas HTTP estructuradas
en lugar de dejar que el runtime las propague.

Cómo disparo el error:
  DELETE /tasks/{task_id} donde task_id no existe — sin ConditionExpression
  (usando TaskRepository.delete directamente) → ClientError →
  excepción propagada sin capturar.

  O más sencillo: invocar con TABLE_NAME apuntando a una tabla inexistente
  → ClientError(ResourceNotFoundException) propagado directamente.

Excepción / comportamiento observado:
  Cualquier excepción de boto3/DynamoDB llega al runtime de Lambda sin capturar.
  El runtime emite en stdout:
    [ERROR] ClientError: ...
  y devuelve a API Gateway un error de función (502 Bad Gateway).

NOTA: Este archivo es código de DEMOSTRACIÓN deliberadamente defectuoso.
      Nunca debe desplegarse como handler de producción.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from common import (
    TaskRepository,
    Payload_Validator,
    ValidationError,
    configure_logger,
    success_response,
)

logger = configure_logger(__name__)


def handler(event: dict, context: object) -> dict:
    """Handler sembrado SE-8: sin try-except en ningún nivel.

    Cualquier excepción (ValidationError, ClientError, KeyError, etc.) se
    propaga sin capturar hasta el runtime de Lambda, provocando un error de
    función (502 en API Gateway) y un log [ERROR] en CloudWatch.
    """
    # BUG SE-8: cero bloques try-except — todas las excepciones se propagan.

    body = Payload_Validator.parse_json_body(event)

    if "title" not in body:
        # ValidationError no capturada → error de función Lambda
        raise ValidationError(
            "MISSING_FIELD",
            "El campo 'title' es obligatorio para crear una tarea.",
        )

    title = Payload_Validator.validate_title(body["title"])

    completed = False
    if "completed" in body:
        completed = Payload_Validator.validate_completed(body["completed"])

    now = datetime.now(timezone.utc)
    timestamp = (
        now.strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{now.microsecond // 1000:03d}"
        + "+00:00"
    )

    task = {
        "task_id": str(uuid.uuid4()),
        "title": title,
        "completed": completed,
        "created_at": timestamp,
        "updated_at": timestamp,
    }

    # ClientError / ParamValidationError no capturado → error de función Lambda
    TaskRepository.create(task)

    return success_response(201, task)
