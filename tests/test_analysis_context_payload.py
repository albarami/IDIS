"""Tests for investment-grade analysis context payloads."""

from __future__ import annotations

import json

from idis.analysis.agents.llm_specialist_agent import _build_context_payload
from idis.analysis.models import (
    AnalysisCalcReference,
    AnalysisClaimReference,
    AnalysisContext,
)
from idis.services.extraction.extractors.llm_client import DeterministicAnalysisLLMClient


def test_context_payload_includes_claim_and_calc_summaries() -> None:
    """Layer 2 agents should receive real claim and calc summaries, not only IDs."""
    ctx = AnalysisContext(
        deal_id="deal-001",
        tenant_id="tenant-001",
        run_id="run-001",
        claim_ids=frozenset({"claim-fin"}),
        calc_ids=frozenset({"calc-arr"}),
        claim_registry={
            "claim-fin": AnalysisClaimReference(
                claim_id="claim-fin",
                claim_text="ARR reached $1.2M in FY2025.",
                claim_class="FINANCIAL",
                source_summary="Financial model, span fin-1",
                sanad_grade="B",
            )
        },
        calc_registry={
            "calc-arr": AnalysisCalcReference(
                calc_id="calc-arr",
                calc_type="ARR",
                output_summary="$1.2M ARR",
                input_claim_ids=["claim-fin"],
                source_summary="Derived from claim-fin",
            )
        },
    )

    payload = json.loads(_build_context_payload(ctx))

    assert payload["claim_registry"]["claim-fin"]["claim_text"] == "ARR reached $1.2M in FY2025."
    assert payload["claim_registry"]["claim-fin"]["source_summary"] == "Financial model, span fin-1"
    assert payload["calc_registry"]["calc-arr"]["output_summary"] == "$1.2M ARR"


def test_deterministic_analysis_uses_claim_summaries_for_sections() -> None:
    """Deterministic analysis should summarize provided evidence rather than emit stubs."""
    context_payload = {
        "deal_metadata": {"deal_id": "deal-001", "company_name": "Acme Corp"},
        "claim_registry": {
            "claim-fin": {
                "claim_id": "claim-fin",
                "claim_text": "ARR reached $1.2M in FY2025.",
                "claim_class": "FINANCIAL",
                "source_summary": "Financial model, span fin-1",
                "sanad_grade": "B",
            },
            "claim-risk": {
                "claim_id": "claim-risk",
                "claim_text": "Two customers account for most contracted revenue.",
                "claim_class": "TRACTION",
                "source_summary": "Customer pipeline, span cust-1",
                "sanad_grade": "C",
            },
        },
        "calc_registry": {
            "calc-arr": {
                "calc_id": "calc-arr",
                "calc_type": "ARR",
                "output_summary": "$1.2M ARR",
                "input_claim_ids": ["claim-fin"],
                "source_summary": "Derived from claim-fin",
            }
        },
        "enrichment_refs": {},
    }
    prompt = (
        "Prompt\n\n---\n\nCONTEXT PAYLOAD:\n"
        f"{json.dumps(context_payload, sort_keys=True)}"
        "\n\nOUTPUT FORMAT CONSTRAINT:"
    )

    report = json.loads(DeterministicAnalysisLLMClient().call(prompt, json_mode=True))

    section_values = json.dumps(report["analysis_sections"], sort_keys=True)
    assert "ARR reached $1.2M in FY2025" in section_values
    assert "Two customers account for most contracted revenue" in section_values
    assert "Deterministic stub summary" not in section_values


def test_deterministic_analysis_synthesizes_sections_and_risk_refs() -> None:
    """Local analysis should not turn noisy sorted claims into raw deliverable bullets."""
    context_payload = {
        "deal_metadata": {"deal_id": "deal-001", "company_name": "Roamless"},
        "claim_registry": {
            "claim-001-noise": {
                "claim_id": "claim-001-noise",
                "claim_text": (
                    "ORIG CO NAME:STRIPE ORIG ID:4270465600 DESC DATE: "
                    "TRACE#:111000022356425 IND NAME:MYNE TECHNOLOGIES INC"
                ),
                "claim_class": "FINANCIAL",
                "source_summary": "Bank statement, span bank-1",
                "sanad_grade": "C",
            },
            "claim-market": {
                "claim_id": "claim-market",
                "claim_text": (
                    "Roamless provides an API for platforms looking to resell "
                    "eSIM connectivity to their own users."
                ),
                "claim_class": "TRACTION",
                "source_summary": "Commercial presentation, span market-1",
                "sanad_grade": "B",
            },
            "claim-funding": {
                "claim_id": "claim-funding",
                "claim_text": "$3M already raised as of February 2024, with $2M remaining.",
                "claim_class": "FINANCIAL",
                "source_summary": "Financial model, span fin-1",
                "sanad_grade": "B",
            },
        },
        "calc_registry": {},
        "enrichment_refs": {},
    }
    prompt = (
        "market_agent prompt\n\n---\n\nCONTEXT PAYLOAD:\n"
        f"{json.dumps(context_payload, sort_keys=True)}"
        "\n\nOUTPUT FORMAT CONSTRAINT:"
    )

    report = json.loads(DeterministicAnalysisLLMClient().call(prompt, json_mode=True))
    section = report["analysis_sections"]["market_and_traction_evidence"]

    assert isinstance(section, dict)
    assert "Roamless provides an API" in section["content"]
    assert "ORIG CO NAME" not in section["content"]
    assert section["claim_refs"] == ["claim-market"]
    assert report["risks"][0]["claim_ids"] == ["claim-market"]


def test_deterministic_analysis_cleans_noisy_claims_into_investor_paragraph() -> None:
    """Local analysis should synthesize clean claims instead of concatenating fragments."""
    context_payload = {
        "deal_metadata": {"deal_id": "deal-001", "company_name": "Roamless"},
        "claim_registry": {
            "claim-bank-noise": {
                "claim_id": "claim-bank-noise",
                "claim_text": (
                    "ORIG CO NAME:STRIPE ORIG ID:4270465600 DESC DATE: TRACE#:111000022356425"
                ),
                "claim_class": "FINANCIAL",
                "source_summary": "Bank statement, span bank-1",
                "sanad_grade": "C",
            },
            "claim-table-noise": {
                "claim_id": "claim-table-noise",
                "claim_text": (
                    "TABLE NO. 14. : Global Embedded SIM for Hardware Market Revenue, "
                    "By Region, 2017 - 2022 (USD Million)"
                ),
                "claim_class": "FINANCIAL",
                "source_summary": "Market report, span table-14",
                "sanad_grade": "C",
            },
            "claim-fragment": {
                "claim_id": "claim-fragment",
                "claim_text": "itself, its customer or its End User to comply with this Section",
                "claim_class": "TRACTION",
                "source_summary": "Contract, span frag-1",
                "sanad_grade": "D",
            },
            "claim-arr": {
                "claim_id": "claim-arr",
                "claim_text": "ARR reached $1.2M in FY2025 with 74% gross margin.",
                "claim_class": "FINANCIAL",
                "source_summary": "Financial model, span fin-1",
                "sanad_grade": "B",
            },
            "claim-burn": {
                "claim_id": "claim-burn",
                "claim_text": "Monthly burn was $365K and cash runway was nine months.",
                "claim_class": "FINANCIAL",
                "source_summary": "Management accounts, span fin-2",
                "sanad_grade": "B",
            },
        },
        "calc_registry": {},
        "enrichment_refs": {},
    }
    prompt = (
        "financial_agent prompt\n\n---\n\nCONTEXT PAYLOAD:\n"
        f"{json.dumps(context_payload, sort_keys=True)}"
        "\n\nOUTPUT FORMAT CONSTRAINT:"
    )

    report = json.loads(DeterministicAnalysisLLMClient().call(prompt, json_mode=True))
    section = report["analysis_sections"]["financial_evidence"]
    content = section["content"]

    assert content.startswith("Financial diligence view:")
    assert "ARR reached $1.2M in FY2025 with 74% gross margin" in content
    assert "Monthly burn was $365K and cash runway was nine months" in content
    assert "ORIG CO NAME" not in content
    assert "TABLE NO." not in content
    assert "End User to comply" not in content
    assert section["claim_refs"] == ["claim-arr", "claim-burn"]


