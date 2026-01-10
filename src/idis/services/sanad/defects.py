"""Sanad Defects — Unified defect detection and result types.

Re-exports from shudhudh and ilal modules with common interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from idis.services.sanad.ilal import (
    IlalDefect,
    IlalDefectCode,
    detect_all_ilal,
    detect_ilal_chain_break,
    detect_ilal_chain_grafting,
    detect_ilal_chronology_impossible,
    detect_ilal_version_drift,
)
from idis.services.sanad.shudhudh import (
    ShudhuhResult,
    detect_shudhudh,
)


class DefectCode(Enum):
    """Unified defect codes for Sanad methodology v2."""

    ILAL_VERSION_DRIFT = "ILAL_VERSION_DRIFT"
    ILAL_CHAIN_BREAK = "ILAL_CHAIN_BREAK"
    ILAL_CHAIN_GRAFTING = "ILAL_CHAIN_GRAFTING"
    ILAL_CHRONOLOGY_IMPOSSIBLE = "ILAL_CHRONOLOGY_IMPOSSIBLE"

    SHUDHUDH_ANOMALY = "SHUDHUDH_ANOMALY"
    SHUDHUDH_UNIT_MISMATCH = "SHUDHUDH_UNIT_MISMATCH"
    SHUDHUDH_TIME_WINDOW = "SHUDHUDH_TIME_WINDOW"

    COI_HIGH_UNDISCLOSED = "COI_HIGH_UNDISCLOSED"
    COI_HIGH_UNCURED = "COI_HIGH_UNCURED"
    COI_DISCLOSURE_MISSING = "COI_DISCLOSURE_MISSING"

    BROKEN_CHAIN = "BROKEN_CHAIN"
    MISSING_LINK = "MISSING_LINK"
    UNKNOWN_SOURCE = "UNKNOWN_SOURCE"
    CONCEALMENT = "CONCEALMENT"
    INCONSISTENCY = "INCONSISTENCY"
    ANOMALY_VS_STRONGER_SOURCES = "ANOMALY_VS_STRONGER_SOURCES"
    CHRONO_IMPOSSIBLE = "CHRONO_IMPOSSIBLE"
    CHAIN_GRAFTING = "CHAIN_GRAFTING"
    CIRCULARITY = "CIRCULARITY"
    STALENESS = "STALENESS"
    UNIT_MISMATCH = "UNIT_MISMATCH"
    TIME_WINDOW_MISMATCH = "TIME_WINDOW_MISMATCH"
    SCOPE_DRIFT = "SCOPE_DRIFT"
    IMPLAUSIBILITY = "IMPLAUSIBILITY"


@dataclass
class DefectResult:
    """Unified defect result structure."""

    code: DefectCode | str
    severity: str
    description: str
    cure_protocol: str
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        code_str = self.code.value if isinstance(self.code, DefectCode) else str(self.code)
        return {
            "code": code_str,
            "severity": self.severity,
            "description": self.description,
            "cure_protocol": self.cure_protocol,
            "metadata": self.metadata or {},
        }

    @classmethod
    def from_ilal(cls, ilal: IlalDefect) -> DefectResult:
        """Create from IlalDefect."""
        return cls(
            code=DefectCode(ilal.code.value),
            severity=ilal.severity,
            description=ilal.description,
            cure_protocol=ilal.cure_protocol,
            metadata=ilal.metadata,
        )

    @classmethod
    def from_shudhudh(cls, shudhudh: ShudhuhResult) -> DefectResult | None:
        """Create from ShudhuhResult if has defect."""
        if not shudhudh.has_anomaly or not shudhudh.defect_code:
            return None

        return cls(
            code=shudhudh.defect_code,
            severity=shudhudh.severity or "MAJOR",
            description=shudhudh.description or "Shudhudh anomaly",
            cure_protocol=shudhudh.cure_protocol or "HUMAN_ARBITRATION",
        )


__all__ = [
    "DefectCode",
    "DefectResult",
    "detect_shudhudh",
    "detect_ilal_version_drift",
    "detect_ilal_chain_break",
    "detect_ilal_chain_grafting",
    "detect_ilal_chronology_impossible",
    "detect_all_ilal",
    "IlalDefect",
    "IlalDefectCode",
    "ShudhuhResult",
]
