"""Unit tests for edge cases in repo_tag.py.

Covers: tag absent, empty, without '/', multiple '/', owner/repo empty.
Requirements: 6.5
"""
import pytest

from services.self_healing_agent.repo_tag import (
    parse_repo_tag,
    RepoRef,
    InvalidRepoTagError,
    GITHUB_REPO_TAG_KEY,
)


class TestRepoTagEdgeCases:
    """Edge case tests for parse_repo_tag."""

    # --- Tag key absent ---

    def test_empty_dict_raises(self):
        """Empty tag dict raises InvalidRepoTagError."""
        with pytest.raises(InvalidRepoTagError, match="not found"):
            parse_repo_tag({})

    def test_other_tags_present_but_github_repo_missing(self):
        """Tags dict without github-repo key raises."""
        with pytest.raises(InvalidRepoTagError, match="not found"):
            parse_repo_tag({"Project": "foo", "Environment": "dev"})

    def test_case_sensitive_key(self):
        """Key matching is case-sensitive: 'Github-Repo' != 'github-repo'."""
        with pytest.raises(InvalidRepoTagError, match="not found"):
            parse_repo_tag({"Github-Repo": "owner/repo"})

    def test_key_with_leading_space(self):
        """Key ' github-repo' (leading space) is not the same as 'github-repo'."""
        with pytest.raises(InvalidRepoTagError, match="not found"):
            parse_repo_tag({" github-repo": "owner/repo"})

    # --- Tag value empty ---

    def test_empty_string_value(self):
        """Empty string value raises."""
        with pytest.raises(InvalidRepoTagError, match="empty"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: ""})

    def test_single_space_value(self):
        """Single space value raises."""
        with pytest.raises(InvalidRepoTagError, match="empty"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: " "})

    def test_multiple_spaces_value(self):
        """Multiple spaces value raises."""
        with pytest.raises(InvalidRepoTagError, match="empty"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: "    "})

    def test_tab_only_value(self):
        """Tab-only value raises."""
        with pytest.raises(InvalidRepoTagError, match="empty"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: "\t"})

    def test_newline_only_value(self):
        """Newline-only value raises."""
        with pytest.raises(InvalidRepoTagError, match="empty"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: "\n"})

    # --- Without '/' ---

    def test_simple_string_no_slash(self):
        """Plain string without slash raises."""
        with pytest.raises(InvalidRepoTagError, match="1"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: "justareponame"})

    def test_dots_but_no_slash(self):
        """String with dots but no slash raises."""
        with pytest.raises(InvalidRepoTagError, match="1"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: "owner.repo"})

    def test_backslash_instead_of_slash(self):
        """Backslash is not treated as separator."""
        with pytest.raises(InvalidRepoTagError, match="1"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: "owner\\repo"})

    # --- Multiple '/' ---

    def test_two_slashes(self):
        """Two slashes (three parts) raises."""
        with pytest.raises(InvalidRepoTagError, match="slashes"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: "a/b/c"})

    def test_three_slashes(self):
        """Three slashes raises."""
        with pytest.raises(InvalidRepoTagError, match="slashes"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: "a/b/c/d"})

    def test_trailing_slash_makes_two(self):
        """'owner/repo/' has 2 slashes (splits to 3 parts)."""
        with pytest.raises(InvalidRepoTagError, match="slashes"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: "owner/repo/"})

    def test_leading_and_trailing_slash(self):
        """'/owner/repo/' has 3 slashes."""
        with pytest.raises(InvalidRepoTagError, match="slashes"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: "/owner/repo/"})

    # --- Owner empty ---

    def test_slash_repo_empty_owner(self):
        """'/repo' means empty owner."""
        with pytest.raises(InvalidRepoTagError, match="Owner"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: "/repo"})

    # --- Repo empty ---

    def test_owner_slash_empty_repo(self):
        """'owner/' means empty repo."""
        with pytest.raises(InvalidRepoTagError, match="Repo"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: "owner/"})

    # --- Both empty ---

    def test_just_slash(self):
        """'/' means both owner and repo are empty."""
        with pytest.raises(InvalidRepoTagError, match="Owner"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: "/"})

    # --- Whitespace in owner/repo ---

    def test_owner_with_space(self):
        """Owner with internal space raises."""
        with pytest.raises(InvalidRepoTagError, match="Owner"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: "ow ner/repo"})

    def test_repo_with_space(self):
        """Repo with internal space raises."""
        with pytest.raises(InvalidRepoTagError, match="Repo"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: "owner/re po"})

    def test_owner_leading_space(self):
        """Owner with leading space raises."""
        with pytest.raises(InvalidRepoTagError, match="Owner"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: " owner/repo"})

    def test_repo_trailing_space(self):
        """Repo with trailing space raises."""
        with pytest.raises(InvalidRepoTagError, match="Repo"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: "owner/repo "})

    def test_owner_with_tab(self):
        """Owner with tab raises."""
        with pytest.raises(InvalidRepoTagError, match="Owner"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: "ow\tner/repo"})

    def test_repo_with_tab(self):
        """Repo with tab raises."""
        with pytest.raises(InvalidRepoTagError, match="Repo"):
            parse_repo_tag({GITHUB_REPO_TAG_KEY: "owner/re\tpo"})

    # --- Valid cases (sanity check) ---

    def test_valid_simple(self):
        """Standard owner/repo is valid."""
        result = parse_repo_tag({GITHUB_REPO_TAG_KEY: "David1187/hackaton-kiro-agente-cloudwatch"})
        assert result == RepoRef(owner="David1187", repo="hackaton-kiro-agente-cloudwatch")

    def test_valid_with_dots_and_dashes(self):
        """Names with dots and dashes are valid."""
        result = parse_repo_tag({GITHUB_REPO_TAG_KEY: "my-org.name/my-repo.v2"})
        assert result.owner == "my-org.name"
        assert result.repo == "my-repo.v2"

    def test_valid_single_char(self):
        """Single char owner and repo are valid."""
        result = parse_repo_tag({GITHUB_REPO_TAG_KEY: "a/b"})
        assert result == RepoRef(owner="a", repo="b")
