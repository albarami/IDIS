# IDIS Sanad Methodology v2 — Enhanced Evidence Chain Grading

**Version:** 2.0  
**Date:** 2026-01-09  
**Status:** Normative  
**Phase:** 3.3 (Sanad Gate)  
**Audience:** Engineering, Trust Systems, Compliance

---

## 1. Overview

This document specifies the enhanced Sanad methodology for IDIS v6.3, incorporating six key enhancements to evidence chain verification:

1. **Source Tiers** — Six-level reliability subgrade ladder
2. **Dabt** — Multi-dimensional precision scoring
3. **Tawatur Independence** — Collusion-impossibility heuristics
4. **Shudhudh** — Reconciliation-first anomaly detection
5. **I'lal** — Hidden defect categories
6. **COI Handling** — Conflict of interest rules and cure protocols

All algorithms are **deterministic** and **fail-closed**.

---

## 2. Source Reliability Tiers (Jarḥ wa Taʿdīl Adaptation)

### 2.1 Six-Level Tier Hierarchy

| Tier | Code | Numeric Weight | Admissibility | Description |
|------|------|----------------|---------------|-------------|
| 1 | `ATHBAT_AL_NAS` | 1.00 | PRIMARY | Highest reliability — audited financials, verified regulatory filings |
| 2 | `THIQAH_THABIT` | 0.90 | PRIMARY | Highly reliable — bank statements, signed contracts |
| 3 | `THIQAH` | 0.80 | PRIMARY | Reliable — internal financial models with version control |
| 4 | `SADUQ` | 0.65 | PRIMARY | Truthful but may err — founder statements, pitch decks |
| 5 | `SHAYKH` | 0.50 | SUPPORT_ONLY | Known but unverified — third-party estimates, press releases |
| 6 | `MAQBUL` | 0.40 | SUPPORT_ONLY | Minimally acceptable — analyst guesses, forum posts |

### 2.2 Admissibility Rules (Deterministic)

```
RULE ADM-001: PRIMARY sources (tiers 1-4) may serve as primary evidence for any claim.
RULE ADM-002: SUPPORT_ONLY sources (tiers 5-6) cannot be primary for HIGH/CRITICAL materiality claims.
RULE ADM-003: If only SUPPORT_ONLY sources exist for a HIGH/CRITICAL claim → grade cap at C.
RULE ADM-004: Tier assignment is deterministic based on source_type metadata.
```

### 2.3 Tier Assignment Logic

```python
def assign_tier(source: EvidenceItem) -> SourceTier:
    source_type = source.source_type.upper()
    
    # Tier 1: ATHBAT_AL_NAS
    if source_type in {"AUDITED_FINANCIAL", "REGULATORY_FILING", "SEC_FILING"}:
        return SourceTier.ATHBAT_AL_NAS
    
    # Tier 2: THIQAH_THABIT
    if source_type in {"BANK_STATEMENT", "SIGNED_CONTRACT", "NOTARIZED_DOCUMENT"}:
        return SourceTier.THIQAH_THABIT
    
    # Tier 3: THIQAH
    if source_type in {"FINANCIAL_MODEL", "INTERNAL_REPORT", "VERSION_CONTROLLED_DOC"}:
        return SourceTier.THIQAH
    
    # Tier 4: SADUQ
    if source_type in {"PITCH_DECK", "FOUNDER_STATEMENT", "EXEC_MEMO", "EMAIL"}:
        return SourceTier.SADUQ
    
    # Tier 5: SHAYKH
    if source_type in {"PRESS_RELEASE", "THIRD_PARTY_ESTIMATE", "NEWS_ARTICLE"}:
        return SourceTier.SHAYKH
    
    # Tier 6: MAQBUL (default for unknown)
    return SourceTier.MAQBUL
```

---

## 3. Dabt (Precision) Multi-Dimensional Scoring

### 3.1 Dabt Dimensions

| Dimension | Code | Range | Description |
|-----------|------|-------|-------------|
| Documentation Precision | `documentation_precision` | 0.0–1.0 | Quality of source documentation |
| Transmission Precision | `transmission_precision` | 0.0–1.0 | Fidelity of extraction/transformation chain |
| Temporal Precision | `temporal_precision` | 0.0–1.0 | Time-window clarity and staleness |
| Cognitive Precision | `cognitive_precision` | 0.0–1.0 or `null` | Agent/human understanding confidence |

