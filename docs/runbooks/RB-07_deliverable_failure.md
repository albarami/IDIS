# RB-07: Deliverable Generation Failures (PDF/DOCX)

**Runbook ID:** RB-07  
**Severity:** SEV-2  
**Owner:** Backend Team  
**Last Updated:** 2026-01-19  
**Alert:** `IDISDeliverableGenerationFailure`

---

## Detection

**Trigger Conditions:**
- Deliverable success rate drops below 99%
- PDF/DOCX generation timeouts
- Template rendering failures

**Observable Signals:**
- `deliverable_failures_total` increasing
- Object store write errors
- Template engine exceptions in logs
- IC export requests timing out

**Dashboard:** `idis-deliverable-success`

---

## Triage

**Immediate Actions (First 10 minutes):**

1. **Identify failing templates:**
   ```promql
   sum by (template_type) (rate(deliverable_failures_total[15m]))
   ```
   Template types: IC_MEMO, SCREENING_SNAPSHOT, AUDIT_APPENDIX

2. **Check object store access:**
   ```bash
   # Test write permissions
   kubectl exec -n idis deploy/deliverables-service -- \
     aws s3 cp /tmp/test.txt s3://$BUCKET_NAME/test/
   
   # Test URL signing
   kubectl exec -n idis deploy/deliverables-service -- \
     aws s3 presign s3://$BUCKET_NAME/test/test.txt
   ```

3. **Verify claim/calc references present:**
   ```sql
   SELECT d.deliverable_id, d.deal_id, d.error_message
   FROM deliverables d
   WHERE d.status = 'FAILED'
   AND d.created_at > NOW() - INTERVAL '1 hour'
   LIMIT 20;
   ```

4. **Check template engine health:**
   ```bash
   kubectl logs -n idis -l component=deliverables-service --tail=200 | grep -i "template\|render\|error"
   ```

5. **Check for resource exhaustion:**
   ```bash
   kubectl top pods -n idis -l component=deliverables-service
   ```

**Escalation Path:**
- SEV-2: Backend on-call
- SEV-1 (if all IC generation blocked): + Incident Commander

---

## Containment

**Immediate Containment Actions:**

1. **Fall back to JSON deliverables:**
   ```bash
   # Enable JSON-only mode (bypasses PDF/DOCX generation)
   kubectl set env deploy/deliverables-service -n idis \
     DELIVERABLE_FORMAT_FALLBACK=json
   ```

2. **Disable large graphs in PDF:**
   ```bash
   # Skip Sanad visualization to reduce timeout risk
   kubectl set env deploy/deliverables-service -n idis \
     SKIP_SANAD_GRAPH=true \
     MAX_CLAIMS_IN_APPENDIX=50
   ```

3. **Scale deliverables service:**
   ```bash
   kubectl scale deploy/deliverables-service -n idis --replicas=5
   ```

4. **Queue priority for critical deliverables:**
   - Prioritize IC memos over screening snapshots
   - Delay non-urgent deliverable requests

5. **Communication:**
   - Notify users that PDF format may be temporarily unavailable
   - Provide JSON alternative access

---

## Recovery

**Recovery Steps:**

1. **Patch generator:**
   - Fix template rendering issues
   - Update PDF library if version-related
   - Fix object store integration

2. **Rerun failed deliverables:**
   ```bash
   # Rerun with idempotency keys
   python scripts/rerun_deliverables.py \
     --deliverable-ids <failed_ids> \
     --format pdf
   ```

3. **Restore PDF/DOCX generation:**
   ```bash
   kubectl set env deploy/deliverables-service -n idis \
     DELIVERABLE_FORMAT_FALLBACK- \
     SKIP_SANAD_GRAPH- \
     MAX_CLAIMS_IN_APPENDIX-
   ```

4. **Verify all deliverable types:**
   ```bash
   # Test each template type
   python scripts/test_deliverables.py \
     --templates ic_memo,screening_snapshot,audit_appendix
   ```

5. **Scale down:**
   ```bash
   kubectl scale deploy/deliverables-service -n idis --replicas=2
   ```

---

## Verification

**Success Criteria:**

1. **Deliverables ready:**
   - [ ] Success rate ≥ 99%
   - [ ] No new failures for 30+ minutes

2. **Access controls correct:**
   - [ ] Signed URLs working
   - [ ] Tenant isolation verified on object store

3. **Format verification:**
   ```bash
   # Download and validate sample deliverables
   python scripts/validate_deliverables.py --sample-size 5
   ```

4. **No-Free-Facts compliance:**
   - [ ] All facts in deliverables have claim_id or calc_id
   - [ ] Validation gate passing

---

## Postmortem

**Required for:** SEV-2 incidents with >30 minute impact

**Analysis Focus:**
1. Root cause of template/generation failure
2. Object store reliability and fallback strategy
3. Resource sizing for deliverables service
4. Timeout thresholds appropriateness

**Quality Review:**
- Verify deliverables generated during incident are valid
- Check for any No-Free-Facts violations that may have slipped through
