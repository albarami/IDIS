# RB-04: Sanad Graph Degradation (Graph DB Slow/Down)

**Runbook ID:** RB-04  
**Severity:** SEV-2  
**Owner:** Data/Backend Team  
**Last Updated:** 2026-01-19  
**Alert:** `IDISSanadRetrievalLatencyHigh`

---

## Detection

**Trigger Conditions:**
- Sanad retrieval p95 latency exceeds 1.2 seconds
- Graph database timeouts
- Grading pipeline stalled

**Observable Signals:**
- `sanad_retrieval_duration_seconds` p95 > 1.2s
- Graph DB connection pool exhaustion
- Claims stuck in "PENDING_GRADE" status
- Truth Dashboard loading slowly or timing out

**Dashboard:** `idis-sanad-grading`

---

## Triage

**Immediate Actions (First 10 minutes):**

1. **Check graph DB health:**
   ```bash
   # Check graph DB pod status
   kubectl get pods -n idis -l component=graph-db
   
   # Check CPU/memory utilization
   kubectl top pods -n idis -l component=graph-db
   ```

2. **Check connection pool status:**
   ```promql
   graph_db_connection_pool_used / graph_db_connection_pool_max
   ```

3. **Identify heavy queries (hotspots):**
   ```bash
   # Check slow query log
   kubectl logs -n idis -l component=graph-db --tail=500 | grep "slow query"
   ```

4. **Check index coverage:**
   ```cypher
   -- List missing indexes
   CALL db.indexes() YIELD name, labelsOrTypes, properties
   RETURN name, labelsOrTypes, properties
   ```

5. **Identify affected tenants:**
   ```promql
   histogram_quantile(0.95, 
     sum by (tenant_id, le) (rate(sanad_retrieval_duration_seconds_bucket[5m]))
   ) > 1.2
   ```

**Escalation Path:**
- SEV-2: Data team on-call + SRE on-call
- SEV-1 (if blocking IC generation): + Incident Commander

---

## Containment

**Immediate Containment Actions:**

1. **Reduce query fanout:**
   ```bash
   # Limit traversal depth temporarily
   kubectl set env deploy/sanad-service -n idis MAX_TRAVERSAL_DEPTH=3
   ```

2. **Enable tenant-scoped caches:**
   ```bash
   # Enable aggressive caching for Sanad lookups
   kubectl set env deploy/sanad-service -n idis SANAD_CACHE_TTL=300
   ```

3. **Rate limit Sanad map visualization requests:**
   - These are often the heaviest queries
   - Reduce concurrent visualization requests

4. **Kill long-running queries:**
   ```cypher
   -- Find and kill queries running > 30 seconds
   CALL dbms.listQueries() YIELD queryId, elapsedTimeMillis
   WHERE elapsedTimeMillis > 30000
   CALL dbms.killQuery(queryId) YIELD queryId
   RETURN queryId
   ```

5. **Communication:**
   - Notify users of potential Truth Dashboard slowness
   - Delay non-urgent Sanad-dependent operations

---

## Recovery

**Recovery Steps:**

1. **Scale graph DB:**
   ```bash
   # Scale read replicas
   kubectl scale statefulset/graph-db-replica -n idis --replicas=3
   ```

2. **Add missing indexes:**
   ```cypher
   CREATE INDEX claim_sanad_idx FOR (c:Claim) ON (c.claim_id, c.tenant_id);
   CREATE INDEX evidence_chain_idx FOR ()-[r:EVIDENCE_FOR]-() ON (r.tenant_id);
   ```

3. **Optimize traversal depth:**
   - Review query patterns
   - Implement pagination for deep chains
   - Pre-compute common traversal paths

4. **Serve from materialized Sanad views (if needed):**
   - Fall back to Postgres materialized views
   - Accept slightly stale data for read operations
   ```bash
   kubectl set env deploy/sanad-service -n idis USE_MATERIALIZED_VIEW=true
   ```

5. **Restore normal operation:**
   - Increase traversal depth limits
   - Reduce cache TTL to normal
   - Disable materialized view fallback

---

## Verification

**Success Criteria:**

1. **Latency restored:**
   - [ ] Sanad retrieval p95 < 1.2 seconds
   - [ ] No graph DB timeouts for 30+ minutes

2. **Grading pipeline unblocked:**
   - [ ] Claims in "PENDING_GRADE" are processing
   - [ ] Grade distribution returning to normal

3. **Functional verification:**
   ```bash
   # Test Sanad retrieval for sample claims
   curl "$API_URL/v1/claims/{claim_id}/sanad" -H "X-Tenant-ID: test"
   ```

4. **Truth Dashboard responsive:**
   - [ ] Dashboard loads within 3 seconds
   - [ ] Sanad visualization renders correctly

---

## Postmortem

**Required for:** SEV-2 incidents with >15 minute latency breach

**Analysis Focus:**
1. Query pattern changes that caused degradation
2. Index coverage adequacy
3. Capacity planning for graph DB
4. Effectiveness of caching strategy

**Capacity Planning:**
- Review growth projections for Sanad graph size
- Plan for horizontal scaling needs
- Evaluate graph DB alternatives if recurring
