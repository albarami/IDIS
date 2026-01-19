# RB-08: Audit Ingestion Lag / Missing Audit Events

**Runbook ID:** RB-08  
**Severity:** SEV-1 (missing events) / SEV-2 (lag only)  
**Owner:** Security/SRE Team  
**Last Updated:** 2026-01-19  
**Alert:** `IDISAuditIngestionLag`, `IDISMissingAuditEvents`

---

## Detection

**Trigger Conditions:**
- Audit ingestion lag exceeds 5 minutes
- Missing audit events for mutating operations (coverage < 100%)
- Audit store availability issues

**Observable Signals:**
- `audit_ingestion_lag_seconds` > 300
- `audit_events_missing_total` increasing
- Audit middleware errors in API logs
- Compliance alerts from audit coverage checks

**Dashboard:** `idis-audit-coverage`

---

## Triage

**Immediate Actions (First 5 minutes):**

1. **Validate audit middleware on API gateway:**
   ```bash
   kubectl logs -n idis -l component=api-gateway --tail=100 | grep -i audit
   ```

2. **Check audit store availability:**
   ```bash
   # Postgres audit table check
   kubectl exec -n idis deploy/api-gateway -- \
     psql $AUDIT_DB_URL -c "SELECT COUNT(*) FROM audit_events WHERE created_at > NOW() - INTERVAL '5 minutes';"
   ```

3. **Check audit queue status:**
   ```promql
   audit_queue_depth
   audit_queue_oldest_message_age_seconds
   ```

4. **Identify missing event types:**
   ```sql
   -- Compare expected vs actual events
   SELECT event_type, COUNT(*) as expected
   FROM (
     SELECT 'claim.created' as event_type FROM claims WHERE created_at > NOW() - INTERVAL '1 hour'
     UNION ALL
     SELECT 'deal.created' FROM deals WHERE created_at > NOW() - INTERVAL '1 hour'
   ) expected
   GROUP BY event_type;
   
   -- vs actual
   SELECT event_type, COUNT(*) as actual
   FROM audit_events
   WHERE created_at > NOW() - INTERVAL '1 hour'
   GROUP BY event_type;
   ```

5. **Check for endpoint coverage gaps:**
   ```promql
   # Endpoints with mutations but no audit events
   sum by (endpoint) (rate(http_requests_total{method=~"POST|PATCH|DELETE"}[5m])) 
   unless 
   sum by (endpoint) (rate(audit_events_emitted_total[5m]))
   ```

**Escalation Path:**
- SEV-1 (missing events): Security lead + Compliance + Incident Commander
- SEV-2 (lag only): SRE on-call

---

## Containment

**Immediate Containment Actions:**

1. **Pause high-risk operations:**
   ```bash
   # If audit integrity compromised, pause:
   # - Human override endpoints
   # - IC export endpoints
   # - Credential/permission changes
   kubectl set env deploy/api-gateway -n idis \
     PAUSE_HIGH_RISK_OPS=true
   ```

2. **Enable synchronous audit fallback:**
   ```bash
   # Switch from async to sync audit writing
   kubectl set env deploy/api-gateway -n idis \
     AUDIT_MODE=synchronous
   ```

3. **Preserve request logs for backfill:**
   ```bash
   # Ensure request logs are retained
   kubectl set env deploy/api-gateway -n idis \
     REQUEST_LOG_RETENTION=72h
   ```

4. **Alert compliance team:**
   - Document timeline of potential audit gap
   - Preserve evidence of detection and response

5. **Communication:**
   - Internal security notification
   - Prepare compliance disclosure if required

---

## Recovery

**Recovery Steps:**

1. **Restore audit pipeline:**
   - Fix audit store connectivity
   - Scale audit workers if needed
   - Clear audit queue backlog

2. **Backfill events from request logs:**
   ```bash
   # If backfill possible
   python scripts/backfill_audit_events.py \
     --start-time "2024-01-19T10:00:00Z" \
     --end-time "2024-01-19T11:00:00Z" \
     --source request_logs
   ```

3. **If backfill impossible:**
   - **This is SEV-1 and requires compliance escalation**
   - Document the gap period
   - Identify affected tenants and operations
   - Prepare disclosure to affected parties if required

4. **Verify coverage restored:**
   ```sql
   SELECT 
     COUNT(DISTINCT request_id) as requests,
     COUNT(DISTINCT audit_event_id) as events,
     COUNT(DISTINCT request_id) = COUNT(DISTINCT audit_event_id) as coverage_ok
   FROM (
     SELECT request_id FROM http_requests WHERE method IN ('POST','PATCH','DELETE') AND created_at > NOW() - INTERVAL '15 minutes'
   ) r
   LEFT JOIN audit_events a ON r.request_id = a.request_id;
   ```

5. **Resume normal operations:**
   ```bash
   kubectl set env deploy/api-gateway -n idis \
     PAUSE_HIGH_RISK_OPS=false \
     AUDIT_MODE=async
   ```

---

## Verification

**Success Criteria:**

1. **Coverage restored:**
   - [ ] Audit coverage = 100% for all mutating operations
   - [ ] No missing events for 30+ minutes

2. **Lag within SLO:**
   - [ ] Audit ingestion lag < 5 minutes
   - [ ] Queue depth at normal levels

3. **Integrity checks pass:**
   ```bash
   # Run audit integrity verification
   python scripts/verify_audit_integrity.py --hours 24
   ```

4. **Compliance verification:**
   - [ ] Gap period documented
   - [ ] Backfill completed or gap disclosed
   - [ ] No compliance violations pending

---

## Postmortem

**Required for:** All SEV-1 incidents; SEV-2 with >15 minute lag

**Critical Analysis:**
1. Why did audit coverage fail?
2. Was the gap detectable earlier?
3. Can backfill cover the gap?
4. What regulatory/contractual implications exist?

**Compliance Focus:**
- Document exact gap period and affected operations
- List tenants potentially affected
- Record disclosure decisions and timeline
- Update audit coverage monitoring thresholds if needed
