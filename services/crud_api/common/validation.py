"""
validation.py — Input validation helpers for all CRUD Lambda handlers.

All public functions raise ``ValidationError`` on invalid input so that
handlers can catch it in a single ``except ValidationError`` clause and
return a 400 response without touching DynamoDB.

ValidationError.code is always UPPER_SNAKE_CASE and stable (it forms part of
the public error contract — see design.md § Data Models).

Supported error codes:
  INVALID_JSON       — body absent, empty, or not valid JSON.
  MISSING_FIELD      — a required field is absent from the parsed body.
  INVALID_TYPE       — a field is present but has the wrong Python type.
  INVALID_TITLE      — title string is out of range (1–255 after strip).
  INVALID_TASK_ID    — task_id absent, blank, or longer than 256 characters.
"""

from __future__ import annotations

import json


class ValidationError(Exception):
    """Raised by Payload_Validator when request input is invalid.

    Attributes:
        code: Machine-readable error identifier in UPPER_SNAKE_CASE.
        message: Human-readable description (safe to return to the caller).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Payload_Validator:  # noqa: N801 — name matches design doc convention
    """Namespace for all payload validation helpers used by CRUD handlers."""

    # ------------------------------------------------------------------ #
    # Body parsing                                                         #
    # ------------------------------------------------------------------ #

    @staticmethod
    def parse_json_body(event: dict) -> dict:
        """Parse and return the JSON body from a Lambda proxy event.

        Args:
            event: Lambda proxy integration event dict.

        Returns:
            Parsed body as a ``dict``.

        Raises:
            ValidationError(INVALID_JSON): if body is absent, empty, or
                                           not valid JSON; or if the decoded
                                           value is not a JSON object (dict).
        """
        body_raw = event.get("body")
        if not body_raw or not body_raw.strip():
            raise ValidationError(
                "INVALID_JSON",
                "El body de la peticion es obligatorio y no puede estar vacio.",
            )
        try:
            body = json.loads(body_raw)
        except (json.JSONDecodeError, ValueError):
            raise ValidationError(
                "INVALID_JSON",
                "El body de la peticion no es JSON valido.",
            )
        if not isinstance(body, dict):
            raise ValidationError(
                "INVALID_JSON",
                "El body de la peticion debe ser un objeto JSON.",
            )
        return body

    # ------------------------------------------------------------------ #
    # Field validators                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def validate_title(value: object) -> str:
        """Validate and normalise a task title.

        Args:
            value: Raw value extracted from the parsed body.

        Returns:
            The title stripped of leading/trailing whitespace.

        Raises:
            ValidationError(INVALID_TYPE):  value is not a string.
            ValidationError(INVALID_TITLE): stripped string is empty or longer
                                             than 255 characters.
        """
        if not isinstance(value, str):
            raise ValidationError(
                "INVALID_TYPE",
                "El campo 'title' debe ser una cadena de texto (string).",
            )
        stripped = value.strip()
        if len(stripped) < 1:
            raise ValidationError(
                "INVALID_TITLE",
                "El campo 'title' no puede estar vacio.",
            )
        if len(stripped) > 255:
            raise ValidationError(
                "INVALID_TITLE",
                "El campo 'title' no puede superar los 255 caracteres.",
            )
        return stripped

    @staticmethod
    def validate_completed(value: object) -> bool:
        """Validate a boolean 'completed' field with strict type checking.

        Integers (0, 1) and strings ("true", "false") are intentionally
        rejected — only ``True`` / ``False`` are accepted.

        Args:
            value: Raw value extracted from the parsed body.

        Returns:
            The boolean value as-is.

        Raises:
            ValidationError(INVALID_TYPE): value is not a strict bool.
        """
        # isinstance(True, int) is True in Python, so we must check bool first
        if not isinstance(value, bool):
            raise ValidationError(
                "INVALID_TYPE",
                "El campo 'completed' debe ser un booleano estricto (true/false).",
            )
        return value

    @staticmethod
    def validate_task_id(event: dict) -> str:
        """Extract and validate the ``task_id`` path parameter.

        Args:
            event: Lambda proxy integration event dict.  The ``task_id`` is
                   expected under ``event["pathParameters"]["task_id"]``.

        Returns:
            The non-empty ``task_id`` string (not stripped — the value is used
            as a DynamoDB key verbatim).

        Raises:
            ValidationError(INVALID_TASK_ID): pathParameters absent/None,
                                              task_id absent, blank/whitespace,
                                              or longer than 256 characters.
        """
        path_params = event.get("pathParameters") or {}
        task_id = path_params.get("task_id")

        if task_id is None:
            raise ValidationError(
                "INVALID_TASK_ID",
                "El parametro 'task_id' es obligatorio en la ruta.",
            )
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValidationError(
                "INVALID_TASK_ID",
                "El parametro 'task_id' no puede estar vacio.",
            )
        if len(task_id) > 256:
            raise ValidationError(
                "INVALID_TASK_ID",
                "El parametro 'task_id' no puede superar los 256 caracteres.",
            )
        return task_id
