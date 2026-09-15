# QuietPilot

Further fresh-account Calendar and remote-push checks were intentionally skipped for this release at the participant's direction; they remain unverified. The completed fresh-account Gmail checks and earlier original-account Calendar/push evidence remain valid. Arrange Google testing-user allowlisting through the submission contact; private contact details are not published.

**Devpost submission draft.** Public source and Android release links are provided below. Arrange Google access through the submission contact. The coordinating submission workflow owns the final video URL and Devpost status.

## One-line Summary

An everyday mail agent that prepares useful next steps, asks before changing your calendar, and verifies the result.

## Problem

An important email is rarely the end of a task. You still have to recognize the deadline, check the date and timezone, decide what belongs on your calendar, and make sure it was saved correctly. Across a busy inbox, that small coordination burden repeats every day.

The target user is someone who already relies on email and a calendar but does not want to build automation rules or supervise a long conversation to keep them in sync.

## Solution

QuietPilot turns useful mail into a bounded piece of work called a **Case**. It reads the Gmail sources the user has connected, filters them through the user's interests, and prepares a concrete next step. A Case keeps the source, proposed change, approval and verified outcome together.

For Calendar work, QuietPilot prepares the event details and asks for one exact approval. The backend checks that the approved plan is still current, creates the event, and reads it back from Google before showing completion. If the source is ambiguous or preparation is incomplete, the agent stops or asks for input instead of inventing a result.

After a successfully verified Calendar Case, the user can activate a narrow **PREPARE_ONLY** routine for future mail from the same sender domain and opportunity type. It may prepare a new Case; it does not inherit the old event or permission to write to Calendar. Each new Calendar action still needs its own approval. Attention notifications bring the user back to a Case when a decision is needed, while ordinary discoveries remain quiet.

## Why This Matters

The useful outcome is a trustworthy handoff from “I should deal with this email” to “I know what will change, and I can see what actually happened.” QuietPilot aims to reduce the work of noticing, translating and checking small obligations without asking users to surrender control of their accounts.

This prototype establishes that loop with real Gmail, one approved Google Calendar event, persistent Case history and Android notifications. We have not yet measured time savings or run a broad user study.

## How We Used AI

The backend uses **Strands Agents SDK for Python** on **Amazon Bedrock AgentCore Runtime**, with **Amazon Nova 2 Lite through Amazon Bedrock**. Strands powers the working source assessment, proposal and verification path.

- Bounded, owner-scoped evidence and capability tools give agents the context they are allowed to inspect.
- Specialized mail assessment and discovery agents produce typed, source-linked suggestions. Structured-output validation and bounded repair reject unsupported fields and actions.
- A Calendar planner and a separate verifier assess the selected source. Date, year, time, offset and quoted-source checks run before a fresh draft can become a saved plan.
- Deterministic code owns authentication, plan versions and hashes, approval scope, retries, idempotency and result verification. A model's recommendation never grants execution authority.

The live path continues beyond inference: an approved action reaches the Google Calendar connector, and its readback becomes the result shown in the mobile Case.

## How We Used Codex

Codex helped turn the product requirements into the mobile app, typed contracts, Strands runtime, AWS infrastructure and verification workflow. It also helped investigate failures that only appeared in the real SDK and deployed path: incomplete MIME sources, malformed structured output, source-reference errors, date formatting, retry state and mobile response decoding.

We used focused synthetic regressions, SDK/Moto integration tests, content-free operational diagnostics and native UI checks to distinguish an implementation that passes tests from an external action that actually completed. Human decisions retained control over account consent, the exact Calendar approval, routine activation and publication.

## Key Features

- **Mail with a next step:** interest-based results connect the reason for a suggestion to an action the system can prepare.
- **One readable Case:** inspect the source, draft, approval state and result in one place; a no-action review can also finish and remain in history.
- **Exact Calendar approval:** preparation creates no external event; the approved plan is checked again before execution.
- **Verified completion:** Calendar creation is followed by server readback and a persistent result, with retry protections tested separately.
- **Reusable preparation:** an explicitly activated sender-domain routine can prepare future deadline or appointment Cases without recurring write authority.
- **Attention notifications:** a generic Android notification opens the current, authenticated, owned Case; ordinary candidate discovery does not send a push.

## Architecture

The Android Expo/React Native app signs in through Cognito and calls an authenticated control API. DynamoDB stores Cases, plans, approvals, routine bindings and dispatch state. SQS and Lambda workers carry out durable background work and invoke Strands on AgentCore. AgentCore Identity manages delegated Google credentials server-side. The Calendar execution path rechecks the approved snapshot and verifies Google's response before completion. The notification path uses Expo Push Service and FCM.

Gmail watch/Pub/Sub delivery and scheduled watch renewal/recovery feed the background queue. The configured path is live; a newly received real matching email creating a routine Case has not yet been observed.

Required attachment: [architecture PNG](docs/submission/architecture.png). Editable source: [architecture SVG](docs/submission/architecture.svg). See the [component and evidence notes](docs/submission/architecture.md).

## Testing Instructions

Follow [judge testing instructions](docs/submission/judge-testing.md). The standalone ARM64 Android preview has embedded JavaScript and does not need Metro or an AWS account. Fresh-account sign-up/sign-in, Gmail consent/return, account separation and same-signer replacement persistence were verified. **The Android release link is below. Google OAuth currently has two allowlisted test users; judges must arrange Google access through the submission contact.** Further fresh-account Calendar/push checks were intentionally skipped and remain unverified.