### 3.2 Scoring Rules (Deterministic, Fail-Closed)

```
RULE DABT-001: Missing dimension → treated as 0.0 (fail closed, never increases score)
RULE DABT-002: cognitive_precision = null → excluded from calculation, does not penalize
RULE DABT-003: Final dabt_score = weighted_mean(available_dimensions)
RULE DABT-004: If all dimensions missing → dabt_score = 0.0
```

### 3.3 Dabt Score Calculation

```python
def calculate_dabt_score(factors: DabtFactors) -> float:
    weights = {
        "documentation_precision": 0.30,
        "transmission_precision": 0.30,
        "temporal_precision": 0.25,
        "cognitive_precision": 0.15,
    }
    
    total_weight = 0.0
    weighted_sum = 0.0
    
    for dim, weight in weights.items():
        value = getattr(factors, dim, None)
        if value is not None:
            # Clamp to [0, 1]
            clamped = max(0.0, min(1.0, value))
            weighted_sum += clamped * weight
            total_weight += weight
    
    if total_weight == 0.0:
        return 0.0  # Fail closed
    
    return weighted_sum / total_weight
```

### 3.4 Dabt Thresholds

| Dabt Score | Quality Band | Impact |
|------------|--------------|--------|
| ≥ 0.90 | EXCELLENT | No penalty |
| 0.75–0.89 | GOOD | No penalty |
| 0.50–0.74 | FAIR | Warning flag |
| < 0.50 | POOR | Grade cap at B |

---

## 4. Tawatur (Multi-Attestation) Independence Assessment

### 4.1 Independence Definition

Two sources are **independent** if and only if ALL of the following hold:

1. **Different source_system** — Not from the same originating system
2. **Different upstream_origin_id** — No shared upstream data source (HARD RULE)
3. **Different artifact identity** — Not the same document/file
4. **Time separation** — Not within same time bucket (configurable, default 1 hour)
5. **No chain overlap** — No shared transmission nodes

### 4.2 Independence Key Computation

```python
def compute_independence_key(source: EvidenceItem) -> str:
    """Compute deterministic independence key for grouping."""
    components = [
        source.source_system or "UNKNOWN",
        source.upstream_origin_id or source.evidence_id,
        source.artifact_id or "NO_ARTIFACT",
        _time_bucket(source.timestamp, bucket_hours=1),
    ]
    return "|".join(components)
```

### 4.3 Collusion Risk Scoring

```python
def compute_collusion_risk(sources: list[EvidenceItem]) -> float:
    """Deterministic collusion risk score [0.0, 1.0]."""
    if len(sources) <= 1:
        return 0.0
    
    # Factor 1: Source system concentration
    systems = [s.source_system for s in sources]
    system_concentration = max(Counter(systems).values()) / len(sources)
    
    # Factor 2: Time clustering
    timestamps = [s.timestamp for s in sources if s.timestamp]
    time_cluster_factor = _compute_time_clustering(timestamps)
    
    # Factor 3: Chain overlap
    chain_overlap_factor = _compute_chain_overlap(sources)
    
    # Weighted combination
    return (
        0.40 * system_concentration +
        0.30 * time_cluster_factor +
        0.30 * chain_overlap_factor
    )
```

### 4.4 Tawatur Classification

| Status | Requirement |
|--------|-------------|
| `NONE` | 0 independent attestations |
| `AHAD_1` | 1 independent attestation |
| `AHAD_2` | 2 independent attestations |
| `MUTAWATIR` | ≥3 independent attestations AND collusion_risk ≤ 0.30 |

```
RULE TAW-001: MUTAWATIR requires independent_count >= 3 AND collusion_risk <= 0.30
RULE TAW-002: High collusion_risk (> 0.30) downgrades MUTAWATIR to AHAD_2
RULE TAW-003: Independence assessment is deterministic and auditable
```

---

## 5. Shudhudh (Anomaly) Detection — Reconciliation-First

