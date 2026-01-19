# RB-06: Debate Sessions Stuck / Max-Rounds Exceeded

**Runbook ID:** RB-06  
**Severity:** SEV-2  
**Owner:** ML/Platform Team  
**Last Updated:** 2026-01-19  
**Alert:** `IDISDebateCompletionRateLow`

---

## Detection

**Trigger Conditions:**
- Debate completion rate below 98%
- Many sessions ending with stop_reason=MAX_ROUNDS
- Debate sessions timing out

**Observable Signals:**
- `debate_completed_total{stop_reason="MAX_ROUNDS"}` spike
- Debates stuck in "IN_PROGRESS" for extended periods
- Muḥāsabah gate rejecting debate outputs
- IC generation delayed due to incomplete debates

**Dashboard:** `idis-debate-completion`

---

## Triage

**Immediate Actions (First 10 minutes):**

1. **Identify which role is looping:**
   ```promql
   sum by (role, tenant_id) (rate(debate_message_total[15m]))
   ```
   - Advocate vs Breaker imbalance indicates one-sided arguments
   - High message count with no resolution indicates looping

2. **Check evidence exhaustion vs missing retrieval:**
   ```promql
   rate(evidence_retrieval_empty_total[15m])
   rate(evidence_call_total[15m])
   ```

3. **Verify Muḥāsabah gate rejects:**
   ```promql
   sum by (reject_reason) (rate(muhasabah_gate_rejects_total[15m]))
   ```

4. **Inspect stuck debate sessions:**
   ```sql
   SELECT debate_id, deal_id, current_round, status, started_at
   FROM debate_runs
   WHERE status = 'IN_PROGRESS'
   AND started_at < NOW() - INTERVAL '30 minutes'
   ORDER BY started_at
   LIMIT 20;
   ```

5. **Check LLM provider health:**
   ```bash
   kubectl logs -n idis -l component=debate-orchestrator --tail=200 | grep -i "timeout\|rate limit\|error"
   ```

**Escalation Path:**
- SEV-2: ML team on-call + Platform on-call
- SEV-1 (if blocking many ICs): + Incident Commander

---

## Containment

**Immediate Containment Actions:**

1. **Reduce max_rounds temporarily:**
   ```bash
   kubectl set env deploy/debate-orchestrator -n idis MAX_DEBATE_ROUNDS=3
   ```

2. **Require Evidence Call stage before continuing:**
   ```bash
   # Force evidence retrieval between rounds
   kubectl set env deploy/debate-orchestrator -n idis FORCE_EVIDENCE_CALL=true
   ```

3. **Escalate to human review for unresolved debates:**
   ```sql
   UPDATE debate_runs
   SET status = 'REQUIRES_HUMAN_REVIEW',
       escalation_reason = 'Max rounds exceeded - auto-escalated'
   WHERE status = 'IN_PROGRESS'
   AND current_round >= 5;
   ```

4. **Enable circuit breaker for debate service:**
   - Prevent new debates from starting if backlog too large
   - Focus resources on completing existing debates

5. **Communication:**
   - Notify IC teams of potential delays
   - Document affected deals

---

## Recovery

**Recovery Steps:**

1. **Patch prompts to focus on claim-level disputes:**
   ```bash
   # Deploy improved prompts
   python scripts/prompts/deploy.py \
     --prompt-id debate_advocate \
     --version v2.1.0 \
     --environment prod
   ```

2. **Improve retrieval for missing claims:**
   - Review retrieval strategy
   - Expand search scope for supporting evidence
   - Add fallback retrieval sources

3. **Fix Muḥāsabah validation issues:**
   - Review reject reasons
   - Adjust confidence thresholds if too strict
   - Ensure agents provide required fields

4. **Resume stuck debates:**
   ```bash
   # Resume debates with fixed configuration
   python scripts/resume_debates.py \
     --debate-ids <stuck_debate_ids> \
     --force-evidence-call
   ```

5. **Restore normal configuration:**
   ```bash
   kubectl set env deploy/debate-orchestrator -n idis \
     MAX_DEBATE_ROUNDS=5 \
     FORCE_EVIDENCE_CALL=false
   ```

---

## Verification

**Success Criteria:**

1. **Completion rate restored:**
   - [ ] Debate completion rate ≥ 98%
   - [ ] MAX_ROUNDS stop reason < 5% of completions

2. **Stable dissent preserved:**
   - [ ] Dissenting opinions captured in deliverables
   - [ ] No forced consensus without evidence

3. **Quality check:**
   ```bash
   # Review sample of completed debates
   python scripts/review_debate_quality.py --sample-size 10
   ```

4. **IC generation unblocked:**
   - [ ] Pending ICs can proceed
   - [ ] No new debates timing out

---

## Postmortem

**Required for:** SEV-2 incidents with >1 hour of degraded completion rate

**Analysis Focus:**
1. Why are agents not reaching consensus?
2. Is evidence retrieval providing sufficient information?
3. Are prompts leading to circular arguments?
4. Should max_rounds be adjusted permanently?

**Quality Metrics:**
- Analyze debate transcripts for patterns
- Review Muḥāsabah compliance rates
- Compare pre/post-incident utility scores
