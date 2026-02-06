"""Provider-agnostic LLM client interface + deterministic test stub.

LLMClient: Protocol for making LLM calls (provider-agnostic).
DeterministicLLMClient: Returns pre-built valid JSON for testing.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    """Provider-agnostic interface for LLM calls."""

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        """Make an LLM call and return the raw response text.

        Args:
            prompt: The full prompt text to send.
            json_mode: If True, request JSON-formatted output.

        Returns:
            Raw response string from the LLM.
        """
        ...


class DeterministicLLMClient:
    """Deterministic LLM client for testing — returns valid JSON based on input.

    Parses the chunk content from the prompt and generates structured claims
    deterministically. No external calls are made.
    """

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        """Return deterministic claim JSON based on prompt content.

        Args:
            prompt: The full prompt text (includes chunk content).
            json_mode: Ignored; always returns JSON.

        Returns:
            JSON string containing an array of extracted claims.
        """
        claims = self._extract_from_prompt(prompt)
        return json.dumps(claims, sort_keys=True)

    def _extract_from_prompt(self, prompt: str) -> list[dict[str, Any]]:
        """Parse prompt content and generate deterministic claims.

        Args:
            prompt: Full prompt text.

        Returns:
            List of claim dicts matching the output schema.
        """
        content_marker = "Content:\n"
        content_start = prompt.find(content_marker)
        if content_start == -1:
            return []

        content = prompt[content_start + len(content_marker) :].strip()
        if not content:
            return []

        lines = [line.strip() for line in content.split("\n") if line.strip()]
        if not lines:
            return []

        claims: list[dict[str, Any]] = []
        for line in lines:
            claim_class = self._classify(line)
            claims.append(
                {
                    "claim_text": line,
                    "claim_class": claim_class,
                    "source_locator": {},
                    "confidence": 0.85,
                    "requires_review": False,
                }
            )

        return claims

    def _classify(self, text: str) -> str:
        """Classify text into a claim class deterministically.

        Args:
            text: Claim text to classify.

        Returns:
            Claim class string.
        """
        text_lower = text.lower()
        if any(kw in text_lower for kw in ["revenue", "arr", "mrr", "margin", "$", "funding"]):
            return "FINANCIAL"
        if any(kw in text_lower for kw in ["customer", "client", "user", "subscriber"]):
            return "TRACTION"
        if any(kw in text_lower for kw in ["tam", "sam", "som", "market size"]):
            return "MARKET_SIZE"
        if any(kw in text_lower for kw in ["competitor", "competition"]):
            return "COMPETITION"
        if any(kw in text_lower for kw in ["team", "employee", "founder", "ceo"]):
            return "TEAM"
        return "OTHER"
