# Runtime setup and verification

Further fresh-account Calendar and remote-push checks were intentionally skipped for this release at the participant's direction; they remain unverified. The completed fresh-account Gmail checks and earlier original-account Calendar/push evidence remain valid. Google testing-user allowlisting still needs to be arranged with the participant; private contact details are not published.

Checkpoint: **15 September 2026, standalone English preview and fresh-account verification**. This guide separates the latest build/account checks from earlier real Calendar and notification evidence. The latest original-account scan completed with 62 messages and a remaining action-suggestion warning; mail collection and complete optional-action preparation are separate outcomes.

The [judge guide](submission/judge-testing.md) contains the standalone APK metadata/checksum and access instructions. The APK includes JavaScript and runs without Metro. Public download approval and the Google judge-allowlisting route remain pending: OAuth testing currently admits two allowlisted users. The [preparation status](submission/preparation-status.md) tracks the remaining handoff.

## Recorded evidence

| Surface | Observation at the cutoff | Limit |
| --- | --- | --- |
| AWS runtime | AgentCore runtime 89/`DEFAULT` 89 is `READY`; the Worker deployment reached `UPDATE_COMPLETE`. | Readiness is infrastructure state, not scan completion or external-action proof. |
| Gmail | The original-account mail UI now shows **“62 messages · September 15”** and **“Mail found. Some task suggestions are still incomplete.”** The scan completed; the earlier finding/incomplete-scan indicators are absent. | Some optional action suggestions remain unprepared. This is not zero-warning or mailbox-wide accuracy proof. Source validators and retry limits are unchanged. |
| Fresh account | Signup/sign-in and Gmail consent/return succeeded. The initial list did not show the original account's work. Session and connection survived replacement with an APK signed by the same dedicated signer; notification ON persisted. | Fresh-account Calendar consent and a new remote-push check are still pending. This observed separation is not a comprehensive multi-tenant security audit. |
| Standalone APK | ARM64 version 0.1.0/code 1, min API 24/target 36, 46,371,800 bytes, non-debuggable, dedicated signer, 4,477,512-byte embedded JavaScript bundle. The fresh-account path ran without Metro. | Verified on a fresh Android emulator. Physical devices, other ABIs and iOS are not verified; public download is pending. Full SHA-256 is in the judge guide. |
| Mail review | A `NO_ACTION` review completed and persisted in native history. | A review outcome, not an external action. |
| Calendar | One earlier mail-derived private deadline event passed preparation, exact `ONCE` approval, Google creation and server readback, appeared as `COMPLETED`, and reopened from native history. | One 15-minute event, no invitees or reminders. It is earlier-account evidence; no new external event was created during the final checks. |
| Mail routines | A real `devpost.com` `DEADLINE` `PREPARE_ONLY` proposal was explicitly activated and remained `ACTIVE` after native/server-list refresh. | A naturally arriving matching mail has not yet been observed creating a fresh routine Case. Each resulting Calendar action still needs its own exact approval. |
| Background Gmail entry | The authenticated Pub/Sub route/configuration was confirmed in a deployment-matched template. Both daily watch renewal and six-hour recovery schedules were confirmed `ENABLED`. | Configuration does not establish observed natural-mail delivery through the entire routine path. |
| Notifications | Actual server → Expo → FCM → Android receipt/tap and OFF/ON persistence were verified earlier. A new English remote notification on the original connected device opened the owned Case and displayed its date/time question; the QA Case was then `STOPPED`. | Fresh-account notification ON persisted, but its new remote-push test and Calendar warning/consent check remain pending. No new external event was created. |
| Local regression | The latest full checks passed **1,919 Python tests in 41.77 seconds** and **577 mobile tests in 41 suites in 5.19 seconds**. The earlier SDK alignment passed Doctor 21/21. | Tests, SDK health and live-service observations are separate evidence. |

A separate integration test used the actual agent SDK, Moto and scripted Google/Expo responses: an activated PREPARE_ONLY routine matched different new mail, prepared a fresh Calendar proposal, waited for its own ONCE approval and produced one generic push request. Reprocessing added no Case, plan or push. This is local integration/duplicate-suppression proof; the natural real-mail trigger remains unobserved.

