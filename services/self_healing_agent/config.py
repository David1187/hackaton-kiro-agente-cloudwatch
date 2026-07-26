"""Configuration utilities for the Self-Healing Agent."""
from collections.abc import Mapping

DEFAULT_MODEL_ID = "qwen.qwen3-coder-30b-a3b-v1:0"


def resolve_model_id(env: Mapping[str, str]) -> str:
    """Resolve the Bedrock model ID from environment variables.

    Returns env["MODEL_ID"] if present and non-empty, otherwise falls back
    to the default model. The model ID is never hardcoded elsewhere in the
    codebase — this is the single source of truth.
    """
    model_id = env.get("MODEL_ID", "").strip()
    if model_id:
        return model_id
    return DEFAULT_MODEL_ID
