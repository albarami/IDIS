# Enrichment Connectors Specification

**Version:** 1.0.0  
**Date:** 2026-02-05  
**Status:** Build Spec  
**Phase:** Phase 7 (Enterprise Hardening)

---

## 1. Overview

This document specifies the enrichment connector framework for IDIS, enabling external data integration while maintaining tenant isolation, source attribution, and conflict handling.

---

## 2. Connector Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Enrichment Service                            │
├─────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │  Connector   │  │  Connector   │  │  Connector   │              │
│  │  Registry    │  │  Executor    │  │  Cache       │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
├─────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │  Credential  │  │  Rate        │  │  Conflict    │              │
│  │  Manager     │  │  Limiter     │  │  Resolver    │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
└─────────────────────────────────────────────────────────────────────┘
         │                   │                   │
         ▼                   ▼                   ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  PitchBook  │     │  Crunchbase │     │  LinkedIn   │
└─────────────┘     └─────────────┘     └─────────────┘
```

---

## 3. Connector Interface

### 3.1 Base Connector

```python
class BaseConnector(ABC):
    """Abstract base for all enrichment connectors."""
    
    connector_id: str
    connector_name: str
    source_tier: SourceTier  # For Sanad grading
    rate_limit: RateLimitConfig
    
    @abstractmethod
    async def authenticate(
        self,
        credentials: ConnectorCredentials,
    ) -> AuthResult:
        """Authenticate with the external service."""
        pass
    
    @abstractmethod
    async def query(
        self,
        entity_type: EntityType,
        entity_id: str | None,
        entity_name: str,
        data_points: list[str],
    ) -> EnrichmentResult:
        """Query for enrichment data."""
        pass
    
    @abstractmethod
    async def health_check(self) -> HealthCheckResult:
        """Check connector health and API availability."""
        pass
    
    def get_source_grade(self) -> str:
        """Return default source grade for this connector."""
        return self.source_tier.to_grade()
```

### 3.2 Connector Registration

```python
CONNECTOR_REGISTRY = {
    "pitchbook": PitchBookConnector,
    "crunchbase": CrunchbaseConnector,
    "linkedin": LinkedInConnector,
    "sec_edgar": SECEdgarConnector,
    "clearbit": ClearbitConnector,
    "apollo": ApolloConnector,
}
```

---

## 4. BYOL Credential Storage

### 4.1 Credential Model

```json
{
  "type": "object",
  "required": ["connector_id", "tenant_id", "credential_type"],
  "properties": {
    "credential_id": {"type": "string", "format": "uuid"},
    "connector_id": {"type": "string"},
    "tenant_id": {"type": "string", "format": "uuid"},
    "credential_type": {
      "type": "string",
      "enum": ["API_KEY", "OAUTH2", "BASIC_AUTH", "CUSTOM"]
    },
    "encrypted_value": {
      "type": "string",
      "description": "AES-256-GCM encrypted credential"
    },
    "key_id": {
      "type": "string",
      "description": "KMS key ID used for encryption"
    },
    "expires_at": {"type": "string", "format": "date-time"},
    "scopes": {"type": "array", "items": {"type": "string"}},
    "created_at": {"type": "string", "format": "date-time"},
    "rotated_at": {"type": "string", "format": "date-time"}
  }
}
```

### 4.2 Tenant Isolation Rules

```python
class CredentialManager:
    """Manage connector credentials with tenant isolation."""
    
    async def store_credential(
        self,
        tenant_id: str,
        connector_id: str,
        credential: PlainCredential,
    ) -> CredentialRef:
        """
        Store credential for a tenant.
        
        Rules:
        1. Credential encrypted with tenant-specific KMS key
        2. Stored in tenant-scoped table partition
        3. Access logged to audit
        """
        # Get tenant's KMS key
        kms_key = await self.kms.get_tenant_key(tenant_id)
        
        # Encrypt credential
        encrypted = await self.kms.encrypt(
            key_id=kms_key.key_id,
            plaintext=credential.to_bytes(),
        )
        
        # Store with tenant scope
        record = ConnectorCredential(
            credential_id=uuid4(),
            tenant_id=tenant_id,
            connector_id=connector_id,
            credential_type=credential.type,
            encrypted_value=encrypted.ciphertext,
            key_id=kms_key.key_id,
        )
        
        await self.repo.save(record)
        
        # Audit
        await self.audit.emit("credential.stored", {
            "tenant_id": tenant_id,
            "connector_id": connector_id,
            "credential_type": credential.type,
        })
        
        return CredentialRef(credential_id=record.credential_id)
    
    async def get_credential(
        self,
        tenant_id: str,
        connector_id: str,
    ) -> PlainCredential:
        """
        Retrieve and decrypt credential.
        
        Tenant isolation enforced by:
        1. Query scoped to tenant_id
        2. KMS key access restricted to tenant
        3. Audit log of access
        """
        record = await self.repo.get_by_tenant_and_connector(
            tenant_id=tenant_id,
            connector_id=connector_id,
        )
        
        if not record:
            raise CredentialNotFoundError(connector_id)
        
        # Decrypt with tenant's key
        plaintext = await self.kms.decrypt(
            key_id=record.key_id,
            ciphertext=record.encrypted_value,
        )
        
        # Audit access
        await self.audit.emit("credential.accessed", {
            "tenant_id": tenant_id,
            "connector_id": connector_id,
        })
        
        return PlainCredential.from_bytes(plaintext)