### 5.1 Philosophy

Shudhudh in hadith science refers to a narration that contradicts stronger sources. In IDIS, we apply **reconciliation-first**: attempt to explain discrepancies before flagging as anomaly.

### 5.2 Reconciliation Heuristics (Deterministic)

| Heuristic | Code | Condition | Action |
|-----------|------|-----------|--------|
| Unit Conversion | `UNIT_RECONCILE` | Values differ by 1000x or 1000000x | Check for k/M labeling, reconcile if explicit |
| Time Window | `TIME_WINDOW_RECONCILE` | FY vs LTM labels present | Reconcile if time windows align |
| Rounding | `ROUNDING_RECONCILE` | Values within 1% | Treat as reconciled |
| Currency | `CURRENCY_RECONCILE` | Different currency labels | Convert if rates available |

### 5.3 Shudhudh Detection Algorithm

```python
def detect_shudhudh(
    claim_values: list[ClaimValue],
    sources: list[EvidenceItem],
) -> ShudhdhResult:
    """
    Reconciliation-first anomaly detection.
    Returns defect only if reconciliation fails AND lower-tier
    contradicts higher-tier/consensus.
    """
    # Step 1: Attempt reconciliation
    reconciled = attempt_reconciliation(claim_values)
    if reconciled.success:
        return ShudhdhResult(defect=None, reconciliation=reconciled)
    
    # Step 2: Identify tier hierarchy
    values_by_tier = group_by_source_tier(claim_values, sources)
    
    # Step 3: Check for lower-tier contradiction
    consensus_value = compute_consensus(values_by_tier)
    
    for value in claim_values:
        tier = get_tier_for_value(value, sources)
        if tier.admissibility == "SUPPORT_ONLY":
            if contradicts(value, consensus_value, threshold=0.05):
                return ShudhdhResult(
                    defect=DefectResult(
                        code=DefectCode.SHUDHUDH_ANOMALY,
                        severity="MAJOR",
                        description=f"Lower-tier source contradicts consensus: {value} vs {consensus_value}",
                        cure_protocol="HUMAN_ARBITRATION",
                    ),
                    reconciliation=reconciled,
                )
    
    # No shudhudh if higher-tier sources align
    return ShudhdhResult(defect=None, reconciliation=reconciled)
```

### 5.4 Shudhudh Defect Codes

| Code | Severity | Description |
|------|----------|-------------|
| `SHUDHUDH_ANOMALY` | MAJOR | Lower-tier contradicts consensus after failed reconciliation |
| `SHUDHUDH_UNIT_MISMATCH` | MINOR | Unit mismatch detected but reconcilable |
| `SHUDHUDH_TIME_WINDOW` | MINOR | Time window mismatch but labeled |

---

## 6. I'lal (Hidden Defects) Detection

### 6.1 I'lal Categories (IDIS Domain Mapping)

| Classical Concept | IDIS Code | Trigger | Severity |
|-------------------|-----------|---------|----------|
| Hidden chain defect | `ILAL_CHAIN_BREAK` | Missing transmission node, broken parent ref | FATAL |
| Chain grafting | `ILAL_CHAIN_GRAFTING` | Inconsistent provenance linkage | FATAL |
| Chronological impossibility | `ILAL_CHRONOLOGY_IMPOSSIBLE` | Timestamps violate causality | FATAL |
| Version drift | `ILAL_VERSION_DRIFT` | SHA drift + metric change | MAJOR |

### 6.2 ILAL_VERSION_DRIFT Detection

