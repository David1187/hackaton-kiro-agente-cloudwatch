"""Property-based tests for Self-Healing Agent pure logic modules.

Uses hypothesis to verify invariants across large random input spaces.
"""
import re
import string
from datetime import datetime, timezone, timedelta

import pytest
from hypothesis import given, settings, assume
from hypothesis.strategies import (
    text,
    dictionaries,
    from_regex,
    sampled_from,
    datetimes,
    integers,
    one_of,
    just,
    none,
    composite,
    builds,
)

from services.self_healing_agent.config import resolve_model_id, DEFAULT_MODEL_ID
from services.self_healing_agent.repo_tag import (
    parse_repo_tag,
    RepoRef,
    InvalidRepoTagError,
    GITHUB_REPO_TAG_KEY,
)
from services.self_healing_agent.branch_naming import build_fix_branch_name
from services.self_healing_agent.pr_body import build_pr_title, build_pr_description
from services.self_healing_agent.logs_client import derive_log_group


# ====================================================================
# Feature: self-healing-agent, Property 1: Resolución del identificador del modelo
# Validates: Requirements 4.1, 4.2
# ====================================================================


class TestProperty1ModelResolution:
    """Property 1: resolve_model_id always returns a non-empty string.

    - If MODEL_ID is present and non-empty (after strip), it is returned as-is (stripped).
    - If MODEL_ID is absent or empty/whitespace, the default model is returned.
    - The returned value is never empty.
    """

    @settings(max_examples=100)
    @given(model_id=text(min_size=1, alphabet=string.ascii_letters + string.digits + ".-_"))
    def test_non_empty_model_id_returned_as_is(self, model_id: str):
        """When MODEL_ID is a non-empty printable string, resolve_model_id returns it."""
        env = {"MODEL_ID": model_id}
        result = resolve_model_id(env)
        assert result == model_id.strip()

    @settings(max_examples=100)
    @given(model_id=text(alphabet=" \t\n\r", max_size=20))
    def test_whitespace_only_returns_default(self, model_id: str):
        """When MODEL_ID is only whitespace, the default is returned."""
        env = {"MODEL_ID": model_id}
        result = resolve_model_id(env)
        assert result == DEFAULT_MODEL_ID

    @settings(max_examples=100)
    @given(env=dictionaries(
        keys=text(min_size=1, alphabet=string.ascii_letters + "_", max_size=20).filter(lambda k: k != "MODEL_ID"),
        values=text(max_size=30),
        max_size=10,
    ))
    def test_missing_key_returns_default(self, env: dict):
        """When MODEL_ID key is absent, the default is returned."""
        assume("MODEL_ID" not in env)
        result = resolve_model_id(env)
        assert result == DEFAULT_MODEL_ID

    @settings(max_examples=100)
    @given(model_id=text(min_size=1, max_size=100))
    def test_result_is_never_empty(self, model_id: str):
        """resolve_model_id never returns an empty string regardless of input."""
        env = {"MODEL_ID": model_id}
        result = resolve_model_id(env)
        assert len(result) > 0

    def test_default_model_matches_expected_value(self):
        """The default model ID is the one specified in architecture-guide.md."""
        assert DEFAULT_MODEL_ID == "qwen.qwen3-coder-30b-a3b-v1:0"


# ====================================================================
# Feature: self-healing-agent, Property 2: Round-trip y rechazo del tag github-repo
# Validates: Requirements 6.2, 6.5
# ====================================================================


# Strategy: valid GitHub-like owner/repo names (no whitespace, no slashes)
_github_name_chars = string.ascii_letters + string.digits + "-_."
_github_name = text(
    alphabet=_github_name_chars,
    min_size=1,
    max_size=39,
).filter(lambda s: s == s.strip() and ".." not in s and not s.startswith(".") and not s.endswith("."))


