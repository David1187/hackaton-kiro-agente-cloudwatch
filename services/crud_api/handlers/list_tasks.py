"""
handlers/list_tasks.py — List Tasks Lambda handler.

GET /tasks

Scans DynamoDB (up to 1000 items) and responds 200 with:
  {"tasks": [...], "count": <int>}

An empty table returns {"tasks": [], "count": 0} — never 404.
All Decimal values from DynamoDB are handled by DecimalEncoder.

Error hierarchy:
  1. ClientError      → 500
  2. ParamValidationError → 500
  3. Exception (net)  → 500

Requirements: 3.1, 3.2, 3.4
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
    """Lambda entry point for GET /tasks.

    Args:
        event:   Lambda proxy integration event (no parameters required).
        context: Lambda context object (unused).

    Returns:
        Lambda proxy response dict.
    """
    try:
        # --- 1. Retrieve all tasks (scan, limit 1000) ---------------------- #
        tasks = TaskRepository.list(limit=1000)

        # --- 2. Respond 200 with tasks + count ---------------------------- #
        return success_response(200, {"tasks": tasks, "count": len(tasks)})

    except botocore.exceptions.ClientError:
        logger.error("ERROR: ClientError en list_tasks", exc_info=True)
        return error_response(500, "DDB_ERROR", "Error al acceder a la base de datos.")

    except botocore.exceptions.ParamValidationError:
        logger.error("ERROR: ParamValidationError en list_tasks", exc_info=True)
        return error_response(500, "DDB_PARAM_ERROR", "Error de parametros en la base de datos.")

    except Exception:
        logger.error("ERROR: fallo no controlado en list_tasks", exc_info=True)
        return error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")
