# Testing QuietPilot

Further fresh-account Calendar and remote-push checks were intentionally skipped for this release at the participant's direction; they remain unverified. The completed fresh-account Gmail checks and earlier original-account Calendar/push evidence remain valid. Arrange Google testing-user allowlisting through the submission contact; private contact details are not published.

**Standalone preview checked and publicly downloadable. Arrange Google access through the submission contact.** Fresh sign-up/sign-in and Gmail consent/return were verified on a separate account. Fresh-account Calendar and remote-push checks were intentionally skipped and remain unverified.

QuietPilot's Android preview connects to hosted AWS services. It includes its JavaScript bundle and does not need an AWS account, developer credentials or a local Metro server. The live Calendar action and remote-push evidence described below came from earlier account checks and is distinct from the fresh-account walkthrough.

## Access handoff

| Item | Status for the final packet |
| --- | --- |
| Android APK download | [Download the standalone APK](https://github.com/kcyoow/quietpilot-agents-for-humans/releases/download/v0.1.0-hackathon/quietpilot-preview.apk). Verify the SHA-256 below. |
| Version | **0.1.0**, version code **1**; non-debuggable, dedicated preview signer. |
| Size | **46,371,800 bytes**. |
| SHA-256 | `ab0f8036150910ca61a7bc6af03791b04dbd5f5e08e17acb95fb6758ea281fa7` |
| Supported device | **ARM64 Android API 24+**, target API 36. A fresh Android emulator was checked; physical devices, other ABIs and iOS are not verified. |
| Embedded JavaScript | **4,477,512 bytes**, included in the APK. The standalone app passed fresh-account checks without Metro. |
| QuietPilot account | Fresh sign-up/sign-in was verified. The initial list did not show the original account's work. No shared personal login is supplied. |
| Google account access | Gmail consent and return were verified for the fresh account. **Google OAuth remains in testing mode with two allowlisted users; judges must arrange allowlisting before Google consent.** Further fresh-account Calendar checks were intentionally skipped and remain unverified. |
| Availability | Final operator confirmation pending. Free judge access must continue through **9 October 2026, 09:00 KST**. |

Use the submission contact to request Google test-user allowlisting before consent. Do not publish or share the developer's personal credentials. A normal QuietPilot signup does not automatically add a Google account to the OAuth test-user list.

Use the APK matching the checksum above. Older development APKs require Metro and are not the standalone judge build. Do not substitute a scenario fixture for a failed LIVE connection.

## Suggested walkthrough

Use a Google account and messages you are comfortable connecting to this prototype. Mail contents are processed by the hosted agent to prepare suggestions. Gmail reading and Calendar access are separate connections; the app should show what is being requested before consent.

1. **Arrange Google test access, then install and sign in.** Complete the allowlisting arrangement above. Install the checksum-matching APK on a compatible Android device/emulator, open it without a developer server, and complete QuietPilot sign-up/sign-in. Confirm LIVE mode.
2. **Connect the supported services.** Connect Gmail for read-only mail access. Connect Calendar separately if you want to test event preparation/execution. Complete account selection and consent yourself.
3. **Review useful mail.** Set a simple interest and scan a mailbox containing a clear, explicit future deadline or appointment. A message should state its date, year, time and timezone. The scan may take time; read its progress and warning state. If no actionable source matches, an empty result is valid.
4. **Prepare a Case.** Choose a suitable result and inspect its source, goal and draft. A recommendation is preparation, not permission to create an event. Ambiguous or incomplete source can lead to a request for input or no action.
5. **Optionally approve one real change.** Only if you intend to create the displayed private Calendar event, review its exact title, date/time, target and effects, then choose the one-time approval. Do not approve just to advance a demo. Wait for server verification; reopen the same Case from history. Do not reapprove an uncertain result to repair a display problem.
6. **Inspect reusable preparation.** From a completed, verified Calendar Case, review the proposed sender-domain deadline/appointment routine. It starts inactive. Explicit activation allows new matching mail to prepare future Cases; each future Calendar action still waits for its own approval. Pause the routine to stop future triggers.
7. **Test attention settings.** Opt into Android notifications if desired. They contain a generic prompt to review a Case, not mail content. A tap should retrieve the current owned Case. A routine activation or ordinary mail result alone is not expected to send a push. Turn notifications off and refresh to check the saved setting.

For a short evaluation, the recorded video can show the previously completed real Calendar result and observed remote notification flow. Those recordings supplement the working test build; they do not replace access to it.

## What the existing evidence establishes

The fresh account passed sign-up/sign-in and Gmail consent/return. Its initial list did not expose the original account's work. Session and connection state survived replacement with an APK signed by the same dedicated signer; notification ON also persisted. Fresh-account Calendar and remote-push checks were intentionally skipped and remain unverified.

The latest original-account scan completed with 62 messages and the warning “Some task suggestions are still incomplete.” Mail collection is complete; some optional action suggestions remain unprepared. One earlier Calendar Case received exact ONCE approval, created one private deadline event, passed Google readback, reached COMPLETED and reopened from history. A NO_ACTION review persisted. A real routine remained ACTIVE after server refresh, and a generic server-to-Android notification opened the exact owned LIVE Case. Those earlier results are not claimed as new actions on the fresh account.

Runtime 89/DEFAULT 89 is READY. The completed scan retains its optional-action warning; source guards and retry limits are unchanged. No mailbox-wide accuracy claim is made. A new English remote notification on the original connected device opened the owned Case/date-time question; that QA Case was stopped. The latest full checks passed 1,919 Python tests and 577 mobile tests in 41 suites. No new external Calendar event was created during these final checks.

New matching-mail routine preparation and duplicate suppression passed local tests using the actual agent SDK, Moto and scripted Google/Expo responses. A naturally arriving matching real email has not yet been observed exercising that full routine path. Background configuration is enabled; the final handoff must not turn that configuration into a claim of observed delivery.

## Developer fallback

For source inspection and local tests, follow the [repository README](../../README.md) and [runtime setup guide](../runtime-verification.md). A developer build requires the documented public deployment configuration and the ignored Firebase Android configuration file. Provisioning a separate AWS/Google environment is a distinct setup process; no personal keys or tokens are supplied here.

Development SCENARIO fixtures are explicitly labeled and useful for checking UI/error states. They do not prove mail ingestion, model inference, external execution or push delivery.