```python
def detect_version_drift(
    claim: Claim,
    documents: list[Document],
) -> DefectResult | None:
    """
    Detect when claim cites old version but newer exists with different value.
    """
    cited_doc = get_cited_document(claim)
    if not cited_doc:
        return None
    
    # Find newer versions
    newer_versions = [
        d for d in documents
        if d.artifact_id == cited_doc.artifact_id
        and d.version > cited_doc.version
    ]
    
    if not newer_versions:
        return None
    
    latest = max(newer_versions, key=lambda d: d.version)
    
    # Check if metric changed
    cited_value = extract_metric_value(cited_doc, claim.claim_type)
    latest_value = extract_metric_value(latest, claim.claim_type)
    
    if cited_value != latest_value:
        return DefectResult(
            code=DefectCode.ILAL_VERSION_DRIFT,
            severity="MAJOR",
            description=(
                f"Claim cites version {cited_doc.version} "
                f"(value: {cited_value}) but version {latest.version} "
                f"exists with updated value: {latest_value}"
            ),
            cure_protocol="REQUIRE_REAUDIT",
            metadata={
                "cited_version": cited_doc.version,
                "cited_sha": cited_doc.sha256,
                "latest_version": latest.version,
                "latest_sha": latest.sha256,
                "value_change": f"{cited_value} → {latest_value}",
            },
        )
    
    return None
```

### 6.3 ILAL_CHAIN_BREAK Detection

```python
def detect_chain_break(sanad: Sanad) -> DefectResult | None:
    """
    Detect broken transmission chain:
    - Missing transmission nodes
    - References to non-existent evidence
    - Orphaned nodes
    """
    chain = sanad.transmission_chain
    node_ids = {n.node_id for n in chain}
    
    for node in chain:
        # Check parent reference
        if node.prev_node_id and node.prev_node_id not in node_ids:
            return DefectResult(
                code=DefectCode.ILAL_CHAIN_BREAK,
                severity="FATAL",
                description=f"Node {node.node_id} references non-existent parent {node.prev_node_id}",
                cure_protocol="RECONSTRUCT_CHAIN",
            )
        
        # Check evidence reference
        if node.evidence_id and not evidence_exists(node.evidence_id):
            return DefectResult(
                code=DefectCode.ILAL_CHAIN_BREAK,
                severity="FATAL",
                description=f"Node {node.node_id} references non-existent evidence {node.evidence_id}",
                cure_protocol="REQUEST_SOURCE",
            )
    
    return None
```

### 6.4 ILAL_CHAIN_GRAFTING Detection

```python
def detect_chain_grafting(sanad: Sanad) -> DefectResult | None:
    """
    Detect inconsistent provenance linkage (chain grafting):
    - Node claims different origin than parent chain suggests
    - Mismatched upstream_origin_id in connected nodes
    """
    chain = sanad.transmission_chain
    
    for i, node in enumerate(chain[1:], 1):
        prev_node = chain[i - 1]
        
        # Check upstream origin consistency
        if (node.upstream_origin_id and prev_node.upstream_origin_id and
            node.upstream_origin_id != prev_node.upstream_origin_id):
            return DefectResult(
                code=DefectCode.ILAL_CHAIN_GRAFTING,
                severity="FATAL",
                description=(
                    f"Inconsistent provenance: node {node.node_id} claims origin "
                    f"{node.upstream_origin_id} but parent suggests {prev_node.upstream_origin_id}"
                ),
                cure_protocol="HUMAN_ARBITRATION",
            )
    
    return None
```

### 6.5 ILAL_CHRONOLOGY_IMPOSSIBLE Detection

```python
def detect_chronology_impossible(sanad: Sanad) -> DefectResult | None:
    """
    Detect timestamps that violate causality:
    - Child node timestamp before parent
    - Evidence timestamp after extraction timestamp
    """
    chain = sanad.transmission_chain
    
    for i, node in enumerate(chain[1:], 1):
        prev_node = chain[i - 1]
        
        if node.timestamp and prev_node.timestamp:
            if node.timestamp < prev_node.timestamp:
                return DefectResult(
                    code=DefectCode.ILAL_CHRONOLOGY_IMPOSSIBLE,
                    severity="FATAL",
                    description=(
                        f"Chronology violation: node {node.node_id} "
                        f"({node.timestamp}) precedes parent {prev_node.node_id} "
                        f"({prev_node.timestamp})"
                    ),
                    cure_protocol="REQUIRE_REAUDIT",
                )
    
    return None
```

---

## 7. COI (Conflict of Interest) Handling

### 7.1 COI Metadata Schema

```python
@dataclass
class COIMetadata:
    coi_present: bool
    coi_severity: Literal["LOW", "MEDIUM", "HIGH"] | None
    coi_disclosed: bool
    coi_type: str | None  # e.g., "FINANCIAL", "PERSONAL", "COMPETITIVE"
    coi_description: str | None
```

