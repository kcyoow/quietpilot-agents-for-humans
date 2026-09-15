# QuietPilot architecture

The [PNG attachment](architecture.png) and [editable SVG](architecture.svg) show the implemented mail-to-Calendar path. They describe components and authority, not a promise that every incoming mail produces an action.

## Main flow

1. **Observe authorized mail.** Manual scans and authenticated Gmail Pub/Sub notifications enter the existing queue/worker flow. Daily watch renewal and six-hour recovery are enabled. Queue messages carry bounded references and job state; Google OAuth credentials stay server-side.
2. **Prepare a source-bound Case.** Strands agents on Amazon Bedrock AgentCore use bounded evidence/capability tools and Amazon Nova 2 Lite. Mail discovery, local preparation and Calendar planner/verifier stages produce typed output. Validators check source relevance, completeness, references and dates/times. AgentCore Identity supplies delegated Google credentials for the authorized connector.
3. **Request exact approval.** The Android app authenticates through Cognito and the control API. A saved plan in DynamoDB binds the owner, source, action, version and hash. The user reviews that plan and grants ONCE approval if the event is wanted.
4. **Execute and verify.** A durable worker rechecks the approved snapshot and claims the action before invoking the Calendar connector. Creation uses idempotency protections; server readback must match before a verified result reaches the Case. An uncertain outcome is not completion.
5. **Return the result.** The app reads current authenticated server state and can reopen the completed Case from history.

The snake-shaped arrows in the diagram follow preparation left to right, then approval/execution right to left. That second row begins at the Android user, not at the model.

## Routines and attention

An owned, completed and verified Calendar Case can propose an inactive sender-domain DEADLINE/APPOINTMENT routine. Explicit activation enables PREPARE_ONLY behavior. Current owner/account/connection/profile bindings, received-after-activation checks, Candidate version/status conditions and deterministic Case IDs limit which new mail may create a Case. A routine never reuses the previous event's parameters or carries its approval forward. A fresh Calendar draft still requires a new exact approval.

Persisted attention state feeds the notification worker, Expo Push Service and FCM. Payloads contain a generic review prompt, not mail contents. Tapping retrieves the current owned LIVE Case. Ordinary discovery remains silent.

## Code entry points

| Responsibility | Source |
| --- | --- |
| Runtime routing and validated preparation | [agentcore_runtime.py](../../services/agent/src/quietpilot_agent/agentcore_runtime.py) |
| Strands mail discovery | [discovery.py](../../services/agent/src/quietpilot_agent/discovery.py) |
| Calendar planner and independent verifier | [calendar_planner.py](../../services/agent/src/quietpilot_agent/calendar_planner.py) |
| Approved Calendar execution | [calendar_execution.py](../../services/worker/src/quietpilot_worker/calendar_execution.py) |
| Source-bound routine creation | [worker routines.py](../../services/worker/src/quietpilot_worker/routines.py), [API routines.py](../../services/control-api/src/quietpilot_control_api/routines.py) |
| Authenticated mobile server state | [WorkspaceProvider.tsx](../../apps/mobile/src/workspace/WorkspaceProvider.tsx) |

## Evidence boundary

Live evidence covers Gmail processing, one ONCE-approved Calendar event with readback/history, explicit routine activation and actual Android remote-notification receipt/tap. The authenticated Gmail background entry and maintenance schedules are configured and enabled. Natural new matching mail creating a routine Case remains unobserved; its positive flow is verified locally using the actual SDK, Moto and scripted Google/Expo services.

This architecture does not claim live SMS, SmartThings or iOS support. The full dated evidence and test-build limitations are in the [runtime verification guide](../runtime-verification.md).

At the latest handoff, runtime 89/DEFAULT 89 is READY and the latest original-account scan completed with 62 messages, with a warning that some optional task suggestions remain incomplete. A standalone ARM64 APK passed fresh-account signup/sign-in, Gmail consent/return and replacement-persistence checks. Further fresh-account Calendar/push checks were intentionally skipped and remain unverified. Hosted Google OAuth is in testing mode with two allowlisted users, so judge access must be arranged explicitly.