def test_deterministic_analysis_reports_missing_evidence_when_only_noise_exists() -> None:
    """If every candidate is noise, local analysis should say evidence is missing."""
    context_payload = {
        "deal_metadata": {"deal_id": "deal-001", "company_name": "Roamless"},
        "claim_registry": {
            "claim-noise": {
                "claim_id": "claim-noise",
                "claim_text": "of and the if you",
                "claim_class": "TEAM",
                "source_summary": "OCR span noise-1",
                "sanad_grade": "D",
            }
        },
        "calc_registry": {},
        "enrichment_refs": {},
    }
    prompt = (
        "team_agent prompt\n\n---\n\nCONTEXT PAYLOAD:\n"
        f"{json.dumps(context_payload, sort_keys=True)}"
        "\n\nOUTPUT FORMAT CONSTRAINT:"
    )

    report = json.loads(DeterministicAnalysisLLMClient().call(prompt, json_mode=True))
    section = report["analysis_sections"]["team_evidence"]

    assert section["content"] == "Not found in provided materials; request source documentation."
    assert section["claim_refs"] == []
    assert report["supported_claim_ids"] == []
    assert report["muhasabah"]["is_subjective"] is True
    assert report["risks"] == []
    assert report["questions_for_founder"] == [
        "Not found in provided materials: source evidence for team_agent analysis."
    ]


def test_deterministic_risk_register_wording_is_specific_and_clean() -> None:
    """Risk wording should be readable and grounded, not generic boilerplate."""
    context_payload = {
        "deal_metadata": {"deal_id": "deal-001", "company_name": "Roamless"},
        "claim_registry": {
            "claim-risk": {
                "claim_id": "claim-risk",
                "claim_text": (
                    "Execution risk in scaling across geographies and customer segments."
                ),
                "claim_class": "RISK",
                "source_summary": "Business presentation, span risk-1",
                "sanad_grade": "C",
            },
            "claim-fragment": {
                "claim_id": "claim-fragment",
                "claim_text": "4.7 Customer assumes all risks in relation to, and shall indemnify",
                "claim_class": "LEGAL",
                "source_summary": "Contract, span frag-1",
                "sanad_grade": "D",
            },
        },
        "calc_registry": {},
        "enrichment_refs": {},
    }
    prompt = (
        "risk_officer_agent prompt\n\n---\n\nCONTEXT PAYLOAD:\n"
        f"{json.dumps(context_payload, sort_keys=True)}"
        "\n\nOUTPUT FORMAT CONSTRAINT:"
    )

    report = json.loads(DeterministicAnalysisLLMClient().call(prompt, json_mode=True))
    risk = report["risks"][0]

    assert risk["description"].startswith("Specific risk:")
    assert (
        "Execution risk in scaling across geographies and customer segments" in risk["description"]
    )
    assert "Follow-up required" not in risk["description"]
    assert "indemnify" not in risk["description"]
    assert risk["claim_ids"] == ["claim-risk"]


def test_deterministic_technical_analysis_excludes_bank_wire_noise() -> None:
    """Bank wires mentioning the company should not become technical diligence."""
    context_payload = {
        "deal_metadata": {"deal_id": "deal-001", "company_name": "Roamless"},
        "claim_registry": {
            "claim-bank-wire": {
                "claim_id": "claim-bank-wire",
                "claim_text": (
                    "ONLINE INTERNATIONAL WIRE TRANSFER A/C: FOREIGN CUR BUS ACCT BK "
                    "ORG: 00000000933802115 MYNE TECHNOLOGIES INC. BEN:/TR0900134000 "
                    "BUSINESS EXPENSES/OCMT/EUR1034,00/EXCH/0.8904/TRN: 3519900289RE"
                ),
                "claim_class": "OTHER",
                "source_summary": "Bank statement, span wire-1",
                "sanad_grade": "C",
            },
            "claim-product": {
                "claim_id": "claim-product",
                "claim_text": (
                    "The company provides an API-based eSIM connectivity platform "
                    "for partners and end users."
                ),
                "claim_class": "TECH",
                "source_summary": "Product presentation, span tech-1",
                "sanad_grade": "B",
            },
        },
        "calc_registry": {},
        "enrichment_refs": {},
    }
    prompt = (
        "technical_agent prompt\n\n---\n\nCONTEXT PAYLOAD:\n"
        f"{json.dumps(context_payload, sort_keys=True)}"
        "\n\nOUTPUT FORMAT CONSTRAINT:"
    )

    report = json.loads(DeterministicAnalysisLLMClient().call(prompt, json_mode=True))
    section = report["analysis_sections"]["product_and_technology_evidence"]

    assert "API-based eSIM connectivity platform" in section["content"]
    assert "ONLINE INTERNATIONAL WIRE TRANSFER" not in section["content"]
    assert "FOREIGN CUR BUS ACCT" not in section["content"]
    assert section["claim_refs"] == ["claim-product"]


