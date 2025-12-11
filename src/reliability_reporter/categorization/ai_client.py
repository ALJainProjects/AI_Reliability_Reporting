"""AI client abstraction for OpenAI and Anthropic."""

import json
import logging
from abc import ABC, abstractmethod

from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class AIClient(ABC):
    """Abstract base class for AI providers."""

    @abstractmethod
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """
        Generate a response from the AI model.

        Args:
            system_prompt: System instructions
            user_prompt: User message/query
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum response tokens

        Returns:
            Model response as string
        """
        pass

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> dict | list:
        """
        Generate a JSON response from the AI model.

        Args:
            system_prompt: System instructions
            user_prompt: User message/query
            temperature: Sampling temperature
            max_tokens: Maximum response tokens

        Returns:
            Parsed JSON response
        """
        response = await self.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # Try to extract JSON from response
        return self._parse_json_response(response)

    def _parse_json_response(self, response: str) -> dict | list:
        """Parse JSON from AI response, handling markdown code blocks."""
        # Try direct JSON parse first
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # Try to extract from markdown code block
        if "```json" in response:
            start = response.find("```json") + 7
            end = response.find("```", start)
            if end > start:
                json_str = response[start:end].strip()
                return json.loads(json_str)

        # Try to extract from generic code block
        if "```" in response:
            start = response.find("```") + 3
            # Skip language identifier if present
            newline = response.find("\n", start)
            if newline > start:
                start = newline + 1
            end = response.find("```", start)
            if end > start:
                json_str = response[start:end].strip()
                return json.loads(json_str)

        # Try to find JSON array or object
        for start_char, end_char in [("[", "]"), ("{", "}")]:
            start = response.find(start_char)
            end = response.rfind(end_char)
            if start != -1 and end > start:
                json_str = response[start : end + 1]
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    continue

        raise ValueError(f"Could not parse JSON from response: {response[:200]}...")

    async def close(self) -> None:
        """Close any resources (override if needed)."""
        pass


class OpenAIClient(AIClient):
    """OpenAI API client."""

    def __init__(self, api_key: str, model: str = "gpt-4o"):
        """
        Initialize OpenAI client.

        Args:
            api_key: OpenAI API key
            model: Model name (default: gpt-4o)
        """
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("openai package required. Install with: pip install openai")

        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """Generate response using OpenAI API."""
        logger.debug(f"OpenAI request: model={self.model}, temp={temperature}")

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )

        return response.choices[0].message.content or ""

    async def close(self) -> None:
        """Close the OpenAI client."""
        await self.client.close()


class AnthropicClient(AIClient):
    """Anthropic API client."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        """
        Initialize Anthropic client.

        Args:
            api_key: Anthropic API key
            model: Model name (default: claude-sonnet-4-20250514)
        """
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            raise ImportError(
                "anthropic package required. Install with: pip install anthropic"
            )

        self.client = AsyncAnthropic(api_key=api_key)
        self.model = model

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """Generate response using Anthropic API."""
        logger.debug(f"Anthropic request: model={self.model}, temp={temperature}")

        response = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=temperature,
        )

        # Extract text from response
        text_parts = []
        for block in response.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)

        return "".join(text_parts)

    async def close(self) -> None:
        """Close the Anthropic client."""
        await self.client.close()


def create_ai_client(
    provider: str, api_key: str, model: str | None = None
) -> AIClient:
    """
    Factory function to create an AI client.

    Args:
        provider: Provider name ("openai" or "anthropic")
        api_key: API key for the provider
        model: Optional model override

    Returns:
        Configured AI client
    """
    if provider == "openai":
        return OpenAIClient(api_key, model=model or "gpt-4o")
    elif provider == "anthropic":
        return AnthropicClient(api_key, model=model or "claude-sonnet-4-20250514")
    else:
        raise ValueError(f"Unknown AI provider: {provider}")
