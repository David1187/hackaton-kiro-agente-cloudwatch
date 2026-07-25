"""
tests/test_handlers.py — Unit tests for CRUD Lambda handlers (comportamiento DEFECTUOSO).

⚠️  Estos tests verifican que los BUGS SEMBRADOS (SE-1 a SE-19) están presentes
y se disparan correctamente. Son el contrato de la demo del hackathon:
si algún test falla, significa que un bug fue "arreglado" accidentalmente.

Organización:
  - TestCreateHandler: SE-1, SE-2, SE-8 (transformado), SE-10, SE-11
  - TestGetHandler: SE-3, SE-9 (reducido), SE-12, SE-13
  - TestListHandler: SE-4, SE-14, SE-15
  - TestUpdateHandler: SE-5, SE-6, SE-16, SE-17
  - TestDeleteHandler: SE-7 (enmascarado), SE-18, SE-19
  - TestCrossCutting: verificaciones transversales
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from unittest.mock import patch, MagicMock

import boto3
import botocore.exceptions
import pytest
from moto import mock_aws

# ---------------------------------------------------------------------------
# Path setup
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

from handlers import create_task, delete_task, get_task, list_tasks, update_task  # noqa: E402
from common.repository import _state  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(body=None, path_params=None, query_params=None):
    """Build a minimal Lambda proxy event."""
    event = {
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path_params or {},
    }
    if query_params is not None:
        event["queryStringParameters"] = query_params
    return event


def _parse_body(response):
    """Parse the JSON body of a handler response."""
    return json.loads(response["body"])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_repo_state():
    """Reset the module-level DynamoDB state before each test."""
    _state["dynamodb"] = None
    _state["table"] = None
    list_tasks._dynamodb = None
    list_tasks._table = None
    delete_task._dynamodb = None
    delete_task._table = None
    get_task._dynamodb = None
    get_task._table = None
    yield
    _state["dynamodb"] = None
    _state["table"] = None
    list_tasks._dynamodb = None
    list_tasks._table = None
    delete_task._dynamodb = None
    delete_task._table = None
    get_task._dynamodb = None
    get_task._table = None


@pytest.fixture
def ddb_table():
    """Spin up a moto DynamoDB table and yield its boto3 Table resource."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="eu-west-1")
        table = ddb.create_table(
            TableName="Tasks",
            KeySchema=[{"AttributeName": "task_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "task_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        yield table


def _seed_task(ddb_table, task_id=None, title="Seed task"):
    """Insert a task directly into the moto table and return its task_id."""
    tid = task_id or str(uuid.uuid4())
    ddb_table.put_item(Item={
        "task_id": tid,
        "title": title,
        "completed": False,
        "created_at": "2026-01-01T00:00:00.000+00:00",
        "updated_at": "2026-01-01T00:00:00.000+00:00",
    })
    return tid



# ===========================================================================
# SE-1, SE-2, SE-8, SE-10, SE-11 — create_task handler
# ===========================================================================

class TestCreateHandler:
    """Tests that verify bugs SE-1, SE-2, SE-8, SE-10, SE-11 in create_task."""

    # --- SE-1: accepts blank/empty title without rejecting ---

    def test_se1_accepts_whitespace_title_returns_201(self, ddb_table):
        """SE-1: whitespace-only title is accepted (should be 400 INVALID_TITLE)."""
        event = _make_event(body={"title": "   "})
        resp = create_task.handler(event, None)
        assert resp["statusCode"] == 201
        body = _parse_body(resp)
        assert body["title"] == "   "

    def test_se1_accepts_empty_title_returns_201(self, ddb_table):
        """SE-1: empty string title is accepted (should be 400 INVALID_TITLE)."""
        event = _make_event(body={"title": ""})
        resp = create_task.handler(event, None)
        assert resp["statusCode"] == 201
        body = _parse_body(resp)
        assert body["title"] == ""

    def test_se1_valid_title_also_works(self, ddb_table):
        """SE-1: valid titles still produce 201 (basic functionality preserved)."""
        event = _make_event(body={"title": "Buy groceries"})
        resp = create_task.handler(event, None)
        assert resp["statusCode"] == 201

    # --- SE-2: hardcoded timestamp ---

    def test_se2_timestamp_is_hardcoded_epoch(self, ddb_table):
        """SE-2: created_at and updated_at are always 1970-01-01T00:00:00.000+00:00."""
        event = _make_event(body={"title": "Any task"})
        resp = create_task.handler(event, None)
        assert resp["statusCode"] == 201
        body = _parse_body(resp)
        assert body["created_at"] == "1970-01-01T00:00:00.000+00:00"
        assert body["updated_at"] == "1970-01-01T00:00:00.000+00:00"

    # --- SE-8 TRANSFORMED: ValidationError propagates (502), others caught ---

    def test_se8_validation_error_propagates_on_invalid_json(self, ddb_table):
        """SE-8: ValidationError from parse_json_body propagates uncaught (502)."""
        event = {"body": "not json", "pathParameters": {}}
        from common.validation import ValidationError
        with pytest.raises(ValidationError):
            create_task.handler(event, None)

    def test_se8_validation_error_propagates_on_missing_body(self, ddb_table):
        """SE-8: ValidationError from missing body propagates uncaught."""
        event = {"body": None, "pathParameters": {}}
        from common.validation import ValidationError
        with pytest.raises(ValidationError):
            create_task.handler(event, None)

    def test_se8_validation_error_propagates_on_missing_title(self, ddb_table):
        """SE-8: ValidationError from missing 'title' propagates uncaught."""
        event = _make_event(body={"completed": False})
        from common.validation import ValidationError
        with pytest.raises(ValidationError):
            create_task.handler(event, None)

    def test_se8_client_error_now_caught_returns_500(self, ddb_table, caplog):
        """SE-8 TRANSFORMED: ClientError is now caught and returns 500 (not propagated)."""
        mock_create = MagicMock(
            side_effect=botocore.exceptions.ClientError(
                {"Error": {"Code": "InternalServerError", "Message": "fail"}},
                "PutItem",
            )
        )
        with patch("common.repository.TaskRepository.create", mock_create):
            event = _make_event(body={"title": "Valid title"})
            with caplog.at_level(logging.ERROR):
                resp = create_task.handler(event, None)
        assert resp["statusCode"] == 500
        assert _parse_body(resp)["error"]["code"] == "DDB_ERROR"
        error_records = [r for r in caplog.records if "ERROR:" in r.getMessage()]
        assert len(error_records) >= 1

    # --- SE-10: priority float not converted to Decimal → TypeError ---

    def test_se10_priority_float_causes_500(self, ddb_table, caplog):
        """SE-10: priority=3.5 (float) causes TypeError → 500."""
        event = _make_event(body={"title": "Task", "priority": 3.5})
        with caplog.at_level(logging.ERROR):
            resp = create_task.handler(event, None)
        assert resp["statusCode"] == 500
        assert _parse_body(resp)["error"]["code"] == "INTERNAL_ERROR"
        error_records = [r for r in caplog.records if "ERROR:" in r.getMessage()]
        assert len(error_records) >= 1

    def test_se10_priority_int_works(self, ddb_table):
        """SE-10: priority as int (no decimal point) works because DynamoDB accepts int."""
        event = _make_event(body={"title": "Task", "priority": 3})
        resp = create_task.handler(event, None)
        # int is fine in DynamoDB (stored as N), only float fails
        assert resp["statusCode"] == 201

    # --- SE-11: task_id from body without validation → type mismatch ---

    def test_se11_numeric_task_id_causes_500(self, ddb_table, caplog):
        """SE-11: task_id=123 (int) from body → ClientError type mismatch → 500."""
        # moto may not enforce type mismatch on PK, so we mock the ClientError
        # NOTE: moto does NOT validate PK type (it accepts int as PK value),
        # so we must mock the error to match real DynamoDB behavior.
        mock_create = MagicMock(
            side_effect=botocore.exceptions.ClientError(
                {"Error": {"Code": "SerializationException", "Message": "NUMBER value cannot be used as PK"}},
                "PutItem",
            )
        )
        with patch("common.repository.TaskRepository.create", mock_create):
            event = _make_event(body={"title": "Task", "task_id": 123})
            with caplog.at_level(logging.ERROR):
                resp = create_task.handler(event, None)
        assert resp["statusCode"] == 500
        assert _parse_body(resp)["error"]["code"] == "DDB_ERROR"
        error_records = [r for r in caplog.records if "ERROR:" in r.getMessage()]
        assert len(error_records) >= 1

    def test_se11_string_task_id_from_body_used(self, ddb_table):
        """SE-11: string task_id from body is used instead of uuid4."""
        custom_id = "custom-id-from-body"
        event = _make_event(body={"title": "Task", "task_id": custom_id})
        resp = create_task.handler(event, None)
        assert resp["statusCode"] == 201
        body = _parse_body(resp)
        assert body["task_id"] == custom_id

    # --- Basic functionality ---

    def test_create_produces_uuid_task_id(self, ddb_table):
        """Basic: task_id is a valid UUID v4 when not provided in body."""
        event = _make_event(body={"title": "UUID test"})
        resp = create_task.handler(event, None)
        body = _parse_body(resp)
        parsed = uuid.UUID(body["task_id"])
        assert parsed.version == 4

    def test_create_completed_defaults_false(self, ddb_table):
        """Basic: completed defaults to False when not provided."""
        event = _make_event(body={"title": "Default completed"})
        resp = create_task.handler(event, None)
        assert _parse_body(resp)["completed"] is False



# ===========================================================================
# SE-3, SE-9, SE-12, SE-13 — get_task handler
# ===========================================================================

class TestGetHandler:
    """Tests that verify bugs SE-3, SE-9, SE-12, SE-13 in get_task."""

    # --- SE-3: KeyError when pathParameters missing ---

    def test_se3_no_path_parameters_returns_500(self, ddb_table, caplog):
        """SE-3: event without pathParameters key triggers KeyError → 500."""
        event = {"httpMethod": "GET", "path": "/tasks"}
        with caplog.at_level(logging.ERROR):
            resp = get_task.handler(event, None)
        assert resp["statusCode"] == 500
        body = _parse_body(resp)
        assert body["error"]["code"] == "INTERNAL_ERROR"
        # SE-9 REDUCED: except Exception now uses logger.error → ERROR: present
        error_records = [r for r in caplog.records if "ERROR:" in r.getMessage()]
        assert len(error_records) >= 1

    def test_se3_empty_path_parameters_returns_500(self, ddb_table, caplog):
        """SE-3: event with pathParameters={} (no task_id key) → KeyError → 500."""
        event = {"pathParameters": {}}
        with caplog.at_level(logging.ERROR):
            resp = get_task.handler(event, None)
        assert resp["statusCode"] == 500
        error_records = [r for r in caplog.records if "ERROR:" in r.getMessage()]
        assert len(error_records) >= 1

    def test_se3_none_path_parameters_returns_500(self, ddb_table, caplog):
        """SE-3: pathParameters=None → TypeError/KeyError → 500."""
        event = {"pathParameters": None}
        with caplog.at_level(logging.ERROR):
            resp = get_task.handler(event, None)
        assert resp["statusCode"] == 500

    # --- SE-9 REDUCED: print() only in NotFoundError ---

    def test_se9_not_found_uses_print_not_logging(self, ddb_table, caplog, capsys):
        """SE-9: 404 not-found uses print() — no ERROR: in log records for 404."""
        # SE-13 fires first (KeyError on completed_at) so we never reach 404.
        # We must mock TaskRepository.get to raise NotFoundError AND mock that
        # the code path doesn't hit task["completed_at"].
        # Actually, since SE-13 fires AFTER get succeeds, if the task doesn't exist
        # NotFoundError fires BEFORE SE-13. Let's test that path.
        fake_id = "ghost-" + str(uuid.uuid4())
        event = _make_event(path_params={"task_id": fake_id})

        with caplog.at_level(logging.DEBUG):
            resp = get_task.handler(event, None)

        assert resp["statusCode"] == 404

        # No ERROR-level log records from the get_task handler for 404
        handler_error_records = [
            r for r in caplog.records
            if "get_task" in r.name and r.levelno == logging.ERROR
        ]
        assert len(handler_error_records) == 0

        # print() output appears in capsys
        captured = capsys.readouterr()
        assert "not found" in captured.out.lower() or fake_id in captured.out

    # --- SE-12: fields query param as ProjectionExpression → ClientError ---

    def test_se12_fields_reserved_word_causes_500(self, ddb_table, caplog):
        """SE-12: ?fields=status → ClientError (reserved word) → 500."""
        tid = _seed_task(ddb_table)
        event = _make_event(path_params={"task_id": tid}, query_params={"fields": "status"})
        # NOTE: moto may NOT enforce reserved word validation for ProjectionExpression.
        # Testing empirically — if moto doesn't raise, we mock it.
        with caplog.at_level(logging.ERROR):
            resp = get_task.handler(event, None)

        if resp["statusCode"] == 500:
            # moto raised the error (or SE-13 fired first)
            body = _parse_body(resp)
            assert body["error"]["code"] in ("DDB_ERROR", "INTERNAL_ERROR")
            error_records = [r for r in caplog.records if "ERROR:" in r.getMessage()]
            assert len(error_records) >= 1
        else:
            # moto did NOT enforce reserved word — test with mock instead
            pytest.skip("moto does not validate reserved words in ProjectionExpression")

    def test_se12_fields_reserved_word_mocked(self, ddb_table, caplog):
        """SE-12: mocked ClientError for reserved word in ProjectionExpression."""
        # moto does not validate reserved words, so we mock the ClientError
        # to confirm the handler's error path works correctly.
        tid = _seed_task(ddb_table)
        event = _make_event(path_params={"task_id": tid}, query_params={"fields": "status"})

        mock_table = MagicMock()
        mock_table.get_item.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "ValidationException",
                       "Message": "Invalid ProjectionExpression: Attribute name is a reserved keyword; status"}},
            "GetItem",
        )
        with patch.object(get_task, "_get_table", return_value=mock_table):
            with caplog.at_level(logging.ERROR):
                resp = get_task.handler(event, None)

        assert resp["statusCode"] == 500
        assert _parse_body(resp)["error"]["code"] == "DDB_ERROR"
        error_records = [r for r in caplog.records if "ERROR:" in r.getMessage()]
        assert len(error_records) >= 1

    # --- SE-13: task['completed_at'] KeyError ALWAYS ---

    def test_se13_existing_task_returns_500_always(self, ddb_table, caplog):
        """SE-13: ANY successful get hits task['completed_at'] → KeyError → 500."""
        tid = _seed_task(ddb_table, title="Readable task")
        event = _make_event(path_params={"task_id": tid})
        with caplog.at_level(logging.ERROR):
            resp = get_task.handler(event, None)
        # Should be 200 but SE-13 makes it 500
        assert resp["statusCode"] == 500
        body = _parse_body(resp)
        assert body["error"]["code"] == "INTERNAL_ERROR"
        error_records = [r for r in caplog.records if "ERROR:" in r.getMessage()]
        assert len(error_records) >= 1

    def test_se13_nonexistent_returns_404_not_500(self, ddb_table):
        """SE-13: non-existent task returns 404 (NotFoundError fires BEFORE SE-13)."""
        event = _make_event(path_params={"task_id": str(uuid.uuid4())})
        resp = get_task.handler(event, None)
        # NotFoundError is raised before we try task["completed_at"]
        assert resp["statusCode"] == 404
        body = _parse_body(resp)
        assert body["error"]["code"] == "RESOURCE_NOT_FOUND"



