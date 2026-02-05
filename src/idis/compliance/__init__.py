"""IDIS Compliance Layer (v6.3 Task 7.5).

Provides enterprise-grade, fail-closed compliance controls:
- Data residency enforcement (region pinning)
- BYOK (Bring Your Own Key) customer-managed key policies
- Retention and legal hold guardrails

All controls fail closed: missing configuration, missing tenant region,
missing key state, or missing hold registry results in denial.
"""

from idis.compliance.byok import (
    BYOKKeyState,
    BYOKPolicy,
    configure_key,
    require_key_active,
    revoke_key,
    rotate_key,
)
from idis.compliance.residency import (
    ResidencyConfigError,
    ResidencyViolationError,
    enforce_region_pin,
    get_service_region_from_env,
)
from idis.compliance.retention import (
    LegalHold,
    LegalHoldRegistry,
    RetentionPolicy,
    apply_hold,
    block_deletion_if_held,
    lift_hold,
)

__all__ = [
    "BYOKKeyState",
    "BYOKPolicy",
    "LegalHold",
    "LegalHoldRegistry",
    "ResidencyConfigError",
    "ResidencyViolationError",
    "RetentionPolicy",
    "apply_hold",
    "block_deletion_if_held",
    "configure_key",
    "enforce_region_pin",
    "get_service_region_from_env",
    "lift_hold",
    "require_key_active",
    "revoke_key",
    "rotate_key",
]
