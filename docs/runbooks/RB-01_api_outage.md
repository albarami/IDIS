# RB-01: API Outage / Elevated 5xx

**Runbook ID:** RB-01  
**Severity:** SEV-1 (if widespread) / SEV-2 (if isolated)  
**Owner:** Platform Team  
**Last Updated:** 2026-01-19  
**Alert:** `IDISApi5xxErrorRate`

---

## Detection

**Trigger Conditions:**
- API 5xx error rate exceeds 1% for 5+ minutes
- Availability SLO breach detected
- Multiple tenants reporting API failures

**Observable Signals:**
- `http_requests_total{status=~"5.."}` spike in Prometheus
- API Gateway health check failures
- Elevated latency on critical endpoints
- Customer-reported issues via support channels

**Dashboard:** `idis-api-availability`

---

## Triage

**Immediate Actions (First 5 minutes):**

1. **Identify scope:**
   - Single tenant or multi-tenant?
   - All endpoints or specific routes?
   - Single region or global?

2. **Check gateway health:**
   ```bash
   kubectl get pods -n idis -l component=api-gateway
   kubectl logs -n idis -l component=api-gateway --tail=100
   ```

3. **Check database connectivity:**
   ```bash
   kubectl exec -n idis deploy/api-gateway -- pg_isready -h $DB_HOST
   ```

4. **Identify failing endpoints (top 5):**
   ```promql
   topk(5, sum by (endpoint) (rate(http_requests_total{status=~"5.."}[5m])))
   ```

5. **Inspect recent deployments and config changes:**
   ```bash
   kubectl rollout history deploy/api-gateway -n idis
   git log --oneline -10 -- src/idis/api/
   ```

**Escalation Path:**
- SEV-2: Platform on-call
- SEV-1: Platform lead + Backend lead + Incident Commander

---

## Containment

**Immediate Containment Actions:**

1. **Roll back last deployment (if correlated):**
   ```bash
   kubectl rollout undo deploy/api-gateway -n idis
   ```

2. **Rate limit abusive traffic patterns:**
   - Enable emergency rate limits in API Gateway config
   - Block suspicious IPs if DDoS suspected

3. **Scale API pods/instances:**
   ```bash
   kubectl scale deploy/api-gateway -n idis --replicas=10
   ```

4. **Enable circuit breakers:**
   - Activate circuit breakers for failing downstream services
   - Enable fallback responses where applicable

5. **Communication:**
   - Post status page update (if customer-facing impact)
   - Notify affected tenant contacts

---

## Recovery

**Recovery Steps:**

1. **Restore database connections:**
   - Verify connection pool health
   - Restart connection pooler if needed
   - Check for long-running queries blocking connections

2. **Fix configuration issues:**
   - Verify environment variables are correct
   - Check secrets/credentials haven't expired
   - Validate service discovery endpoints

3. **Verify tenant routing logic:**
   - Confirm tenant middleware is correctly extracting tenant_id
   - Check RLS policies are not blocking valid requests

4. **Restore normal traffic flow:**
   - Gradually reduce rate limits
   - Scale down emergency replicas
   - Re-enable disabled features

---

## Verification

**Success Criteria:**

1. **SLO metrics restored:**
   - [ ] p95 latency back within SLO (<300ms GET, <600ms POST/PATCH)
   - [ ] 5xx rate below 0.1%
   - [ ] Availability above 99.9%

2. **Smoke tests pass:**
   ```bash
   # Create deal
   curl -X POST $API_URL/v1/deals -H "X-Tenant-ID: test" -d '{"name": "test"}'
   
   # List claims
   curl $API_URL/v1/claims -H "X-Tenant-ID: test"
   
   # Health check
   curl $API_URL/health
   ```

3. **Audit trail intact:**
   - [ ] Verify audit events are being emitted
   - [ ] Check audit ingestion lag is within SLO

4. **No customer-reported issues:**
   - [ ] Support queue clear of new API-related tickets
   - [ ] Status page updated to "Operational"

---

## Postmortem

**Required for:** SEV-1 and SEV-2 incidents

**Timeline:**
- Draft postmortem within 48 hours
- Review meeting within 5 business days
- Action items tracked in issue tracker

**Template Sections:**
1. Incident summary
2. Timeline of events
3. Root cause analysis (5 Whys)
4. Impact assessment (tenants affected, duration, SLO burn)
5. What went well
6. What could be improved
7. Action items with owners and deadlines

**Distribution:**
- Engineering team
- SRE team
- Product stakeholders
- Customer success (if customer-facing impact)
