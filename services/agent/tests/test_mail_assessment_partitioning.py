from __future__ import annotations

import inspect
import json
from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
from dataclasses import fields
from threading import Barrier, Event, Lock, Thread

import pytest
from quietpilot_agent.bedrock_model import BedrockModelFactory
from quietpilot_agent.local_model import DeterministicModel, ModelPlan
from quietpilot_agent.mail_interests import (
    MailFieldAssessment,
    MailInterestOutputError,
    MailInterestProfile,
    match_interest_mail,
)
from quietpilot_agent.models import EvidenceRecord

_MISSING = object()


def records(count=8):
    return [
        EvidenceRecord(
            user_id="synthetic-owner",
            ref=f"original-mail-{index}",
            revision=1,
            source="gmail",
            title=f"학교 소식 {index}",
            facts=[],
            untrusted_text=f"이 메일만의 합성 원문 private-body-{index}예요.",
        )
        for index in range(1, count + 1)
    ]


PROFILE = MailInterestProfile(revision=1, tags=["학교"])


class ScopedModel(DeterministicModel):
    def __init__(self, role, plan, owner):
        super().__init__(role, plan)
        self.owner = owner

    async def stream(self, messages, *args, **kwargs):
        first = self.stream_calls == 0
        if first:
            prompt = next(
                json.loads(block["text"])
                for message in messages
                for block in message.get("content", [])
                if "text" in block
                and block["text"].startswith("{")
                and "evidence" in json.loads(block["text"])
            )
            aliases = tuple(item["ref"] for item in prompt["evidence"])
            self.aliases = aliases
            values = self.plan.output.model_dump(mode="json")
            if not self.owner.negative:
                for item in prompt["evidence"]:
                    alias = item["ref"]
                    values.update(
                        {
                            f"{alias}_tag_refs": "t1",
                            f"{alias}_importance": "NORMAL",
                            f"{alias}_summary": "School news is available.",
                            f"{alias}_reason": "Matches the saved school topic.",
                            f"{alias}_source_ref": next(iter(item["source_passages"])),
                        }
                    )
            if (
                self.role == "mail_interest_verifier"
                and self.owner.fail_alias in aliases
            ):
                values[f"{self.owner.fail_alias}_source_ref"] = ""
            self.plan = ModelPlan(
                steps=(), output=type(self.plan.output).model_construct(**values)
            )
            with self.owner.lock:
                self.owner.contexts.append((self.role, aliases, prompt))
        with self.owner.lock:
            self.owner.active += 1
            self.owner.max_active = max(self.owner.max_active, self.owner.active)
        try:
            if first:
                self.owner.before(self.role, self.aliases)
            async for event in super().stream(messages, *args, **kwargs):
                yield event
        finally:
            with self.owner.lock:
                self.owner.active -= 1


class ScopedFactory:
    def __init__(
        self,
        *,
        batch_size=_MISSING,
        concurrency=_MISSING,
        negative=False,
        fail_alias=None,
    ):
        if batch_size is not _MISSING:
            self.mail_batch_size = batch_size
        if concurrency is not _MISSING:
            self.mail_concurrency = concurrency
        self.negative = negative
        self.fail_alias = fail_alias
        self.lock = Lock()
        self.models = []
        self.contexts = []
        self.active = 0
        self.max_active = 0
        self.before = lambda role, aliases: None

    def create(self, role, plan):
        model = ScopedModel(role, plan, self)
        with self.lock:
            self.models.append(model)
        return model


def test_generic_factory_retains_original_eight_record_batch_and_serial_calls():
    factory = ScopedFactory()
    result = match_interest_mail(PROFILE, records(), factory)
    assert [item.evidence_ref for item in result] == [item.ref for item in records()]
    assert len(factory.models) == 2 and factory.max_active == 1
    assert all(len(aliases) == 8 for _, aliases, _ in factory.contexts)


def test_singleton_partition_uses_at_most_four_independent_sdk_contexts():
    factory = ScopedFactory(batch_size=1, concurrency=4)
    barrier = Barrier(4)
    first_wave = 0

    def before(role, aliases):
        nonlocal first_wave
        del aliases
        if role == "mail_interest_matcher":
            with factory.lock:
                join = first_wave < 4
                first_wave += 1
            if join:
                barrier.wait(timeout=5)

    factory.before = before
    result = match_interest_mail(PROFILE, records(), factory)
    assert [item.evidence_ref for item in result] == [item.ref for item in records()]
    assert factory.max_active == 4 and factory.active == 0
    assert len(factory.models) == len({id(model) for model in factory.models}) == 16
    assert {aliases for _, aliases, _ in factory.contexts} == {
        (f"m{index}",) for index in range(1, 9)
    }
    for _, aliases, prompt in factory.contexts:
        assert len(prompt["evidence"]) == 1
        encoded = json.dumps(prompt, ensure_ascii=False)
        own_index = int(aliases[0][1:])
        assert f"private-body-{own_index}" in encoded
        for index in range(1, 9):
            if index != own_index:
                assert f"private-body-{index}" not in encoded
        assert "synthetic-owner" not in encoded and "original-mail-" not in encoded


