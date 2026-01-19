# RB-03: Claim Validator Spike (No-Free-Facts / Schema Reject)

**Runbook ID:** RB-03  
**Severity:** SEV-1 (if affecting IC deliverables) / SEV-2 (if contained to pipeline)  
**Owner:** Backend Team  
**Last Updated:** 2026-01-19  
**Alert:** `IDISNoFreeFactsViolation`

---

## Detection

**Trigger Conditions:**
- High rate of rejected claims or deliverable validation failures
- No-Free-Facts validator blocking IC deliverables
- Muḥāsabah gate rejecting agent outputs

**Observable Signals:**
- `validator_rejects_total` spike
- `no_free_facts_violations_total` increase
- `deliverable_no_free_facts_failures_total` > 0
- Deliverable generation success rate drop

**Dashboard:** `idis-claim-registry`, `idis-deliverable-success`

---

## Triage

**Immediate Actions (First 5 minutes):**

1. **Determine rejection source:**
   ```promql
   sum by (source) (rate(validator_rejects_total[5m]))
   ```
   Sources:
   - `extraction_service`: Claims from document extraction
   - `agent_outputs`: Claims from debate agents
   - `deliverables_generator`: Claims at deliverable export

2. **Identify specific validator failures:**
   ```promql
   sum by (validator, reason) (rate(validator_rejects_total[5m]))
   ```

3. **Inspect recent prompt/validator changes:**
   ```bash
   git log --oneline -20 -- src/idis/validators/
   git log --oneline -20 -- prompts/
   ```

4. **Check affected claims sample:**
   ```sql
   SELECT claim_id, claim_type, validation_error
   FROM claims
   WHERE validation_status = 'REJECTED'
   AND created_at > NOW() - INTERVAL '1 hour'
   LIMIT 20;
   ```

**Escalation Path:**
- SEV-1 (IC deliverables affected): Incident Commander + Backend lead + ML lead
- SEV-2: Backend on-call

---

## Containment

**Immediate Containment Actions:**

1. **Freeze prompt updates:**
   ```bash
   # Set prompt registry to read-only mode
   kubectl set env deploy/prompt-registry -n idis REGISTRY_READ_ONLY=true
   ```

2. **Revert last prompt/validator changes:**
   ```bash
   # Revert to last known good prompt version
   python scripts/prompts/rollback.py --prompt-id <prompt_id> --to-version <version>
   ```

3. **Force "SUBJECTIVE" labeling (temporary measure):**
   - Enable subjective-fallback mode for affected claim types
   - This prevents No-Free-Facts violations but flags output as unverified
   ```bash
   kubectl set env deploy/claim-service -n idis SUBJECTIVE_FALLBACK_ENABLED=true
   ```

4. **Block affected deliverables:**
   - Prevent export of deliverables with validation failures
   - Queue for manual review

5. **Communication:**
   - Alert IC preparation teams of potential delays
   - Document impact scope

---

## Recovery

**Recovery Steps:**

1. **Fix schema mismatch:**
   - Identify discrepancy between claim structure and validator expectations
   - Update validator rules or fix claim generator
   - Deploy fix

2. **Fix extraction/agent output:**
   - If extraction service issue: update extraction prompts/rules
   - If agent output issue: fix agent prompts or output parser

3. **Re-run affected pipeline stages:**
   ```bash
   # Reprocess claims from specific deals
   python scripts/reprocess_claims.py --deal-ids <deal_id_list>
   ```

4. **Disable subjective fallback:**
   ```bash
   kubectl set env deploy/claim-service -n idis SUBJECTIVE_FALLBACK_ENABLED=false
   ```

5. **Re-enable prompt updates:**
   ```bash
   kubectl set env deploy/prompt-registry -n idis REGISTRY_READ_ONLY=false
   ```

---

## Verification

**Success Criteria:**

1. **Deliverables pass No-Free-Facts gate:**
   - [ ] `deliverable_no_free_facts_failures_total` = 0 for 30+ minutes
   - [ ] Test deliverable generation for affected deals

2. **Claim creation success stabilizes:**
   - [ ] Validator rejection rate below baseline
   - [ ] No new validation errors for known-good claims

3. **Trust invariant verification:**
   ```bash
   # Run trust invariant tests
   pytest tests/test_no_free_facts.py tests/test_muhasabah.py -v
   ```

4. **Sample audit:**
   - [ ] Review 5 claims from affected period
   - [ ] Verify all have proper Sanad chains
   - [ ] Verify all IC-bound facts have claim_id or calc_id

---

## Postmortem

**Required for:** All SEV-1 incidents; SEV-2 with >1 hour duration

**Critical Analysis Points:**
1. Why did the validator change break compatibility?
2. Was there adequate testing before prompt/validator changes?
3. Should subjective-fallback be permanent for certain claim types?
4. How can we prevent No-Free-Facts violations from reaching IC deliverables?

**Trust Invariant Focus:**
- Document any temporary trust invariant relaxations
- Verify all relaxations were reverted
- Confirm no unverified facts reached IC deliverables
