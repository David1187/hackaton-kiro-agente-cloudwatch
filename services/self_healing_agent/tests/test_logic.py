"""Unit tests for the Self-Healing Agent pure logic modules."""
import pytest
from datetime import datetime, timezone

from services.self_healing_agent.config import resolve_model_id, DEFAULT_MODEL_ID
from services.self_healing_agent.repo_tag import (
    parse_repo_tag,
    RepoRef,
    InvalidRepoTagError,
    GITHUB_REPO_TAG_KEY,
)
from services.self_healing_agent.branch_naming import build_fix_branch_name
from services.self_healing_agent.pr_body import build_pr_title, build_pr_description
from services.self_healing_agent.logs_client import derive_log_group, derive_function_name


# ====================================================================
# config.py tests
# ====================================================================


class TestResolveModelId:
    """Tests for resolve_model_id."""

    def test_returns_env_value_when_present(self):
        env = {"MODEL_ID": "anthropic.claude-v2"}
        assert resolve_model_id(env) == "anthropic.claude-v2"

    def test_returns_default_when_key_missing(self):
        assert resolve_model_id({}) == DEFAULT_MODEL_ID

    def test_returns_default_when_value_empty(self):
        assert resolve_model_id({"MODEL_ID": ""}) == DEFAULT_MODEL_ID

    def test_returns_default_when_value_whitespace(self):
        assert resolve_model_id({"MODEL_ID": "   "}) == DEFAULT_MODEL_ID

    def test_strips_whitespace_from_value(self):
        env = {"MODEL_ID": "  my-model  "}
        assert resolve_model_id(env) == "my-model"

    def test_ignores_other_keys(self):
        env = {"OTHER_KEY": "some-value"}
        assert resolve_model_id(env) == DEFAULT_MODEL_ID


# ====================================================================
# repo_tag.py tests
# ====================================================================


class TestParseRepoTag:
    """Tests for parse_repo_tag."""

    def test_valid_tag(self):
        result = parse_repo_tag({"github-repo": "owner/repo"})
        assert result == RepoRef(owner="owner", repo="repo")

    def test_valid_tag_with_dashes_and_dots(self):
        result = parse_repo_tag({"github-repo": "my-org/my-repo.name"})
        assert result.owner == "my-org"
        assert result.repo == "my-repo.name"

    def test_str_representation(self):
        ref = RepoRef(owner="owner", repo="repo")
        assert str(ref) == "owner/repo"

    def test_tag_missing_raises(self):
        with pytest.raises(InvalidRepoTagError, match="not found"):
            parse_repo_tag({"other-tag": "value"})

    def test_empty_dict_raises(self):
        with pytest.raises(InvalidRepoTagError, match="not found"):
            parse_repo_tag({})

    def test_empty_value_raises(self):
        with pytest.raises(InvalidRepoTagError, match="empty"):
            parse_repo_tag({"github-repo": ""})

    def test_whitespace_only_raises(self):
        with pytest.raises(InvalidRepoTagError, match="empty"):
            parse_repo_tag({"github-repo": "   "})

    def test_no_slash_raises(self):
        with pytest.raises(InvalidRepoTagError, match="1"):
            parse_repo_tag({"github-repo": "noslash"})

    def test_multiple_slashes_raises(self):
        with pytest.raises(InvalidRepoTagError, match="slashes"):
            parse_repo_tag({"github-repo": "a/b/c"})

    def test_empty_owner_raises(self):
        with pytest.raises(InvalidRepoTagError, match="Owner"):
            parse_repo_tag({"github-repo": "/repo"})

    def test_empty_repo_raises(self):
        with pytest.raises(InvalidRepoTagError, match="Repo"):
            parse_repo_tag({"github-repo": "owner/"})

    def test_owner_with_leading_space_raises(self):
        with pytest.raises(InvalidRepoTagError, match="Owner"):
            parse_repo_tag({"github-repo": " owner/repo"})

    def test_repo_with_trailing_space_raises(self):
        with pytest.raises(InvalidRepoTagError, match="Repo"):
            parse_repo_tag({"github-repo": "owner/repo "})

    def test_owner_with_internal_space_raises(self):
        with pytest.raises(InvalidRepoTagError, match="Owner"):
            parse_repo_tag({"github-repo": "ow ner/repo"})

    def test_repo_with_internal_tab_raises(self):
        with pytest.raises(InvalidRepoTagError, match="Repo"):
            parse_repo_tag({"github-repo": "owner/re\tpo"})


