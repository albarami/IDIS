# RB-10: Security Incident — Tenant Isolation / Exfiltration Suspected

**Runbook ID:** RB-10  
**Severity:** SEV-1 (CRITICAL)  
**Owner:** Security Team  
**Last Updated:** 2026-01-19  
**Alert:** `IDISTenantIsolationViolation`

---

## Detection

**Trigger Conditions:**
- Tenant isolation alarm triggered
- Cross-tenant data access detected
- Unusual access patterns flagged
- DLP (Data Loss Prevention) trigger
- Suspicious API key or credential usage

**Observable Signals:**
- `tenant_isolation_violations_total` > 0
- Access logs showing cross-tenant queries
- Unusual data export volumes
- Failed RLS policy enforcements
- Anomalous user behavior patterns

**Dashboard:** All dashboards (cross-check tenant filters)

---

## Triage

**THIS IS A SEV-1 INCIDENT — IMMEDIATE ACTION REQUIRED**

**Immediate Actions (First 5 minutes):**

1. **Confirm the incident is real:**
   - Review the triggering alert/signal
   - Check for false positives (test accounts, admin operations)
   - Verify tenant_id mismatch in logs

2. **Identify scope:**
   - Which tenants are affected?
   - What data may have been accessed?
   - What time range is involved?

3. **Identify the access vector:**
   - API key compromise?
   - RLS bypass bug?
   - Cache keying error?
   - Misconfigured permissions?

4. **Preserve evidence:**
   ```bash
   # Snapshot relevant logs immediately
   kubectl logs -n idis -l component=api-gateway --since=2h > incident_logs_$(date +%s).txt
   
   # Export audit events
   psql $AUDIT_DB_URL -c "COPY (SELECT * FROM audit_events WHERE created_at > NOW() - INTERVAL '2 hours') TO STDOUT CSV HEADER" > audit_export_$(date +%s).csv
   ```

5. **Notify security/compliance leads immediately:**
   - Security team lead
   - CISO (if significant)
   - Legal/compliance (for disclosure preparation)

**Escalation Path:**
- SEV-1: Security lead + CISO + Incident Commander + Legal

---

## Containment

**IMMEDIATE CONTAINMENT ACTIONS (within 15 minutes):**

1. **Disable affected credentials:**
   ```bash
   # Revoke API keys associated with the incident
   python scripts/security/revoke_api_keys.py --keys <key_ids>
   
   # Invalidate user sessions
   python scripts/security/invalidate_sessions.py --user-ids <user_ids>
   ```

2. **Freeze exports:**
   ```bash
   # Disable all deliverable exports temporarily
   kubectl set env deploy/deliverables-service -n idis \
     EXPORTS_DISABLED=true
   ```

3. **Revoke OAuth tokens:**
   ```bash
   # Revoke all tokens for affected accounts
   python scripts/security/revoke_oauth_tokens.py --scope affected
   ```

4. **Enable enhanced logging:**
   ```bash
   # Increase audit verbosity
   kubectl set env deploy/api-gateway -n idis \
     AUDIT_LEVEL=verbose \
     LOG_REQUEST_BODIES=true
   ```

5. **Isolate affected systems (if needed):**
   ```bash
   # If active exfiltration suspected, isolate the service
   kubectl scale deploy/api-gateway -n idis --replicas=0
   # Redirect traffic to maintenance page
   ```

---

## Recovery

**Recovery Steps (after containment confirmed):**

1. **Root cause fix:**

   **If RLS bug:**
   ```sql
   -- Verify and fix RLS policies
   ALTER TABLE claims ENABLE ROW LEVEL SECURITY;
   DROP POLICY IF EXISTS tenant_isolation ON claims;
   CREATE POLICY tenant_isolation ON claims
     USING (tenant_id = current_setting('app.tenant_id')::uuid);
   ```

   **If cache keying error:**
   ```bash
   # Flush all caches
   kubectl exec -n idis deploy/cache -- redis-cli FLUSHALL
   
   # Fix cache key generation
   # Deploy fix that includes tenant_id in all cache keys
   ```

   **If misconfigured RBAC:**
   ```bash
   # Review and fix role assignments
   python scripts/security/audit_rbac.py --fix-violations
   ```

2. **Validate isolation tests across tenants:**
   ```bash
   # Run comprehensive isolation test suite
   pytest tests/test_tenant_isolation.py -v --tb=long
   ```

3. **Rotate potentially compromised secrets:**
   ```bash
   # Rotate all secrets as precaution
   python scripts/security/rotate_all_secrets.py --confirm
   ```

4. **Restore services with fixes:**
   ```bash
   kubectl scale deploy/api-gateway -n idis --replicas=3
   kubectl set env deploy/deliverables-service -n idis EXPORTS_DISABLED-
   ```

5. **Document regulatory notifications:**
   - Determine if breach notification is required
   - Prepare customer notifications if needed
   - Document timeline for compliance records

---

## Verification

**Success Criteria:**

1. **Isolation test suite passes:**
   ```bash
   # Full isolation verification
   pytest tests/test_tenant_isolation.py tests/test_rls.py -v
   ```
   - [ ] All tenant isolation tests pass
   - [ ] RLS policies verified on all tables
   - [ ] Cache isolation verified

2. **Audit integrity verified:**
   - [ ] Audit events for incident period preserved
   - [ ] No gaps in audit trail
   - [ ] Evidence chain documented

3. **No ongoing violations:**
   - [ ] `tenant_isolation_violations_total` = 0 for 24+ hours
   - [ ] No anomalous access patterns detected

4. **Credentials rotated:**
   - [ ] All affected API keys replaced
   - [ ] OAuth tokens refreshed
   - [ ] Database credentials rotated (if applicable)

---

## Postmortem

**MANDATORY for all tenant isolation incidents**

**Timeline:** 
- Incident report within 24 hours
- Full postmortem within 72 hours
- Action item review within 1 week

**Required Sections:**

1. **Incident Summary:**
   - What happened
   - When it was detected
   - Impact scope (tenants, data, duration)

2. **Timeline of Events:**
   - Minute-by-minute reconstruction
   - Detection → Containment → Recovery

3. **Root Cause Analysis:**
   - Technical root cause
   - Contributing factors
   - Why existing controls failed

4. **Impact Assessment:**
   - Tenants affected
   - Data potentially exposed
   - Regulatory/contractual implications

5. **Remediation Actions:**
   - Immediate fixes applied
   - Long-term improvements planned
   - Prevention measures

6. **Disclosure Requirements:**
   - Regulatory notifications required?
   - Customer notifications required?
   - Timeline for notifications

**Distribution:**
- Executive team
- Security team
- Legal/Compliance
- Board (if significant)
- Affected customers (as required)

---

## Regulatory Considerations

**GDPR (if applicable):**
- 72-hour notification window to supervisory authority
- Notification to affected individuals if high risk

**SOC2:**
- Document incident in security incident log
- Update risk assessment
- Evidence for next audit

**Customer Contracts:**
- Review SLA breach notification requirements
- Document SLA credits if applicable