### 7.2 COI Rules (Deterministic)

| Condition | Rule | Effect |
|-----------|------|--------|
| HIGH + undisclosed | `COI-001` | Grade cap at C, defect emitted |
| HIGH + disclosed | `COI-002` | Requires stronger corroboration (Tawatur pass with ≥1 tier-1/2 source) |
| MEDIUM + undisclosed | `COI-003` | Warning flag, no automatic downgrade |
| MEDIUM + disclosed | `COI-004` | No penalty |
| LOW | `COI-005` | No penalty |

### 7.3 COI Cure Protocol

```python
def evaluate_coi_cure(
    source: EvidenceItem,
    corroborating_sources: list[EvidenceItem],
    tawatur_result: TawaturResult,
) -> COICureResult:
    """
    Evaluate if COI can be cured by independent corroboration.
    """
    coi = source.coi_metadata
    
    if not coi or not coi.coi_present:
        return COICureResult(cured=True, reason="No COI present")
    
    if coi.coi_severity == "HIGH" and not coi.coi_disclosed:
        # Check for independent high-tier corroboration
        high_tier_independent = [
            s for s in corroborating_sources
            if s.evidence_id != source.evidence_id
            and get_tier(s) in {SourceTier.ATHBAT_AL_NAS, SourceTier.THIQAH_THABIT}
            and not (s.coi_metadata and s.coi_metadata.coi_present)
        ]
        
        if high_tier_independent and tawatur_result.independence_pass:
            return COICureResult(
                cured=True,
                reason=f"Cured by {len(high_tier_independent)} independent high-tier sources",
            )
        else:
            return COICureResult(
                cured=False,
                reason="HIGH undisclosed COI requires independent high-tier corroboration",
                grade_cap="C",
            )
    
    if coi.coi_severity == "HIGH" and coi.coi_disclosed:
        # Requires stronger corroboration but not automatic block
        if tawatur_result.status == TawaturType.MUTAWATIR:
            return COICureResult(cured=True, reason="Disclosed COI cured by MUTAWATIR")
        else:
            return COICureResult(
                cured=False,
                reason="HIGH disclosed COI requires MUTAWATIR corroboration",
                requires_additional_corroboration=True,
            )
    
    # MEDIUM/LOW: no cure needed
    return COICureResult(cured=True, reason=f"{coi.coi_severity} COI does not require cure")
```

### 7.4 COI Defect Codes

| Code | Severity | Trigger |
|------|----------|---------|
| `COI_HIGH_UNDISCLOSED` | MAJOR | HIGH severity COI not disclosed |
| `COI_HIGH_UNCURED` | MAJOR | HIGH COI without sufficient independent corroboration |
| `COI_DISCLOSURE_MISSING` | MINOR | COI metadata incomplete |

---

## 8. Integrated Grade Calculation (grade_sanad_v2)

### 8.1 Algorithm