# ===========================================================================
# SE-4, SE-14, SE-15 — list_tasks handler
# ===========================================================================

class TestListHandler:
    """Tests that verify bugs SE-4, SE-14, SE-15 in list_tasks."""

    # --- SE-4: scan without Limit ---

    def test_se4_scan_called_without_limit(self, ddb_table):
        """SE-4: verify the scan call omits the Limit parameter when no query params."""
        mock_table = MagicMock()
        mock_table.scan.return_value = {"Items": []}

        with patch.object(list_tasks, "_get_table", return_value=mock_table):
            list_tasks.handler(_make_event(), None)

        mock_table.scan.assert_called_once_with()

    def test_se4_returns_all_items(self, ddb_table):
        """SE-4: scan without Limit returns all items in the table."""
        for i in range(10):
            _seed_task(ddb_table, title=f"Task {i}")

        resp = list_tasks.handler(_make_event(), None)
        assert resp["statusCode"] == 200
        body = _parse_body(resp)
        assert body["count"] == 10
        assert len(body["tasks"]) == 10

    def test_se4_empty_table_returns_empty_list(self, ddb_table):
        """Basic: empty table → 200 with tasks=[], count=0."""
        resp = list_tasks.handler(_make_event(), None)
        assert resp["statusCode"] == 200
        body = _parse_body(resp)
        assert body["tasks"] == []
        assert body["count"] == 0

    # --- SE-14: limit as string not cast to int ---

    def test_se14_limit_string_causes_500(self, ddb_table, caplog):
        """SE-14: limit='10' (string) → ParamValidationError → 500."""
        event = _make_event(query_params={"limit": "10"})
        with caplog.at_level(logging.ERROR):
            resp = list_tasks.handler(event, None)
        assert resp["statusCode"] == 500
        body = _parse_body(resp)
        assert body["error"]["code"] == "DDB_PARAM_ERROR"
        error_records = [r for r in caplog.records if "ERROR:" in r.getMessage()]
        assert len(error_records) >= 1

    def test_se14_any_limit_value_fails(self, ddb_table, caplog):
        """SE-14: ANY limit value (always string from query params) fails."""
        event = _make_event(query_params={"limit": "1"})
        with caplog.at_level(logging.ERROR):
            resp = list_tasks.handler(event, None)
        assert resp["statusCode"] == 500

    # --- SE-15: next as string not decoded to dict ---

    def test_se15_next_string_causes_500(self, ddb_table, caplog):
        """SE-15: next='abc' (string) → ParamValidationError → 500."""
        event = _make_event(query_params={"next": "some_pagination_token"})
        with caplog.at_level(logging.ERROR):
            resp = list_tasks.handler(event, None)
        assert resp["statusCode"] == 500
        body = _parse_body(resp)
        assert body["error"]["code"] == "DDB_PARAM_ERROR"
        error_records = [r for r in caplog.records if "ERROR:" in r.getMessage()]
        assert len(error_records) >= 1

    # --- Error handling preserved ---

    def test_client_error_returns_500_with_error_log(self, ddb_table, caplog):
        """Error handling: ClientError still logged with ERROR: prefix."""
        mock_table = MagicMock()
        mock_table.scan.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "x"}}, "Scan"
        )
        with patch.object(list_tasks, "_get_table", return_value=mock_table):
            with caplog.at_level(logging.ERROR):
                resp = list_tasks.handler(_make_event(), None)
        assert resp["statusCode"] == 500
        error_records = [r for r in caplog.records if "ERROR:" in r.getMessage()]
        assert len(error_records) >= 1