Earlier local records `native-remote-push-retest.json` and `native-notification-toggle-proof.json` retain remote-delivery/navigation and setting-persistence evidence. They are distinct from the new account and from the one-event Calendar/NO_ACTION history checks. Private evidence records and account identifiers are excluded from the public source package.

All native observations above used Android emulators. The earlier Calendar app link reached a Google device sign-in screen; the verified external result is Google's server readback consumed by QuietPilot, not an independently inspected Calendar-app display.

## Local prerequisites

Run commands from the repository root. Use npm with the root `package-lock.json` for this workflow.

- Node.js `>=22.13 <23` and npm.
- Python `>=3.12 <3.13` and uv.
- For Android: Java 17, Android SDK/platform tools and an emulator or connected device. Use the Expo development build, not Expo Go, for the native path.
- For LIVE verification: access to the existing authorized AWS/Google deployment and a Cognito user in its configured pool.

On macOS:

```sh
source scripts/project-env.sh
npm ci
uv sync --all-packages --frozen
```

`project-env.sh` selects installed toolchain paths. It expects Java 17 to exist and defaults the Android SDK to the user's macOS SDK location; it does not install dependencies, authenticate to AWS or deploy anything. On other hosts, configure the same tool versions and SDK paths directly.

### Configure the mobile client

Set these values in the ignored `apps/mobile/.env.local` file using outputs from the intended deployment:

| Variable | Required value |
| --- | --- |
| `EXPO_PUBLIC_COGNITO_USER_POOL_ID` | That deployment's Cognito user pool ID. |
| `EXPO_PUBLIC_COGNITO_USER_POOL_CLIENT_ID` | Its public mobile user pool client ID. |
| `EXPO_PUBLIC_CONTROL_API_URL` | Its HTTPS control API base URL, without an added `/v1` suffix; the clients append their endpoint paths. |

These variables are bundled into the app. They must not contain passwords, AWS credentials, Google client secrets or OAuth tokens. Restart Metro after changing them. Authentication is implemented by [the Cognito adapter](../apps/mobile/src/auth/cognitoAuth.ts); the product, mail and connection clients use the same control API base URL.

Use a real Cognito account for the selected deployment. Signup confirmation, password entry, MFA and Google consent remain user-controlled. There is no shared or prefilled test login in the current app.

The hosted Google OAuth project is in testing mode with two allowlisted users. QuietPilot signup does not automatically grant Google test access. Judges must arrange allowlisting before attempting consent; the final submission must supply a concrete contact/access route and keep free access available through 9 October 2026, 09:00 KST.

### Existing cloud and Google prerequisites

The live path depends on Cognito, the authenticated control API, DynamoDB state, SQS/worker processing, an AgentCore runtime with model access, and the server-side Google credential provider. The mobile app does not call AgentCore directly. Gmail ingestion additionally depends on its configured Google push/watch and recovery resources.

Google must have Gmail and Calendar APIs enabled, a valid OAuth client/provider and matching callback configuration. The current source requests `gmail.readonly` for mail and `calendar.events.owned` for Calendar work. Calendar connection is a separate capability: a connected Gmail account alone is insufficient. The native return URI is `quietpilot://oauth-return`; the Google provider callback and server return URLs must match the approved deployment. Keep client secrets and provider tokens in server-managed credential storage.

Android remote notifications additionally require the configured EAS project ID, matching Firebase Android configuration and assigned FCM V1 credentials. The notification project is separate from the existing Gmail project. A fresh clone must provide the intentionally ignored `apps/mobile/google-services.json` from the intended Firebase Android registration; its Android package must match the app's configured package, and the Firebase/EAS projects must match the intended notification deployment. Keep service-account private keys server-side and never commit or include them or push tokens in examples/evidence. Install a development APK containing the native notification module; a Metro reload alone cannot add that module to an old APK.

