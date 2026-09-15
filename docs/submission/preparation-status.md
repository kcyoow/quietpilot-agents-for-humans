# Submission preparation status

Further fresh-account Calendar and remote-push checks were intentionally skipped for this release at the participant's direction; they remain unverified. The completed fresh-account Gmail checks and earlier original-account Calendar/push evidence remain valid. Google testing-user allowlisting still needs to be arranged with the participant; private contact details are not published.

**Release preparation record — 15 September 2026. Source/APK publication is authorized; Devpost submission and video publication remain separate.**

This packet prepares the verified mail-to-Calendar product for the Agents for Humans Hackathon. The working deadline is **15 September 2026, 09:30 KST**. Official requirements and registration were checked by the coordinating agent during this session; this file is a local handoff, not proof of a Devpost submission.

## Scope and completion checks

Editable paths: `devpost-submission.md`, `README.md`, `docs/runtime-verification.md`, `docs/submission/*` and the proposed root `LICENSE`. Video recording/rendering, application changes, deployment, account testing and publication belong to the coordinating work. Source mail, credentials, historical results, Git history and unrelated files are protected.

- [x] Read the submission skill and the relevant scope, PRD, technical spec, checklist and build notes.
- [x] Write the English project story, judge instructions and accurate evidence boundary.
- [x] Create and visually inspect a readable architecture SVG and PNG below 35 MiB.
- [x] Draft a three-minute English narration, shot list and synchronized subtitle file.
- [x] Preserve the Expo notice while adding a proposed MIT license for QuietPilot source.
- [x] Check local links, required sections, tagline length, subtitle timing and artifact sizes.

## Current verified checkpoint

The following facts were supplied by the coordinating work after the final English regression and standalone preview checks. They distinguish the new account from the earlier external-action proof:

- Runtime 89/DEFAULT 89 is READY, and Worker deployment is UPDATE_COMPLETE. The latest original-account scan **completed with 62 messages on September 15**, with “Mail found. Some task suggestions are still incomplete.” Optional action preparation still has a warning; validation guards/retry limits remain unchanged. This is not zero-warning or mailbox-wide accuracy proof.
- The standalone ARM64 APK is version 0.1.0/code 1, minimum API 24/target 36, non-debuggable and signed with a dedicated signer. Size: 46,371,800 bytes; embedded JavaScript: 4,477,512 bytes. Its checksum is recorded in the [judge guide](judge-testing.md).
- A fresh account passed signup/sign-in and Gmail consent/return. The initial list did not contain the original account's work. Session/connection survived same-signer APK replacement and notification ON persisted. Fresh-account Calendar and remote-push checks were intentionally skipped and remain unverified.
- One private Calendar event completed exact ONCE approval, Google creation, server readback and native history reopening. A separate NO_ACTION review persisted in history.
- One real sender-domain deadline routine was explicitly activated as PREPARE_ONLY. Its ACTIVE state survived a server refresh. The authenticated Gmail Pub/Sub path and both maintenance schedules are configured/enabled. A newly received real matching email has not yet been observed creating a routine Case.
- An actual server → Expo → FCM → Android notification was received, and a HOME tap opened the exact owned LIVE Case. OFF/ON server persistence and device resume were checked. A new English remote notification on the original connected device opened the owned Case/date-time question; that QA Case was then STOPPED.
- The latest full regression passed **1,919 Python tests in 41.77 seconds** and **577 mobile tests in 41 suites in 5.19 seconds**. The earlier SDK alignment passed Expo Doctor 21/21. The natural new-mail routine path has SDK/Moto/scripted integration evidence, separate from live activation and remote push evidence.
- The participant's authenticated AWS Builder profile was verified. The required identifier is held privately for form entry and is not included in this packet.

## Final handoff items

| Item | Current status | Required before treating it as ready |
| --- | --- | --- |
| Public source repository | [New source repository](https://github.com/kcyoow/quietpilot-agents-for-humans). Existing private history is preserved separately. | Publish a final reviewed source-only export with fresh history; do not push the private historical commits. |
| Standalone Android test build | **Built and fresh-account path checked.** Exact metadata/checksum is in the judge guide. | Approve and verify a public APK download; finish fresh-account Calendar consent/new push checks. |
| Independent judge account | Signup/sign-in, Gmail consent/return, initial account separation and replacement persistence verified. | Complete fresh-account Calendar consent and new remote-push verification. |
| Hosted testing access | **Google OAuth testing gate: two allowlisted users.** Judges must arrange access. | Add the actual allowlisting/contact route and confirm free service availability through **9 October 2026, 09:00 KST**. |
| English product build | Latest Python/mobile regression passed; standalone account checks recorded above. | Retain the completed scan's optional-action warning and finish remaining live checks; preserve original source text and historical results. |
| Demo video | **Corrected 186.02-second English film reviewed:** `artifacts/submission/quietpilot-demo-english.mp4`. Full decode passed; genuine English-only historical UI crops, actual English routine ending and labeled notification/later-Case edit. | Approve publication and verify the resulting accessible URL. |
| Screenshots | Selection/capture handled with the final app. | Add only privacy-reviewed, clearly labeled live-product images. |
| Architecture attachment | **Local artifact checked:** SVG + 1800 × 1180 PNG, 310,440 bytes. | Attach the generated PNG; retain SVG source in the repository. |
| Participant fields | **Everyday Agents**, **Individual**, **Korea Republic of** confirmed; authenticated Builder profile verified. | Enter the required private identifier directly into the official form after final approval. |
| Final regression | **1,919 Python / 577 mobile across 41 suites passed.** | Record only new checks justified by later changes; this document refresh runs no application tests. |

No natural real-mail routine trigger, physical-device test, iOS support, SmartThings control or SMS integration is claimed as verified. The earlier planning documents contain a broader intended product; the submission story follows the narrower current evidence.

## Review

The English submission draft contains the required 16 sections and a 116-character tagline. The judge page now records the standalone APK and verified fresh-account path while preserving the Google testing-user gate and remaining Calendar/push checks. README/runtime notes record the completed 62-message scan and its remaining optional-action preparation warning. The root MIT draft and Expo copyright notice are preserved; Space Mono retains its original metadata and [OFL 1.1 notice](../../apps/mobile/assets/fonts/LICENSE-SpaceMono.txt).

The architecture was rendered locally with the bundled Sharp library and visually inspected. Its 310,440-byte PNG is below 35 MiB. The 390-word narration and 34 retimed caption cues accompany the final 186.02-second video. A complete video/audio decode and representative frame review passed. No generated video assets were changed by this document refresh. All nine Markdown documents passed the local-link check; tagline length, supplied APK metadata, removal of the old public-target URL and consistent latest-test/completed-scan-with-warning claims were checked. Scoped diff checks passed. Application tests, model calls, builds and deployments were not rerun here.

The coordinating agent owns the remaining live checks, screenshots/video, publication approval and Devpost state updates. The source export must be regenerated only after the final freeze; this refresh performs no export. No Devpost/GitHub object, public upload, commit, push, new Calendar approval or external action was created by this document task.
