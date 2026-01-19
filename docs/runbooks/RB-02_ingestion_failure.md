# RB-02: Ingestion Pipeline Failure (Parsing/OCR)

**Runbook ID:** RB-02  
**Severity:** SEV-2  
**Owner:** Data/Backend Team  
**Last Updated:** 2026-01-19  
**Alert:** `IDISIngestionFailureRate`, `IDISOCRQueueTimeHigh`

---

## Detection

**Trigger Conditions:**
- Ingestion failure rate exceeds 2% for 15+ minutes
- OCR queue p95 wait time exceeds 60 minutes for 30+ minutes
- Queue backlog growing continuously

**Observable Signals:**
- `ingestion_errors_total` increasing
- `ocr_queue_depth` above normal thresholds
- Document status stuck in "PROCESSING"
- Customer-reported failed document uploads

**Dashboard:** `idis-ingestion`, `idis-queue-depth`

---

## Triage

**Immediate Actions (First 10 minutes):**

1. **Identify failing document types:**
   ```promql
   sum by (doc_type) (rate(ingestion_errors_total[15m]))
   ```

2. **Check OCR worker saturation:**
   ```bash
   kubectl top pods -n idis -l component=ocr-worker
   kubectl get pods -n idis -l component=ocr-worker
   ```

3. **Verify object store access:**
   ```bash
   kubectl exec -n idis deploy/ingestion-service -- \
     aws s3 ls s3://$BUCKET_NAME/documents/ --max-items 1
   ```

4. **Check antivirus scan status:**
   - Verify AV service is healthy
   - Check for quarantined documents

5. **Inspect parser logs for errors:**
   ```bash
   kubectl logs -n idis -l component=parser --tail=200 | grep -i error
   ```

**Escalation Path:**
- SEV-2: Data team on-call + Backend on-call
- SEV-1 (if affecting IC deliverables): + Incident Commander

---

## Containment

**Immediate Containment Actions:**

1. **Pause auto-ingest for new documents (if needed):**
   ```bash
   kubectl scale deploy/ingestion-webhook -n idis --replicas=0
   ```
   - This prevents queue overflow while investigating

2. **Route OCR to separate pool:**
   ```bash
   # Scale up dedicated OCR worker pool
   kubectl scale deploy/ocr-worker-overflow -n idis --replicas=5
   ```

3. **Increase OCR concurrency:**
   ```bash
   kubectl set env deploy/ocr-worker -n idis OCR_CONCURRENCY=4
   ```

4. **Isolate problematic document types:**
   - Route failing doc types to dead-letter queue
   - Continue processing known-good types

5. **Communication:**
   - Notify affected tenants of processing delays
   - Update internal status channel

---

## Recovery

**Recovery Steps:**

1. **Fix parser bugs:**
   - Identify specific parser failure mode
   - Deploy hotfix or rollback parser version
   - Validate fix with sample documents

2. **Patch extractor rules:**
   - Update extraction patterns for failing doc types
   - Add handling for edge cases

3. **Requeue failed documents (idempotent):**
   ```bash
   # Use idempotency keys to safely requeue
   python scripts/requeue_failed_documents.py --tenant-id $TENANT_ID --since "2h ago"
   ```

4. **Restore normal ingestion flow:**
   - Scale ingestion webhook back up
   - Return to normal OCR worker count
   - Disable overflow pool

---

## Verification

**Success Criteria:**

1. **Ingestion success restored:**
   - [ ] Ingestion success rate returns to ≥99%
   - [ ] No new ingestion errors for 15+ minutes

2. **Backlog drains:**
   - [ ] Queue depth below normal threshold
   - [ ] Queue wait time p95 below 10 minutes

3. **Sample document validation:**
   ```bash
   # Test ingestion of each doc type
   python scripts/test_ingestion.py --doc-types pdf,xlsx,docx
   ```

4. **Audit trail:**
   - [ ] All requeued documents have audit events
   - [ ] No orphaned document records

---

## Postmortem

**Required for:** SEV-2 incidents with >30 minute duration

**Focus Areas:**
1. Root cause of parser/OCR failure
2. Queue capacity planning adequacy
3. Monitoring coverage gaps
4. Recovery automation opportunities

**Metrics to Include:**
- Documents affected
- Tenant impact
- Time to detection
- Time to resolution
- Error budget burn