The existing development deployment was authorized and has been exercised. This repository's local install and validation commands do not provision a fresh AWS/Google environment. For a deployment change, use the established scoped deployment process: verify the target and authorized spend, inspect the exact candidate and change set, preserve the existing stateful resources and OAuth configuration, and check runtime readiness plus the affected native behavior after deployment. Do not treat older dated foundation manifests as a current one-command installation recipe.

### Start the Android client

Build/install the development client when needed:

```sh
source scripts/project-env.sh
npm run android -w mobile
```

For an already installed client, start Metro with the command used in the verified development session:

```sh
source scripts/project-env.sh
EXPO_NO_TELEMETRY=1 npm run start -w mobile -- --dev-client --localhost --port 8081 --android
```

Use the actual selected device/emulator and Metro port. If its localhost transport needs an explicit ADB mapping, `adb reverse tcp:8081 tcp:8081` maps that port. Confirm the app shows the current QuietPilot screen, not merely the Expo launcher or an old cached bundle. The debug APK is produced at `apps/mobile/android/app/build/outputs/apk/debug/app-debug.apk` after a successful Android debug build.

For a browser frontend session:

```sh
source scripts/project-env.sh
npm run web -w mobile
```

The browser session is useful for frontend checks. It is not a replacement for the native OAuth/deep-link verification recorded here.

## LIVE and SCENARIO

The [workspace provider](../apps/mobile/src/workspace/WorkspaceProvider.tsx) uses LIVE unless a development-only scenario is explicitly active. A fresh fixture snapshot is also marked LIVE and contains no preloaded connector work. LIVE reads the authenticated user's server state; missing configuration or unavailable services should be treated as an error or empty state, not filled with fixture data.

Development builds expose a scenario screen at `/prototype-scenarios`. Its explicitly selected examples cover empty, single-candidate, Google, SmartThings, SMS and full fixture states. Select the LIVE/server option to return to LIVE. A saved scenario can persist between app sessions, so check the current mode before collecting evidence.

Scenario Cases and actions simulate workspace behavior. They do not prove that Google or SmartThings is connected, that a device changed state, or that an OS notification was delivered. The scenario selector does not replace Cognito authentication with an offline login.

## Local verification

Choose checks for the affected code; these commands do not deploy resources or send real mail/Calendar actions:

```sh
source scripts/project-env.sh
npm run mobile:verify
npm test
```

`mobile:verify` covers mobile format, lint, types, Jest and Expo Doctor. `npm test` covers workspace validation, generated contracts, contract types, all Python packages, infrastructure types/tests and mobile Jest tests. Package installation and Expo Doctor may need network access.

For focused checks or a local infrastructure synthesis:

```sh
source scripts/project-env.sh
npm run test:python
npm run check:contracts
npm run infra:verify
```

`test:python` explicitly includes the existing ephemeral Moto dependencies through uv; the latest full run passed 1,919 Python tests. The infrastructure synth uses `--no-lookups` and emits local CloudFormation assets. Local agent tests inject deterministic models; integration tests use mocked services, including Moto and HTTP fixtures. A passing local test is separate from a real AgentCore invocation, a live Google result or a native consumer check. SDK dependency validation and the final APK's native checks are recorded separately above.

## Verify the real mail-to-Calendar path

Use the intended signed-in account and its own source data. This procedure can create an actual Calendar event only at the explicit approval step; a verification attempt is not a reason to approve a new or changed event automatically.