```python
def grade_sanad_v2(
    sanad: Sanad,
    sources: list[EvidenceItem],
    claim: Claim,
    documents: list[Document] | None = None,
) -> SanadGradeResult:
    """
    Integrated Sanad grading with v2 methodology.
    Deterministic. Fail-closed.
    """
    defects: list[DefectResult] = []
    grade_caps: list[str] = []
    
    # 1. Source tier assessment
    primary_tier = assign_tier(sanad.primary_source)
    base_grade = tier_to_base_grade(primary_tier)
    
    # 2. Dabt scoring
    dabt = calculate_dabt_score(sanad.dabt_factors)
    if dabt < 0.50:
        grade_caps.append("B")
    
    # 3. Tawatur assessment
    tawatur = assess_tawatur(sources)
    
    # 4. I'lal detection (FATAL defects)
    ilal_defects = [
        detect_chain_break(sanad),
        detect_chain_grafting(sanad),
        detect_chronology_impossible(sanad),
    ]
    if documents:
        ilal_defects.append(detect_version_drift(claim, documents))
    
    for defect in ilal_defects:
        if defect:
            defects.append(defect)
            if defect.severity == "FATAL":
                return SanadGradeResult(
                    grade="D",
                    defects=defects,
                    explanation="FATAL I'lal defect detected",
                )
    
    # 5. Shudhudh detection
    shudhudh = detect_shudhudh(claim.values, sources)
    if shudhudh.defect:
        defects.append(shudhudh.defect)
    
    # 6. COI evaluation
    for source in sources:
        coi_result = evaluate_coi_cure(source, sources, tawatur)
        if not coi_result.cured:
            defects.append(DefectResult(
                code=DefectCode.COI_HIGH_UNCURED,
                severity="MAJOR",
                description=coi_result.reason,
                cure_protocol="REQUIRE_INDEPENDENT_CORROBORATION",
            ))
            if coi_result.grade_cap:
                grade_caps.append(coi_result.grade_cap)
    
    # 7. Calculate final grade
    grade = base_grade
    
    # Apply MAJOR defect downgrades
    major_count = sum(1 for d in defects if d.severity == "MAJOR")
    for _ in range(major_count):
        grade = downgrade(grade)
    
    # Apply Tawatur upgrade (only if no MAJOR defects)
    if major_count == 0 and tawatur.status == TawaturType.MUTAWATIR:
        grade = upgrade(grade)
    
    # Apply grade caps
    if grade_caps:
        strictest_cap = min(grade_caps, key=grade_order)
        if grade_order(grade) < grade_order(strictest_cap):
            grade = strictest_cap
    
    return SanadGradeResult(
        grade=grade,
        defects=defects,
        tawatur=tawatur,
        dabt_score=dabt,
        source_tier=primary_tier,
        explanation=build_explanation(grade, defects, tawatur, dabt),
    )
```

### 8.2 Grade Order

```python
GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}

def grade_order(grade: str) -> int:
    return GRADE_ORDER.get(grade, 3)

def downgrade(grade: str) -> str:
    order = grade_order(grade)
    if order >= 3:
        return "D"
    return ["A", "B", "C", "D"][order + 1]

def upgrade(grade: str) -> str:
    order = grade_order(grade)
    if order <= 0:
        return "A"
    return ["A", "B", "C", "D"][order - 1]
```

---

## 9. Fail-Closed Rules Summary

| Component | Fail-Closed Behavior |
|-----------|---------------------|
| Source Tier | Unknown source → MAQBUL (lowest tier) |
| Dabt | Missing dimension → 0.0 |
| Tawatur | Cannot verify independence → treated as dependent |
| Shudhudh | Cannot reconcile → anomaly flagged |
| I'lal | Missing chain node → FATAL defect |
| COI | Missing COI metadata on flagged source → MAJOR defect |
| Grade | Any FATAL defect → Grade D |

---

## 10. Audit Events

All Sanad v2 operations emit audit events per taxonomy:

| Event | Trigger |
|-------|---------|
| `sanad.graded` | Grade calculation completed |
| `sanad.defect.detected` | Any defect detected |
| `sanad.defect.fatal` | FATAL defect forces grade D |
| `sanad.tawatur.assessed` | Independence assessment completed |
| `sanad.coi.evaluated` | COI cure evaluation completed |

---

## 11. Test Coverage Requirements

| Test Category | File | Required Tests |
|---------------|------|----------------|
| Unit: Tiers | `test_sanad_methodology_v2_unit.py` | All 6 tiers map correctly |
| Unit: Dabt | `test_sanad_methodology_v2_unit.py` | Fail-closed on missing dimensions |
| Unit: Tawatur | `test_sanad_methodology_v2_unit.py` | Independence key uniqueness |
| Unit: Shudhudh | `test_sanad_methodology_v2_unit.py` | Reconciliation before anomaly |
| Unit: I'lal | `test_sanad_methodology_v2_unit.py` | All 4 defect types trigger |
| Unit: COI | `test_sanad_methodology_v2_unit.py` | Cure protocol enforcement |
| GDBS: Adversarial | `test_sanad_methodology_v2_gdbs.py` | deal_002, deal_007, deal_008 |

---

## 12. Revision History

| Date | Version | Author | Changes |
|------|---------|--------|---------|
| 2026-01-09 | 2.0 | Cascade | Initial v2 methodology specification |
