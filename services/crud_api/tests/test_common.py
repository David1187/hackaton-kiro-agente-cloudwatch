"""
tests/test_common.py — Unit and property-based tests for crud_api/common/.

Covers:
  2.5  Property test: validate_title
  2.6  Property test: validate_completed
  2.7  Property test: validate_task_id
  2.8  Property test: error_response never leaks internal details
  2.10 Property test: TaskRepository error translation
  2.11 Unit tests:    Error_Logger (configure_logger) + DecimalEncoder
"""

from __future__ import annotations

import decimal
import io
import json
import logging
import os
import sys

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from moto import mock_aws
import boto3

# ---------------------------------------------------------------------------
# Ensure crud_api root is on sys.path (conftest.py also does this, but being
# explicit here makes the file runnable standalone too).
# ---------------------------------------------------------------------------
_CRUD_API_ROOT = os.path.join(os.path.dirname(__file__), "..")
if _CRUD_API_ROOT not in sys.path:
    sys.path.insert(0, _CRUD_API_ROOT)

os.environ.setdefault("TABLE_NAME", "Tasks")
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")

from common.encoding import DecimalEncoder  # noqa: E402
from common.logging_config import configure_logger  # noqa: E402
from common.responses import error_response, success_response  # noqa: E402
from common.validation import Payload_Validator, ValidationError  # noqa: E402


# ===========================================================================
# 2.11 — Unit tests: DecimalEncoder
# ===========================================================================


class TestDecimalEncoder:
    """DecimalEncoder converts Decimal to int (no fractional) or float."""

    def test_integer_decimal_becomes_int(self):
        result = json.dumps(decimal.Decimal("42"), cls=DecimalEncoder)
        assert result == "42"
        assert isinstance(json.loads(result), int)

    def test_fractional_decimal_becomes_float(self):
        result = json.dumps(decimal.Decimal("3.14"), cls=DecimalEncoder)
        loaded = json.loads(result)
        assert isinstance(loaded, float)
        assert abs(loaded - 3.14) < 1e-6

    def test_zero_decimal_becomes_int(self):
        result = json.dumps(decimal.Decimal("0"), cls=DecimalEncoder)
        assert json.loads(result) == 0
        assert isinstance(json.loads(result), int)

    def test_negative_integer_decimal(self):
        result = json.dumps(decimal.Decimal("-10"), cls=DecimalEncoder)
        assert json.loads(result) == -10

    def test_negative_fractional_decimal(self):
        result = json.dumps(decimal.Decimal("-1.5"), cls=DecimalEncoder)
        loaded = json.loads(result)
        assert isinstance(loaded, float)

    def test_non_decimal_falls_through_to_default(self):
        with pytest.raises(TypeError):
            json.dumps(object(), cls=DecimalEncoder)

    def test_nested_dict_with_decimal(self):
        data = {"price": decimal.Decimal("9.99"), "count": decimal.Decimal("3")}
        result = json.loads(json.dumps(data, cls=DecimalEncoder))
        assert result["count"] == 3
        assert isinstance(result["count"], int)
        assert isinstance(result["price"], float)


# ===========================================================================
# 2.11 — Unit tests: configure_logger (Error_Logger)
# ===========================================================================


class TestConfigureLogger:
    """configure_logger returns a logger whose error records start with ERROR:"""

    def _capture_log_output(self, logger: logging.Logger, message: str, exc_info: bool = False) -> str:
        """Helper: capture text emitted by logger to a StringIO stream."""
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger.addHandler(handler)
        try:
            logger.error(message, exc_info=exc_info)
        finally:
            logger.removeHandler(handler)
        return buf.getvalue()

    def test_returns_logger_instance(self):
        logger = configure_logger("test.module")
        assert isinstance(logger, logging.Logger)

    def test_error_record_starts_with_ERROR_prefix(self):
        logger = configure_logger("test.prefix")
        output = self._capture_log_output(logger, "something went wrong")
        # The first characters must be the literal "ERROR:"
        assert output.startswith("ERROR:"), f"Output was: {output!r}"

    def test_no_duplicate_handlers_on_repeated_calls(self):
        name = "test.no_dup_handlers"
        logger = configure_logger(name)
        initial_count = len(logger.handlers)
        configure_logger(name)  # second call
        assert len(logger.handlers) == initial_count

    def test_exc_info_includes_traceback(self):
        logger = configure_logger("test.exc_info")
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger.addHandler(handler)
        try:
            try:
                raise ValueError("test error")
            except ValueError:
                logger.error("ERROR: caught in test", exc_info=True)
        finally:
            logger.removeHandler(handler)
        output = buf.getvalue()
        assert "ERROR:" in output
        assert "ValueError" in output

    def test_logger_level_is_warning_or_lower(self):
        # Logger must emit ERROR records (level 40); WARNING threshold (30) or lower is acceptable.
        logger = configure_logger("test.level")
        assert logger.level <= logging.WARNING