def test_deterministic_technical_analysis_reports_missing_when_only_ocr_noise_exists() -> None:
    """Technical sections should not force OCR/contact fragments into prose."""
    context_payload = {
        "deal_metadata": {"deal_id": "deal-001", "company_name": "Roamless"},
        "claim_registry": {
            "claim-contact-contract": {
                "claim_id": "claim-contact-contract",
                "claim_text": (
                    "MYNE TECHNOLOGIES INC. 1401 Pennsylvania Ave. Unit 105, "
                    "Wilmington DE 19806 United States E-mail: finance@roamless.com "
                    "4.11 During the term of this agreement or upon termination"
                ),
                "claim_class": "FINANCIAL",
                "source_summary": "Contract schedule, span contact-1",
                "sanad_grade": "C",
            },
            "claim-ocr-marketing": {
                "claim_id": "claim-ocr-marketing",
                "claim_text": (
                    "We power entemrises globallY. to connect more peopk,_places "
                    "and products and eng,1ge more deepJY. with customers."
                ),
                "claim_class": "TRACTION",
                "source_summary": "OCR presentation, span ocr-1",
                "sanad_grade": "C",
            },
            "claim-expense": {
                "claim_id": "claim-expense",
                "claim_text": "MomentMomentMomentMomentMomentMomentMoment tech organizer ($50).",
                "claim_class": "FINANCIAL",
                "source_summary": "Expense ledger, span exp-1",
                "sanad_grade": "C",
            },
            "claim-chips-credit": {
                "claim_id": "claim-chips-credit",
                "claim_text": (
                    "CHIPS CREDIT VIA: WELLS FARGO BANK, N.A./0509 B/O: REVO CAPITAL "
                    "FUND II B.V. REF: NBNF=MYNE TECHNOLOGIES INC. WILMINGTON DE "
                    "19806-4125 US/AC-00000000933 ORG=/NL61UGBI8263368898"
                ),
                "claim_class": "OTHER",
                "source_summary": "Bank statement, span chips-1",
                "sanad_grade": "C",
            },
            "claim-ocr-event": {
                "claim_id": "claim-ocr-event",
                "claim_text": (
                    "ExP-lore toI)ics like 5G, AI, 6G, APis and immersive technolo~ "
                    "straight from the Ericsson booth, at the world's most influential "
                    "connectivicy. event."
                ),
                "claim_class": "OTHER",
                "source_summary": "OCR event brochure, span event-1",
                "sanad_grade": "C",
            },
        },
        "calc_registry": {},
        "enrichment_refs": {},
    }
    prompt = (
        "technical_agent prompt\n\n---\n\nCONTEXT PAYLOAD:\n"
        f"{json.dumps(context_payload, sort_keys=True)}"
        "\n\nOUTPUT FORMAT CONSTRAINT:"
    )

    report = json.loads(DeterministicAnalysisLLMClient().call(prompt, json_mode=True))
    section = report["analysis_sections"]["product_and_technology_evidence"]

    assert section["content"] == "Not found in provided materials; request source documentation."
    assert section["claim_refs"] == []
    assert report["muhasabah"]["is_subjective"] is True
    assert report["risks"] == []
    assert "MYNE TECHNOLOGIES INC. 1401 Pennsylvania" not in section["content"]
    assert "entemrises" not in section["content"]
    assert "MomentMoment" not in section["content"]
    assert "CHIPS CREDIT" not in section["content"]
    assert "ExP-lore" not in section["content"]
