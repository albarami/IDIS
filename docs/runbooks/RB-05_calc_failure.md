# RB-05: Calc Service Failures / Reproducibility Issues

**Runbook ID:** RB-05  
**Severity:** SEV-2  
**Owner:** Data/Backend Team  
**Last Updated:** 2026-01-19  
**Alert:** `IDISCalcReproducibilityFailure`

---

## Detection

**Trigger Conditions:**
- Calc run failure rate exceeds threshold
- Reproducibility check failures > 0.1% in 24 hours
- Calc-Sanad hash mismatches detected

**Observable Signals:**
- `calc_reproducibility_failures_total` increasing
- `calc_failures_total` spike
- Different outputs for same input claims
- IC deliverables blocked due to calc failures

**Dashboard:** `idis-calc-success`

---

## Triage

**Immediate Actions (First 10 minutes):**

1. **Identify failing calc_name + version:**
   ```promql
   sum by (calc_name, calc_version) (rate(calc_failures_total[15m]))
   ```

2. **Check for dependency changes:**
   ```bash
   # Check Python package versions
   kubectl exec -n idis deploy/calc-service -- pip freeze | diff - requirements.lock
   
   # Check calc service version
   kubectl get deploy/calc-service -n idis -o jsonpath='{.spec.template.spec.containers[0].image}'
   ```

3. **Check for environment drift:**
   ```bash
   # Compare env vars across pods
   kubectl exec -n idis deploy/calc-service -- env | sort > pod1_env.txt
   # Compare with expected
   diff pod1_env.txt expected_env.txt
   ```

4. **Validate input claims and grades:**
   ```sql
   SELECT c.claim_id, c.grade, c.extraction_confidence, c.dhabt_score
   FROM claims c
   JOIN calc_inputs ci ON c.claim_id = ci.claim_id
   WHERE ci.calc_id IN (SELECT calc_id FROM calcs WHERE status = 'FAILED' AND created_at > NOW() - INTERVAL '1 hour')
   LIMIT 20;
   ```

5. **Check extraction gate blocks:**
   ```promql
   rate(calc_extraction_gate_blocks_total[15m])
   ```

**Escalation Path:**
- SEV-2: Data team on-call
- SEV-1 (if IC calcs affected): + Backend lead + Incident Commander

---

## Containment

**Immediate Containment Actions:**

1. **Block affected calculators from IC outputs:**
   ```bash
   # Disable specific calc for IC-bound outputs
   kubectl set env deploy/calc-service -n idis \
     BLOCKED_CALCS_FOR_IC="revenue_growth,market_size"
   ```

2. **Force `blocked_for_ic=true` on affected calcs:**
   ```sql
   UPDATE calcs
   SET blocked_for_ic = true,
       block_reason = 'Reproducibility failure - under investigation'
   WHERE calc_name IN ('affected_calc_name')
   AND created_at > NOW() - INTERVAL '24 hours';
   ```

3. **Create defects for affected outputs:**
   ```python
   # Script to create defects
   python scripts/create_calc_defects.py --calc-ids <affected_calc_ids>
   ```

4. **Halt automated IC generation:**
   - Require human review for ICs with affected calcs
   - Queue for manual verification

5. **Communication:**
   - Notify IC preparation teams of affected deals
   - Document which calcs are blocked

---

## Recovery

**Recovery Steps:**

1. **Roll back calc version:**
   ```bash
   # Roll back to last known good version
   kubectl set image deploy/calc-service -n idis \
     calc-service=idis/calc-service:v1.2.3-stable
   ```

2. **Pin environment:**
   ```bash
   # Ensure requirements.lock is used
   kubectl set env deploy/calc-service -n idis \
     PIP_CONSTRAINT=/app/requirements.lock
   ```

3. **Re-run calcs with pinned env and formula hash:**
   ```bash
   # Re-run with strict reproducibility mode
   python scripts/rerun_calcs.py \
     --calc-ids <affected_calc_ids> \
     --formula-hash <expected_hash> \
     --strict-mode
   ```

4. **Verify reproducibility:**
   ```bash
   # Run reproducibility check
   python scripts/verify_reproducibility.py --calc-ids <calc_ids>
   ```

5. **Unblock calcs for IC:**
   ```sql
   UPDATE calcs
   SET blocked_for_ic = false,
       block_reason = null
   WHERE calc_name IN ('fixed_calc_name')
   AND reproducibility_verified = true;
   ```

---

## Verification

**Success Criteria:**

1. **Reproducibility hash stable:**
   - [ ] Same inputs produce same reproducibility_hash
   - [ ] Reproducibility failure rate < 0.1%

2. **Outputs match golden tests:**
   ```bash
   pytest tests/test_calc_golden.py -v
   ```

3. **Calc-Sanad integrity:**
   - [ ] All re-run calcs have valid Calc-Sanad
   - [ ] Input claim_ids traced correctly
   - [ ] Formula hash matches expected

4. **IC deliverables unblocked:**
   - [ ] Affected calcs no longer blocking IC generation
   - [ ] Defects resolved or waived with justification

---

## Postmortem

**Required for:** All reproducibility failures affecting IC outputs

**Critical Analysis:**
1. Root cause of reproducibility failure
2. How did environment drift occur?
3. Was the extraction gate working correctly?
4. Should affected calcs have been blocked earlier?

**Trust Invariant Focus:**
- Deterministic numerics is a hard gate
- Document any calcs that reached IC with potential issues
- Review calc versioning and pinning strategy