Developers can use the [README](README.md) and [runtime setup guide](docs/runtime-verification.md) to install dependencies, configure an existing deployment and run local checks. Those commands do not provision a fresh AWS/Google environment or replace judge access.

The latest checks passed **1,919 Python tests in 41.77 seconds and 577 mobile tests in 41 suites in 5.19 seconds**; the earlier SDK alignment passed Expo Doctor 21/21. Runtime 89/DEFAULT 89 is READY. The latest original-account scan completed with **62 messages**, with the warning “Some task suggestions are still incomplete.” This confirms mail collection, not complete optional-action preparation or mailbox-wide accuracy. Validation guards remain unchanged.

Live evidence also includes one earlier ONCE-approved Calendar event with readback/history, a persisted NO_ACTION review, a real activated routine and Android remote-notification receipt/tap. A new English notification on the original connected device opened the owned Case/date-time question; that QA Case was then stopped. These checks created no new external event.

## Public Demo Link

[Download the Android preview](https://github.com/kcyoow/quietpilot-agents-for-humans/releases/download/v0.1.0-hackathon/quietpilot-preview.apk). **Arrange Google testing-user allowlisting through the submission contact.** The standalone APK and hosted fresh-account path have been tested; Google access is restricted to two currently allowlisted test users. A live-demo URL is optional in the form, but usable testing access is required. Free access must remain available through **9 October 2026, 09:00 KST**.

## Public Repository Link

[QuietPilot public source](https://github.com/kcyoow/quietpilot-agents-for-humans). This repository begins with the reviewed source-only snapshot; the original private repository/history is preserved separately. MIT, Expo and Space Mono OFL notices are included.

## Demo Video

The current demo is a **70.75-second actual native interaction edit**, `artifacts/submission/quietpilot-demo-live.mp4`, distributed separately from the repository. It replaces the rejected slideshow. [Watch the unlisted demo](https://www.youtube.com/watch?v=soepNZl7xAo). The earlier slideshow and its subtitles are not the current demo.

The film must not be interpreted as fresh Calendar creation. The latest direct-request QA failed during preparation after clarification; the earlier one-event approval/readback proof remains historical. See the [current media notes](docs/submission/video-script.md).

## Screenshot Shot List

1. Live mail results with a clear, useful next step; mask personal senders and unrelated mail.
2. A Calendar Case showing the proposed change and its approval boundary.
3. The verified completed Case reopened from history; label the event as previously approved.
4. The explicitly active PREPARE_ONLY routine, including pause control and its limited authority.
5. A generic OS notification and the exact owned Case opened from its tap.

Capture the final English UI where available. Source-derived text and earlier saved results must not be rewritten for a screenshot; translate their meaning in captions when needed.

## Submission Readiness Notes

The working product has real external-action evidence, a standalone APK and fresh-account Gmail checks. The public source and APK are available at the links above. Further fresh-account Calendar/push checks were intentionally skipped; judge Google access is arranged through the submission contact. Final video/Devpost publication is handled separately. The [preparation status](docs/submission/preparation-status.md) keeps these separate from a completed submission.

The latest direct-request Calendar QA failed during preparation after clarification. It did not create a new event. The earlier ONCE-approved event/readback remains separate historical proof.

## Known Limitations

- The current live product proof is Android on an emulator with Gmail and Google Calendar. Physical devices, iOS, SmartThings control and SMS integration are not claimed as verified.
- The live Calendar proof covers one private deadline event without invitees or reminders. A 15-minute deadline marker represents a deadline, not the duration of the underlying activity.
- A real routine is active, and background entry points are configured. The positive new-mail-to-routine-to-approval-waiting path is covered by SDK/Moto tests with scripted services; it has not yet been observed on a naturally arriving matching real email.
- A scan with no warning is not a mailbox-wide accuracy or relevance score. Truncated sources, ambiguous timing and unsupported actions can still stop preparation.
- Google OAuth is in testing mode with two allowlisted users. A new judge must arrange access; further fresh-account Calendar/push checks were intentionally skipped and remain unverified. The latest scan completed with 62 messages, but some optional action suggestions remain incomplete. This is a hosted prototype, not a production-scale multi-tenant launch.

## TODO Official Form Fields

| Field | Draft value/status |
| --- | --- |
| Project title | QuietPilot |
| Tagline | An everyday mail agent that prepares useful next steps, asks before changing your calendar, and verifies the result. |
| Category | **Everyday Agents** |
| Submitter Type | **Individual** — participant confirmed. |
| Country | **Korea Republic of** — participant confirmed; use the exact form option. |
| AWS Builder ID | Authenticated Builder profile verified; enter the required value privately in the official form. It is deliberately omitted from public materials. |
| Public repository | https://github.com/kcyoow/quietpilot-agents-for-humans — public source-only history. |
| Architecture file | `docs/submission/architecture.png` after artifact review. |
| Working video | 70.75-second actual native interaction edit; [Unlisted demo](https://www.youtube.com/watch?v=soepNZl7xAo). |
| Testing access | Public standalone APK available; arrange Google allowlisting through the submission contact. Further fresh-account Calendar/push checks were intentionally skipped. |

No additional personal identifier is included unless the actual form requires it.
