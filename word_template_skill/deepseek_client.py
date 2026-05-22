from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from config import DeepSeekConfig


@dataclass
class GenerationResult:
    section_title: str
    prompt: list[dict[str, str]]
    response: str = ""
    usage: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    elapsed_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.response.strip())

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "section_title": self.section_title,
            "prompt": self.prompt,
            "response": self.response,
            "usage": self.usage,
            "error": self.error,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


class DeepSeekClient:
    """Small wrapper around the OpenAI-compatible Chat Completions API."""

    def __init__(self, config: DeepSeekConfig, logger: Optional[logging.Logger] = None) -> None:
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - install-time concern
            raise RuntimeError(
                "The openai package is required. Install dependencies with pip install -r requirements.txt"
            ) from exc

        self._client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            max_retries=0,
        )

    def generate_section(
        self,
        *,
        section_title: str,
        messages: list[dict[str, str]],
    ) -> GenerationResult:
        started = time.monotonic()
        last_error: Optional[BaseException] = None

        for attempt in range(1, self.config.retries + 1):
            try:
                self.logger.info("Generating section %s, attempt %s", section_title, attempt)
                response = self._client.chat.completions.create(
                    model=self.config.model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                )
                content = response.choices[0].message.content or ""
                usage = _usage_to_dict(getattr(response, "usage", None))
                elapsed = time.monotonic() - started
                self.logger.info("Generated section %s in %.2fs", section_title, elapsed)
                return GenerationResult(
                    section_title=section_title,
                    prompt=messages,
                    response=content,
                    usage=usage,
                    elapsed_seconds=elapsed,
                )
            except Exception as exc:  # noqa: BLE001 - log and retry all API failures
                last_error = exc
                self.logger.exception(
                    "DeepSeek generation failed for section %s on attempt %s",
                    section_title,
                    attempt,
                )
                if attempt < self.config.retries:
                    time.sleep(min(2 ** (attempt - 1), 8))

        elapsed = time.monotonic() - started
        return GenerationResult(
            section_title=section_title,
            prompt=messages,
            error=str(last_error) if last_error else "Unknown DeepSeek error",
            elapsed_seconds=elapsed,
        )


def _usage_to_dict(usage: Any) -> Optional[dict[str, Any]]:
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if isinstance(usage, dict):
        return usage
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }
