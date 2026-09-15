# QuietPilot

Further fresh-account Calendar and remote-push checks were intentionally skipped for this release at the participant's direction; they remain unverified. The completed fresh-account Gmail checks and earlier original-account Calendar/push evidence remain valid. Google testing-user allowlisting still needs to be arranged with the participant; private contact details are not published.

**Devpost submission draft.** Source and Android release links are provided below. Video publication and judge-access contact details remain pending. Nothing in this file has been sent to Devpost.

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

Follow [judge testing instructions](docs/submission/judge-testing.md). The standalone ARM64 Android preview has embedded JavaScript and does not need Metro or an AWS account. Fresh-account sign-up/sign-in, Gmail consent/return, account separation and same-signer replacement persistence were verified. **The Android release link is below. Google OAuth currently has two allowlisted test users; judges must arrange Google access with the submitter.** Calendar consent and a new push check on the fresh account remain pending.

Developers can use the [README](README.md) and [runtime setup guide](docs/runtime-verification.md) to install dependencies, configure an existing deployment and run local checks. Those commands do not provision a fresh AWS/Google environment or replace judge access.

The latest checks passed **1,919 Python tests in 41.77 seconds and 577 mobile tests in 41 suites in 5.19 seconds**; the earlier SDK alignment passed Expo Doctor 21/21. Runtime 89/DEFAULT 89 is READY. The latest original-account scan completed with **62 messages**, with the warning “Some task suggestions are still incomplete.” This confirms mail collection, not complete optional-action preparation or mailbox-wide accuracy. Validation guards remain unchanged.

Live evidence also includes one earlier ONCE-approved Calendar event with readback/history, a persisted NO_ACTION review, a real activated routine and Android remote-notification receipt/tap. A new English notification on the original connected device opened the owned Case/date-time question; that QA Case was then stopped. These checks created no new external event.

## Public Demo Link

[Download the Android preview](https://github.com/kcyoow/quietpilot-agents-for-humans/releases/download/v0.1.0-hackathon/quietpilot-preview.apk). **Google allowlisting/contact instructions remain pending.** The standalone APK and hosted fresh-account path have been tested; Google access is restricted to two currently allowlisted test users. A live-demo URL is optional in the form, but usable testing access is required. Free access must remain available through **9 October 2026, 09:00 KST**.

## Public Repository Link

[QuietPilot public source](https://github.com/kcyoow/quietpilot-agents-for-humans). This repository begins with the reviewed source-only snapshot; the original private repository/history is preserved separately. MIT, Expo and Space Mono OFL notices are included.

## Demo Video

**Local film completed and reviewed; public URL pending approval.** The 186.02-second video uses English narration and captions, actual native footage and the architecture diagram. Video/audio decoding and representative frame review passed; the official maximum is five minutes.

Final local artifact: `artifacts/submission/quietpilot-demo-english.mp4`, distributed separately from the source export. The corrected film passed full decoding and uses genuine English-only historical UI crops, a labeled notification/later-Case edit and the actual English routine screen at the end. No saved record or app code was changed for these shots.

The [English narration and shot list](docs/submission/video-script.md) and [retimed subtitles](docs/submission/video-subtitles.srt) cover the problem, live mail, one previously approved and verified Calendar result, routine authority, notifications and Strands architecture. Historical execution evidence is labeled; the edit does not imply a new event was created during the recording.

## Screenshot Shot List

1. Live mail results with a clear, useful next step; mask personal senders and unrelated mail.
2. A Calendar Case showing the proposed change and its approval boundary.
3. The verified completed Case reopened from history; label the event as previously approved.
4. The explicitly active PREPARE_ONLY routine, including pause control and its limited authority.
5. A generic OS notification and the exact owned Case opened from its tap.

Capture the final English UI where available. Source-derived text and earlier saved results must not be rewritten for a screenshot; translate their meaning in captions when needed.

## Submission Readiness Notes

The working product has real external-action evidence, a standalone APK and fresh-account Gmail checks. Remaining items are fresh-account Calendar warning/consent and new push verification, the new repository and APK/video URLs, concrete judge Google access and final form entry. The [preparation status](docs/submission/preparation-status.md) keeps these separate from a completed submission.

## Known Limitations

- The current live product proof is Android on an emulator with Gmail and Google Calendar. Physical devices, iOS, SmartThings control and SMS integration are not claimed as verified.
- The live Calendar proof covers one private deadline event without invitees or reminders. A 15-minute deadline marker represents a deadline, not the duration of the underlying activity.
- A real routine is active, and background entry points are configured. The positive new-mail-to-routine-to-approval-waiting path is covered by SDK/Moto tests with scripted services; it has not yet been observed on a naturally arriving matching real email.
- A scan with no warning is not a mailbox-wide accuracy or relevance score. Truncated sources, ambiguous timing and unsupported actions can still stop preparation.
- Google OAuth is in testing mode with two allowlisted users. A new judge must arrange access; fresh-account Calendar consent and a new push check are still pending. The latest scan completed with 62 messages, but some optional action suggestions remain incomplete. This is a hosted prototype, not a production-scale multi-tenant launch.

## TODO Official Form Fields

| Field | Draft value/status |
| --- | --- |
| Project title | QuietPilot |
| Tagline | An everyday mail agent that prepares useful next steps, asks before changing your calendar, and verifies the result. |
| Category | **Everyday Agents** |
| Submitter Type | **Individual** — participant confirmed. |
| Country | **Korea Republic of** — participant confirmed; use the exact form option. |
| AWS Builder ID | Authenticated Builder profile verified; enter the required value privately in the official form. It is deliberately omitted from public materials. |
| Public repository | Pending approval and URL for a new repository with fresh source-only history. |
| Architecture file | `docs/submission/architecture.png` after artifact review. |
| Working video | 186.02-second English film reviewed; public URL pending approval. |
| Testing access | Standalone APK/fresh-account Gmail path verified; public download and judge Google allowlisting arrangements pending. Maintain free access through the judging cutoff. |

No additional personal identifier is included unless the actual form requires it.
