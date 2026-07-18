"""
Tests for the LLM client — mock the OpenAI SDK to verify retry logic,
JSON parsing, and error handling without real API calls.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from survey_agent.decision.llm_client import LLMClient, LLMError
from survey_agent.config import LLMConfig


class TestLLMClient:
    """Test LLMClient with mocked OpenAI SDK."""

    @pytest.fixture
    def llm_config(self) -> LLMConfig:
        """Create a test LLM config."""
        return LLMConfig(
            api_key="test-api-key",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-v4-pro",
            max_tokens=4096,
            temperature=0.0,
            request_timeout=30,
            max_retries=2,
        )

    @pytest.fixture
    def mock_openai_response(self) -> MagicMock:
        """Create a mock OpenAI chat completion response."""
        mock = MagicMock()
        mock.choices = [MagicMock()]
        mock.choices[0].message = MagicMock()
        mock.choices[0].message.content = json.dumps({
            "thought": "Test thought",
            "status": "CONTINUE",
            "actions": [{"type": "click", "ui_id": "ui-id-0", "reason": "test"}],
        })
        mock.usage = MagicMock()
        mock.usage.prompt_tokens = 100
        mock.usage.completion_tokens = 50
        mock.usage.total_tokens = 150
        return mock

    # ------------------------------------------------------------------
    # Successful call
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_chat_json_success(
        self, llm_config: LLMConfig, mock_openai_response: MagicMock
    ) -> None:
        """A successful LLM call should return the parsed JSON dict."""
        with patch(
            "survey_agent.decision.llm_client.AsyncOpenAI"
        ) as MockAsyncOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(
                return_value=mock_openai_response
            )
            MockAsyncOpenAI.return_value = mock_client

            client = LLMClient(llm_config)
            result = await client.chat_json(
                system_prompt="You are a survey expert.",
                user_prompt="Fill this form.",
            )

        assert result["thought"] == "Test thought"
        assert result["status"] == "CONTINUE"
        assert result["actions"][0]["type"] == "click"

    # ------------------------------------------------------------------
    # Retry on failure
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_retry_on_api_error(
        self, llm_config: LLMConfig, mock_openai_response: MagicMock
    ) -> None:
        """When the first call fails, it should retry and succeed on the second."""
        with patch(
            "survey_agent.decision.llm_client.AsyncOpenAI"
        ) as MockAsyncOpenAI:
            mock_client = MagicMock()
            # First call fails, second succeeds
            mock_client.chat.completions.create = AsyncMock(
                side_effect=[
                    Exception("API timeout"),
                    mock_openai_response,
                ]
            )
            MockAsyncOpenAI.return_value = mock_client

            client = LLMClient(llm_config)
            result = await client.chat_json(
                system_prompt="Test",
                user_prompt="Test",
            )

        assert result["thought"] == "Test thought"
        assert mock_client.chat.completions.create.call_count == 2

    # ------------------------------------------------------------------
    # Exhausted retries
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(
        self, llm_config: LLMConfig
    ) -> None:
        """Should raise LLMError after all retries are exhausted."""
        with patch(
            "survey_agent.decision.llm_client.AsyncOpenAI"
        ) as MockAsyncOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(
                side_effect=Exception("Persistent failure")
            )
            MockAsyncOpenAI.return_value = mock_client

            client = LLMClient(llm_config)
            with pytest.raises(LLMError, match="LLM call failed"):
                await client.chat_json(
                    system_prompt="Test",
                    user_prompt="Test",
                )

        # Called max_retries times (2)
        assert mock_client.chat.completions.create.call_count == 2

    # ------------------------------------------------------------------
    # Invalid JSON response
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_retry_on_invalid_json(
        self, llm_config: LLMConfig, mock_openai_response: MagicMock
    ) -> None:
        """When the LLM returns invalid JSON, it should retry."""
        with patch(
            "survey_agent.decision.llm_client.AsyncOpenAI"
        ) as MockAsyncOpenAI:
            mock_client = MagicMock()
            invalid_response = MagicMock()
            invalid_response.choices = [MagicMock()]
            invalid_response.choices[0].message = MagicMock()
            invalid_response.choices[0].message.content = "not valid json {{"
            invalid_response.usage = None

            mock_client.chat.completions.create = AsyncMock(
                side_effect=[
                    invalid_response,      # Invalid JSON → retry
                    mock_openai_response,  # Valid → success
                ]
            )
            MockAsyncOpenAI.return_value = mock_client

            client = LLMClient(llm_config)
            result = await client.chat_json(
                system_prompt="Test",
                user_prompt="Test",
            )

        assert result["thought"] == "Test thought"
        assert mock_client.chat.completions.create.call_count == 2

    # ------------------------------------------------------------------
    # Empty response handling
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_retry_on_none_content(
        self, llm_config: LLMConfig, mock_openai_response: MagicMock
    ) -> None:
        """When the response content is None, it should retry."""
        with patch(
            "survey_agent.decision.llm_client.AsyncOpenAI"
        ) as MockAsyncOpenAI:
            mock_client = MagicMock()
            none_response = MagicMock()
            none_response.choices = [MagicMock()]
            none_response.choices[0].message = MagicMock()
            none_response.choices[0].message.content = None
            none_response.usage = None

            mock_client.chat.completions.create = AsyncMock(
                side_effect=[
                    none_response,         # None content → retry
                    mock_openai_response,  # Valid → success
                ]
            )
            MockAsyncOpenAI.return_value = mock_client

            client = LLMClient(llm_config)
            result = await client.chat_json(
                system_prompt="Test",
                user_prompt="Test",
            )

        assert result["thought"] == "Test thought"

    # ------------------------------------------------------------------
    # Missing API key
    # ------------------------------------------------------------------

    def test_missing_api_key_raises_valueerror(self) -> None:
        """Should raise ValueError if no API key is provided."""
        config = LLMConfig(
            api_key="",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-v4-pro",
        )
        with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
            LLMClient(config)