# ====================================================================
# branch_naming.py tests
# ====================================================================


class TestBuildFixBranchName:
    """Tests for build_fix_branch_name."""

    def test_basic_format(self):
        ts = datetime(2025, 1, 15, 10, 30, 45, tzinfo=timezone.utc)
        result = build_fix_branch_name("my-function", ts)
        assert result == "fix/auto-heal-my-function-20250115T103045Z"

    def test_prefix_always_present(self):
        ts = datetime(2025, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("fn", ts)
        assert result.startswith("fix/auto-heal-")

    def test_sanitizes_spaces(self):
        ts = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("my function name", ts)
        assert " " not in result
        assert "fix/auto-heal-my-function-name-" in result

    def test_sanitizes_special_chars(self):
        ts = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("fn~with^special:chars", ts)
        assert "~" not in result
        assert "^" not in result
        assert ":" not in result

    def test_collapses_consecutive_dashes(self):
        ts = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("fn~~name", ts)
        assert "--" not in result

    def test_naive_datetime_treated_as_utc(self):
        ts = datetime(2025, 3, 20, 14, 25, 0)
        result = build_fix_branch_name("fn", ts)
        assert "20250320T142500Z" in result

    def test_non_utc_converted_to_utc(self):
        from datetime import timedelta
        tz_plus_2 = timezone(timedelta(hours=2))
        ts = datetime(2025, 3, 20, 16, 0, 0, tzinfo=tz_plus_2)
        result = build_fix_branch_name("fn", ts)
        # 16:00+02:00 = 14:00 UTC
        assert "20250320T140000Z" in result

    def test_empty_lambda_name_uses_unknown(self):
        ts = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        result = build_fix_branch_name("", ts)
        assert "unknown" in result

    def test_uniqueness_by_timestamp(self):
        ts1 = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2025, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
        r1 = build_fix_branch_name("fn", ts1)
        r2 = build_fix_branch_name("fn", ts2)
        assert r1 != r2


# ====================================================================
# pr_body.py tests
# ====================================================================


class TestBuildPrTitle:
    """Tests for build_pr_title."""

    def test_includes_lambda_name(self):
        title = build_pr_title("create-task")
        assert "create-task" in title

    def test_starts_with_fix(self):
        title = build_pr_title("my-fn")
        assert title.startswith("fix:")


class TestBuildPrDescription:
    """Tests for build_pr_description."""

    def test_includes_lambda_name(self):
        desc = build_pr_description("my-fn", "KeyError: task_id")
        assert "my-fn" in desc

    def test_includes_error_summary(self):
        desc = build_pr_description("my-fn", "KeyError: task_id")
        assert "KeyError: task_id" in desc

    def test_includes_review_warning(self):
        desc = build_pr_description("fn", "error")
        assert "human review" in desc.lower() or "Human review" in desc


# ====================================================================
# logs_client.py tests
# ====================================================================


class TestDeriveLogGroup:
    """Tests for derive_log_group."""

    def test_basic_format(self):
        assert derive_log_group("my-fn") == "/aws/lambda/my-fn"

    def test_preserves_function_name(self):
        name = "TodoCrudStatelessStack-FnCreate1234ABC-xyz"
        assert derive_log_group(name) == f"/aws/lambda/{name}"


class TestDeriveFunctionName:
    """Tests for derive_function_name."""

    def test_extracts_from_metric_dimensions(self):
        event = {
            "detail": {
                "alarmName": "TestAlarm",
                "configuration": {
                    "metrics": [
                        {
                            "metricStat": {
                                "metric": {
                                    "dimensions": {
                                        "FunctionName": "my-lambda-fn"
                                    }
                                }
                            }
                        }
                    ]
                },
            }
        }
        assert derive_function_name(event) == "my-lambda-fn"

    def test_raises_on_empty_event(self):
        with pytest.raises(ValueError):
            derive_function_name({})

    def test_raises_on_no_dimensions(self):
        event = {
            "detail": {
                "alarmName": "TestAlarm",
                "configuration": {"metrics": []},
            }
        }
        with pytest.raises(ValueError):
            derive_function_name(event)