```

---

## 5. Conflict Handling

### 5.1 Conflict Types

| Type | Description | Resolution |
|------|-------------|------------|
| `VALUE_CONFLICT` | Enrichment value differs from internal claim | Create defect |
| `STALE_CONFLICT` | Enrichment is newer than internal claim | Flag for review |
| `MISSING_INTERNAL` | Enrichment has data not in internal claims | Create new claim |
| `CONTRADICTORY` | Direct contradiction between sources | Human arbitration |

### 5.2 Conflict Detection

```python
async def detect_conflicts(
    internal_claims: list[Claim],
    enrichment_data: EnrichmentResult,
) -> list[Conflict]:
    """Detect conflicts between internal claims and enrichment."""
    
    conflicts = []
    
    for enrichment_claim in enrichment_data.claims:
        # Find matching internal claim
        internal = find_matching_claim(
            internal_claims,
            enrichment_claim,
            match_on=["claim_class", "metric_key"],
        )
        
        if internal is None:
            # New information from enrichment
            conflicts.append(Conflict(
                type=ConflictType.MISSING_INTERNAL,
                enrichment_claim=enrichment_claim,
                internal_claim=None,
                resolution_strategy="CREATE_CLAIM",
            ))
            continue
        
        # Compare values
        if not values_match(internal.value_struct, enrichment_claim.value_struct):
            # Determine which is authoritative
            if enrichment_claim.source_tier < internal.source_tier:
                # Enrichment is more authoritative
                conflicts.append(Conflict(
                    type=ConflictType.VALUE_CONFLICT,
                    enrichment_claim=enrichment_claim,
                    internal_claim=internal,
                    resolution_strategy="FLAG_DEFECT",
                ))
            else:
                # Internal is more authoritative
                conflicts.append(Conflict(
                    type=ConflictType.VALUE_CONFLICT,
                    enrichment_claim=enrichment_claim,
                    internal_claim=internal,
                    resolution_strategy="IGNORE_ENRICHMENT",
                ))
    
    return conflicts
