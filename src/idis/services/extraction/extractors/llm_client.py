"""Provider-agnostic LLM client interface + deterministic test stub.

LLMClient: Protocol for making LLM calls (provider-agnostic).
DeterministicLLMClient: Returns pre-built valid JSON for testing.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    """Provider-agnostic interface for LLM calls."""

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        """Make an LLM call and return the raw response text.

        Args:
            prompt: The full prompt text to send.
            json_mode: If True, request JSON-formatted output.

        Returns:
            Raw response string from the LLM.
        """
        ...


class DeterministicLLMClient:
    """Deterministic LLM client for testing — returns valid JSON based on input.

    Parses the chunk content from the prompt and generates structured claims
    deterministically. No external calls are made.
    """

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        """Return deterministic claim JSON based on prompt content.

        Args:
            prompt: The full prompt text (includes chunk content).
            json_mode: Ignored; always returns JSON.

        Returns:
            JSON string containing an array of extracted claims.
        """
        claims = self._extract_from_prompt(prompt)
        return json.dumps(claims, sort_keys=True)

    def _extract_from_prompt(self, prompt: str) -> list[dict[str, Any]]:
        """Parse prompt content and generate deterministic claims.

        Args:
            prompt: Full prompt text.

        Returns:
            List of claim dicts matching the output schema.
        """
        content_marker = "Content:\n"
        content_start = prompt.find(content_marker)
        if content_start == -1:
            return []

        content = prompt[content_start + len(content_marker) :].strip()
        if not content:
            return []

        lines = [line.strip() for line in content.split("\n") if line.strip()]
        if not lines:
            return []

        claims: list[dict[str, Any]] = []
        for line in lines:
            claim_class = self._classify(line)
            claims.append(
                {
                    "claim_text": line,
                    "claim_class": claim_class,
                    "source_locator": {},
                    "confidence": 0.85,
                    "requires_review": False,
                }
            )

        return claims

    def _classify(self, text: str) -> str:
        """Classify text into a claim class deterministically.

        Args:
            text: Claim text to classify.

        Returns:
            Claim class string.
        """
        text_lower = text.lower()
        if any(kw in text_lower for kw in ["revenue", "arr", "mrr", "margin", "$", "funding"]):
            return "FINANCIAL"
        if any(kw in text_lower for kw in ["customer", "client", "user", "subscriber"]):
            return "TRACTION"
        if any(kw in text_lower for kw in ["tam", "sam", "som", "market size"]):
            return "MARKET_SIZE"
        if any(kw in text_lower for kw in ["competitor", "competition"]):
            return "COMPETITION"
        if any(kw in text_lower for kw in ["team", "employee", "founder", "ceo"]):
            return "TEAM"
        return "OTHER"


class DeterministicAnalysisLLMClient:
    """Deterministic LLM client for analysis agents — returns valid AgentReport JSON.

    Parses the CONTEXT PAYLOAD from the analysis prompt to extract real
    claim/calc IDs, then builds a fully-valid AgentReport dict that passes
    AgentReport Pydantic validation, No-Free-Facts, and Muhasabah gates.
    """

    _CONTEXT_MARKER = "CONTEXT PAYLOAD:\n"
    _CONSTRAINT_MARKER = "\n\nOUTPUT FORMAT CONSTRAINT:"
    _TIMESTAMP = "2026-01-01T00:00:00+00:00"

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        """Return deterministic AgentReport JSON based on prompt context.

        Args:
            prompt: The full prompt text (includes CONTEXT PAYLOAD JSON block).
            json_mode: Ignored; always returns JSON.

        Returns:
            JSON string containing a single AgentReport-shaped object.
        """
        claim_records, calc_records = self._extract_registry_records(prompt)
        report = self._build_report(
            claim_records=claim_records,
            calc_records=calc_records,
            agent_type=self._agent_type_from_prompt(prompt),
        )
        return json.dumps(report, sort_keys=True)

    def _extract_registry_records(
        self, prompt: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Extract claim and calc summaries from the CONTEXT PAYLOAD in the prompt.

        Args:
            prompt: Full prompt containing a CONTEXT PAYLOAD JSON block.

        Returns:
            Tuple of sorted claim and calc records.

        Raises:
            ValueError: If context payload cannot be parsed (fail-closed).
        """
        ctx_start = prompt.find(self._CONTEXT_MARKER)
        if ctx_start == -1:
            raise ValueError(
                "DETERMINISTIC_ANALYSIS_CONTEXT_PARSE_FAILED: "
                "no CONTEXT PAYLOAD marker found in prompt"
            )

        json_start = ctx_start + len(self._CONTEXT_MARKER)
        json_text = prompt[json_start:]

        constraint_pos = json_text.find(self._CONSTRAINT_MARKER)
        if constraint_pos != -1:
            json_text = json_text[:constraint_pos]

        json_text = json_text.strip()

        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "DETERMINISTIC_ANALYSIS_CONTEXT_PARSE_FAILED: "
                f"invalid JSON in CONTEXT PAYLOAD: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise ValueError(
                "DETERMINISTIC_ANALYSIS_CONTEXT_PARSE_FAILED: "
                f"expected dict, got {type(payload).__name__}"
            )

        claim_registry = payload.get("claim_registry", {})
        calc_registry = payload.get("calc_registry", {})

        claim_records = self._registry_records(claim_registry, id_key="claim_id")
        calc_records = self._registry_records(calc_registry, id_key="calc_id")

        return claim_records, calc_records

    @staticmethod
    def _registry_records(registry: Any, *, id_key: str) -> list[dict[str, Any]]:
        """Normalize registry payload values to sorted dictionaries."""
        if not isinstance(registry, dict):
            return []

        records: list[dict[str, Any]] = []
        for ref_id, raw in sorted(registry.items()):
            if isinstance(raw, dict):
                record = dict(raw)
                record.setdefault(id_key, ref_id)
            else:
                record = {id_key: str(raw or ref_id)}
            records.append(record)
        return records

    @staticmethod
    def _agent_type_from_prompt(prompt: str) -> str:
        """Best-effort extraction of the specialist agent type from its prompt."""
        for agent_type in (
            "financial_agent",
            "historian_agent",
            "market_agent",
            "risk_officer_agent",
            "sector_specialist_agent",
            "team_agent",
            "technical_agent",
            "terms_agent",
        ):
            if agent_type in prompt:
                return agent_type
        return "analysis_agent"

    def _build_report(
        self,
        *,
        claim_records: list[dict[str, Any]],
        calc_records: list[dict[str, Any]],
        agent_type: str,
    ) -> dict[str, Any]:
        """Build a valid AgentReport dict using the provided registry IDs.

        Args:
            claim_records: Claim summaries from the context payload.
            calc_records: Calc summaries from the context payload.
            agent_type: Specialist agent type inferred from the prompt.

        Returns:
            Dict matching AgentReport schema, passing NFF and Muhasabah.
        """
        selected_claims = self._select_claims_for_agent(agent_type, claim_records)
        selected_claim_ids = [
            str(record.get("claim_id", "")).strip()
            for record in selected_claims
            if str(record.get("claim_id", "")).strip()
        ]
        selected_calc_records = calc_records[:5]
        selected_calc_ids = [
            str(record.get("calc_id", "")).strip()
            for record in selected_calc_records
            if str(record.get("calc_id", "")).strip()
        ]
        risk_evidence_claim = selected_claim_ids[:1]
        risk_evidence_calc = selected_calc_ids[:1]

        risks = []
        if risk_evidence_claim or risk_evidence_calc:
            risk_text = self._risk_description(agent_type, selected_claims or claim_records)
            risks.append(
                {
                    "risk_id": "det-risk-001",
                    "description": risk_text,
                    "severity": "MEDIUM",
                    "claim_ids": risk_evidence_claim,
                    "calc_ids": risk_evidence_calc,
                    "enrichment_ref_ids": [],
                }
            )

        analysis_sections = self._analysis_sections(
            agent_type=agent_type,
            claim_records=selected_claims,
            calc_records=selected_calc_records,
        )
        evidence_summary = self._evidence_summary(
            selected_claims or claim_records,
            selected_calc_records,
        )
        is_subjective = not selected_claim_ids

        return {
            "supported_claim_ids": list(selected_claim_ids),
            "supported_calc_ids": list(selected_calc_ids),
            "analysis_sections": analysis_sections,
            "risks": risks,
            "questions_for_founder": self._questions_for_agent(agent_type, bool(selected_claims)),
            "confidence": 0.65,
            "confidence_justification": (
                "Deterministic local analysis based on available extracted claims and calculations"
            ),
            "muhasabah": {
                "agent_id": "deterministic-stub",
                "output_id": "det-output-001",
                "supported_claim_ids": list(selected_claim_ids),
                "supported_calc_ids": list(selected_calc_ids),
                "evidence_summary": evidence_summary,
                "counter_hypothesis": "Evidence may be incomplete or outdated",
                "falsifiability_tests": [
                    {
                        "test_description": "Verify claims against source documents",
                        "required_evidence": "Original source documents for each claim",
                        "pass_fail_rule": "Claims without traceable sources are ungrounded",
                    }
                ],
                "uncertainties": [
                    {
                        "uncertainty": "Stub output not validated against real LLM analysis",
                        "impact": "MEDIUM",
                        "mitigation": "Run with real LLM backend for production analysis",
                    }
                ],
                "failure_modes": ["incomplete_evidence", "deterministic_local_synthesis"],
                "confidence": 0.65,
                "confidence_justification": (
                    "Deterministic local analysis based on provided evidence summaries"
                ),
                "timestamp": self._TIMESTAMP,
                "is_subjective": is_subjective,
            },
            "enrichment_ref_ids": [],
        }

    @staticmethod
    def _claim_text(record: dict[str, Any]) -> str:
        return str(record.get("claim_text") or record.get("text") or record.get("claim_id") or "")

    def _select_claims_for_agent(
        self,
        agent_type: str,
        claim_records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Pick the most relevant evidence summaries for a specialist agent."""
        keywords_by_agent = {
            "financial_agent": ("FINANCIAL", "revenue", "arr", "mrr", "cost", "burn", "runway"),
            "market_agent": ("MARKET_SIZE", "TRACTION", "COMPETITION", "market", "customer"),
            "sector_specialist_agent": ("MARKET_SIZE", "TRACTION", "COMPETITION", "sector"),
            "risk_officer_agent": ("risk", "dependent", "concentration", "legal", "uncertain"),
            "team_agent": ("TEAM", "founder", "ceo", "team", "employee"),
            "technical_agent": (
                "TECH",
                "technology",
                "platform",
                "product",
                "architecture",
                "api",
                "sdk",
                "connectivity",
                "sim profile",
            ),
            "terms_agent": ("LEGAL", "valuation", "cap table", "share", "terms"),
            "historian_agent": ("FINANCIAL", "TRACTION", "TECH", "TEAM", "market", "customer"),
        }
        keywords = tuple(item.lower() for item in keywords_by_agent.get(agent_type, ()))
        selected: list[dict[str, Any]] = []
        for record in claim_records:
            haystack = f"{record.get('claim_class', '')} {self._claim_text(record)}".lower()
            if any(keyword in haystack for keyword in keywords):
                selected.append(record)
        ranked = sorted(
            selected or claim_records,
            key=lambda record: self._claim_quality_score(agent_type, record),
            reverse=True,
        )
        high_signal = [
            record for record in ranked if self._claim_quality_score(agent_type, record) >= 12
        ]
        if agent_type == "technical_agent":
            high_signal = [
                record for record in high_signal if self._is_technical_signal_claim(record)
            ]
        return high_signal[:4]

    @staticmethod
    def _claim_quality_score(agent_type: str, record: dict[str, Any]) -> int:
        """Rank extracted claims for use in synthesized diligence sections."""
        if DeterministicAnalysisLLMClient._is_id_only_record(record):
            return 12
        if DeterministicAnalysisLLMClient._is_low_signal_claim(record):
            return -100
        text = DeterministicAnalysisLLMClient._clean_claim_text(record)
        lowered = text.lower()
        words = [word for word in lowered.replace("/", " ").split() if word]
        score = 0

        preferred_classes = {
            "financial_agent": ("FINANCIAL",),
            "market_agent": ("MARKET_SIZE", "TRACTION", "COMPETITION"),
            "sector_specialist_agent": ("MARKET_SIZE", "TRACTION", "COMPETITION"),
            "risk_officer_agent": ("LEGAL", "RISK", "TRACTION", "FINANCIAL"),
            "team_agent": ("TEAM",),
            "technical_agent": ("TECH", "PRODUCT"),
            "terms_agent": ("LEGAL", "FINANCIAL"),
            "historian_agent": ("FINANCIAL", "TRACTION", "TECH", "TEAM", "MARKET_SIZE"),
            "analysis_agent": ("FINANCIAL", "TRACTION", "TECH", "TEAM", "MARKET_SIZE", "RISK"),
        }
        claim_class = str(record.get("claim_class") or "").upper()
        if claim_class in preferred_classes.get(agent_type, ()):
            score += 20

        if 8 <= len(words) <= 45:
            score += 8
        if any(char.isdigit() for char in text):
            score += 4
        if any(marker in lowered for marker in ("$", "%", "revenue", "arr", "customer", "api")):
            score += 6
        if any(
            marker in lowered
            for marker in (
                "burn",
                "runway",
                "gross margin",
                "registered users",
                "monthly usage",
                "valuation cap",
                "execution risk",
                "dependent",
                "contracted revenue",
                "customers account",
            )
        ):
            score += 8
        if text.endswith((".", ")", "%")):
            score += 3

        low_signal_markers = (
            "orig co name",
            "trace#",
            "desc date",
            "ind id",
            "trn:",
            "sec:ccd",
            "routing number",
            "account number",
            "severability",
            "independent counsel",
            "table no.",
            "fig no.",
            "indemnify",
            "comply with this section",
            "price per share, as adjusted",
            "online international wire transfer",
            "foreign cur bus acct",
            "ben:/",
            "/ocmt/",
            "/exch/",
            "business expenses",
            "consultancy expenses",
            "in witness whereof",
            "stockholder proposal",
        )
        if any(marker in lowered for marker in low_signal_markers):
            score -= 35

        incomplete_endings = (
            " to",
            " of",
            " and",
            " an",
            " the",
            " if you",
            " as",
            " in",
        )
        if any(lowered.endswith(ending) for ending in incomplete_endings):
            score -= 15
        if len(words) < 5:
            score -= 20
        if text.count("%") >= 4 and len(words) < 12:
            score -= 15
        if not any(char.isalpha() for char in text):
            score -= 25

        return score

    @staticmethod
    def _is_low_signal_claim(record: dict[str, Any]) -> bool:
        """Identify OCR/statement/legal boilerplate that should not drive prose."""
        if DeterministicAnalysisLLMClient._is_id_only_record(record):
            return False
        raw_text = DeterministicAnalysisLLMClient._claim_text(record).lower()
        low_signal_markers = (
            "orig co name",
            "trace#",
            "desc date",
            "ind id",
            "routing number",
            "account number",
            "online international wire transfer",
            "foreign cur bus acct",
            "ben:/",
            "/ocmt/",
            "/exch/",
            "business expenses",
            "consultancy expenses",
            "in witness whereof",
            "duly executed and delivered",
            "stockholder proposal",
            "proxy statement",
            "po box",
            "during the term of this agreement",
            "upon termination",
            "pennsylvania ave",
            "e-mail:",
            "finance@",
            "entemrises",
            "peopk",
            "eng,1ge",
            "deepjy",
            "jldg",
            "chips credit",
            "wells fargo",
            "b/o:",
            "nbnf=",
            "/ac-",
            "org=/",
            "exch/",
            "ocmt/",
            "technolo~",
            "connectivicy",
            "toi)ics",
            "ericsson booth",
        )
        if any(marker in raw_text for marker in low_signal_markers):
            return True

        cleaned = DeterministicAnalysisLLMClient._clean_claim_text(record).lower()
        if "moment moment moment" in cleaned:
            return True
        words = [word for word in cleaned.replace("/", " ").split() if word]
        if len(words) <= 3:
            return True
        return cleaned in {"$", "0.00"}

    @staticmethod
    def _is_id_only_record(record: dict[str, Any]) -> bool:
        """Allow legacy deterministic prompts that provide only registry IDs."""
        claim_id = str(record.get("claim_id") or "").strip()
        return bool(claim_id) and not record.get("claim_text") and not record.get("text")

    @staticmethod
    def _is_technical_signal_claim(record: dict[str, Any]) -> bool:
        """Require real product/technical signal before selecting tech evidence."""
        if DeterministicAnalysisLLMClient._is_low_signal_claim(record):
            return False
        claim_class = str(record.get("claim_class") or "").upper()
        if claim_class in {"TECH", "PRODUCT"}:
            return True
        text = DeterministicAnalysisLLMClient._clean_claim_text(record).lower()
        technical_terms = (
            "api",
            "platform",
            "architecture",
            "roadmap",
            "software",
            "integration",
            "connectivity",
            "technical",
        )
        return any(term in text for term in technical_terms)

    @staticmethod
    def _clean_claim_text(record: dict[str, Any]) -> str:
        """Normalize extracted claim text before using it in investor prose."""
        text = DeterministicAnalysisLLMClient._claim_text(record).strip()
        if not text:
            return ""
        text = text.replace("\u00a0", " ").replace("#", "; ")
        text = re.sub(r"[\u2022\u25a0]+", " ", text)
        text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
        text = re.sub(r"\s+", " ", text).strip(" -:;")
        text = text.replace("e SIM", "eSIM").replace("AP Is", "APIs")
        text = text.replace("cashpurchases", "cash purchases")
        text = re.sub(r"^\d+(?:\.\d+)*\s+", "", text)
        text = re.sub(r"\s+([,.;:%)])", r"\1", text)
        text = re.sub(r"([(])\s+", r"\1", text)
        if len(text) > 220:
            text = text[:217].rstrip(" ,;:") + "..."
        return text

    def _analysis_sections(
        self,
        *,
        agent_type: str,
        claim_records: list[dict[str, Any]],
        calc_records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build evidence-backed analysis sections for deterministic local mode."""
        section_name = {
            "financial_agent": "financial_evidence",
            "market_agent": "market_and_traction_evidence",
            "sector_specialist_agent": "sector_positioning_evidence",
            "risk_officer_agent": "risk_evidence",
            "team_agent": "team_evidence",
            "technical_agent": "product_and_technology_evidence",
            "terms_agent": "terms_evidence",
            "historian_agent": "investment_thesis_evidence",
        }.get(agent_type, "evidence_summary")

        claim_refs: list[str] = []
        calc_refs: list[str] = []
        evidence_items: list[str] = []
        sanad_grade: str | None = None
        for record in claim_records:
            claim_id = str(record.get("claim_id", ""))
            text = self._clean_claim_text(record)
            if not text:
                continue
            if claim_id:
                claim_refs.append(claim_id)
            if sanad_grade is None and record.get("sanad_grade"):
                sanad_grade = str(record.get("sanad_grade"))
            evidence_items.append(text)

        for record in calc_records[:5]:
            calc_id = str(record.get("calc_id", ""))
            output = str(record.get("output_summary") or record.get("output_primary_value") or "")
            calc_type = str(record.get("calc_type") or "calculation")
            if output:
                evidence_items.append(f"{calc_type}: {output}")
                claim_refs.extend(str(item) for item in record.get("input_claim_ids") or [])
                if calc_id:
                    calc_refs.append(calc_id)

        if not evidence_items:
            content = "Not found in provided materials; request source documentation."
        else:
            content = self._synthesize_analysis_content(
                agent_type=agent_type,
                evidence_items=evidence_items,
                calc_items=[
                    item
                    for item in (
                        str(
                            record.get("output_summary") or record.get("output_primary_value") or ""
                        ).strip()
                        for record in calc_records[:3]
                    )
                    if item
                ],
            )

        return {
            section_name: {
                "content": content,
                "claim_refs": sorted(set(claim_refs)),
                "calc_refs": sorted(set(calc_refs)),
                "sanad_grade": sanad_grade,
                "confidence": 0.65,
            }
        }

    @staticmethod
    def _risk_description(agent_type: str, claim_records: list[dict[str, Any]]) -> str:
        if not claim_records:
            return f"{agent_type} identified diligence gaps in the provided materials."
        text = DeterministicAnalysisLLMClient._clean_claim_text(claim_records[0])
        if not text:
            return f"{agent_type} identified diligence gaps in the provided materials."
        focus_by_agent = {
            "financial_agent": "Financial diligence risk",
            "market_agent": "Commercial diligence risk",
            "sector_specialist_agent": "Sector diligence risk",
            "risk_officer_agent": "Specific risk",
            "team_agent": "Team diligence risk",
            "technical_agent": "Technical diligence risk",
            "terms_agent": "Terms diligence risk",
            "historian_agent": "Investment thesis risk",
        }
        focus = focus_by_agent.get(agent_type, "Specific risk")
        return f"{focus}: {text}. Confirm mitigation, exposure, and source support before IC."

    def _evidence_summary(
        self,
        claim_records: list[dict[str, Any]],
        calc_records: list[dict[str, Any]],
    ) -> str:
        summaries = [self._clean_claim_text(record) for record in claim_records[:3]]
        summaries.extend(
            str(record.get("output_summary") or record.get("output_primary_value") or "")
            for record in calc_records[:2]
        )
        summaries = [item for item in summaries if item]
        return "; ".join(summaries) if summaries else "No source evidence found in context"

    @staticmethod
    def _synthesize_analysis_content(
        *,
        agent_type: str,
        evidence_items: list[str],
        calc_items: list[str],
    ) -> str:
        """Turn selected evidence into compact diligence prose."""
        evidence_text = "; ".join(evidence_items[:3])
        calc_text = "; ".join(calc_items[:2])
        calc_clause = f" Calculation support: {calc_text}." if calc_text else ""

        if agent_type == "financial_agent":
            return (
                f"Financial diligence view: source-backed metrics indicate {evidence_text}."
                f"{calc_clause} Diligence gaps: reconcile revenue bridge, burn, runway, "
                "and unit economics against the underlying workbooks before IC."
            )
        if agent_type in {"market_agent", "sector_specialist_agent"}:
            return (
                f"Commercial diligence view: the strongest traction evidence indicates "
                f"{evidence_text}.{calc_clause} Diligence gaps: verify customer "
                "concentration, conversion, and repeatability of this demand."
            )
        if agent_type == "risk_officer_agent":
            return (
                f"Risk diligence view: the evidence points to {evidence_text}.{calc_clause} "
                "Mitigation required: size exposure, assign an owner, and confirm "
                "contractual or operating mitigants before IC."
            )
        if agent_type == "historian_agent":
            upside = evidence_items[1] if len(evidence_items) > 1 else evidence_items[0]
            return (
                f"Investment thesis: the provided materials support continued diligence "
                f"around {evidence_items[0]}. Upside: {upside}.{calc_clause} "
                "Diligence gaps: reconcile the strongest commercial and financial "
                "evidence with the risk register before IC."
            )
        if agent_type == "terms_agent":
            return (
                f"Recommendation: treat the disclosed terms as diligence inputs, not a "
                f"final investment decision, until valuation and cap-table mechanics "
                f"are verified. Evidence reviewed: {evidence_text}.{calc_clause} "
                "Diligence gaps: confirm conversion mechanics, ownership, and investor rights."
            )
        if agent_type == "team_agent":
            return (
                f"Team diligence view: the available materials indicate {evidence_text}."
                f"{calc_clause} Diligence gaps: verify founder roles, key-person risk, "
                "hiring plan, and operating ownership."
            )
        if agent_type == "technical_agent":
            return (
                f"Product and technology diligence view: evidence indicates {evidence_text}."
                f"{calc_clause} Diligence gaps: validate architecture, integration "
                "dependencies, roadmap, and defensibility."
            )
        return f"Analysis view: evidence indicates {evidence_text}.{calc_clause}"

    @staticmethod
    def _questions_for_agent(agent_type: str, has_evidence: bool) -> list[str]:
        if not has_evidence:
            return [f"Not found in provided materials: source evidence for {agent_type} analysis."]
        question_by_agent = {
            "financial_agent": (
                "Please provide the revenue bridge, burn, runway, and unit economics backup."
            ),
            "market_agent": (
                "Please provide customer concentration, pipeline conversion, "
                "and market sizing support."
            ),
            "sector_specialist_agent": (
                "Please provide positioning evidence against direct competitors."
            ),
            "risk_officer_agent": (
                "Please provide mitigation plans for the highlighted evidence-backed risks."
            ),
            "team_agent": (
                "Please provide founder backgrounds, hiring plan, and key-person risk mitigants."
            ),
            "technical_agent": (
                "Please provide product architecture, roadmap, and technical diligence materials."
            ),
            "terms_agent": "Please provide cap table, valuation, and financing terms backup.",
            "historian_agent": (
                "Please reconcile the core investment thesis with the strongest contrary evidence."
            ),
        }
        return [question_by_agent.get(agent_type, "Please provide source backup for key claims.")]


_SCORING_DIMENSIONS = (
    "MARKET_ATTRACTIVENESS",
    "TEAM_QUALITY",
    "PRODUCT_DEFENSIBILITY",
    "TRACTION_VELOCITY",
    "FUND_THESIS_FIT",
    "CAPITAL_EFFICIENCY",
    "SCALABILITY",
    "RISK_PROFILE",
)


class DeterministicScoringLLMClient:
    """Deterministic LLM client for scoring agents — returns valid scorecard JSON.

    Parses the CONTEXT PAYLOAD from the scoring prompt to extract real
    claim/calc IDs, then builds a fully-valid scoring response with all 8
    dimensions that passes DimensionScore Pydantic validation, NFF, and
    Muhasabah gates.
    """

    _CONTEXT_MARKER = "CONTEXT PAYLOAD:\n"
    _TIMESTAMP = "2026-01-01T00:00:00+00:00"

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        """Return deterministic scoring JSON based on prompt context.

        Args:
            prompt: The full scoring prompt (includes CONTEXT PAYLOAD JSON block).
            json_mode: Ignored; always returns JSON.

        Returns:
            JSON string containing a scorecard object with dimension_scores.
        """
        claim_ids, calc_ids, evidence_summary = self._extract_registry_context(prompt)
        response = self._build_scoring_response(claim_ids, calc_ids, evidence_summary)
        return json.dumps(response, sort_keys=True)

    def _extract_registry_context(self, prompt: str) -> tuple[list[str], list[str], str]:
        """Extract claim/calc IDs and a short evidence summary from the context payload.

        The scoring runner embeds the payload as:
            CONTEXT PAYLOAD:\\n{json}
        with claim_registry and calc_registry as dicts keyed by ID.

        Args:
            prompt: Full prompt containing a CONTEXT PAYLOAD JSON block.

        Returns:
            Tuple of (sorted claim_ids, sorted calc_ids, evidence summary).

        Raises:
            ValueError: If context payload cannot be parsed (fail-closed).
        """
        ctx_start = prompt.find(self._CONTEXT_MARKER)
        if ctx_start == -1:
            raise ValueError(
                "DETERMINISTIC_SCORING_CONTEXT_PARSE_FAILED: "
                "no CONTEXT PAYLOAD marker found in prompt"
            )

        json_text = prompt[ctx_start + len(self._CONTEXT_MARKER) :].strip()

        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "DETERMINISTIC_SCORING_CONTEXT_PARSE_FAILED: "
                f"invalid JSON in CONTEXT PAYLOAD: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise ValueError(
                "DETERMINISTIC_SCORING_CONTEXT_PARSE_FAILED: "
                f"expected dict, got {type(payload).__name__}"
            )

        claim_registry = payload.get("claim_registry", {})
        calc_registry = payload.get("calc_registry", {})

        claim_ids = sorted(claim_registry.keys()) if isinstance(claim_registry, dict) else []
        calc_ids = sorted(calc_registry.keys()) if isinstance(calc_registry, dict) else []
        evidence_items: list[str] = []
        if isinstance(claim_registry, dict):
            for raw in claim_registry.values():
                if isinstance(raw, dict):
                    text = str(raw.get("claim_text") or raw.get("claim_id") or "").strip()
                else:
                    text = str(raw).strip()
                if text:
                    evidence_items.append(text)
        if isinstance(calc_registry, dict):
            for raw in calc_registry.values():
                if isinstance(raw, dict):
                    text = str(
                        raw.get("output_summary")
                        or raw.get("output_primary_value")
                        or raw.get("calc_id")
                        or ""
                    ).strip()
                else:
                    text = str(raw).strip()
                if text:
                    evidence_items.append(text)
        evidence_summary = "; ".join(evidence_items[:3]) or "available evidence"

        return claim_ids, calc_ids, evidence_summary

    def _build_dimension_score(
        self,
        dimension: str,
        claim_ids: list[str],
        calc_ids: list[str],
        evidence_summary: str,
    ) -> dict[str, Any]:
        """Build a single valid DimensionScore dict.

        Args:
            dimension: ScoreDimension value (e.g. MARKET_ATTRACTIVENESS).
            claim_ids: Sorted claim IDs from context.
            calc_ids: Sorted calc IDs from context.

        Returns:
            Dict matching DimensionScore schema with valid Muhasabah.
        """
        return {
            "dimension": dimension,
            "score": 0.65,
            "rationale": (
                f"Deterministic local scoring assessment for {dimension} based on: "
                f"{evidence_summary}"
            ),
            "supported_claim_ids": list(claim_ids),
            "supported_calc_ids": list(calc_ids),
            "enrichment_refs": [],
            "confidence": 0.60,
            "confidence_justification": (
                f"Deterministic stub: moderate confidence for {dimension}"
            ),
            "muhasabah": {
                "agent_id": "deterministic-scoring-stub",
                "output_id": f"det-score-{dimension.lower()}",
                "supported_claim_ids": list(claim_ids),
                "supported_calc_ids": list(calc_ids),
                "evidence_summary": f"Deterministic evidence for {dimension} from registries",
                "counter_hypothesis": f"Evidence for {dimension} may be incomplete",
                "falsifiability_tests": [
                    {
                        "test_description": f"Verify {dimension} claims against sources",
                        "required_evidence": "Original source documents",
                        "pass_fail_rule": "Claims without traceable sources are ungrounded",
                    }
                ],
                "uncertainties": [
                    {
                        "uncertainty": f"Stub scoring for {dimension} not LLM-validated",
                        "impact": "MEDIUM",
                        "mitigation": "Run with real LLM backend for production scoring",
                    }
                ],
                "failure_modes": ["incomplete_evidence", "stub_limitations"],
                "confidence": 0.60,
                "confidence_justification": (
                    f"Deterministic stub: moderate confidence for {dimension}"
                ),
                "timestamp": self._TIMESTAMP,
                "is_subjective": False,
            },
        }

    def _build_scoring_response(
        self,
        claim_ids: list[str],
        calc_ids: list[str],
        evidence_summary: str,
    ) -> dict[str, Any]:
        """Build a complete scoring response with all 8 dimensions.

        Args:
            claim_ids: Sorted claim IDs from context.
            calc_ids: Sorted calc IDs from context.

        Returns:
            Dict with dimension_scores containing all 8 required dimensions.
        """
        dimension_scores: dict[str, dict[str, Any]] = {}
        for dim in _SCORING_DIMENSIONS:
            dimension_scores[dim] = self._build_dimension_score(
                dim,
                claim_ids,
                calc_ids,
                evidence_summary,
            )

        return {"dimension_scores": dimension_scores}