# ===========================================================================
# SE-5, SE-6, SE-16, SE-17 — update_task handler
# ===========================================================================

class TestUpdateHandler:
    """Tests that verify bugs SE-5, SE-6, SE-16, SE-17 in update_task."""

    # --- SE-5: accepts empty body {} → DynamoDB error ---

    def test_se5_empty_body_causes_500(self, ddb_table):
        """SE-5: empty body {} reaches DynamoDB → error (should be 400)."""
        tid = _seed_task(ddb_table)
        event = _make_event(body={}, path_params={"task_id": tid})
        resp = update_task.handler(event, None)
        assert resp["statusCode"] == 500

    def test_se5_empty_body_logged_with_error_prefix(self, ddb_table, caplog):
        """SE-5: the DynamoDB error is logged with ERROR: prefix."""
        tid = _seed_task(ddb_table)
        event = _make_event(body={}, path_params={"task_id": tid})
        with caplog.at_level(logging.ERROR):
            update_task.handler(event, None)
        error_records = [r for r in caplog.records if "ERROR:" in r.getMessage()]
        assert len(error_records) >= 1

    # --- SE-6: updated_at never refreshed ---

    def test_se6_updated_at_unchanged_after_update(self, ddb_table):
        """SE-6: updated_at remains frozen at the original value after update."""
        tid = _seed_task(ddb_table)
        original_updated_at = "2026-01-01T00:00:00.000+00:00"

        event = _make_event(body={"title": "New title"}, path_params={"task_id": tid})
        resp = update_task.handler(event, None)
        assert resp["statusCode"] == 200
        body = _parse_body(resp)
        assert body["updated_at"] == original_updated_at

    def test_se6_title_is_updated_correctly(self, ddb_table):
        """SE-6: title is updated — only updated_at is the bug."""
        tid = _seed_task(ddb_table, title="Original")
        event = _make_event(body={"title": "Updated"}, path_params={"task_id": tid})
        resp = update_task.handler(event, None)
        assert resp["statusCode"] == 200
        body = _parse_body(resp)
        assert body["title"] == "Updated"

    # --- SE-16: validate_title called instead of validate_completed ---

    def test_se16_completed_bool_causes_400(self, ddb_table, caplog):
        """SE-16: {"completed":true} → validate_title(True) → ValidationError → 400."""
        tid = _seed_task(ddb_table)
        event = _make_event(body={"completed": True}, path_params={"task_id": tid})
        with caplog.at_level(logging.ERROR):
            resp = update_task.handler(event, None)
        assert resp["statusCode"] == 400
        body = _parse_body(resp)
        assert body["error"]["code"] == "INVALID_TYPE"
        # The error message says 'title' because validate_title was called
        assert "title" in body["error"]["message"].lower() or "cadena" in body["error"]["message"].lower()
        # Logger emits ERROR: for ValidationError
        error_records = [r for r in caplog.records if "ERROR:" in r.getMessage()]
        assert len(error_records) >= 1

    def test_se16_completed_can_never_be_updated(self, ddb_table):
        """SE-16: completed field can never be updated — always fails."""
        tid = _seed_task(ddb_table)
        event = _make_event(body={"completed": False}, path_params={"task_id": tid})
        resp = update_task.handler(event, None)
        # Even False (bool) fails because validate_title expects string
        assert resp["statusCode"] == 400

    # --- SE-17: priority float not converted to Decimal ---

    def test_se17_priority_float_causes_500(self, ddb_table, caplog):
        """SE-17: priority=2.5 (float) in update → TypeError → 500."""
        tid = _seed_task(ddb_table)
        event = _make_event(body={"title": "ok", "priority": 2.5}, path_params={"task_id": tid})
        with caplog.at_level(logging.ERROR):
            resp = update_task.handler(event, None)
        assert resp["statusCode"] == 500
        body = _parse_body(resp)
        assert body["error"]["code"] == "INTERNAL_ERROR"
        error_records = [r for r in caplog.records if "ERROR:" in r.getMessage()]
        assert len(error_records) >= 1

    def test_se17_priority_int_works(self, ddb_table):
        """SE-17: priority as int works (DynamoDB accepts int natively)."""
        tid = _seed_task(ddb_table)
        event = _make_event(body={"title": "ok", "priority": 3}, path_params={"task_id": tid})
        resp = update_task.handler(event, None)
        assert resp["statusCode"] == 200

    # --- Basic functionality preserved ---

    def test_update_nonexistent_returns_404(self, ddb_table):
        """Basic: updating non-existent task → 404."""
        event = _make_event(body={"title": "Ghost"}, path_params={"task_id": str(uuid.uuid4())})
        resp = update_task.handler(event, None)
        assert resp["statusCode"] == 404

    def test_update_missing_task_id_returns_400(self, ddb_table):
        """Basic: missing task_id → 400 INVALID_TASK_ID."""
        event = _make_event(body={"title": "No ID"}, path_params={})
        resp = update_task.handler(event, None)
        assert resp["statusCode"] == 400
        assert _parse_body(resp)["error"]["code"] == "INVALID_TASK_ID"

    def test_update_invalid_json_returns_400(self, ddb_table):
        """Basic: invalid JSON body → 400 INVALID_JSON (ValidationError caught)."""
        tid = _seed_task(ddb_table)
        event = {"body": "not json", "pathParameters": {"task_id": tid}}
        resp = update_task.handler(event, None)
        assert resp["statusCode"] == 400
        assert _parse_body(resp)["error"]["code"] == "INVALID_JSON"



