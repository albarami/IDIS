# RB-09: Integration Provider Outage (CRM / Docs / Enrichment)

**Runbook ID:** RB-09  
**Severity:** SEV-2 (single provider) / SEV-3 (with fallbacks)  
**Owner:** Integrations Team  
**Last Updated:** 2026-01-19  
**Alert:** `IDISIntegrationProviderErrors`

---

## Detection

**Trigger Conditions:**
- Integration error rate spike for specific provider
- Webhook delivery failures exceeding threshold
- Enrichment data stale or unavailable

**Observable Signals:**
- `integration_errors_total{provider="X"}` increasing
- `webhook_delivery_failures_total` spike
- Timeout errors in integration logs
- Users reporting missing CRM sync or enrichment data

**Dashboard:** `idis-integration-health`

---

## Triage

**Immediate Actions (First 10 minutes):**

1. **Identify affected provider and endpoints:**
   ```promql
   sum by (provider, endpoint) (rate(integration_errors_total[15m]))
   ```

2. **Check rate limits:**
   ```bash
   kubectl logs -n idis -l component=integrations-service --tail=200 | grep -i "rate limit\|429"
   ```

3. **Check auth token expiry:**
   ```bash
   # Check OAuth token status
   kubectl exec -n idis deploy/integrations-service -- \
     python -c "from idis.integrations import check_token; print(check_token('provider_name'))"
   ```

4. **Verify provider status page:**
   - Check provider's status page for known outages
   - Check provider's API health endpoints if available

5. **Assess impact scope:**
   ```sql
   SELECT tenant_id, COUNT(*) as affected_deals
   FROM deals
   WHERE crm_sync_status = 'FAILED'
   AND updated_at > NOW() - INTERVAL '1 hour'
   GROUP BY tenant_id;
   ```

**Escalation Path:**
- SEV-3 (fallback available): Integrations on-call
- SEV-2 (no fallback, material impact): + Backend on-call

---

## Containment

**Immediate Containment Actions:**

1. **Switch to polling fallback (if applicable):**
   ```bash
   # Disable webhook, enable polling
   kubectl set env deploy/integrations-service -n idis \
     CRM_MODE=polling \
     CRM_POLL_INTERVAL=5m
   ```

2. **Delay enrichment steps:**
   ```bash
   # Mark enrichment as deferred
   kubectl set env deploy/enrichment-service -n idis \
     ENRICHMENT_DEFER_ON_ERROR=true
   ```

3. **Create "stale" defects for affected data:**
   ```bash
   # Create defects for claims with stale enrichment
   python scripts/create_stale_defects.py \
     --provider crm_provider \
     --since "1 hour ago"
   ```

4. **Queue webhook retries:**
   ```bash
   # Queue failed webhooks for retry
   python scripts/queue_webhook_retries.py \
     --provider affected_provider \
     --max-retries 10
   ```

5. **Communication:**
   - Notify affected tenants of sync delays
   - Update internal status with provider information

---

## Recovery

**Recovery Steps:**

1. **Re-auth integration:**
   ```bash
   # Rotate and refresh OAuth tokens
   python scripts/integrations/refresh_tokens.py --provider crm_provider
   ```

2. **Rotate tokens if compromised:**
   ```bash
   # Full token rotation
   python scripts/integrations/rotate_credentials.py \
     --provider crm_provider \
     --store-in-vault
   ```

3. **Reconcile missed events:**
   ```bash
   # Use provider's replay API to catch up
   python scripts/integrations/reconcile.py \
     --provider crm_provider \
     --since "2 hours ago"
   ```

4. **Clear stale defects:**
   ```sql
   UPDATE defects
   SET status = 'RESOLVED',
       resolution = 'Data refreshed after provider recovery'
   WHERE defect_type = 'STALE_ENRICHMENT'
   AND provider = 'crm_provider'
   AND created_at > NOW() - INTERVAL '4 hours';
   ```

5. **Restore normal mode:**
   ```bash
   kubectl set env deploy/integrations-service -n idis \
     CRM_MODE=webhook \
     CRM_POLL_INTERVAL- \
     ENRICHMENT_DEFER_ON_ERROR-
   ```

---

## Verification

**Success Criteria:**

1. **Sync resumes:**
   - [ ] Integration success rate restored
   - [ ] No new errors for 15+ minutes

2. **Audit shows recovered events:**
   - [ ] Audit events for reconciled data present
   - [ ] No gaps in sync timeline

3. **Data verification:**
   ```bash
   # Compare local vs remote data for sample records
   python scripts/integrations/verify_sync.py \
     --provider crm_provider \
     --sample-size 20
   ```

4. **Webhook delivery restored:**
   - [ ] Webhook success rate back to normal
   - [ ] Retry queue draining

---

## Postmortem

**Required for:** SEV-2 incidents; SEV-3 with >2 hour duration

**Analysis Focus:**
1. Root cause of provider failure
2. Was fallback mechanism effective?
3. Data reconciliation completeness
4. Communication timeliness to affected users

**Resilience Review:**
- Evaluate multi-provider strategy
- Review fallback mechanism reliability
- Consider local caching improvements