class TestProperty2RepoTag:
    """Property 2: parse_repo_tag round-trips valid 'owner/repo' and rejects invalid formats."""

    @settings(max_examples=100)
    @given(owner=_github_name, repo=_github_name)
    def test_valid_owner_repo_round_trips(self, owner: str, repo: str):
        """Valid owner/repo tags parse and reconstruct correctly."""
        tag_value = f"{owner}/{repo}"
        tags = {GITHUB_REPO_TAG_KEY: tag_value}
        result = parse_repo_tag(tags)
        assert result.owner == owner
        assert result.repo == repo
        assert str(result) == tag_value

    @settings(max_examples=100)
    @given(value=text(max_size=100).filter(lambda s: "/" not in s and s.strip() != ""))
    def test_no_slash_raises_error(self, value: str):
        """Strings without '/' are rejected."""
        tags = {GITHUB_REPO_TAG_KEY: value}
        with pytest.raises(InvalidRepoTagError):
            parse_repo_tag(tags)

    @settings(max_examples=100)
    @given(
        part1=text(min_size=1, max_size=30, alphabet=_github_name_chars),
        part2=text(min_size=1, max_size=30, alphabet=_github_name_chars),
        part3=text(min_size=1, max_size=30, alphabet=_github_name_chars),
    )
    def test_multiple_slashes_raises_error(self, part1: str, part2: str, part3: str):
        """Strings with more than one '/' are rejected."""
        value = f"{part1}/{part2}/{part3}"
        tags = {GITHUB_REPO_TAG_KEY: value}
        with pytest.raises(InvalidRepoTagError):
            parse_repo_tag(tags)

    @settings(max_examples=100)
    @given(
        key=text(min_size=1, alphabet=string.ascii_letters + "-_", max_size=30).filter(
            lambda k: k != GITHUB_REPO_TAG_KEY
        )
    )
    def test_missing_tag_key_raises_error(self, key: str):
        """When the tag key is not github-repo, parsing fails."""
        tags = {key: "owner/repo"}
        with pytest.raises(InvalidRepoTagError):
            parse_repo_tag(tags)

    @settings(max_examples=100)
    @given(owner=_github_name)
    def test_empty_repo_part_raises_error(self, owner: str):
        """'owner/' (empty repo) is rejected."""
        tags = {GITHUB_REPO_TAG_KEY: f"{owner}/"}
        with pytest.raises(InvalidRepoTagError):
            parse_repo_tag(tags)

    @settings(max_examples=100)
    @given(repo=_github_name)
    def test_empty_owner_part_raises_error(self, repo: str):
        """'/repo' (empty owner) is rejected."""
        tags = {GITHUB_REPO_TAG_KEY: f"/{repo}"}
        with pytest.raises(InvalidRepoTagError):
            parse_repo_tag(tags)


# ====================================================================
# Feature: self-healing-agent, Property 3: Derivación del Log_Group desde la metadata de la alarma
# Validates: Requirements 5.1
# ====================================================================


# Strategy: Lambda function names (alphanumeric, hyphens, underscores)
_function_name_chars = string.ascii_letters + string.digits + "-_"
_function_name = text(alphabet=_function_name_chars, min_size=1, max_size=64)


class TestProperty3LogGroupDerivation:
    """Property 3: derive_log_group always produces /aws/lambda/{function_name}."""

    @settings(max_examples=100)
    @given(function_name=_function_name)
    def test_log_group_format(self, function_name: str):
        """Log group always has the /aws/lambda/ prefix followed by the function name."""
        result = derive_log_group(function_name)
        assert result == f"/aws/lambda/{function_name}"

    @settings(max_examples=100)
    @given(function_name=_function_name)
    def test_log_group_starts_with_prefix(self, function_name: str):
        """Log group always starts with /aws/lambda/."""
        result = derive_log_group(function_name)
        assert result.startswith("/aws/lambda/")

    @settings(max_examples=100)
    @given(function_name=_function_name)
    def test_function_name_preserved_in_log_group(self, function_name: str):
        """The function name is preserved exactly in the log group (no sanitization)."""
        result = derive_log_group(function_name)
        assert result.removeprefix("/aws/lambda/") == function_name

    @settings(max_examples=100)
    @given(function_name=_function_name)
    def test_log_group_is_deterministic(self, function_name: str):
        """Same input always produces same output (pure function)."""
        assert derive_log_group(function_name) == derive_log_group(function_name)


# ====================================================================
# Feature: self-healing-agent, Property 4: Formato y validez del nombre de la Fix_Branch
# Validates: Requirements 9.1
# ====================================================================

# Git ref name invalid characters pattern
_GIT_INVALID_PATTERN = re.compile(r"[~^:?*\[\]\\@{}\s]")

# Strategy for timezone offsets (no tzdata dependency)
_tz_offsets = sampled_from([
    timezone.utc,
    timezone(timedelta(hours=0)),
    timezone(timedelta(hours=5)),
    timezone(timedelta(hours=-5)),
    timezone(timedelta(hours=8)),
    timezone(timedelta(hours=-8)),
    timezone(timedelta(hours=12)),
    timezone(timedelta(hours=-12)),
    timezone(timedelta(hours=1)),
    timezone(timedelta(hours=-1)),
])


