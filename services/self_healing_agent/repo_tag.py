"""Parsing and validation of the github-repo Lambda tag."""
from dataclasses import dataclass

GITHUB_REPO_TAG_KEY = "github-repo"


class InvalidRepoTagError(Exception):
    """Raised when the github-repo tag is missing or has an invalid format."""

    pass


@dataclass(frozen=True, slots=True)
class RepoRef:
    """Validated reference to a GitHub repository (owner/repo)."""

    owner: str
    repo: str

    def __str__(self) -> str:
        return f"{self.owner}/{self.repo}"


def parse_repo_tag(tags: dict) -> RepoRef:
    """Parse and validate the github-repo tag from a Lambda's tag set.

    Args:
        tags: Dictionary of Lambda tags (key -> value).

    Returns:
        A validated RepoRef with owner and repo.

    Raises:
        InvalidRepoTagError: If the tag is missing, empty, malformed,
            or contains whitespace in owner/repo.
    """
    if GITHUB_REPO_TAG_KEY not in tags:
        raise InvalidRepoTagError(
            f"Tag '{GITHUB_REPO_TAG_KEY}' not found in Lambda tags. "
            f"Available tags: {list(tags.keys())}"
        )

    value = tags[GITHUB_REPO_TAG_KEY]

    if not value or not value.strip():
        raise InvalidRepoTagError(
            f"Tag '{GITHUB_REPO_TAG_KEY}' is empty or whitespace-only."
        )

    # Must contain exactly one '/'
    parts = value.split("/")
    if len(parts) != 2:
        raise InvalidRepoTagError(
            f"Tag '{GITHUB_REPO_TAG_KEY}' must have format 'owner/repo', "
            f"got '{value}' ({len(parts) - 1} slashes found, expected 1)."
        )

    owner, repo = parts

    if not owner or owner != owner.strip():
        raise InvalidRepoTagError(
            f"Owner part is empty or contains leading/trailing whitespace: '{owner}'."
        )

    if not repo or repo != repo.strip():
        raise InvalidRepoTagError(
            f"Repo part is empty or contains leading/trailing whitespace: '{repo}'."
        )

    # Check for internal whitespace
    if " " in owner or "\t" in owner:
        raise InvalidRepoTagError(
            f"Owner contains whitespace: '{owner}'."
        )

    if " " in repo or "\t" in repo:
        raise InvalidRepoTagError(
            f"Repo contains whitespace: '{repo}'."
        )

    return RepoRef(owner=owner, repo=repo)