# ===========================================================================
# SE-7 (enmascarado), SE-18, SE-19 — delete_task handler
# ===========================================================================

class TestDeleteHandler:
    """Tests that verify bugs SE-7 (masked), SE-18, SE-19 in delete_task."""

    # --- SE-18: wrong PK name 'id' instead of 'task_id' ---

    def test_se18_non_numeric_task_id_returns_500(self, ddb_table, caplog):
        """SE-18: non-numeric task_id → Key={'id': ...} → ClientError → 500."""
        tid = _seed_task(ddb_table)
        event = _make_event(path_params={"task_id": tid})
        with caplog.at_level(logging.ERROR):
            resp = delete_task.handler(event, None)
        assert resp["statusCode"] == 500
        body = _parse_body(resp)
        assert body["error"]["code"] == "DDB_ERROR"
        error_records = [r for r in caplog.records if "ERROR:" in r.getMessage()]
        assert len(error_records) >= 1

    def test_se18_uuid_task_id_always_fails(self, ddb_table, caplog):
        """SE-18: any UUID (non-numeric) task_id always fails with 500."""
        fake_id = "does-not-exist-" + str(uuid.uuid4())
        event = _make_event(path_params={"task_id": fake_id})
        with caplog.at_level(logging.ERROR):
            resp = delete_task.handler(event, None)
        assert resp["statusCode"] == 500

    # --- SE-19: numeric task_id converted to int ---

    def test_se19_numeric_task_id_returns_500(self, ddb_table, caplog):
        """SE-19: task_id='12345' (all digits) → int(task_id) → ClientError type mismatch → 500."""
        # moto may not enforce type mismatch on Key, so we mock if needed
        event = _make_event(path_params={"task_id": "12345"})
        with caplog.at_level(logging.ERROR):
            resp = delete_task.handler(event, None)

        if resp["statusCode"] == 500:
            body = _parse_body(resp)
            assert body["error"]["code"] == "DDB_ERROR"
            error_records = [r for r in caplog.records if "ERROR:" in r.getMessage()]
            assert len(error_records) >= 1
        else:
            pytest.skip("moto does not enforce PK type validation for delete_item")

    def test_se19_numeric_task_id_mocked(self, ddb_table, caplog):
        """SE-19: mocked ClientError for numeric PK type mismatch."""
        # moto may not validate PK type, so we confirm handler behavior with mock
        event = _make_event(path_params={"task_id": "99999"})
        mock_table = MagicMock()
        mock_table.delete_item.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "SerializationException",
                       "Message": "Type mismatch for key task_id"}},
            "DeleteItem",
        )
        with patch.object(delete_task, "_get_table", return_value=mock_table):
            with caplog.at_level(logging.ERROR):
                resp = delete_task.handler(event, None)
        assert resp["statusCode"] == 500
        assert _parse_body(resp)["error"]["code"] == "DDB_ERROR"
        error_records = [r for r in caplog.records if "ERROR:" in r.getMessage()]
        assert len(error_records) >= 1

    # --- SE-7 MASKED: code exists but never reached ---

    def test_se7_masked_by_se18(self, ddb_table, caplog):
        """SE-7 MASKED: the original delete_item(Key={'task_id':...}) without
        ConditionExpression is never reached because SE-18 fires first."""
        fake_id = "nonexistent-task"
        event = _make_event(path_params={"task_id": fake_id})
        with caplog.at_level(logging.ERROR):
            resp = delete_task.handler(event, None)
        # SE-18 fires → ClientError → 500 (not 200 as SE-7 would produce)
        assert resp["statusCode"] == 500
        # If SE-7 were active (no SE-18), this would be 200

    # --- Basic validation still works ---

    def test_delete_missing_task_id_returns_400(self, ddb_table):
        """Basic: missing task_id → 400 INVALID_TASK_ID."""
        event = _make_event(path_params={})
        resp = delete_task.handler(event, None)
        assert resp["statusCode"] == 400
        assert _parse_body(resp)["error"]["code"] == "INVALID_TASK_ID"

    def test_delete_empty_task_id_returns_400(self, ddb_table):
        """Basic: whitespace task_id → 400 INVALID_TASK_ID."""
        event = _make_event(path_params={"task_id": "   "})
        resp = delete_task.handler(event, None)
        assert resp["statusCode"] == 400


