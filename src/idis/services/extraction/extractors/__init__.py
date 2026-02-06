"""Extractor implementations for claim extraction pipeline.

Provides:
- Extractor protocol (re-exported from service.py)
- LLMClaimExtractor: LLM-backed claim extractor
- LLMClient protocol + DeterministicLLMClient for testing
"""

from idis.services.extraction.extractors.base import Extractor
from idis.services.extraction.extractors.claim_extractor import LLMClaimExtractor
from idis.services.extraction.extractors.llm_client import (
    DeterministicLLMClient,
    LLMClient,
)

__all__ = [
    "DeterministicLLMClient",
    "Extractor",
    "LLMClaimExtractor",
    "LLMClient",
]
