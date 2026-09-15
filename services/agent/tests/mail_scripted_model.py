"""Synthetic test verdicts encoded as the actual required per-mail fields."""

import json
import unicodedata

from quietpilot_agent.local_model import DeterministicModel, ModelPlan, ToolStep
from quietpilot_agent.mail_interests import (
    MailDecision,
    MailDecisionAssessment,
    MailInterestAssessment,
)


class FixtureMailModel(DeterministicModel):
    async def stream(self, messages, *args, **kwargs):
        output = self.plan.output
        if isinstance(
            output, (MailInterestAssessment, MailDecisionAssessment)
        ) and not getattr(self, "encoded", False):
            prompt = next(
                json.loads(block["text"])
                for message in messages
                for block in message.get("content", [])
                if "text" in block
                and block["text"].startswith("{")
                and "evidence" in json.loads(block["text"])
            )
            evidence = {r["ref"]: r for r in prompt["evidence"]}
            tags = prompt["interest_profile"]["tags"]
            if isinstance(output, MailInterestAssessment):
                decisions = []
                for match in output.matches:
                    record = evidence.get(match.evidence_ref)
                    quote = (
                        next(iter(record["source_passages"].values()), "")
                        if record
                        else "not supplied source"
                    )
                    decisions.append(
                        MailDecision(
                            evidence_ref=match.evidence_ref,
                            matched_tags=match.matched_tags,
                            description_match=not match.matched_tags
                            and bool(prompt["interest_profile"]["description"].strip()),
                            importance=match.importance,
                            summary=match.summary,
                            reason=match.reason,
                            supporting_quotes=[quote] if len(quote) >= 8 else [],
                        )
                    )
                cited = {m.evidence_ref for m in output.matches}
                decisions += [
                    MailDecision(
                        evidence_ref=ref,
                        matched_tags=[],
                        description_match=False,
                        importance="LOW",
                        summary="",
                        reason="",
                        supporting_quotes=[],
                    )
                    for ref in output.assessed_evidence_refs
                    if ref not in cited
                ]
                output = MailDecisionAssessment(
                    assessed_evidence_refs=output.assessed_evidence_refs,
                    decisions=decisions,
                )
            refs = [d.evidence_ref for d in output.decisions]
            if len(refs) != len(set(refs)):
                raise ValueError(
                    "Duplicate fixture decisions cannot be represented as unique mail fields"
                )
            if any(len(d.supporting_quotes) > 1 for d in output.decisions):
                raise ValueError(
                    "Fixture must provide the single source quote used by the field protocol"
                )

            def source_ref(decision):
                if not decision.supporting_quotes:
                    return ""
                quote = " ".join(
                    unicodedata.normalize("NFC", decision.supporting_quotes[0]).split()
                )
                passages = evidence.get(decision.evidence_ref, {}).get(
                    "source_passages", {}
                )
                if len(quote) >= 8:
                    for ref, text in passages.items():
                        if quote in " ".join(
                            unicodedata.normalize("NFC", text).split()
                        ):
                            return ref
                return "unavailable_source"

            fields = {}
            for d in output.decisions:
                values = {
                    "tag_refs": ",".join(
                        sorted(
                            f"t{tags.index(tag) + 1}" if tag in tags else "unknown_tag"
                            for tag in d.matched_tags
                        )
                    ),
                    "description_match": "YES" if d.description_match else "NO",
                    "excluded": "YES" if d.excluded else "NO",
                    "importance": d.importance,
                    "summary": d.summary,
                    "reason": d.reason,
                    "source_ref": source_ref(d),
                }
                fields.update(
                    {
                        f"{d.evidence_ref}_{name}": value
                        for name, value in values.items()
                    }
                )
            step = ToolStep("MailFieldAssessment", fields)
            self.plan = ModelPlan(
                steps=(*self.plan.steps, *([step] * 4)), output=self.plan.output
            )
            self.encoded = True
        async for event in super().stream(messages, *args, **kwargs):
            yield event
