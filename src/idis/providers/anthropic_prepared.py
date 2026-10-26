"""Pure description of one Anthropic text request before it is sent."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from idis.providers.anthropic_capabilities import RequestTransport


@dataclass(frozen=True, slots=True)
class PreparedLlmRequest:
    """Keyword arguments and reservation inputs for one text request.

    ``params`` are the keywords sent to ``messages.create`` or
    ``messages.stream``. The streaming helper adds ``stream: true`` itself.
    ``payload_bytes`` is the UTF-8 length of the canonical JSON of ``params``
    (sorted keys, compact separators, ``ensure_ascii=False``).

    Attributes:
        params: SDK keyword arguments, without the stream flag.
        transport: ``create`` or ``stream``.
        model: Exact model id.
        max_tokens: Output-token ceiling, including thinking.
        payload_bytes: Canonical UTF-8 size of ``params``.
    """

    params: Mapping[str, Any]
    transport: RequestTransport
    model: str
    max_tokens: int
    payload_bytes: int
