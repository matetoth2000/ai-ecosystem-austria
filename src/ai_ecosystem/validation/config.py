"""Configuration and API-key helpers for validation workflows.

This module does not create OpenAI clients or perform API calls. It only
centralizes validation configuration and credential checks for future runners.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ai_ecosystem.db.init_db import find_project_root, load_config


class OpenAIAPIKeyMissingError(RuntimeError):
    """Raised when validation needs an OpenAI API key but none is configured."""


def load_validation_config(project_root: Path | None = None) -> dict[str, Any]:
    """Load the validation section from config/config.yaml."""
    root = project_root or find_project_root()
    config = load_config(root)
    return dict(config.get("validation", {}))


def _load_python_dotenv(project_root: Path) -> None:
    """Load .env with python-dotenv when the optional dependency is installed."""
    env_path = project_root / ".env"
    if not env_path.is_file():
        return

    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]
    except ImportError:
        return

    load_dotenv(env_path, override=False)


def _read_openai_key_from_env_file(project_root: Path) -> str | None:
    """Read OPENAI_API_KEY from a simple local .env file without exposing it."""
    env_path = project_root / ".env"
    if not env_path.is_file():
        return None

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() != "OPENAI_API_KEY":
            continue
        value = value.strip().strip("\"'")
        return value or None
    return None


def load_openai_api_key(project_root: Path | None = None) -> str:
    """Return OPENAI_API_KEY from the environment or local .env.

    Raises a clear error if the key is missing. The returned value should not be
    logged or written to project artifacts.
    """
    root = project_root or find_project_root()
    _load_python_dotenv(root)

    api_key = os.environ.get("OPENAI_API_KEY") or _read_openai_key_from_env_file(root)
    if api_key:
        return api_key

    raise OpenAIAPIKeyMissingError(
        "Missing OPENAI_API_KEY. Set it in the environment or in a local .env file "
        "that is ignored by Git. Do not commit API keys."
    )
