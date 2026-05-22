from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - handled by install docs
    load_dotenv = None  # type: ignore[assignment]


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-pro"


class ConfigError(RuntimeError):
    """Raised when required runtime configuration is missing or invalid."""


@dataclass(frozen=True)
class DeepSeekConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    temperature: float = 0.3
    max_tokens: int = 4096
    timeout_seconds: float = 90.0
    retries: int = 3


def load_environment(env_file: Optional[Path] = None) -> None:
    """Load variables from .env when python-dotenv is installed."""
    if load_dotenv is None:
        return

    if env_file is not None:
        load_dotenv(env_file)
        return

    load_dotenv()


def load_deepseek_config(
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout_seconds: Optional[float] = None,
    retries: Optional[int] = None,
    require_api_key: bool = True,
) -> DeepSeekConfig:
    """Build DeepSeek config from environment plus CLI overrides."""
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if require_api_key and not api_key:
        raise ConfigError(
            "DEEPSEEK_API_KEY is not set. Put it in the environment or a .env file."
        )

    base_url = os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    env_model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

    return DeepSeekConfig(
        api_key=api_key,
        base_url=base_url,
        model=model or env_model,
        temperature=temperature if temperature is not None else 0.3,
        max_tokens=max_tokens if max_tokens is not None else 4096,
        timeout_seconds=timeout_seconds if timeout_seconds is not None else 90.0,
        retries=retries if retries is not None else 3,
    )
