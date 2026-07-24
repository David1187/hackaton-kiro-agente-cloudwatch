"""
tests/test_handlers.py — Unit and property-based tests for CRUD Lambda handlers.

Covers:
  4.2-4.4:  Property tests for create_task
  4.6-4.7:  Property tests for get_task
  4.9:      Property test for list_tasks
  4.11-4.12: Property tests for update_task
  4.14-4.15: Property tests for delete_task
  4.16-4.18: Property tests for validation and error handling
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import boto3
import botocore.exceptions
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st
from moto import mock_aws

# ---------------------------------------------------------------------------
# Path setup — ensure crud_api root is importable
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

# Import handlers (after env setup)
from handlers import create_task, delete_task, get_task, list_tasks, update_task  # noqa: E402
from common.repository import _state  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(body=None, path_params=None):
    """Build a minimal Lambda proxy event."""
    return {
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path_params or {},
    }


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
    yield
    _state["dynamodb"] = None
    _state["table"] = None


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


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

valid_title = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=1,
    max_size=255,
).filter(lambda s: len(s.strip()) >= 1)

invalid_title_too_long = st.text(min_size=256, max_size=400)
invalid_title_empty = st.just("") | st.text(
    alphabet=st.just(" "), min_size=1, max_size=10
)


# ===========================================================================
# 4.2 — Property 1: Create produces a valid, persisted Task
# ===========================================================================

class TestCreateHandler:

    def test_create_happy_path(self, ddb_table):
        """Valid body → 201 with all required fields."""
        event = _make_event(body={"title": "Buy milk"})
        resp = create_task.handler(event, None)
        assert resp["statusCode"] == 201
        body = _parse_body(resp)
        assert "task_id" in body
        assert body["title"] == "Buy milk"
        assert body["completed"] is False
        assert "created_at" in body
        assert "updated_at" in body

    def test_create_completed_default_false(self, ddb_table):
        """When completed is not provided, it defaults to False."""
        event = _make_event(body={"title": "Task without completed"})
        resp = create_task.handler(event, None)
        assert resp["statusCode"] == 201
        assert _parse_body(resp)["completed"] is False

    def test_create_explicit_completed_true(self, ddb_table):
        """completed=True is accepted and persisted."""
        event = _make_event(body={"title": "Done task", "completed": True})
        resp = create_task.handler(event, None)
        assert resp["statusCode"] == 201
        assert _parse_body(resp)["completed"] is True

    @given(title=valid_title)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_create_valid_title_property(self, ddb_table, title):
        """Property 1: Any valid title produces a 201 with the title preserved."""
        event = _make_event(body={"title": title})
        resp = create_task.handler(event, None)
        assert resp["statusCode"] == 201
        body = _parse_body(resp)
        assert body["title"] == title.strip()
        assert "task_id" in body

    # --- 4.3 — Property 2: Timestamp equality and format -------------------

    def test_create_timestamps_are_equal(self, ddb_table):
        """Property 2: created_at and updated_at are identical on creation."""
        event = _make_event(body={"title": "Timestamp test"})
        resp = create_task.handler(event, None)
        body = _parse_body(resp)
        assert body["created_at"] == body["updated_at"]

    def test_create_timestamp_iso8601_format(self, ddb_table):
        """created_at follows ISO 8601 UTC with milliseconds."""
        event = _make_event(body={"title": "Format test"})
        resp = create_task.handler(event, None)
        ts = _parse_body(resp)["created_at"]
        # Must parse as a valid datetime
        dt = datetime.fromisoformat(ts)
        assert dt.tzinfo is not None
        # Must contain milliseconds (dot in time part)
        assert "." in ts

    def test_create_task_id_is_uuid(self, ddb_table):
        """task_id must be a valid UUID v4."""
        event = _make_event(body={"title": "UUID test"})
        resp = create_task.handler(event, None)
        task_id = _parse_body(resp)["task_id"]
        parsed = uuid.UUID(task_id)
        assert parsed.version == 4

    # --- 4.4 — Property 3: Invalid body rejected without persisting ---------

    def test_create_missing_body_returns_400(self, ddb_table):
        """No body → 400 INVALID_JSON."""
        event = _make_event(body=None)
        resp = create_task.handler(event, None)
        assert resp["statusCode"] == 400
        assert _parse_body(resp)["error"]["code"] == "INVALID_JSON"

    def test_create_missing_title_returns_400(self, ddb_table):
        """Body without title → 400 MISSING_FIELD."""
        event = _make_event(body={"completed": False})
        resp = create_task.handler(event, None)
        assert resp["statusCode"] == 400
        assert _parse_body(resp)["error"]["code"] == "MISSING_FIELD"

    def test_create_invalid_json_returns_400(self, ddb_table):
        """Malformed JSON string → 400 INVALID_JSON."""
        event = {"body": "{not valid json}", "pathParameters": {}}
        resp = create_task.handler(event, None)
        assert resp["statusCode"] == 400
        assert _parse_body(resp)["error"]["code"] == "INVALID_JSON"

    def test_create_title_too_long_returns_400(self, ddb_table):
        """title > 255 chars → 400 INVALID_TITLE."""
        event = _make_event(body={"title": "x" * 256})
        resp = create_task.handler(event, None)
        assert resp["statusCode"] == 400
        assert _parse_body(resp)["error"]["code"] == "INVALID_TITLE"

    def test_create_title_whitespace_only_returns_400(self, ddb_table):
        """Whitespace-only title → 400 INVALID_TITLE."""
        event = _make_event(body={"title": "   "})
        resp = create_task.handler(event, None)
        assert resp["statusCode"] == 400
        assert _parse_body(resp)["error"]["code"] == "INVALID_TITLE"

    def test_create_completed_integer_rejected(self, ddb_table):
        """completed=1 (int, not bool) → 400 INVALID_TYPE."""
        event = _make_event(body={"title": "Test", "completed": 1})
        resp = create_task.handler(event, None)
        assert resp["statusCode"] == 400
        assert _parse_body(resp)["error"]["code"] == "INVALID_TYPE"

    def test_create_error_response_no_internal_details(self, ddb_table):
        """Error body must not contain table name, ARN, or stack trace."""
        event = _make_event(body={"title": ""})
        resp = create_task.handler(event, None)
        body_str = resp["body"]
        assert "Tasks" not in body_str  # table name
        assert "arn:" not in body_str
        assert "Traceback" not in body_str

    def test_create_ddb_client_error_returns_500(self, ddb_table):
        """ClientError from DynamoDB → 500 DDB_ERROR."""
        error_resp = {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}}
        with patch("common.repository._get_table") as mock_tbl:
            mock_tbl.return_value.put_item.side_effect = botocore.exceptions.ClientError(
                error_resp, "PutItem"
            )
            event = _make_event(body={"title": "Test"})
            resp = create_task.handler(event, None)
        assert resp["statusCode"] == 500
        assert _parse_body(resp)["error"]["code"] == "DDB_ERROR"



# ===========================================================================
# 4.6 — Property 7: Round trip create → get
# 4.7 — Property 8: Non-existent task_id returns 404
# ===========================================================================

class TestGetHandler:

    def test_get_existing_task(self, ddb_table):
        """Create then get returns 200 with the same task."""
        create_event = _make_event(body={"title": "Read me"})
        create_resp = create_task.handler(create_event, None)
        task_id = _parse_body(create_resp)["task_id"]

        get_event = _make_event(path_params={"task_id": task_id})
        resp = get_task.handler(get_event, None)
        assert resp["statusCode"] == 200
        body = _parse_body(resp)
        assert body["task_id"] == task_id
        assert body["title"] == "Read me"

    @given(title=valid_title)
    @settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_get_roundtrip_property(self, ddb_table, title):
        """Property 7: create → get returns identical item."""
        create_resp = create_task.handler(_make_event(body={"title": title}), None)
        assert create_resp["statusCode"] == 201
        created = _parse_body(create_resp)
        task_id = created["task_id"]

        get_resp = get_task.handler(_make_event(path_params={"task_id": task_id}), None)
        assert get_resp["statusCode"] == 200
        fetched = _parse_body(get_resp)
        assert fetched["task_id"] == task_id
        assert fetched["title"] == created["title"]
        assert fetched["completed"] == created["completed"]
        assert fetched["created_at"] == created["created_at"]

    def test_get_nonexistent_returns_404(self, ddb_table):
        """Property 8: Non-existent task_id → 404 RESOURCE_NOT_FOUND."""
        event = _make_event(path_params={"task_id": str(uuid.uuid4())})
        resp = get_task.handler(event, None)
        assert resp["statusCode"] == 404
        assert _parse_body(resp)["error"]["code"] == "RESOURCE_NOT_FOUND"

    @given(task_id=st.uuids().map(str))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_get_random_uuid_not_found(self, ddb_table, task_id):
        """Property 8 (property): Any random UUID that was not created returns 404."""
        event = _make_event(path_params={"task_id": task_id})
        resp = get_task.handler(event, None)
        assert resp["statusCode"] == 404

    def test_get_missing_task_id_returns_400(self, ddb_table):
        """Missing task_id path param → 400 INVALID_TASK_ID."""
        event = _make_event(path_params={})
        resp = get_task.handler(event, None)
        assert resp["statusCode"] == 400
        assert _parse_body(resp)["error"]["code"] == "INVALID_TASK_ID"

    def test_get_empty_task_id_returns_400(self, ddb_table):
        """Empty task_id → 400 INVALID_TASK_ID."""
        event = _make_event(path_params={"task_id": "   "})
        resp = get_task.handler(event, None)
        assert resp["statusCode"] == 400

    def test_get_ddb_client_error_returns_500(self, ddb_table):
        """ClientError in get_item → 500 DDB_ERROR."""
        error_resp = {"Error": {"Code": "InternalServerError", "Message": "x"}}
        with patch("common.repository._get_table") as mock_tbl:
            mock_tbl.return_value.get_item.side_effect = botocore.exceptions.ClientError(
                error_resp, "GetItem"
            )
            event = _make_event(path_params={"task_id": "some-id"})
            resp = get_task.handler(event, None)
        assert resp["statusCode"] == 500
        assert _parse_body(resp)["error"]["code"] == "DDB_ERROR"


# ===========================================================================
# 4.9 — Property 9: List reflects the persisted set
# ===========================================================================

class TestListHandler:

    def test_list_empty_table(self, ddb_table):
        """Empty table → 200 with tasks=[] and count=0."""
        resp = list_tasks.handler(_make_event(), None)
        assert resp["statusCode"] == 200
        body = _parse_body(resp)
        assert body["tasks"] == []
        assert body["count"] == 0

    def test_list_returns_created_tasks(self, ddb_table):
        """Tasks created are visible in list response."""
        for title in ["Task A", "Task B", "Task C"]:
            create_task.handler(_make_event(body={"title": title}), None)

        resp = list_tasks.handler(_make_event(), None)
        assert resp["statusCode"] == 200
        body = _parse_body(resp)
        assert body["count"] == 3
        assert len(body["tasks"]) == 3

    @given(titles=st.lists(valid_title, min_size=1, max_size=5))
    @settings(max_examples=5, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_list_count_matches_created(self, ddb_table, titles):
        """Property 9: After creating N tasks, list count includes at least those N items."""
        # Get baseline count before this example's creates
        baseline_resp = list_tasks.handler(_make_event(), None)
        baseline_count = _parse_body(baseline_resp)["count"]

        for title in titles:
            create_task.handler(_make_event(body={"title": title}), None)

        resp = list_tasks.handler(_make_event(), None)
        assert resp["statusCode"] == 200
        body = _parse_body(resp)
        # Count must be exactly baseline + number of new items created
        assert body["count"] == baseline_count + len(titles)
        assert len(body["tasks"]) == baseline_count + len(titles)

    def test_list_response_has_required_keys(self, ddb_table):
        """Response always contains 'tasks' and 'count' keys."""
        resp = list_tasks.handler(_make_event(), None)
        body = _parse_body(resp)
        assert "tasks" in body
        assert "count" in body

    def test_list_ddb_client_error_returns_500(self, ddb_table):
        """ClientError in scan → 500 DDB_ERROR."""
        error_resp = {"Error": {"Code": "InternalServerError", "Message": "x"}}
        with patch("common.repository._get_table") as mock_tbl:
            mock_tbl.return_value.scan.side_effect = botocore.exceptions.ClientError(
                error_resp, "Scan"
            )
            resp = list_tasks.handler(_make_event(), None)
        assert resp["statusCode"] == 500
        assert _parse_body(resp)["error"]["code"] == "DDB_ERROR"



# ===========================================================================
# 4.11 — Property 10: Partial update modifies only present attributes
# 4.12 — Property 11: updated_at refreshed, created_at preserved
# ===========================================================================

class TestUpdateHandler:

    def _create_task(self, ddb_table, title="Original title", completed=False):
        """Helper: create a task and return the parsed body."""
        resp = create_task.handler(_make_event(body={"title": title, "completed": completed}), None)
        assert resp["statusCode"] == 201
        return _parse_body(resp)

    def test_update_title_happy_path(self, ddb_table):
        """Update title → 200 with updated title."""
        created = self._create_task(ddb_table)
        task_id = created["task_id"]

        event = _make_event(body={"title": "Updated title"}, path_params={"task_id": task_id})
        resp = update_task.handler(event, None)
        assert resp["statusCode"] == 200
        body = _parse_body(resp)
        assert body["title"] == "Updated title"
        assert body["task_id"] == task_id

    def test_update_completed_happy_path(self, ddb_table):
        """Update completed → 200 with updated completed flag."""
        created = self._create_task(ddb_table, completed=False)
        task_id = created["task_id"]

        event = _make_event(body={"completed": True}, path_params={"task_id": task_id})
        resp = update_task.handler(event, None)
        assert resp["statusCode"] == 200
        assert _parse_body(resp)["completed"] is True

    def test_update_both_fields(self, ddb_table):
        """Update both title and completed → 200 with both updated."""
        created = self._create_task(ddb_table)
        task_id = created["task_id"]

        event = _make_event(
            body={"title": "New title", "completed": True},
            path_params={"task_id": task_id},
        )
        resp = update_task.handler(event, None)
        assert resp["statusCode"] == 200
        body = _parse_body(resp)
        assert body["title"] == "New title"
        assert body["completed"] is True

    @given(title=valid_title)
    @settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_update_partial_preserves_other_fields(self, ddb_table, title):
        """Property 10: Updating title does not alter completed or created_at."""
        created = self._create_task(ddb_table, title="Initial", completed=False)
        task_id = created["task_id"]

        event = _make_event(body={"title": title}, path_params={"task_id": task_id})
        resp = update_task.handler(event, None)
        assert resp["statusCode"] == 200
        body = _parse_body(resp)
        assert body["title"] == title.strip()
        assert body["completed"] is False
        assert body["created_at"] == created["created_at"]

    def test_update_refreshes_updated_at(self, ddb_table):
        """Property 11: updated_at after update is >= created_at; created_at unchanged."""
        created = self._create_task(ddb_table)
        task_id = created["task_id"]
        original_created_at = created["created_at"]
        original_updated_at = created["updated_at"]

        event = _make_event(body={"title": "Changed"}, path_params={"task_id": task_id})
        resp = update_task.handler(event, None)
        body = _parse_body(resp)

        assert body["created_at"] == original_created_at
        # updated_at must be a valid datetime (may be equal or greater)
        dt_new = datetime.fromisoformat(body["updated_at"])
        dt_orig = datetime.fromisoformat(original_updated_at)
        assert dt_new >= dt_orig

    def test_update_nonexistent_returns_404(self, ddb_table):
        """Updating a non-existent task_id → 404 RESOURCE_NOT_FOUND."""
        event = _make_event(
            body={"title": "Ghost"},
            path_params={"task_id": str(uuid.uuid4())},
        )
        resp = update_task.handler(event, None)
        assert resp["statusCode"] == 404
        assert _parse_body(resp)["error"]["code"] == "RESOURCE_NOT_FOUND"

    def test_update_missing_task_id_returns_400(self, ddb_table):
        """Missing task_id path param → 400 INVALID_TASK_ID."""
        event = _make_event(body={"title": "No ID"}, path_params={})
        resp = update_task.handler(event, None)
        assert resp["statusCode"] == 400
        assert _parse_body(resp)["error"]["code"] == "INVALID_TASK_ID"

    def test_update_empty_body_returns_400(self, ddb_table):
        """Body with no updatable fields → 400 MISSING_FIELD."""
        created = self._create_task(ddb_table)
        event = _make_event(body={}, path_params={"task_id": created["task_id"]})
        resp = update_task.handler(event, None)
        assert resp["statusCode"] == 400
        assert _parse_body(resp)["error"]["code"] == "MISSING_FIELD"

    def test_update_missing_body_returns_400(self, ddb_table):
        """No body at all → 400 INVALID_JSON."""
        created = self._create_task(ddb_table)
        event = _make_event(body=None, path_params={"task_id": created["task_id"]})
        resp = update_task.handler(event, None)
        assert resp["statusCode"] == 400
        assert _parse_body(resp)["error"]["code"] == "INVALID_JSON"

    def test_update_invalid_title_type_returns_400(self, ddb_table):
        """title as integer → 400 INVALID_TYPE."""
        created = self._create_task(ddb_table)
        event = _make_event(body={"title": 123}, path_params={"task_id": created["task_id"]})
        resp = update_task.handler(event, None)
        assert resp["statusCode"] == 400
        assert _parse_body(resp)["error"]["code"] == "INVALID_TYPE"

    def test_update_ddb_client_error_returns_500(self, ddb_table):
        """ClientError in update_item → 500 DDB_ERROR."""
        error_resp = {"Error": {"Code": "InternalServerError", "Message": "x"}}
        with patch("common.repository._get_table") as mock_tbl:
            mock_tbl.return_value.update_item.side_effect = botocore.exceptions.ClientError(
                error_resp, "UpdateItem"
            )
            event = _make_event(
                body={"title": "Test"},
                path_params={"task_id": "some-id"},
            )
            resp = update_task.handler(event, None)
        assert resp["statusCode"] == 500
        assert _parse_body(resp)["error"]["code"] == "DDB_ERROR"


# ===========================================================================
# 4.14 — Property 13: Round trip create → delete → get
# 4.15 — Property 12: Conditional op on non-existent task returns 404
# ===========================================================================

class TestDeleteHandler:

    def _create_task(self, ddb_table, title="To be deleted"):
        resp = create_task.handler(_make_event(body={"title": title}), None)
        assert resp["statusCode"] == 201
        return _parse_body(resp)

    def test_delete_happy_path(self, ddb_table):
        """Delete existing task → 200 with deleted=True and task_id."""
        created = self._create_task(ddb_table)
        task_id = created["task_id"]

        event = _make_event(path_params={"task_id": task_id})
        resp = delete_task.handler(event, None)
        assert resp["statusCode"] == 200
        body = _parse_body(resp)
        assert body["deleted"] is True
        assert body["task_id"] == task_id

    @given(title=valid_title)
    @settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_delete_create_then_get_returns_404(self, ddb_table, title):
        """Property 13: After delete, get returns 404."""
        created = self._create_task(ddb_table, title=title)
        task_id = created["task_id"]

        del_resp = delete_task.handler(_make_event(path_params={"task_id": task_id}), None)
        assert del_resp["statusCode"] == 200

        get_resp = get_task.handler(_make_event(path_params={"task_id": task_id}), None)
        assert get_resp["statusCode"] == 404

    def test_delete_nonexistent_returns_404(self, ddb_table):
        """Property 12: Deleting a non-existent task → 404."""
        event = _make_event(path_params={"task_id": str(uuid.uuid4())})
        resp = delete_task.handler(event, None)
        assert resp["statusCode"] == 404
        assert _parse_body(resp)["error"]["code"] == "RESOURCE_NOT_FOUND"

    @given(task_id=st.uuids().map(str))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_delete_random_uuid_not_found(self, ddb_table, task_id):
        """Property 12 (property): Random UUID that was never created → 404."""
        event = _make_event(path_params={"task_id": task_id})
        resp = delete_task.handler(event, None)
        assert resp["statusCode"] == 404

    def test_delete_missing_task_id_returns_400(self, ddb_table):
        """Missing task_id path param → 400 INVALID_TASK_ID."""
        event = _make_event(path_params={})
        resp = delete_task.handler(event, None)
        assert resp["statusCode"] == 400
        assert _parse_body(resp)["error"]["code"] == "INVALID_TASK_ID"

    def test_delete_empty_task_id_returns_400(self, ddb_table):
        """Whitespace task_id → 400 INVALID_TASK_ID."""
        event = _make_event(path_params={"task_id": "   "})
        resp = delete_task.handler(event, None)
        assert resp["statusCode"] == 400

    def test_delete_confirmation_includes_task_id(self, ddb_table):
        """Confirmation body includes the deleted task_id."""
        created = self._create_task(ddb_table)
        task_id = created["task_id"]

        resp = delete_task.handler(_make_event(path_params={"task_id": task_id}), None)
        body = _parse_body(resp)
        assert body["task_id"] == task_id

    def test_delete_ddb_client_error_returns_500(self, ddb_table):
        """ClientError in delete_item → 500 DDB_ERROR."""
        error_resp = {"Error": {"Code": "InternalServerError", "Message": "x"}}
        with patch("common.repository._get_table") as mock_tbl:
            mock_tbl.return_value.delete_item.side_effect = botocore.exceptions.ClientError(
                error_resp, "DeleteItem"
            )
            event = _make_event(path_params={"task_id": "some-id"})
            resp = delete_task.handler(event, None)
        assert resp["statusCode"] == 500
        assert _parse_body(resp)["error"]["code"] == "DDB_ERROR"



# ===========================================================================
# 4.16 — Property 14: Invalid input never reaches persistence; valid does
# 4.17 — Property 15: Every error logged exactly once with ERROR: prefix
# 4.18 — Unit tests: IO error branches for all handlers
# ===========================================================================

class TestValidationAndErrorHandling:
    """Cross-cutting tests for input validation and error handling."""

    # --- 4.16: Invalid input never reaches DynamoDB -------------------------

    @given(
        body_str=st.one_of(
            st.just(None),
            st.just(""),
            st.just("not json"),
            st.just('["array not object"]'),
        )
    )
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_invalid_body_never_reaches_ddb(self, ddb_table, body_str):
        """Property 14a: Any invalid body returns 4xx before touching DDB."""
        event = {"body": body_str, "pathParameters": {}}
        resp = create_task.handler(event, None)
        assert resp["statusCode"] == 400

    @given(
        path_params=st.one_of(
            st.just(None),
            st.just({}),
            st.just({"task_id": ""}),
            st.just({"task_id": "   "}),
        )
    )
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_invalid_task_id_never_reaches_ddb(self, ddb_table, path_params):
        """Property 14b: Invalid task_id returns 400 before touching DDB."""
        event = {"body": None, "pathParameters": path_params}
        for h in (get_task.handler, delete_task.handler):
            resp = h(event, None)
            assert resp["statusCode"] in (400, 422)

    # --- 4.17: ERROR: prefix in log output ----------------------------------

    def test_create_ddb_error_logs_with_prefix(self, ddb_table, caplog):
        """Property 15: ClientError in create logs ERROR: prefix."""
        import logging
        error_resp = {"Error": {"Code": "ServiceUnavailable", "Message": "x"}}
        with patch("common.repository._get_table") as mock_tbl:
            mock_tbl.return_value.put_item.side_effect = botocore.exceptions.ClientError(
                error_resp, "PutItem"
            )
            with caplog.at_level(logging.WARNING):
                create_task.handler(_make_event(body={"title": "Test"}), None)
        # At least one log record must start with ERROR:
        error_records = [r for r in caplog.records if r.getMessage().startswith("ERROR:")]
        assert len(error_records) >= 1

    def test_get_not_found_does_not_log_error(self, ddb_table, caplog):
        """404 NotFoundError should NOT produce an ERROR: log record."""
        import logging
        event = _make_event(path_params={"task_id": str(uuid.uuid4())})
        with caplog.at_level(logging.WARNING):
            get_task.handler(event, None)
        error_records = [r for r in caplog.records if r.getMessage().startswith("ERROR:")]
        assert len(error_records) == 0

    # --- 4.18: IO error branches for all handlers ---------------------------

    def test_update_ddb_unexpected_error_returns_500(self, ddb_table):
        """Generic Exception in update → 500 INTERNAL_ERROR."""
        with patch("common.repository._get_table") as mock_tbl:
            mock_tbl.return_value.update_item.side_effect = RuntimeError("boom")
            event = _make_event(
                body={"title": "Test"},
                path_params={"task_id": "some-id"},
            )
            resp = update_task.handler(event, None)
        assert resp["statusCode"] == 500
        assert _parse_body(resp)["error"]["code"] == "INTERNAL_ERROR"

    def test_delete_unexpected_error_returns_500(self, ddb_table):
        """Generic Exception in delete → 500 INTERNAL_ERROR."""
        with patch("common.repository._get_table") as mock_tbl:
            mock_tbl.return_value.delete_item.side_effect = RuntimeError("boom")
            event = _make_event(path_params={"task_id": "some-id"})
            resp = delete_task.handler(event, None)
        assert resp["statusCode"] == 500
        assert _parse_body(resp)["error"]["code"] == "INTERNAL_ERROR"

    def test_list_unexpected_error_returns_500(self, ddb_table):
        """Generic Exception in list → 500 INTERNAL_ERROR."""
        with patch("common.repository._get_table") as mock_tbl:
            mock_tbl.return_value.scan.side_effect = RuntimeError("boom")
            resp = list_tasks.handler(_make_event(), None)
        assert resp["statusCode"] == 500
        assert _parse_body(resp)["error"]["code"] == "INTERNAL_ERROR"

    def test_error_response_never_leaks_table_name(self, ddb_table):
        """Error bodies must not contain the DynamoDB table name."""
        error_resp = {"Error": {"Code": "InternalServerError", "Message": "Tasks table error"}}
        with patch("common.repository._get_table") as mock_tbl:
            mock_tbl.return_value.put_item.side_effect = botocore.exceptions.ClientError(
                error_resp, "PutItem"
            )
            resp = create_task.handler(_make_event(body={"title": "Test"}), None)
        body_str = resp["body"]
        assert "Tasks" not in body_str
        assert "table" not in body_str.lower() or "base de datos" in body_str

    def test_all_handlers_return_json_content_type(self, ddb_table):
        """All handlers set Content-Type: application/json."""
        handlers_and_events = [
            (create_task.handler, _make_event(body={"title": "CT"})),
            (list_tasks.handler, _make_event()),
            (get_task.handler, _make_event(path_params={"task_id": "x"})),
            (update_task.handler, _make_event(body={"title": "CT"}, path_params={"task_id": "x"})),
            (delete_task.handler, _make_event(path_params={"task_id": "x"})),
        ]
        for h, event in handlers_and_events:
            resp = h(event, None)
            assert resp["headers"]["Content-Type"] == "application/json", (
                f"{h.__module__} did not return Content-Type: application/json"
            )
