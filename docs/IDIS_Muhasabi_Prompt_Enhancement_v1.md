# Muḥāsibī Prompt Enhancement (Layer 2 Agents)

## Origin

Three metacognitive disciplines from Al-Muḥāsibī's cognitive framework
(ʿilm al-khawāṭir — Science of Thought Patterns) are integrated into
Layer 2 analysis agent prompts. These enhance the existing Muḥāsabah
gate by making agent self-accounting more substantive.

## Disciplines Applied

### 1. Nafs Check (Default Interpretation Awareness)

Every agent must identify and label its default/conventional
interpretation before proceeding with analysis. This combats the
mediocrity bias — the tendency to pattern-match to the most common
narrative for a deal's stage and sector.

Output: `analysis_sections.nafs_check`

### 2. Mujāhada (Assumption Inversion)

Every agent must identify the single assumption that, if wrong, would
most materially change its verdict. This forces analytical discipline
beyond "good enough" conclusions.

Output: entry in `risks[]` with evidence links

### 3. Insight Type Classification

Every analysis section is self-classified as conventional, deal-specific,
or contradictory. This makes the Muḥāsabah record honest about what
is genuinely novel versus what is standard analysis.

Output: `insight_type` field in each `analysis_sections` entry

## Scope

These are prompt-level enhancements only. No code, model, or
architectural changes are required. The AgentReport schema already
supports these through existing fields (analysis_sections: dict[str, Any],
risks: list[Risk]).

## Applicability

Applied to all Layer 2 specialist agent prompts (Financial, Market,
and all Phase 8.C agents).