def test_actual_future_completion_order_does_not_change_result_order(monkeypatch):
    second_done = Event()
    completion_order = []

    class RecordingExecutor(RealThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            offset = kwargs["alias_offset"]
            future = super().submit(fn, *args, **kwargs)

            def completed(_future):
                completion_order.append(offset)
                if offset == 1:
                    second_done.set()

            future.add_done_callback(completed)
            return future

    monkeypatch.setattr(
        "quietpilot_agent.mail_interests.ThreadPoolExecutor", RecordingExecutor
    )
    factory = ScopedFactory(batch_size=1, concurrency=2)

    def before(role, aliases):
        if role == "mail_interest_verifier" and aliases == ("m1",):
            assert second_done.wait(timeout=5)

    factory.before = before
    result = match_interest_mail(PROFILE, records(2), factory)
    assert completion_order == [1, 0]
    assert [item.evidence_ref for item in result] == [item.ref for item in records(2)]


def test_all_negative_singletons_still_receive_both_independent_assessments():
    factory = ScopedFactory(batch_size=1, concurrency=4, negative=True)
    assert match_interest_mail(PROFILE, records(), factory) == []
    assert len(factory.models) == 16
    assert all(model.stream_calls == 1 for model in factory.models)
    assert len({(role, aliases) for role, aliases, _ in factory.contexts}) == 16


def test_later_child_failure_cancels_pending_work_before_blocked_first_child_finishes(
    monkeypatch,
):
    cancel_seen = Event()
    release_active = Event()
    finished = Event()
    submitted = []
    outcome = {}

    class RecordingExecutor(RealThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            future = super().submit(fn, *args, **kwargs)
            submitted.append(future)

            def completed(value):
                if value.cancelled():
                    cancel_seen.set()

            future.add_done_callback(completed)
            return future

    monkeypatch.setattr(
        "quietpilot_agent.mail_interests.ThreadPoolExecutor", RecordingExecutor
    )
    factory = ScopedFactory(batch_size=1, concurrency=2, fail_alias="m2")

    def before(role, aliases):
        if role == "mail_interest_matcher" and aliases != ("m2",):
            assert release_active.wait(timeout=5)

    factory.before = before

    def run():
        try:
            outcome["result"] = match_interest_mail(PROFILE, records(), factory)
        except MailInterestOutputError as error:
            outcome["error"] = error
        finally:
            finished.set()

    thread = Thread(target=run)
    thread.start()
    try:
        assert cancel_seen.wait(timeout=5)
        assert not finished.is_set()
    finally:
        release_active.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert isinstance(outcome.get("error"), MailInterestOutputError)
    assert "result" not in outcome
    assert any(future.cancelled() for future in submitted)
    assert all(future.done() for future in submitted)
    assert factory.active == 0


@pytest.mark.parametrize(
    "options",
    [
        {"batch_size": True},
        {"batch_size": "1"},
        {"batch_size": 0},
        {"batch_size": 9},
        {"concurrency": False},
        {"concurrency": 2.0},
        {"concurrency": 0},
        {"concurrency": 5},
        {"concurrency": None},
    ],
)
def test_invalid_factory_capabilities_fail_before_any_model_creation(options):
    factory = ScopedFactory(**options)
    with pytest.raises((TypeError, ValueError)):
        match_interest_mail(PROFILE, records(), factory)
    assert factory.models == []


@pytest.mark.parametrize("kind", ["profile", "count", "duplicate"])
def test_input_validation_happens_before_any_partition_starts(kind):
    factory = ScopedFactory(batch_size=1, concurrency=4)
    source = records(9) if kind == "count" else records(2)
    if kind == "duplicate":
        source[1] = source[0]
    profile = MailInterestProfile(revision=1) if kind == "profile" else PROFILE
    with pytest.raises(ValueError):
        match_interest_mail(profile, source, factory)
    assert factory.models == []


def test_bedrock_capabilities_are_class_only_and_not_request_parameters(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "quietpilot_agent.bedrock_model.BedrockModel",
        lambda **kwargs: captured.append(kwargs),
    )
    factory = BedrockModelFactory()
    assert factory.mail_batch_size == 1 and factory.mail_concurrency == 4
    assert {item.name for item in fields(factory)} == {"model_id", "region"}
    assert set(inspect.signature(BedrockModelFactory).parameters) == {
        "model_id",
        "region",
    }
    factory.create(
        "mail_interest_verifier", ModelPlan(steps=(), output=MailFieldAssessment())
    )
    assert (
        "mail_batch_size" not in captured[0] and "mail_concurrency" not in captured[0]
    )
