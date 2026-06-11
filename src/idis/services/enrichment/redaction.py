"""Secret redaction for enrichment URL-key providers (Slice86, decision D-H).

Some provider APIs (FRED, Finnhub, FMP) only accept the API key as a URL query parameter, so
key-bearing URLs can appear inside transport error strings and httpx's request log line. This
module provides the two containment layers:

  - ``redact_secret_params``: scrub api_key/apikey/token query values from any text (used when
    connectors build ``*FetchError`` messages, keeping the URL for debugging minus the secret);
  - ``install_httpx_redaction_filter``: an idempotent logging filter on the ``httpx`` logger
    that scrubs the same values from every emitted record (httpx logs the full request URL at
    INFO whenever an operator attaches a handler).
"""

from __future__ import annotations

import logging
import re

_SECRET_QUERY_PARAM_PATTERN = re.compile(
    r"(?i)\b(api_key|apikey|token)=[^&\s\"']+",
)

_REDACTED_REPLACEMENT = r"\1=[redacted]"


def redact_secret_params(text: str) -> str:
    """Return ``text`` with api_key/apikey/token query values replaced by ``[redacted]``."""
    return _SECRET_QUERY_PARAM_PATTERN.sub(_REDACTED_REPLACEMENT, text)


class SecretParamRedactionFilter(logging.Filter):
    """Logging filter that scrubs secret query values from emitted records."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact_secret_params(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def install_httpx_redaction_filter() -> None:
    """Attach the redaction filter to the ``httpx`` logger exactly once (idempotent)."""
    logger = logging.getLogger("httpx")
    if not any(isinstance(existing, SecretParamRedactionFilter) for existing in logger.filters):
        logger.addFilter(SecretParamRedactionFilter())
