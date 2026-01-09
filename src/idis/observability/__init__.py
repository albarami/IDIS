"""IDIS Observability module.

Provides OpenTelemetry tracing baseline per v6.3 Tech Stack requirements.
"""

from idis.observability.tracing import configure_tracing, get_current_trace_id

__all__ = ["configure_tracing", "get_current_trace_id"]
