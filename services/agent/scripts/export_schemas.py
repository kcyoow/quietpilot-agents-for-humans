"""Export deterministic Pydantic JSON Schemas for cross-language generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quietpilot_agent.models import CandidateProposal, CasePlanProposal

SCHEMAS = {
    "candidate-proposal.schema.json": CandidateProposal.model_json_schema,
    "case-plan-proposal.schema.json": CasePlanProposal.model_json_schema,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for filename, schema_factory in SCHEMAS.items():
        content = json.dumps(
            schema_factory(), ensure_ascii=False, indent=2, sort_keys=True
        )
        (args.output_dir / filename).write_text(f"{content}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
