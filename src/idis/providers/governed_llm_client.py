"""Governed text client: reserve, call, then consume or refund by typed outcome.

Nothing in this module selects the wrapper. ``admit_planning_rates`` defaults
to false and no caller in ``src/`` passes true. Only a pinned resolver row is
admitted, and a non-official row is refused unless planning rates were admitted.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable
from uuid import uuid4

from idis.models.anthropic_price_catalog_v2 import (
    AnthropicCandidatePrice,
    candidate_price_row_sha256,
)
from idis.models.methodology_pricing import compute_anthropic_usd
from idis.providers.anthropic_failure import (
    LlmProviderOutcomeUnknown,
    LlmProviderRateLimited,
    LlmProviderRequestRejected,
    LlmResponseAmbiguous,
    LlmResponseContextExceeded,
    LlmResponseEmpty,
    LlmResponseIncomplete,
    LlmResponseRefused,
    LlmResponseTruncated,
    LlmResponseUnexpectedBlock,
)
from idis.providers.anthropic_result import LlmResult, LlmUsage
from idis.providers.budget import PostgresProviderBudgetStore, ProviderBudgetExceededError

_OFFICIAL_RATE_BASIS = "OFFICIAL"
_STANDARD_SERVICE_TIER = "standard"
_CACHE_USAGE_FIELDS = (
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "cache_creation_5m_input_tokens",
    "cache_creation_1h_input_tokens",
)
_RECEIVED_RESPONSE_ERRORS = (
    LlmResponseRefused,
    LlmResponseTruncated,
    LlmResponseContextExceeded,
    LlmResponseIncomplete,
    LlmResponseEmpty,
    LlmResponseUnexpectedBlock,
    LlmResponseAmbiguous,
)
_CONFIRMED_NOT_ACCEPTED = (LlmProviderRequestRejected, LlmProviderRateLimited)

logger = logging.getLogger(__name__)


class LlmPriceBasisNotAdmitted(RuntimeError):
    """The price row is not official and planning rates were not admitted."""

    code: ClassVar[str] = "LLM_PRICE_BASIS_NOT_ADMITTED"

    def __init__(self) -> None:
        super().__init__(self.code)


class LlmReservedContextExceeded(RuntimeError):
    """Reserved input bytes plus the output ceiling exceed the price-tier window."""

    code: ClassVar[str] = "LLM_RESERVED_CONTEXT_EXCEEDED"

    def __init__(self) -> None:
        super().__init__(self.code)


class LlmUsageUnpricedMode(RuntimeError):
    """The response reported cache usage or a service tier this price mode cannot bill."""

    code: ClassVar[str] = "LLM_USAGE_UNPRICED_MODE"

    def __init__(self) -> None:
        super().__init__(self.code)


class LlmBudgetSettlementFailed(RuntimeError):
    """The ledger did not accept consume or refund. The reservation stays reserved."""

    code: ClassVar[str] = "LLM_BUDGET_SETTLEMENT_FAILED"
    reservation_id: str
    intended: Literal["consume", "refund"]
    provider_code: str | None

    def __init__(
        self,
        *,
        reservation_id: str,
        intended: Literal["consume", "refund"],
        provider_code: str | None,
    ) -> None:
        self.reservation_id = reservation_id
        self.intended = intended
        self.provider_code = provider_code
        super().__init__(self.code)


class LlmPriceRowNotAdmitted(RuntimeError):
    """The price object is not the pinned resolver row."""

    code: ClassVar[str] = "LLM_PRICE_ROW_NOT_ADMITTED"

    def __init__(self) -> None:
        super().__init__(self.code)


class LlmReservationRecordFailed(RuntimeError):
    """The reservation record was not stored, so the provider was not called."""

    code: ClassVar[str] = "LLM_RESERVATION_RECORD_FAILED"

    def __init__(self) -> None:
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class ReservationRecord:
    """Identity of one reserved call, without the prompt text."""

    reservation_id: str
    tenant_id: str
    component_id: str
    policy_hash: str
    model: str
    max_tokens: int
    payload_bytes: int
    vector_sha256: str


class _PreparedCall(Protocol):
    """Reservation inputs taken from ``prepare_request``."""

    payload_bytes: int
    max_tokens: int
    model: str


class _PreparedTextClient(Protocol):
    """Inner client the wrapper can prepare and then call."""

    def prepare_request(self, prompt: str, *, json_mode: bool = False) -> _PreparedCall:
        """Return the request description without performing I/O."""
        ...

    def call_typed(self, prompt: str, *, json_mode: bool = False) -> LlmResult:
        """Send one prompt and return the typed completion."""
        ...


@runtime_checkable
class BudgetPort(Protocol):
    """Reserve, consume, and refund, each in its own durable unit of work."""

    def reserve(self, vector: Mapping[str, object]) -> bool:
        """Reserve ``vector``. False means the cap refused it."""
        ...

    def consume(self, vector: Mapping[str, object]) -> bool:
        """Consume the same vector that was reserved."""
        ...

    def refund(self, vector: Mapping[str, object]) -> bool:
        """Refund the same vector that was reserved."""
        ...


def _default_reservation_id() -> str:
    return str(uuid4())


class PostgresBudgetPort:
    """Adapt ``PostgresProviderBudgetStore`` to one transaction per ledger call.

    Each method opens ``connection_factory()``, sets the tenant, and calls the
    matching ``*_in_tx`` method. The transaction commits when the method
    returns and rolls back when it raises.
    """

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        tenant_id: str,
        global_max_calls: int,
    ) -> None:
        """Bind the connection factory, tenant, and construction-time call cap.

        Args:
            connection_factory: Callable returning a context manager for one
                transaction, as ``methodology_postgres_runtime`` uses.
            tenant_id: Tenant placed in ``set_tenant_local`` for each call.
            global_max_calls: Cap bound into ``PostgresProviderBudgetStore``.
        """
        self._connection_factory = connection_factory
        self._tenant_id = tenant_id
        self._global_max_calls = global_max_calls

    def reserve(self, vector: Mapping[str, object]) -> bool:
        """Reserve ``vector`` in its own transaction."""
        return self._run("reserve", vector)

    def consume(self, vector: Mapping[str, object]) -> bool:
        """Consume ``vector`` in its own transaction."""
        return self._run("consume", vector)

    def refund(self, vector: Mapping[str, object]) -> bool:
        """Refund ``vector`` in its own transaction."""
        return self._run("refund", vector)

    def _run(self, operation: str, vector: Mapping[str, object]) -> bool:
        from idis.persistence.db import set_tenant_local

        store = PostgresProviderBudgetStore(global_max_calls=self._global_max_calls)
        values = dict(vector)
        with self._connection_factory() as connection:
            set_tenant_local(connection, self._tenant_id)
            if operation == "reserve":
                return bool(store.reserve_in_tx(connection, **values))
            if operation == "consume":
                return bool(store.consume_in_tx(connection, **values))
            return bool(store.refund_in_tx(connection, **values))


class GovernedLlmClient:
    """``LLMClient`` that reserves a priced vector around one typed text call.

    The consumed or refunded vector is the reserved vector. Actual usage is
    not reconciled in this component. An unknown outcome leaves the
    reservation RESERVED.
    """

    def __init__(
        self,
        *,
        inner: _PreparedTextClient,
        port: BudgetPort,
        price: object,
        tenant_id: str,
        policy_hash_provider: Callable[[], str],
        global_max_calls: int,
        admitted_row_sha256: str,
        component_id: str = "anthropic",
        reservation_id_factory: Callable[[], str] = _default_reservation_id,
        admit_planning_rates: bool = False,
        on_reserved: Callable[[ReservationRecord], None] | None = None,
    ) -> None:
        """Bind the inner client, the budget port, and the resolved price row.

        Args:
            inner: Object with ``prepare_request`` and ``call_typed``.
            port: Reserve, consume, and refund port.
            price: Resolved candidate price row to pin before reserve.
            tenant_id: Tenant written into the governed vector.
            policy_hash_provider: Supplies the signed policy hash per call.
            global_max_calls: Construction-bound call cap copied into the vector.
            admitted_row_sha256: Hash the caller pinned for this price row.
            component_id: Ledger component. Defaults to ``anthropic``.
            reservation_id_factory: Supplies one reservation id per call.
            admit_planning_rates: When false, a row whose ``rate_basis`` is
                not ``OFFICIAL`` is refused before reserve. Defaults to false.
            on_reserved: Called after reserve and before the provider. When
                omitted, the wrapper logs the reservation id and vector hash.
        """
        self._inner = inner
        self._port = port
        self._price = price
        self._tenant_id = tenant_id
        self._policy_hash_provider = policy_hash_provider
        self._global_max_calls = global_max_calls
        self._admitted_row_sha256 = admitted_row_sha256
        self._component_id = component_id
        self._reservation_id_factory = reservation_id_factory
        self._admit_planning_rates = admit_planning_rates
        self._on_reserved = on_reserved

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        """Reserve, send, and consume or refund according to the typed outcome.

        Args:
            prompt: The full prompt text to send.
            json_mode: If True, instruct the model to return JSON.

        Returns:
            The text of one clean completion whose usage is standard and
            has no cache tokens.

        Raises:
            LlmRequestShapeRejected: ``prepare_request`` rejected the shape.
                No reservation is created.
            LlmPriceRowNotAdmitted: The price is not the pinned resolver row.
                No reservation is created.
            LlmPriceBasisNotAdmitted: The row is not official and planning
                rates were not admitted.
            LlmReservedContextExceeded: Reserved input plus output exceeds
                the row's context window.
            ProviderBudgetExceededError: Reserve returned false or the port
                raised that error. The provider is not called.
            LlmReservationRecordFailed: The reservation hook failed and the
                refund was accepted. The provider is not called.
            LlmBudgetSettlementFailed: Consume or refund returned false or
                raised. The text is not returned.
            LlmUsageUnpricedMode: The response was consumed and then refused
                because usage is outside the priced mode. The text is not
                returned.
            LlmProviderError: Re-raised after a successful refund or consume,
                or while the reservation stays reserved, according to the outcome.
        """
        prepared = self._inner.prepare_request(prompt, json_mode=json_mode)
        price = self._admitted_price()
        self._require_official_or_admitted_basis(price)
        vector = self._reservation_vector(prepared, price)
        self._reserve(vector)
        self._notify_reserved(prepared, vector)
        failure, result = self._call_typed(prompt, json_mode=json_mode)
        if failure is not None:
            self._settle_failure(failure, vector)
        if result is None:
            raise LlmProviderOutcomeUnknown(request_id=None) from None
        self._require_settlement("consume", vector, None)
        if _usage_unpriced(result.usage):
            raise LlmUsageUnpricedMode() from None
        return result.text

    def _admitted_price(self) -> AnthropicCandidatePrice:
        price = self._price
        if not isinstance(price, AnthropicCandidatePrice):
            raise LlmPriceRowNotAdmitted() from None
        digest = candidate_price_row_sha256(price)
        if digest != price.row_sha256 or digest != self._admitted_row_sha256:
            raise LlmPriceRowNotAdmitted() from None
        return price

    def _require_official_or_admitted_basis(self, price: AnthropicCandidatePrice) -> None:
        if price.rate_basis != _OFFICIAL_RATE_BASIS and not self._admit_planning_rates:
            raise LlmPriceBasisNotAdmitted() from None

    def _reservation_vector(
        self, prepared: _PreparedCall, price: AnthropicCandidatePrice
    ) -> dict[str, object]:
        input_tokens = prepared.payload_bytes
        output_tokens = prepared.max_tokens
        if input_tokens + output_tokens > price.context_window_limit:
            raise LlmReservedContextExceeded() from None
        usd = Decimal(
            compute_anthropic_usd(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                input_usd_per_million_tokens=price.input_usd_per_million_tokens,
                output_usd_per_million_tokens=price.output_usd_per_million_tokens,
            )
        )
        return {
            "tenant_id": self._tenant_id,
            "policy_hash": self._policy_hash_provider(),
            "component_id": self._component_id,
            "calls": 1,
            "usd": usd,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "embedding_tokens": 0,
            "global_max_calls": self._global_max_calls,
            "reservation_id": self._reservation_id_factory(),
        }

    def _reserve(self, vector: Mapping[str, object]) -> None:
        try:
            allowed = self._port.reserve(vector)
        except ProviderBudgetExceededError as exc:
            denied: ProviderBudgetExceededError | None = exc
            allowed = False
        else:
            denied = None
        if denied is not None:
            raise denied from None
        if not allowed:
            raise ProviderBudgetExceededError(
                provider=self._component_id,
                tenant_id=self._tenant_id,
                limit=self._global_max_calls,
                used=0,
            ) from None

    def _call_typed(
        self, prompt: str, *, json_mode: bool
    ) -> tuple[Exception | None, LlmResult | None]:
        try:
            return None, self._inner.call_typed(prompt, json_mode=json_mode)
        except Exception as exc:
            return exc, None

    def _notify_reserved(self, prepared: _PreparedCall, vector: Mapping[str, object]) -> None:
        digest = _vector_sha256(vector)
        reservation_id = str(vector["reservation_id"])
        hook = self._on_reserved
        if hook is None:
            logger.info("%s %s", reservation_id, digest)
            return
        record = ReservationRecord(
            reservation_id=reservation_id,
            tenant_id=self._tenant_id,
            component_id=self._component_id,
            policy_hash=str(vector["policy_hash"]),
            model=prepared.model,
            max_tokens=prepared.max_tokens,
            payload_bytes=prepared.payload_bytes,
            vector_sha256=digest,
        )
        recorded = True
        try:
            hook(record)
        except Exception:
            recorded = False
        if recorded:
            return
        self._require_settlement("refund", vector, None)
        raise LlmReservationRecordFailed() from None

    def _settle_failure(self, failure: Exception, vector: Mapping[str, object]) -> None:
        if isinstance(failure, _CONFIRMED_NOT_ACCEPTED):
            self._require_settlement("refund", vector, _outcome_code(failure))
            raise failure from None
        if isinstance(failure, LlmProviderOutcomeUnknown):
            raise failure from None
        if isinstance(failure, _RECEIVED_RESPONSE_ERRORS):
            self._require_settlement("consume", vector, _outcome_code(failure))
            raise failure from None
        raise failure from None

    def _require_settlement(
        self,
        intended: Literal["consume", "refund"],
        vector: Mapping[str, object],
        provider_code: str | None,
    ) -> None:
        refused = self._settlement_failure(intended, vector, provider_code)
        if refused is not None:
            raise refused from None

    def _settlement_failure(
        self,
        intended: Literal["consume", "refund"],
        vector: Mapping[str, object],
        provider_code: str | None,
    ) -> LlmBudgetSettlementFailed | None:
        refused: LlmBudgetSettlementFailed | None = None
        try:
            if intended == "consume":
                accepted = self._port.consume(vector)
            else:
                accepted = self._port.refund(vector)
        except Exception:
            refused = _settlement_error(vector, intended, provider_code)
        else:
            if not accepted:
                refused = _settlement_error(vector, intended, provider_code)
        return refused


def _settlement_error(
    vector: Mapping[str, object],
    intended: Literal["consume", "refund"],
    provider_code: str | None,
) -> LlmBudgetSettlementFailed:
    return LlmBudgetSettlementFailed(
        reservation_id=str(vector["reservation_id"]),
        intended=intended,
        provider_code=provider_code,
    )


def _outcome_code(failure: BaseException) -> str | None:
    code = getattr(failure, "code", None)
    if isinstance(code, str):
        return code
    return None


def _vector_sha256(vector: Mapping[str, object]) -> str:
    rendered = {
        key: format(Decimal(str(value)), "f") if key == "usd" else value
        for key, value in vector.items()
    }
    encoded = json.dumps(rendered, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _usage_unpriced(usage: LlmUsage) -> bool:
    if usage.service_tier != _STANDARD_SERVICE_TIER:
        return True
    return any(getattr(usage, name) != 0 for name in _CACHE_USAGE_FIELDS)