# ===========================================================================
# Cross-cutting tests
# ===========================================================================

class TestCrossCutting:
    """Verify basic structural requirements across all handlers."""

    def test_create_success_has_json_content_type(self, ddb_table):
        """Create successful response has Content-Type: application/json."""
        resp = create_task.handler(_make_event(body={"title": "CT"}), None)
        assert resp["headers"]["Content-Type"] == "application/json"

    def test_list_success_has_json_content_type(self, ddb_table):
        """List successful response has Content-Type: application/json."""
        resp = list_tasks.handler(_make_event(), None)
        assert resp["headers"]["Content-Type"] == "application/json"

    def test_update_success_has_json_content_type(self, ddb_table):
        """Update successful response has Content-Type: application/json."""
        tid = _seed_task(ddb_table)
        resp = update_task.handler(
            _make_event(body={"title": "New"}, path_params={"task_id": tid}), None
        )
        assert resp["headers"]["Content-Type"] == "application/json"

    def test_error_responses_have_json_content_type(self, ddb_table):
        """All error responses set Content-Type: application/json."""
        # get_task → 500 (SE-13) or 404
        resp = get_task.handler(_make_event(path_params={"task_id": "nope"}), None)
        assert resp["headers"]["Content-Type"] == "application/json"

        # update_task with bad task_id → 400
        resp = update_task.handler(_make_event(body={"title": "X"}, path_params={}), None)
        assert resp["headers"]["Content-Type"] == "application/json"

    def test_create_persists_to_dynamodb(self, ddb_table):
        """End-to-end: create works (get will fail due to SE-13 but item is stored)."""
        create_resp = create_task.handler(_make_event(body={"title": "Roundtrip"}), None)
        assert create_resp["statusCode"] == 201
        task_id = _parse_body(create_resp)["task_id"]

        # Verify directly in DynamoDB (can't use get handler due to SE-13)
        item = ddb_table.get_item(Key={"task_id": task_id})
        assert "Item" in item
        assert item["Item"]["title"] == "Roundtrip"
