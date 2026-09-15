# Agent runtime boundary

The local runtime uses `strands-agents==1.53.0` with an explicitly injected,
network-free deterministic model. It exercises the current Strands event loop,
Agents-as-Tools, Pydantic structured-output tool, and repair hooks without selecting
the SDK's default Bedrock provider.

Static tool registries are intentionally limited to:

- orchestrator: `signal_analyst`, `capability_analyst`, `case_planner`,
  `propose_candidate`, `propose_case_plan`
- Signal Analyst: `read_evidence_context`
- Capability Analyst: `read_capability_context`
- Case Planner: no static tool

The read tools are bound to a fresh user-scoped context and one call each. Proposal
tools stage data in an invocation transaction; only a grounded final typed output can
commit it. No connector executor or external mutation tool is registered.

Run the local proof from the repository root:

```sh
uv run --package quietpilot-agent pytest -q services/agent/tests
uv run ruff check services/agent
npm run check:contracts
```

The approved cloud gate added a Python 3.12 CodeZip configuration and an injectable
production factory whose default is `global.amazon.nova-2-lite-v1:0` from
`ap-northeast-2`. `QUIETPILOT_BEDROCK_MODEL_ID` remains an explicit deployment-time
override. Local tests still inject the deterministic model and never call AWS. A
bounded Nova 2 Lite control call succeeded, but do not present that control call or a
valid local package as a deployed AgentCore proof.

The runtime sets `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_unredacted_attributes=` so
Strands redacts model inputs, outputs, system instructions and tool arguments/results.
It also sets `DISABLE_ADOT_OBSERVABILITY=true` because AgentCore's default botocore
auto-instrumentation records Bedrock message content independently of the Strands
redaction policy. QuietPilot retains its own content-free operational logs; do not
remove either deployment variable without a separate privacy review.