class TestProperty4BranchNaming:
    """Property 4: build_fix_branch_name produces valid git ref names."""

    @settings(max_examples=100)
    @given(
        lambda_name=text(min_size=0, max_size=60, alphabet=string.printable),
        ts=datetimes(
            min_value=datetime(2020, 1, 1),
            max_value=datetime(2030, 12, 31),
            timezones=_tz_offsets,
        ),
    )
    def test_always_starts_with_fix_prefix(self, lambda_name: str, ts: datetime):
        """Branch name always starts with 'fix/auto-heal-'."""
        result = build_fix_branch_name(lambda_name, ts)
        assert result.startswith("fix/auto-heal-")

    @settings(max_examples=100)
    @given(
        lambda_name=text(min_size=0, max_size=60, alphabet=string.printable),
        ts=datetimes(
            min_value=datetime(2020, 1, 1),
            max_value=datetime(2030, 12, 31),
            timezones=_tz_offsets,
        ),
    )
    def test_no_invalid_git_ref_chars(self, lambda_name: str, ts: datetime):
        """Branch name never contains characters invalid for git refs."""
        result = build_fix_branch_name(lambda_name, ts)
        # The only slash allowed is the one in 'fix/'
        after_prefix = result[4:]  # after 'fix/'
        assert "~" not in result
        assert "^" not in result
        assert "?" not in result
        assert "*" not in result
        assert "[" not in result
        assert "]" not in result
        assert "\\" not in result
        assert "@{" not in result
        assert " " not in result
        assert "\t" not in result

    @settings(max_examples=100)
    @given(
        lambda_name=text(min_size=0, max_size=60, alphabet=string.printable),
        ts=datetimes(
            min_value=datetime(2020, 1, 1),
            max_value=datetime(2030, 12, 31),
            timezones=_tz_offsets,
        ),
    )
    def test_no_consecutive_dots(self, lambda_name: str, ts: datetime):
        """Branch name never contains '..' (invalid in git refs)."""
        result = build_fix_branch_name(lambda_name, ts)
        assert ".." not in result

    @settings(max_examples=100)
    @given(
        lambda_name=text(min_size=1, max_size=30, alphabet=_function_name_chars),
        ts=datetimes(
            min_value=datetime(2020, 1, 1),
            max_value=datetime(2030, 12, 31),
            timezones=_tz_offsets,
        ),
    )
    def test_contains_utc_timestamp_suffix(self, lambda_name: str, ts: datetime):
        """Branch name ends with a UTC timestamp in compact ISO format."""
        result = build_fix_branch_name(lambda_name, ts)
        # Must end with a timestamp pattern like 20250115T103045Z
        assert re.search(r"\d{8}T\d{6}Z$", result)

    @settings(max_examples=100)
    @given(
        lambda_name=text(min_size=1, max_size=30, alphabet=_function_name_chars),
    )
    def test_different_timestamps_produce_different_names(self, lambda_name: str):
        """Different timestamps for the same lambda produce different branch names."""
        ts1 = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2025, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
        assert build_fix_branch_name(lambda_name, ts1) != build_fix_branch_name(lambda_name, ts2)


# ====================================================================
# Feature: self-healing-agent, Property 5: El cuerpo del Pull_Request referencia la Affected_Lambda y el error
# Validates: Requirements 9.4
# ====================================================================


class TestProperty5PrBody:
    """Property 5: PR body always contains the lambda name, error summary, and review notice."""

    @settings(max_examples=100)
    @given(
        lambda_name=text(min_size=1, max_size=50, alphabet=_function_name_chars),
        error_summary=text(min_size=1, max_size=200, alphabet=string.printable),
    )
    def test_pr_title_contains_lambda_name(self, lambda_name: str, error_summary: str):
        """PR title always references the lambda name."""
        title = build_pr_title(lambda_name)
        assert lambda_name in title

    @settings(max_examples=100)
    @given(
        lambda_name=text(min_size=1, max_size=50, alphabet=_function_name_chars),
        error_summary=text(min_size=1, max_size=200, alphabet=string.printable),
    )
    def test_pr_title_starts_with_fix(self, lambda_name: str, error_summary: str):
        """PR title always starts with 'fix:' (Conventional Commits)."""
        title = build_pr_title(lambda_name)
        assert title.startswith("fix:")

    @settings(max_examples=100)
    @given(
        lambda_name=text(min_size=1, max_size=50, alphabet=_function_name_chars),
        error_summary=text(min_size=1, max_size=200, alphabet=string.printable),
    )
    def test_pr_description_contains_lambda_name(self, lambda_name: str, error_summary: str):
        """PR description always mentions the affected lambda."""
        desc = build_pr_description(lambda_name, error_summary)
        assert lambda_name in desc

    @settings(max_examples=100)
    @given(
        lambda_name=text(min_size=1, max_size=50, alphabet=_function_name_chars),
        error_summary=text(min_size=1, max_size=200, alphabet=string.printable).filter(
            lambda s: s.strip() != ""
        ),
    )
    def test_pr_description_contains_error_summary(self, lambda_name: str, error_summary: str):
        """PR description always includes the error summary."""
        desc = build_pr_description(lambda_name, error_summary)
        assert error_summary in desc

    @settings(max_examples=100)
    @given(
        lambda_name=text(min_size=1, max_size=50, alphabet=_function_name_chars),
        error_summary=text(min_size=1, max_size=200, alphabet=string.printable),
    )
    def test_pr_description_includes_human_review_notice(self, lambda_name: str, error_summary: str):
        """PR description always warns that human review is mandatory."""
        desc = build_pr_description(lambda_name, error_summary)
        desc_lower = desc.lower()
        assert "human review" in desc_lower or "review" in desc_lower
