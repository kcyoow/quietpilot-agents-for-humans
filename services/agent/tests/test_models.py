import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from quietpilot_agent.models import CandidateProposal, CasePlanProposal

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("model", "filename"),
    [
        (CandidateProposal, "candidate-proposal.schema.json"),
        (CasePlanProposal, "case-plan-proposal.schema.json"),
    ],
)
def test_exported_schema_matches_model(model: type, filename: str) -> None:
    exported = json.loads((ROOT / "contracts" / "agent" / filename).read_text())
    assert exported == model.model_json_schema()


def test_candidate_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        CandidateProposal.model_validate(
            {
                "outcome": "Prepare class deadline",
                "summary": "One grounded candidate",
                "evidence_refs": ["mail:1"],
                "confidence": 0.9,
                "primary_group_hint": "일정·준비",
                "risk": "LOW",
                "fingerprint_inputs": ["deadline", "2026-08-30"],
                "unexpected": "not allowed",
            }
        )