# ===========================================================================
# 2.5 — Property tests: validate_title
# ===========================================================================


class TestValidateTitleProperties:
    """Property-based tests for Payload_Validator.validate_title."""

    @given(
        st.text(
            alphabet=st.characters(blacklist_categories=("Cs",)),
            min_size=1,
            max_size=255,
        ).filter(lambda s: len(s.strip()) >= 1)
    )
    def test_valid_titles_are_accepted(self, title: str):
        """Any non-empty string up to 255 printable chars after strip is valid."""
        result = Payload_Validator.validate_title(title)
        assert isinstance(result, str)
        assert len(result) >= 1
        assert len(result) <= 255

    @given(st.text(alphabet=" \t\n\r", min_size=1, max_size=50))
    def test_whitespace_only_is_rejected(self, title: str):
        """Strings that strip to empty must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.validate_title(title)
        assert exc_info.value.code in ("INVALID_TITLE",)

    @given(st.integers() | st.floats(allow_nan=False) | st.booleans() | st.none())
    def test_non_string_is_rejected(self, value):
        """Non-string types must raise ValidationError with INVALID_TYPE."""
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.validate_title(value)
        assert exc_info.value.code == "INVALID_TYPE"

    @given(st.text(min_size=256, max_size=300).filter(lambda s: len(s.strip()) > 255))
    def test_too_long_title_is_rejected(self, title: str):
        """Titles longer than 255 chars after strip must raise INVALID_TITLE."""
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.validate_title(title)
        assert exc_info.value.code == "INVALID_TITLE"

    def test_strip_is_applied(self):
        """validate_title strips whitespace and returns the normalised value."""
        result = Payload_Validator.validate_title("  hello world  ")
        assert result == "hello world"

    def test_exactly_255_chars_is_accepted(self):
        title = "a" * 255
        assert Payload_Validator.validate_title(title) == title

    def test_empty_string_is_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.validate_title("")
        assert exc_info.value.code == "INVALID_TITLE"


# ===========================================================================
# 2.6 — Property tests: validate_completed
# ===========================================================================


class TestValidateCompletedProperties:
    """Property-based tests for Payload_Validator.validate_completed."""

    @given(st.booleans())
    def test_true_and_false_are_accepted(self, value: bool):
        assert Payload_Validator.validate_completed(value) == value

    @given(st.integers())
    def test_integers_are_rejected(self, value: int):
        """Even 0 and 1 must be rejected — strict bool only."""
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.validate_completed(value)
        assert exc_info.value.code == "INVALID_TYPE"

    @given(st.text())
    def test_strings_are_rejected(self, value: str):
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.validate_completed(value)
        assert exc_info.value.code == "INVALID_TYPE"

    @given(st.none())
    def test_none_is_rejected(self, value):
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.validate_completed(value)
        assert exc_info.value.code == "INVALID_TYPE"

    @given(st.floats(allow_nan=False))
    def test_floats_are_rejected(self, value: float):
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.validate_completed(value)
        assert exc_info.value.code == "INVALID_TYPE"


# ===========================================================================
# 2.7 — Property tests: validate_task_id
# ===========================================================================


class TestValidateTaskIdProperties:
    """Property-based tests for Payload_Validator.validate_task_id."""

    def _make_event(self, task_id) -> dict:
        return {"pathParameters": {"task_id": task_id}}

    @given(
        st.text(
            alphabet=st.characters(blacklist_categories=("Cs",)),
            min_size=1,
            max_size=256,
        ).filter(lambda s: s.strip())
    )
    def test_valid_task_ids_are_accepted(self, task_id: str):
        event = self._make_event(task_id)
        result = Payload_Validator.validate_task_id(event)
        assert result == task_id

    @given(st.text(alphabet=" \t\n\r", min_size=1, max_size=10))
    def test_whitespace_only_task_id_is_rejected(self, task_id: str):
        event = self._make_event(task_id)
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.validate_task_id(event)
        assert exc_info.value.code == "INVALID_TASK_ID"

    @given(st.text(min_size=257, max_size=300))
    def test_too_long_task_id_is_rejected(self, task_id: str):
        event = self._make_event(task_id)
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.validate_task_id(event)
        assert exc_info.value.code == "INVALID_TASK_ID"

    def test_missing_path_parameters_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.validate_task_id({})
        assert exc_info.value.code == "INVALID_TASK_ID"

    def test_none_path_parameters_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.validate_task_id({"pathParameters": None})
        assert exc_info.value.code == "INVALID_TASK_ID"

    def test_missing_task_id_key_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.validate_task_id({"pathParameters": {}})
        assert exc_info.value.code == "INVALID_TASK_ID"

    def test_exactly_256_chars_is_accepted(self):
        task_id = "x" * 256
        result = Payload_Validator.validate_task_id(self._make_event(task_id))
        assert result == task_id

    def test_257_chars_is_rejected(self):
        task_id = "x" * 257
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.validate_task_id(self._make_event(task_id))
        assert exc_info.value.code == "INVALID_TASK_ID"


# ===========================================================================
# 2.8 — Property tests: error_response never leaks internal details
# ===========================================================================


class TestErrorResponseNoLeakage:
    """error_response must have correct structure and not introduce internal leakage."""

    @given(
        st.integers(min_value=400, max_value=599),
        st.from_regex(r"[A-Z][A-Z0-9_]{1,39}", fullmatch=True),
        st.text(min_size=1, max_size=200),
    )
    def test_arbitrary_error_response_has_correct_structure(
        self, status_code: int, code: str, message: str
    ):
        resp = error_response(status_code, code, message)
        assert resp["statusCode"] == status_code
        assert resp["headers"]["Content-Type"] == "application/json"
        body = json.loads(resp["body"])
        assert "error" in body
        assert body["error"]["code"] == code
        assert body["error"]["message"] == message

    @given(st.text(min_size=1, max_size=200))
    @settings(max_examples=50)
    def test_error_body_has_only_error_key(self, message: str):
        """The wrapper must only inject {"error": ...}, nothing extra."""
        resp = error_response(500, "INTERNAL_ERROR", message)
        body = json.loads(resp["body"])
        # Wrapper must only have "error" key — no accidental leakage
        assert set(body.keys()) == {"error"}
        assert set(body["error"].keys()) == {"code", "message"}

    def test_success_response_uses_decimal_encoder(self):
        payload = {"price": decimal.Decimal("4.99"), "count": decimal.Decimal("2")}
        resp = success_response(200, payload)
        body = json.loads(resp["body"])
        assert isinstance(body["count"], int)
        assert isinstance(body["price"], float)

    def test_error_response_status_codes(self):
        for code, msg in [(400, "bad"), (404, "not found"), (500, "internal")]:
            resp = error_response(code, "X", msg)
            assert resp["statusCode"] == code

    def test_success_response_status_code(self):
        resp = success_response(201, {"task_id": "abc"})
        assert resp["statusCode"] == 201


# ===========================================================================
# Additional unit tests: parse_json_body
# ===========================================================================


class TestParseJsonBody:
    def test_valid_json_body(self):
        event = {"body": '{"title": "test"}'}
        result = Payload_Validator.parse_json_body(event)
        assert result == {"title": "test"}

    def test_missing_body_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.parse_json_body({})
        assert exc_info.value.code == "INVALID_JSON"

    def test_none_body_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.parse_json_body({"body": None})
        assert exc_info.value.code == "INVALID_JSON"

    def test_empty_body_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.parse_json_body({"body": ""})
        assert exc_info.value.code == "INVALID_JSON"

    def test_whitespace_body_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.parse_json_body({"body": "   "})
        assert exc_info.value.code == "INVALID_JSON"

    def test_malformed_json_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.parse_json_body({"body": "{not json}"})
        assert exc_info.value.code == "INVALID_JSON"

    def test_json_array_raises(self):
        """Top-level JSON arrays are not valid task bodies."""
        with pytest.raises(ValidationError) as exc_info:
            Payload_Validator.parse_json_body({"body": "[1,2,3]"})
        assert exc_info.value.code == "INVALID_JSON"


# ===========================================================================
# 2.10 — Tests: TaskRepository with moto (DynamoDB Local mock)
# ===========================================================================

TABLE_NAME = "Tasks"


@pytest.fixture()
def ddb_table():
    """Fixture: create a moto-mocked DynamoDB table and reset _state."""
    with mock_aws():
        # Reset lazy state so _get_table() re-initialises inside mock context
        import common.repository as repo_mod
        repo_mod._state["dynamodb"] = None
        repo_mod._state["table"] = None

        dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
        dynamodb.create_table(
            TableName=TABLE_NAME,
            KeySchema=[{"AttributeName": "task_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "task_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield

        # Clean up _state after test
        repo_mod._state["dynamodb"] = None
        repo_mod._state["table"] = None


def _sample_task(task_id: str = "test-123") -> dict:
    return {
        "task_id": task_id,
        "title": "Test Task",
        "completed": False,
        "created_at": "2025-01-01T00:00:00.000Z",
        "updated_at": "2025-01-01T00:00:00.000Z",
    }


class TestTaskRepositoryUnit:
    """Unit tests for TaskRepository using moto."""

    def test_create_returns_task_id(self, ddb_table):
        from common.repository import TaskRepository
        task = _sample_task("create-001")
        result = TaskRepository.create(task)
        assert result == "create-001"

    def test_get_existing_item(self, ddb_table):
        from common.repository import TaskRepository
        task = _sample_task("get-001")
        TaskRepository.create(task)
        retrieved = TaskRepository.get("get-001")
        assert retrieved["task_id"] == "get-001"
        assert retrieved["title"] == "Test Task"

    def test_get_nonexistent_raises_not_found(self, ddb_table):
        from common.repository import TaskRepository
        with pytest.raises(TaskRepository.NotFoundError):
            TaskRepository.get("nonexistent-xyz")

    def test_delete_existing_item(self, ddb_table):
        from common.repository import TaskRepository
        task = _sample_task("del-001")
        TaskRepository.create(task)
        result = TaskRepository.delete("del-001")
        assert result == "del-001"
        # Confirm gone
        with pytest.raises(TaskRepository.NotFoundError):
            TaskRepository.get("del-001")

    def test_delete_nonexistent_raises_not_found(self, ddb_table):
        from common.repository import TaskRepository
        with pytest.raises(TaskRepository.NotFoundError):
            TaskRepository.delete("ghost-task")

    def test_update_nonexistent_raises_not_found(self, ddb_table):
        from common.repository import TaskRepository
        with pytest.raises(TaskRepository.NotFoundError):
            TaskRepository.update(
                "ghost-task",
                {"title": "new title", "updated_at": "2025-01-01T00:00:01.000Z"},
            )

    def test_update_existing_item(self, ddb_table):
        from common.repository import TaskRepository
        task = _sample_task("upd-001")
        TaskRepository.create(task)
        updated = TaskRepository.update(
            "upd-001",
            {"title": "updated title", "updated_at": "2025-01-01T00:00:01.000Z"},
        )
        assert updated["title"] == "updated title"
        assert updated["task_id"] == "upd-001"

    def test_list_returns_all_items(self, ddb_table):
        from common.repository import TaskRepository
        for i in range(3):
            TaskRepository.create(_sample_task(f"list-{i}"))
        items = TaskRepository.list()
        assert len(items) == 3

    def test_list_empty_table(self, ddb_table):
        from common.repository import TaskRepository
        items = TaskRepository.list()
        assert items == []

    def test_update_completed_field(self, ddb_table):
        from common.repository import TaskRepository
        task = _sample_task("comp-001")
        TaskRepository.create(task)
        updated = TaskRepository.update(
            "comp-001",
            {"completed": True, "updated_at": "2025-01-01T00:00:02.000Z"},
        )
        assert updated["completed"] is True


class TestTaskRepositoryPropertyBased:
    """Property-based tests for TaskRepository error translation (task 2.10)."""

    @given(
        st.text(
            alphabet=st.characters(blacklist_categories=("Cs",)),
            min_size=1,
            max_size=36,
        ).filter(lambda s: s.strip())
    )
    @settings(max_examples=10, deadline=None)  # deadline=None: moto context setup is I/O-bound
    def test_get_nonexistent_always_raises_not_found(self, task_id: str):
        """Any task_id not in the table must raise NotFoundError, never another exception."""
        with mock_aws():
            import common.repository as repo_mod
            repo_mod._state["dynamodb"] = None
            repo_mod._state["table"] = None

            dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
            dynamodb.create_table(
                TableName=TABLE_NAME,
                KeySchema=[{"AttributeName": "task_id", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "task_id", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )

            from common.repository import TaskRepository
            with pytest.raises(TaskRepository.NotFoundError):
                TaskRepository.get(task_id)

            repo_mod._state["dynamodb"] = None
            repo_mod._state["table"] = None

    @given(
        st.text(
            alphabet=st.characters(blacklist_categories=("Cs",)),
            min_size=1,
            max_size=36,
        ).filter(lambda s: s.strip())
    )
    @settings(max_examples=10, deadline=None)  # deadline=None: moto context setup is I/O-bound
    def test_delete_nonexistent_always_raises_not_found(self, task_id: str):
        """Any task_id not in the table must raise NotFoundError on delete."""
        with mock_aws():
            import common.repository as repo_mod
            repo_mod._state["dynamodb"] = None
            repo_mod._state["table"] = None

            dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
            dynamodb.create_table(
                TableName=TABLE_NAME,
                KeySchema=[{"AttributeName": "task_id", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "task_id", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )

            from common.repository import TaskRepository
            with pytest.raises(TaskRepository.NotFoundError):
                TaskRepository.delete(task_id)

            repo_mod._state["dynamodb"] = None
            repo_mod._state["table"] = None