1. **Confirm LIVE and connections.** Sign in, leave any saved scenario, and open connected services. Verify Gmail and Google Calendar show connected for the intended account. Complete any required sign-in/consent yourself. A connection badge does not yet prove mail processing or external execution.
2. **Read mail results.** Open the mail view, check the saved interests and use the manual rescan control if a fresh scan is needed. Record scan state, completion, displayed count and warnings separately. An `ERROR` scan may retain verified partial results; those rows do not mean the whole scan completed. A completed list can still have incomplete task recommendations.
3. **Compare selected results with source.** Review important/actionable mail, inclusion and exclusion rules, order, summaries and date/time/zone facts. Keep private source text out of logs and reports. A correct topic tag alone does not establish importance or factual accuracy.
4. **Prepare a suitable Case.** Use a real mail-derived proposal whose action is supported by the connected capabilities. Verify the proposed Calendar title, date, start/end, timezone, target calendar and effects against the source. A 15-minute deadline marker represents a deadline; it must not be described as the duration of the underlying activity. Preparation and an approval screen are not execution proof.
5. **Approve exactly once when intended.** Review the saved plan and use its one-time approval control only for the event you intend to create. That `ONCE` decision authorizes this plan, not future mail, invitations, reminders or a recurring rule. If the result is uncertain, inspect the existing Case before considering a retry.
6. **Verify completion and persistence.** Wait for server execution and Google readback, then confirm the native Case reaches `COMPLETED` with its verified-result message. Reopen the same Case from the completed/history list and confirm the result remains. `QUEUED`, a successful model call, or an empty error log does not prove completion. Do not reapprove solely to repair a display or network error.

Record the source mode, runtime/build, timestamps, state transitions, count, warning codes, approval scope, verified-result status and history reopen outcome. Keep local, deployed and live-service observations distinct. Redact account/resource identifiers and private content from any screenshots or shared evidence.

## Routine and notification evidence boundaries

The real `devpost.com` deadline routine is explicitly activated, and its `ACTIVE` state survived server-list refresh. Pub/Sub authentication and enabled maintenance schedules are configured. A naturally arriving matching mail producing a fresh Case/Calendar proposal remains unobserved. That proposal must still wait for its own `ONCE` approval; activation never grants recurring Calendar execution permission.

Actual server-to-device receipt and a HOME notification tap opening the owned LIVE Case are verified. The latest English remote notification on the original connected device also opened its owned Case/date-time question, and that QA Case was stopped afterward. OFF persisted on server refresh, and re-registration ON remained enabled after resume. On the fresh account, ON persisted through APK replacement; new remote-push verification is still pending. An Expo ticket alone is not device-receipt proof. Foreground refresh, duplicate suppression, OS permission revocation and logout cleanup retain their separate local/live evidence boundaries.

## Source and logging safeguards

Scanning uses a bounded 4,000-character mail source extraction. Case preparation refetches the selected source with a separate 16,000-character limit. That larger limit does not waive validation: missing, snippet-only or `source_truncated=true` evidence is still a preparation stop condition. Obtain sufficient bounded source or report the limitation; do not bypass the check.

The implementation removes recognized credential values from extracted mail and protects generated display text while preserving relevant source facts. Source/date/timezone validation remains required before plan acceptance. This is a defined protection boundary, not a claim that every possible secret format is detected.

Preserve the current runtime privacy settings `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_unredacted_attributes=` and `DISABLE_ADOT_OBSERVABILITY=true`. Use content-free operational diagnostics such as fixed validator codes, counts and states. Do not print raw mail, model prompts/results, authorization headers or provider tokens to troubleshoot failures.

## Known limits at the cutoff

- The latest scan completed with 62 messages and a warning that some task suggestions remain incomplete. Optional-action preparation, mailbox-wide recommendation quality and semantic grouping are not claimed complete.
- Failed-page resume and duplicate-execution protections had local SDK/Moto/queue coverage; the later successful scan started its counts again, and repeated live Calendar writes were not used as proof of idempotency.
- Live routine activation, the SDK 57.0.22 development APK replacement with login retention, remote Android notification receipt, owned LIVE Case navigation, and notification toggle persistence are verified. A new matching real-mail routine trigger remains unobserved.
- SmartThings control and SMS integration have no completed live proof. No new Calendar approval or write was performed during routine/notification verification.
- Physical-device QA and a repeatable full-product demonstration remain distinct from the emulator flow above.
- The standalone English APK and fresh-account signup/sign-in/Gmail consent/persistence checks passed. Fresh-account Calendar consent, a new remote-push check and public download remain pending. The completed 62-message scan still has an optional-action preparation warning. Existing source-derived text and historical results are preserved; English captions may translate their meaning without rewriting them.
