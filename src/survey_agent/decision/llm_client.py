"""
LLM Client — thin wrapper around the OpenAI SDK for DeepSeek V4 Pro API calls.

Avoids heavy frameworks like LangChain. Just:
  1. Send system + user prompts
  2. Get back a structured JSON response
  3. Handle retries on failure

Supports both synchronous and asynchronous calling patterns.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from openai import AsyncOpenAI, OpenAI

from survey_agent.utils.logger import get_logger

if TYPE_CHECKING:
    from survey_agent.config import LLMConfig

logger = get_logger(__name__)


class LLMClient:
    """
    Minimal wrapper around OpenAI-compatible APIs (DeepSeek).

    Focuses on structured (JSON) output for the survey agent use case.

    Usage:
        client = LLMClient(config)
        result = await client.chat_json(
            system_prompt="You are a survey expert...",
            user_prompt="Fill this form: ..."
        )
    """

    def __init__(self, config: LLMConfig) -> None:
        if not config.api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY is not set. "
                "Set it in .env or as an environment variable."
            )

        self._config = config

        # Async client (primary)
        self._async_client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=float(config.request_timeout),
            max_retries=0,  # We handle retries ourselves
        )

        # Sync client (fallback)
        self._sync_client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=float(config.request_timeout),
            max_retries=0,
        )

    @property
    def model(self) -> str:
        return self._config.model

    # ------------------------------------------------------------------
    # Async API (primary)
    # ------------------------------------------------------------------

    async def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """
        Send a chat completion request and parse the response as JSON.

        Uses response_format={"type": "json_object"} for guaranteed JSON output.

        Args:
            system_prompt: System-level instructions.
            user_prompt: User message with page context + requirements.
            temperature: Override default temperature.
            max_tokens: Override default max tokens.

        Returns:
            Parsed JSON response as a Python dict.

        Raises:
            LLMError: If the LLM call fails after all retries.
        """
        temp = temperature if temperature is not None else self._config.temperature
        tokens = max_tokens if max_tokens is not None else self._config.max_tokens

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        last_error = None
        for attempt in range(1, self._config.max_retries + 1):
            try:
                response = await self._async_client.chat.completions.create(
                    model=self._config.model,
                    messages=messages,
                    temperature=temp,
                    max_tokens=tokens,
                    response_format={"type": "json_object"},
                )
                content = response.choices[0].message.content
                if content is None:
                    raise ValueError("LLM returned empty response.")

                usage = response.usage
                if usage:
                    logger.info(
                        f"LLM call: {usage.prompt_tokens} prompt + "
                        f"{usage.completion_tokens} completion = "
                        f"{usage.total_tokens} tokens"
                    )

                return json.loads(content)

            except json.JSONDecodeError as e:
                last_error = e
                logger.warning(
                    f"LLM returned invalid JSON (attempt {attempt}/{self._config.max_retries}): {e}"
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    f"LLM call failed (attempt {attempt}/{self._config.max_retries}): {e}"
                )

            if attempt < self._config.max_retries:
                delay = 2.0 ** attempt  # Exponential backoff: 2s, 4s, 8s
                logger.info(f"Retrying in {delay:.0f}s...")
                await asyncio.sleep(delay)

        raise LLMError(
            f"LLM call failed after {self._config.max_retries} attempts. "
            f"Last error: {last_error}"
        )

    async def chat_text(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """
        Send a chat completion and return raw text (for non-JSON use cases).

        Args:
            system_prompt: System instructions.
            user_prompt: User message.
            temperature: Override temperature.
            max_tokens: Override max tokens.

        Returns:
            Raw text response from the LLM.
        """
        temp = temperature if temperature is not None else self._config.temperature
        tokens = max_tokens if max_tokens is not None else self._config.max_tokens

        response = await self._async_client.chat.completions.create(
            model=self._config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temp,
            max_tokens=tokens,
        )
        return response.choices[0].message.content or ""

    # ------------------------------------------------------------------
    # Sync API (for non-async contexts, e.g., debugging)
    # ------------------------------------------------------------------

    def chat_json_sync(
        self,
        system_prompt: str,
        user_prompt: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Synchronous version of chat_json()."""
        temp = kwargs.get("temperature", self._config.temperature)
        tokens = kwargs.get("max_tokens", self._config.max_tokens)

        response = self._sync_client.chat.completions.create(
            model=self._config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temp,
            max_tokens=tokens,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        if content is None:
            raise LLMError("LLM returned empty response.")
        return json.loads(content)


class LLMError(Exception):
    """Raised when the LLM call fails irrecoverably."""
    pass