```

### 5.3 Conflict Resolution

```python
CONFLICT_RESOLUTION_RULES = {
    ConflictType.VALUE_CONFLICT: {
        "enrichment_tier_higher": "CREATE_DEFECT_MAJOR",
        "internal_tier_higher": "LOG_AND_IGNORE",
        "same_tier": "HUMAN_ARBITRATION",
    },
    ConflictType.STALE_CONFLICT: {
        "enrichment_newer": "FLAG_FOR_REVIEW",
        "internal_newer": "IGNORE_ENRICHMENT",
    },
    ConflictType.MISSING_INTERNAL: {
        "material_metric": "CREATE_CLAIM_GRADE_C",
        "non_material": "LOG_ONLY",
    },
    ConflictType.CONTRADICTORY: {
        "always": "HUMAN_ARBITRATION",
    },
}
```

---

## 6. Source Tier Mapping

### 6.1 Connector to Source Tier

| Connector | Source Tier | Grade | Rationale |
|-----------|-------------|-------|-----------|
| SEC EDGAR | `THIQAH_THABIT` | B+ | Regulatory filing |
| PitchBook | `SHAYKH` | C | Third-party estimate |
| Crunchbase | `SHAYKH` | C | Third-party estimate |
| LinkedIn | `MAQBUL` | D | Unverified profile data |
| Clearbit | `MAQBUL` | D | Aggregated estimates |
| News APIs | `SHAYKH` | C | Press coverage |

### 6.2 Grade Capping

```python
def cap_enrichment_grade(
    connector: BaseConnector,
    claim_materiality: Materiality,
) -> str:
    """
    Cap grade for enrichment-sourced claims.
    
    Rules:
    1. Enrichment sources cannot be primary for HIGH/CRITICAL claims
    2. Maximum grade is C for most connectors
    3. SEC EDGAR can reach B for regulatory data
    """
    tier = connector.source_tier
    
    if claim_materiality in [Materiality.HIGH, Materiality.CRITICAL]:
        if tier.admissibility == "SUPPORT_ONLY":
            return "C"  # Cannot be primary
    
    return tier.max_grade
```

---

## 7. Rate Limiting

### 7.1 Per-Connector Limits

```python
CONNECTOR_RATE_LIMITS = {
    "pitchbook": RateLimitConfig(
        requests_per_hour=100,
        requests_per_day=500,
        concurrent_max=5,
        backoff_strategy="exponential",
    ),
    "crunchbase": RateLimitConfig(
        requests_per_hour=200,
        requests_per_day=1000,
        concurrent_max=10,
        backoff_strategy="exponential",
    ),
    "linkedin": RateLimitConfig(
        requests_per_hour=50,
        requests_per_day=200,
        concurrent_max=2,
        backoff_strategy="fixed",
        fixed_backoff_seconds=60,
    ),
    "sec_edgar": RateLimitConfig(
        requests_per_second=10,
        requests_per_day=None,  # No daily limit
        concurrent_max=5,
        backoff_strategy="none",
    ),
}
```

### 7.2 Tenant-Level Limits

```python
TENANT_ENRICHMENT_LIMITS = {
    "free": {
        "requests_per_month": 100,
        "connectors_allowed": ["crunchbase"],
    },
    "standard": {
        "requests_per_month": 1000,
        "connectors_allowed": ["crunchbase", "clearbit"],
    },
    "enterprise": {
        "requests_per_month": None,  # Unlimited
        "connectors_allowed": "all",
    },
}
```

---

## 8. Connector Implementations

### 8.1 PitchBook Connector

```python
class PitchBookConnector(BaseConnector):
    connector_id = "pitchbook"
    connector_name = "PitchBook"
    source_tier = SourceTier.SHAYKH
    
    async def query(
        self,
        entity_type: EntityType,
        entity_id: str | None,
        entity_name: str,
        data_points: list[str],
    ) -> EnrichmentResult:
        """Query PitchBook API for company/investor data."""
        
        if entity_type == EntityType.COMPANY:
            return await self._query_company(entity_name, data_points)
        elif entity_type == EntityType.PERSON:
            return await self._query_person(entity_name, data_points)
        elif entity_type == EntityType.INVESTOR:
            return await self._query_investor(entity_name, data_points)
        else:
            raise UnsupportedEntityTypeError(entity_type)
    
    async def _query_company(
        self,
        company_name: str,
        data_points: list[str],
    ) -> EnrichmentResult:
        # Search for company
        search_result = await self.client.search_companies(company_name)
        
        if not search_result.matches:
            return EnrichmentResult(found=False)
        
        company_id = search_result.matches[0].id
        
        # Fetch requested data points
        claims = []
        for data_point in data_points:
            if data_point == "funding_history":
                funding = await self.client.get_funding_rounds(company_id)
                claims.extend(self._funding_to_claims(funding))
            elif data_point == "valuation":
                valuation = await self.client.get_valuation(company_id)
                claims.append(self._valuation_to_claim(valuation))
            # ... more data points
        
        return EnrichmentResult(
            found=True,
            source_connector=self.connector_id,
            source_tier=self.source_tier,
            claims=claims,
            retrieved_at=datetime.now(timezone.utc),
        )
