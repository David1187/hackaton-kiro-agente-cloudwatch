"""
tests/test_seeded.py — Unit tests for seeded (intentionally buggy) handlers.

For each SE-1 … SE-9:
  • Runs the handler with the documented triggering input.
  • Verifies the expected exception type or observable behaviour.
  • Checks detectability (or lack thereof) via the ERROR: log prefix.
  • Confirms isolation: the correct handlers in handlers/ are unaffected.

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 11.1-11.9
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid

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

# Correct handlers (must remain unaffected)
from handlers import create_task as correct_create
from handlers import get_task as correct_get

# Seeded handlers
from seeded import se1_create_no_title_validation as se1
from seeded import se2_create_hardcoded_timestamp as se2
from seeded import se3_get_missing_task_id as se3
from seeded import se4_list_no_limit as se4
from seeded import se5_update_no_field_validation as se5
from seeded import se6_update_no_updated_at as se6
from seeded import se7_delete_no_condition as se7
from seeded import se8_no_try_except as se8
from seeded import se9_print_instead_of_logging as se9

from common.repository import _state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(body=None, path_params=None):
    return {
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path_params or {},
    }


def _parse_body(response):
    return json.loads(response["body"])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_repo_state():
    """Reset module-level DynamoDB state between tests."""
    _state["dynamodb"] = None
    _state["table"] = None
    # Also reset se4 and se7 module-level state
    se4._dynamodb = None
    se4._table = None
    se7._dynamodb = None
    se7._table = None
    yield
    _state["dynamodb"] = None
    _state["table"] = None
    se4._dynamodb = None
    se4._table = None
    se7._dynamodb = None
    se7._table = None


@pytest.fixture
def ddb_table():
    """Moto DynamoDB table, yielded inside mock_aws context."""
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


def _seed_task(ddb_table, task_id: str = None, title: str = "Seed task") -> str:
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
# SE-1 — Create sin validación de title
# ===========================================================================

class TestSE1CreateNoTitleValidation:

    def test_se1_accepts_blank_title(self, ddb_table):
        """SE-1: handler accepts a whitespace-only title and returns 201."""
        event = _make_event(body={"title": "   "})
        resp = se1.handler(event, None)
        # Bug: responds 201 instead of 400
        assert resp["statusCode"] == 201
        body = _parse_body(resp)
        # The raw (un-stripped) whitespace is stored
        assert body["title"] == "   "

    def test_se1_accepts_empty_title(self, ddb_table):
        """SE-1: handler accepts an empty string title and returns 201."""
        event = _make_event(body={"title": ""})
        resp = se1.handler(event, None)
        assert resp["statusCode"] == 201
        body = _parse_body(resp)
        assert body["title"] == ""

    def test_se1_isolation_correct_handler_rejects_blank_title(self, ddb_table):
        """Isolation: the correct create handler still rejects blank titles with 400."""
        event = _make_event(body={"title": "   "})
        resp = correct_create.handler(event, None)
        assert resp["statusCode"] == 400
        body = _parse_body(resp)
        assert body["error"]["code"] == "INVALID_TITLE"


# ===========================================================================
# SE-2 — Create con timestamp hardcoded
# ===========================================================================

class TestSE2HardcodedTimestamp:

    HARDCODED = "1970-01-01T00:00:00.000+00:00"

    def test_se2_timestamp_is_hardcoded(self, ddb_table):
        """SE-2: created_at and updated_at are always the epoch constant."""
        event = _make_event(body={"title": "Any task"})
        resp = se2.handler(event, None)
        assert resp["statusCode"] == 201
        body = _parse_body(resp)
        assert body["created_at"] == self.HARDCODED
        assert body["updated_at"] == self.HARDCODED

    def test_se2_two_calls_same_timestamp(self, ddb_table):
        """SE-2: two separate creates always produce the same timestamp."""
        e1 = _make_event(body={"title": "Task A"})
        e2 = _make_event(body={"title": "Task B"})
        r1 = se2.handler(e1, None)
        r2 = se2.handler(e2, None)
        b1 = _parse_body(r1)
        b2 = _parse_body(r2)
        assert b1["created_at"] == b2["created_at"] == self.HARDCODED

    def test_se2_isolation_correct_handler_uses_real_timestamp(self, ddb_table):
        """Isolation: the correct create handler uses a dynamic timestamp."""
        event = _make_event(body={"title": "Real task"})
        resp = correct_create.handler(event, None)
        body = _parse_body(resp)
        assert body["created_at"] != self.HARDCODED
        # Verify it is a valid ISO 8601 date (not the epoch constant)
        from datetime import datetime
        ts = body["created_at"].replace("+00:00", "+00:00")
        parsed = datetime.fromisoformat(ts)
        assert parsed.year >= 2024



# ===========================================================================
# SE-3 — Get sin manejo de task_id ausente (KeyError)
# ===========================================================================

class TestSE3MissingTaskId:

    def test_se3_no_path_parameters_returns_500(self, ddb_table):
        """SE-3: event without pathParameters triggers KeyError → 500."""
        event = {"httpMethod": "GET", "path": "/tasks"}
        resp = se3.handler(event, None)
        assert resp["statusCode"] == 500
        body = _parse_body(resp)
        assert body["error"]["code"] == "INTERNAL_ERROR"

    def test_se3_empty_path_parameters_returns_500(self, ddb_table):
        """SE-3: event with empty pathParameters dict triggers KeyError → 500."""
        event = {"pathParameters": {}}
        resp = se3.handler(event, None)
        assert resp["statusCode"] == 500

    def test_se3_error_logged_with_prefix(self, ddb_table, caplog):
        """SE-3: the KeyError is logged with the ERROR: prefix."""
        event = {"httpMethod": "GET", "path": "/tasks"}
        with caplog.at_level(logging.ERROR):
            se3.handler(event, None)
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("ERROR:" in r.getMessage() for r in error_records), (
            "Expected at least one log record starting with ERROR: but found: "
            + str([r.getMessage() for r in error_records])
        )

    def test_se3_isolation_correct_handler_returns_400(self, ddb_table):
        """Isolation: the correct get handler returns 400 for missing task_id."""
        event = {"httpMethod": "GET", "path": "/tasks"}
        resp = correct_get.handler(event, None)
        assert resp["statusCode"] == 400
        body = _parse_body(resp)
        assert body["error"]["code"] == "INVALID_TASK_ID"


# ===========================================================================
# SE-4 — List sin límite en el scan
# ===========================================================================

class TestSE4ListNoLimit:

    def test_se4_returns_all_items_without_limit(self, ddb_table):
        """SE-4: scan has no Limit — returns all items from the table."""
        # Seed 5 tasks
        for i in range(5):
            _seed_task(ddb_table, title=f"Task {i}")

        event = _make_event()
        resp = se4.handler(event, None)
        assert resp["statusCode"] == 200
        body = _parse_body(resp)
        # All 5 items returned (no limit enforced)
        assert body["count"] == 5

    def test_se4_scan_called_without_limit_param(self, ddb_table):
        """SE-4: verify the scan call omits the Limit parameter."""
        from unittest.mock import patch, MagicMock

        mock_table = MagicMock()
        mock_table.scan.return_value = {"Items": []}

        with patch.object(se4, "_get_table", return_value=mock_table):
            se4.handler(_make_event(), None)

        mock_table.scan.assert_called_once()
        call_kwargs = mock_table.scan.call_args
        # Limit must NOT be in the call arguments
        assert "Limit" not in (call_kwargs.kwargs if call_kwargs.kwargs else {}), (
            "SE-4 bug: Limit was passed to scan but should be absent"
        )

    def test_se4_isolation_correct_list_uses_limit(self, ddb_table):
        """Isolation: the correct list handler passes Limit=1000 to scan."""
        from handlers import list_tasks as correct_list
        from unittest.mock import patch, MagicMock

        mock_table = MagicMock()
        mock_table.scan.return_value = {"Items": []}

        with patch("common.repository._get_table", return_value=mock_table):
            correct_list.handler(_make_event(), None)

        mock_table.scan.assert_called_once_with(Limit=1000)


# ===========================================================================
# SE-5 — Update sin validación de campos presentes
# ===========================================================================

class TestSE5UpdateNoFieldValidation:

    def test_se5_empty_body_produces_error(self, ddb_table):
        """SE-5: empty body {} reaches DynamoDB with empty attrs → error."""
        tid = _seed_task(ddb_table)
        event = _make_event(body={}, path_params={"task_id": tid})
        resp = se5.handler(event, None)
        # Should be 400 by contract, but SE-5 lets it reach DynamoDB → 500
        assert resp["statusCode"] in (400, 500)
        body = _parse_body(resp)
        # The error is NOT the expected MISSING_FIELD 400
        if resp["statusCode"] == 400:
            # If it somehow returns 400, it must NOT be via the missing field guard
            # (which was removed in SE-5) — this case is theoretically unreachable
            # but we document the bug expectation:
            pytest.fail(
                "SE-5 should not return 400 MISSING_FIELD because the guard was removed"
            )
        # statusCode 500 expected: DynamoDB rejects the empty UpdateExpression
        assert resp["statusCode"] == 500

    def test_se5_empty_body_logged_with_error_prefix(self, ddb_table, caplog):
        """SE-5: the DynamoDB error from empty attrs is logged with ERROR: prefix."""
        tid = _seed_task(ddb_table)
        event = _make_event(body={}, path_params={"task_id": tid})
        with caplog.at_level(logging.ERROR):
            se5.handler(event, None)
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("ERROR:" in r.getMessage() for r in error_records), (
            "Expected ERROR: prefix in log records, got: "
            + str([r.getMessage() for r in error_records])
        )

    def test_se5_isolation_correct_update_rejects_empty_body(self, ddb_table):
        """Isolation: the correct update handler returns 400 MISSING_FIELD for {}."""
        from handlers import update_task as correct_update
        tid = _seed_task(ddb_table)
        event = _make_event(body={}, path_params={"task_id": tid})
        resp = correct_update.handler(event, None)
        assert resp["statusCode"] == 400
        body = _parse_body(resp)
        assert body["error"]["code"] == "MISSING_FIELD"



# ===========================================================================
# SE-6 — Update sin actualizar updated_at
# ===========================================================================

class TestSE6UpdateNoUpdatedAt:

    def test_se6_updated_at_unchanged_after_update(self, ddb_table):
        """SE-6: updated_at stays frozen at the original value after update."""
        tid = _seed_task(ddb_table)
        original_updated_at = "2026-01-01T00:00:00.000+00:00"

        event = _make_event(body={"title": "New title"}, path_params={"task_id": tid})
        resp = se6.handler(event, None)
        assert resp["statusCode"] == 200
        body = _parse_body(resp)
        # updated_at must still be the original seed value
        assert body["updated_at"] == original_updated_at, (
            f"SE-6 bug: updated_at should be frozen but got {body['updated_at']!r}"
        )

    def test_se6_title_does_change(self, ddb_table):
        """SE-6: title is updated correctly; only updated_at is the bug."""
        tid = _seed_task(ddb_table, title="Original")
        event = _make_event(body={"title": "Updated"}, path_params={"task_id": tid})
        resp = se6.handler(event, None)
        assert resp["statusCode"] == 200
        body = _parse_body(resp)
        assert body["title"] == "Updated"

    def test_se6_isolation_correct_update_refreshes_updated_at(self, ddb_table):
        """Isolation: the correct update handler always refreshes updated_at."""
        from handlers import update_task as correct_update
        import time

        tid = _seed_task(ddb_table)
        time.sleep(0.01)  # ensure time has advanced
        event = _make_event(body={"title": "New"}, path_params={"task_id": tid})
        resp = correct_update.handler(event, None)
        assert resp["statusCode"] == 200
        body = _parse_body(resp)
        # updated_at must differ from the seeded value
        assert body["updated_at"] != "2026-01-01T00:00:00.000+00:00"


# ===========================================================================
# SE-7 — Delete sin ConditionExpression
# ===========================================================================

class TestSE7DeleteNoCondition:

    def test_se7_deleting_nonexistent_task_returns_200(self, ddb_table):
        """SE-7: deleting a non-existent task_id silently succeeds (200)."""
        fake_id = "does-not-exist-" + str(uuid.uuid4())
        event = _make_event(path_params={"task_id": fake_id})
        resp = se7.handler(event, None)
        # Bug: should be 404 but returns 200
        assert resp["statusCode"] == 200
        body = _parse_body(resp)
        assert body["deleted"] is True
        assert body["task_id"] == fake_id

    def test_se7_deleting_existing_task_works(self, ddb_table):
        """SE-7: deleting an existing task still works correctly."""
        tid = _seed_task(ddb_table)
        event = _make_event(path_params={"task_id": tid})
        resp = se7.handler(event, None)
        assert resp["statusCode"] == 200

    def test_se7_isolation_correct_delete_returns_404(self, ddb_table):
        """Isolation: the correct delete handler returns 404 for non-existent tasks."""
        from handlers import delete_task as correct_delete
        fake_id = "does-not-exist-" + str(uuid.uuid4())
        event = _make_event(path_params={"task_id": fake_id})
        resp = correct_delete.handler(event, None)
        assert resp["statusCode"] == 404
        body = _parse_body(resp)
        assert body["error"]["code"] == "RESOURCE_NOT_FOUND"


# ===========================================================================
# SE-8 — Handler sin try-except
# ===========================================================================

class TestSE8NoTryExcept:

    def test_se8_missing_title_raises_validation_error(self, ddb_table):
        """SE-8: ValidationError is not caught and propagates to the caller."""
        event = _make_event(body={"completed": False})
        with pytest.raises(Exception):
            # ValidationError (subclass of Exception) propagates uncaught
            se8.handler(event, None)

    def test_se8_boto3_error_propagates(self, ddb_table):
        """SE-8: boto3 ClientError is not caught and propagates."""
        from unittest.mock import patch, MagicMock

        mock_create = MagicMock(
            side_effect=botocore.exceptions.ClientError(
                {"Error": {"Code": "InternalServerError", "Message": "fail"}},
                "PutItem",
            )
        )
        with patch("common.repository.TaskRepository.create", mock_create):
            event = _make_event(body={"title": "Valid title"})
            with pytest.raises(botocore.exceptions.ClientError):
                se8.handler(event, None)

    def test_se8_isolation_correct_create_catches_exceptions(self, ddb_table):
        """Isolation: the correct create handler catches exceptions and returns 500."""
        from unittest.mock import patch, MagicMock

        mock_create = MagicMock(
            side_effect=botocore.exceptions.ClientError(
                {"Error": {"Code": "InternalServerError", "Message": "fail"}},
                "PutItem",
            )
        )
        with patch("common.repository.TaskRepository.create", mock_create):
            event = _make_event(body={"title": "Valid title"})
            resp = correct_create.handler(event, None)
            assert resp["statusCode"] == 500


# ===========================================================================
# SE-9 — Logging vía print() (no detectable por Metric Filter)
# ===========================================================================

class TestSE9PrintInsteadOfLogging:

    def test_se9_404_uses_print_not_logging(self, ddb_table, caplog, capsys):
        """SE-9: not-found error uses print() instead of logging.error()."""
        fake_id = "ghost-" + str(uuid.uuid4())
        event = _make_event(path_params={"task_id": fake_id})

        with caplog.at_level(logging.ERROR):
            resp = se9.handler(event, None)

        assert resp["statusCode"] == 404

        # No ERROR: log records from se9's own code
        se9_error_records = [
            r for r in caplog.records
            if r.name.startswith("seeded.se9") and r.levelno == logging.ERROR
        ]
        assert len(se9_error_records) == 0, (
            "SE-9 should not emit logging.error() records; "
            f"found: {[r.getMessage() for r in se9_error_records]}"
        )

        # The print() output appears in stdout (capsys), not in log records
        captured = capsys.readouterr()
        assert "not found" in captured.out.lower() or "task not found" in captured.out.lower() or fake_id in captured.out

    def test_se9_no_error_prefix_in_logs(self, ddb_table, caplog):
        """SE-9: no ERROR: prefix in log records — Metric Filter won't fire."""
        fake_id = "ghost-" + str(uuid.uuid4())
        event = _make_event(path_params={"task_id": fake_id})

        with caplog.at_level(logging.DEBUG):
            se9.handler(event, None)

        # Filter to records that come from se9 module
        se9_records = [r for r in caplog.records if "se9" in r.name]
        for record in se9_records:
            assert not record.getMessage().startswith("ERROR:"), (
                f"SE-9 should NOT emit ERROR: but found: {record.getMessage()!r}"
            )

    def test_se9_isolation_correct_get_uses_logging(self, ddb_table, caplog):
        """Isolation: the correct get handler emits ERROR: log on not-found... actually returns 404 without error log."""
        # The correct handler returns 404 gracefully (no error log for NotFoundError)
        fake_id = "ghost-" + str(uuid.uuid4())
        event = _make_event(path_params={"task_id": fake_id})
        resp = correct_get.handler(event, None)
        assert resp["statusCode"] == 404
        body = _parse_body(resp)
        assert body["error"]["code"] == "RESOURCE_NOT_FOUND"