```

### 8.2 SEC EDGAR Connector

```python
class SECEdgarConnector(BaseConnector):
    connector_id = "sec_edgar"
    connector_name = "SEC EDGAR"
    source_tier = SourceTier.THIQAH_THABIT  # Higher tier - regulatory
    
    async def query(
        self,
        entity_type: EntityType,
        entity_id: str | None,
        entity_name: str,
        data_points: list[str],
    ) -> EnrichmentResult:
        """Query SEC EDGAR for regulatory filings."""
        
        if entity_type != EntityType.COMPANY:
            return EnrichmentResult(found=False)
        
        # Search by CIK or company name
        cik = await self._resolve_cik(entity_name)
        if not cik:
            return EnrichmentResult(found=False)
        
        claims = []
        
        for data_point in data_points:
            if data_point == "financials":
                filings = await self.client.get_filings(
                    cik=cik,
                    form_types=["10-K", "10-Q"],
                )
                claims.extend(self._filings_to_claims(filings))
            elif data_point == "insider_transactions":
                forms = await self.client.get_filings(
                    cik=cik,
                    form_types=["4"],
                )
                claims.extend(self._insider_to_claims(forms))
        
        return EnrichmentResult(
            found=True,
            source_connector=self.connector_id,
            source_tier=self.source_tier,
            claims=claims,
            retrieved_at=datetime.now(timezone.utc),
        )
```

---

## 9. Module Structure

```
src/idis/services/enrichment/
├── __init__.py
├── service.py              # Main enrichment service
├── registry.py             # Connector registry
├── credentials/
│   ├── __init__.py
│   ├── manager.py          # Credential CRUD
│   └── kms.py              # KMS integration
├── connectors/
│   ├── __init__.py
│   ├── base.py             # Abstract connector
│   ├── pitchbook.py
│   ├── crunchbase.py
│   ├── linkedin.py
│   ├── sec_edgar.py
│   ├── clearbit.py
│   └── apollo.py
├── conflict/
│   ├── __init__.py
│   ├── detector.py
│   └── resolver.py
├── rate_limit/
│   ├── __init__.py
│   └── limiter.py
└── cache/
    ├── __init__.py
    └── cache_store.py
```

---

## 10. Acceptance Criteria

### 10.1 Functional Requirements
- [ ] Connector interface implemented
- [ ] BYOL credential storage with encryption
- [ ] Tenant isolation enforced
- [ ] Conflict detection and resolution
- [ ] Rate limiting per connector and tenant
- [ ] Source tier mapping for grading

### 10.2 Security Requirements
- [ ] Credentials encrypted at rest (AES-256-GCM)
- [ ] Tenant-scoped KMS keys
- [ ] No cross-tenant credential access
- [ ] Audit logging for credential operations

### 10.3 Test Hooks

```python
# Unit tests
def test_credential_encryption()
def test_tenant_isolation()
def test_conflict_detection()
def test_rate_limiting()

# Integration tests
def test_pitchbook_connector()
def test_sec_edgar_connector()
def test_enrichment_to_claims()

# Security tests
def test_cross_tenant_access_blocked()
def test_credential_audit_logging()
```
