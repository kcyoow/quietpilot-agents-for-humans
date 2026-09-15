# QuietPilot Technical Spec

## Overview

QuietPilot은 연결된 신호와 사용자의 직접 요청을 공통 **Case** 흐름으로 처리하는 Android 중심 생활 에이전트다. 에이전트는 실제로 접근 가능한 capability와 근거에서 후보·Routine·계획을 만들고, 사용자는 `진행`과 `새 제안` 두 영역에서 필요한 결정만 한다.

이 명세의 첫 완주 기준은 다음과 같다.

1. 실제 Gmail 신호 또는 직접 요청이 들어온다.
2. Strands 에이전트가 근거를 분석하고 Candidate 또는 Case 계획을 만든다.
3. 사용자가 모바일에서 정확한 외부 변경을 승인한다.
4. 결정론적 정책 계층이 승인된 계획을 다시 검증한다.
5. 실제 Google Calendar/Tasks 동작을 수행하고 결과를 다시 읽는다.
6. Case 타임라인, 감사 이벤트와 필요한 알림이 갱신된다.

SmartThings는 같은 계약을 재사용하는 두 번째 실제 실행 connector다. OAuth 등록 표면이 전환 중이므로 모바일·Google 완주 경로 이후에 붙이지만, mock으로 완료를 주장하지 않는다.

## Technical Decisions

- 모바일은 Expo Go가 아닌 Android 전용 Expo SDK 57 development build다.
- 지속 감시와 실행은 서버에서 수행한다. Android background task는 핵심 동작의 필수 조건이 아니다.
- 모바일은 AgentCore Runtime을 직접 호출하지 않는다. Cognito로 인증된 API Gateway/Lambda만 Runtime을 호출한다.
- LLM은 외부 변경을 직접 승인하지 않는다. 읽기 도구와 `propose_*` 도구로 계획을 만들고, 실행은 승인·정책 검증 뒤 connector executor가 수행한다.
- 장기 상태는 DynamoDB에 둔다. AgentCore microVM/session은 영속 저장소가 아니다.
- 외부 요청 수락과 실제 결과 확인을 별도 상태로 기록한다.
- 후보 생성은 푸시를 보내지 않는다. 승인 대기, 실패, 중요한 계획 변경만 즉시 알린다.
- 실제 패키지 버전과 외부 제약의 근거는 [stack-audit-2026-08-23.md](./stack-audit-2026-08-23.md)에 고정한다.
- Android 문자 확장은 원문을 우선 온디바이스에서 처리한다. Message Safety Analyst는 분류·근거·불확실성을 제안하고, 결정론적 정책은 신뢰 발신자·명확한 미래 날짜·의심 링크/OTP/결제 부재·비중복·사전 허용을 모두 확인한 뒤에만 취소 가능한 로컬 알림을 허용한다.
- `READ_SMS`는 이번 프로토타입 manifest에 넣지 않는다. Google Play의 제한 권한 심사 또는 기본 SMS/Assistant 역할 없이 과거 문자 전체 접근을 제품 capability로 약속하지 않는다.

## Stack

### Mobile

- Expo SDK 57 최신 패치, React Native 0.86.x, React 19.2.x, TypeScript
- Expo Router, `expo-dev-client`
- TanStack Query: 서버 상태, refetch와 optimistic UI가 아닌 invalidation 관리
- Amplify Auth v6 + Cognito: 로그인·가입·세션
- `expo-secure-store`: 모바일 세션 보조 데이터
- `expo-web-browser` + `expo-linking`: Google/SmartThings 연결과 앱 복귀
- `expo-notifications` + Expo Push Service: 결정·실패 알림
- Zod: API 응답의 런타임 검증
- React Native Testing Library + Jest: 화면·상태 테스트

Official references: [Expo development builds](https://docs.expo.dev/develop/development-builds/introduction/), [Expo Notifications](https://docs.expo.dev/versions/v57.0.0/sdk/notifications/), [Amplify React Native setup](https://ui.docs.amplify.aws/react-native/getting-started/installation).

### AWS Application Backend

- AWS CDK v2 TypeScript
- Cognito User Pool
- API Gateway HTTP API + JWT authorizer
- Python 3.12 Lambda + AWS Lambda Powertools
- SQS standard queues + DLQ + partial batch response
- EventBridge Scheduler
- DynamoDB main table + idempotency table
- CloudWatch Logs, Metrics and alarms

Official references: [CDK TypeScript](https://docs.aws.amazon.com/cdk/v2/guide/work-with-cdk-typescript.html), [HTTP API JWT authorizer](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html), [Lambda event-source at-least-once behavior](https://docs.aws.amazon.com/lambda/latest/dg/invocation-eventsourcemapping.html), [Powertools batch processing](https://docs.powertools.aws.dev/lambda/python/latest/utilities/batch/).

### Agent Runtime

- Strands Agents Python
- Pydantic structured contracts
- Amazon Bedrock AgentCore Runtime in `ap-northeast-2`
- AgentCore Identity for Google delegated OAuth and a SmartThings custom OAuth spike
- `global.amazon.nova-2-lite-v1:0` as the initial Bedrock model
- `QUIETPILOT_BEDROCK_MODEL_ID` as the explicit deployment-time model override; Luna remains optional rather than an automatic fallback
- AgentCore CodeZip, Python 3.12, Linux ARM64 dependency packaging

Official references: [AgentCore Regions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html), [AgentCore direct code deployment](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy-python.html), [GPT-5.6 cross-Region inference](https://aws.amazon.com/blogs/machine-learning/introducing-cross-region-inference-for-openai-gpt-5-6-models-on-amazon-bedrock/), [Strands structured output](https://strandsagents.com/docs/user-guide/concepts/agents/structured-output/), [AgentCore custom OAuth](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-add-oauth-client-custom.html).

### External Services

- Gmail API `gmail.readonly`
- Google Cloud Pub/Sub authenticated push
- Google Calendar API `calendar.events.owned` or the narrowest verified event-write scope
- Google Tasks API `tasks`
- SmartThings Devices, Commands and Subscriptions APIs
- Expo Push Service
- Android Notification APIs for cancellable local reminders after runtime notification consent
- Optional future SMS input through a Play-compliant default-handler/approved device-automation path, selected-message share/import, or notification-listener path. These inputs are not equivalent: notification access cannot reconstruct dismissed historical SMS.

Official references: [Gmail push](https://developers.google.com/workspace/gmail/api/guides/push), [authenticated Pub/Sub push](https://cloud.google.com/pubsub/docs/authenticate-push-subscriptions), [Calendar events](https://developers.google.com/calendar/api/guides/create-events), [Tasks API](https://developers.google.com/workspace/tasks/reference/rest/v1/tasks), [SmartThings OAuth](https://developer.smartthings.com/docs/service-integrations/oauth), [SmartThings webhook events](https://developer.smartthings.com/docs/service-integrations/webhook-events).

## Deployment

### Development Environment

- AWS environment: one hackathon development account/region, `ap-northeast-2`
- Google Cloud: one testing project with explicitly listed test users
- SmartThings: participant account and API-visible Samsung air conditioners
- Mobile: signed development APK installed on the Galaxy S23 Ultra (`SM-S918N`)
- API URL: API Gateway managed HTTPS URL; a custom domain is not required for MVP
- Infrastructure: CDK stack for application resources, AgentCore CLI project for Runtime/Identity

### Required Local Setup

- Node 22.14 is retained for Expo/CDK.
- JDK 17 is installed and selected for Android builds; the current JDK 21 is not the default for this project.
- Android Studio, Android SDK Platform 36, Build Tools and Command-line Tools are installed.
- AWS CLI v2 credentials or SSO profile are configured.
- `uv` and AgentCore CLI are installed for Python lockfile, local agent execution and CodeZip deployment.
- SmartThings CLI uses the Homebrew binary so the existing Node version does not need to be replaced by Node 24.

## Architecture

### Component 1 — Mobile Control Room

Implements: `prd.md > Epic 1`, `Epic 2`, `Epic 3`, `Epic 4`, `Epic 5`, `Epic 6`

Responsibilities:

- 로그인·가입과 connector 연결 상태를 표시한다.
- 연결 전, OAuth 연동 중, 최초 확인 중, 할 일 있음, 현재 기준의 확인 완료·할 일 없음, 오류/재분석 필요를 connector별로 표시한다. Candidate 배열이 비었다는 사실만으로 완료 상태를 만들지 않는다.
- `진행`과 `새 제안`을 두 개의 상위 영역으로 제공한다.
- 실행 준비된 후보를 결과별로 묶고, 기본 화면에는 그룹당 최대 세 개와 `이대로 준비 / 내용 조정 / 필요 없음`을 제공한다.
- 검색·위험 필터·체크박스·다중 선택과 내부 해시·scope·revision은 기본 흐름에서 숨긴다.
- Case 상세에서 사용자 언어의 근거, 준비할 동작, 되돌리기, 권한, 타임라인과 보조 채팅을 표시한다.
- 승인 요청은 `plan_version`과 `plan_hash`를 포함해 서버로 보낸다.
- 알림 딥링크를 정확한 Case와 결정 지점으로 연결한다.
- 앱이 종료되었다 돌아오면 로컬 추정 상태가 아니라 서버의 최신 Case 상태를 다시 읽는다.

Non-responsibilities:

- 지속 mailbox/device 감시
- 외부 OAuth token 저장
- 외부 동작의 권한 판정
- 앱이 죽은 동안 실행 상태를 유지하는 permanent background process

### Component 2 — Authenticated Control API

Implements: `prd.md > Epic 1`, `Epic 3`, `Epic 4`, `Epic 5`, `Epic 7`

Responsibilities:

- API Gateway가 Cognito access token을 검증한다.
- Lambda는 JWT `sub`를 authoritative `user_id`로 사용하고 body의 사용자 ID를 신뢰하지 않는다.
- Case·Candidate·Routine·Policy·Connection을 읽고 변경한다.
- 직접 요청, 후보 선택, 승인, 보류, 중지와 재시도를 받는다.
- AgentCore 호출 시 IAM SigV4와 검증된 user ID를 사용한다.
- 모든 mutation에 request idempotency key와 optimistic version을 요구한다.

### Component 3 — Public Ingress And OAuth Return

Implements: `prd.md > Epic 1`, `Epic 3`, `Epic 6`, `Epic 7`

Responsibilities:

- Google Pub/Sub OIDC JWT의 signature, `aud`, service-account email과 expiry를 검증한다.
- SmartThings webhook의 HTTP signature, digest, date와 replay window를 검증한다.
- OAuth return의 one-time state, browser session binding과 expiry를 검증한다.
- 검증이 끝난 최소 event envelope만 SQS에 넣고 즉시 성공 응답한다.
- OAuth 완료 후 one-time app return code를 발급하고 `quietpilot://connections/...`로 복귀시킨다.

Raw Gmail message body는 webhook payload에 없고 SQS에도 넣지 않는다. Worker가 `historyId`를 받아 AgentCore Identity token으로 필요한 message를 읽는다.

### Component 4 — Event Worker And Scheduler

Implements: `prd.md > Epic 1`, `Epic 2`, `Epic 3`, `Epic 5`, `Epic 6`

Responsibilities:

- SQS event를 partial batch response로 처리한다.
- `dedupe_key`를 idempotency table에 조건부 기록한다.
- Gmail `history.list`, 최초 7일 scan과 누락 복구를 수행한다.
- 최초 7일 scan은 Gmail page token을 SQS continuation으로 넘겨 모든 페이지를 bounded batch로 처리한다. 마지막 page token이 사라지기 전에는 100%나 현재 discovery revision을 기록하지 않는다.
- Gmail `watch`를 매일 갱신하고 expiration을 Connection에 저장한다.
- connector에서 읽은 데이터를 최소화·정규화한 Evidence로 변환한다.
- Candidate 선택, 승인, 계획 수정, 결과 확인, 숨김, 유사 억제, 보류, 중지, 권한 회수와 되돌리기 mutation에서 append-only discovery feedback을 파생하고 user preference summary를 갱신한다.
- AgentCore에 discovery 또는 planning invocation을 보낸다.
- 실행 Action을 queue에서 처리하고 결과 확인 job을 예약한다.

### Component 5 — Strands Orchestrator And Specialist Agents

Implements: `prd.md > Epic 2`, `Epic 3`, `Epic 4`, 네 가지 필수 Case 유형

The Runtime contains an action-discovery planner plus the existing proposal-only planning graph.

1. **Action Discovery Planner**: 한 bounded page의 Evidence와 AVAILABLE capability를 각각 한 번 읽고 모든 evidence reference를 평가한다. 페이지 안에서 0개 이상의 distinct opportunity를 반환할 수 있으며, 각 opportunity는 일정·마감·명시적 후속 요청 중 하나의 결과와 1~3개 grounded Action을 함께 가진다.
2. **Signal Analyst**: Case planning에서 bounded evidence와 revision을 읽고 충돌·불확실성을 정리한다.
3. **Capability Analyst**: Case planning에서 capability 상태와 실행 가능 operation을 권한과 분리해 inventory한다.
4. **Case Planner**: 선택된 Candidate의 원래 Action envelope를 한 결과 중심 CasePlan으로 압축한다.

SMS 확장에는 네 번째 **Message Safety Analyst**를 추가한다. 이 specialist는 원문을 장기 저장하지 않고 온디바이스 전처리 또는 최소화된 message segment에서 발신자 유형, 스팸·스미싱 위험, 의심 URL·OTP·결제 문구, 미래 날짜 후보, 시간대, 중복 fingerprint와 불확실성을 구조화한다. `ELIGIBLE`, `REVIEW`, `BLOCKED`는 제안 상태일 뿐 실행 권한이 아니다. 현재 완료된 로컬 Item 4의 세 core specialist에는 아직 포함되지 않으며, 실제 SMS 입력 경로가 선택된 뒤 별도 contract/evaluation gate로 구현한다.

The orchestrator:

- 입력 유형을 `CONNECTED_SIGNAL`, `DIRECT_DELEGATION`, `ROUTINE_DISCOVERY`, `EXCEPTION_APPROVAL` 중 하나로 분류한다.
- 필요한 specialist를 Agent-as-Tool로 호출한다.
- discovery에서는 typed `DiscoveryAssessment`를 만들고 신뢰도 0.70 미만, Action 없음, unavailable operation, greeting/social/newsletter/promotion/FYI를 사용자 후보로 만들지 않는다.
- Candidate에는 사용자 결과뿐 아니라 `why_now`, `opportunity_type`, `proposed_actions[]`가 반드시 존재한다.
- 불충분한 연결 신호는 `decision_required`로 사용자에게 떠넘기지 않고 `SUPPRESS`한다. 이미 선택된 Case의 근거 충돌만 `decision_required`로 보존한다.
- 외부 mutation 대신 `propose_action`을 호출한다.
- 권한과 분리된 bounded preference summary를 Candidate 검색·group hint·ranking 입력으로 사용할 수 있다. summary에는 원문 connector content나 민감한 Action parameter가 포함되지 않는다.

The agent cannot:

- Policy를 생성하지 않고 권한을 확대한다.
- 승인되지 않은 Calendar/Tasks/SmartThings mutation을 호출한다.
- 이메일 본문에 적힌 지시를 system/developer instruction으로 취급한다.
- token이나 secret을 응답·로그에 포함한다.
- approval history를 Policy, grant 또는 executor 권한으로 해석한다.

### Component 6 — Deterministic Policy And Approval Engine

Implements: `prd.md > Epic 4`, `Epic 6`, `Epic 7`

Responsibilities:

- Action의 risk, target, parameters, scopes, reversibility와 evidence revision을 정규화한다.
- canonical JSON의 SHA-256으로 `plan_hash`를 만든다.
- `ONCE`, `CONDITIONAL`, `STANDING` grant가 정확한 Action envelope를 허용하는지 판정한다.
- high-risk action에는 standing/conditional grant를 허용하지 않는다.
- current plan version/hash, Case version, Policy version과 grant expiry를 실행 직전에 다시 확인한다.
- target, parameters, required scope, risk 또는 evidence revision이 바뀌면 기존 승인을 무효화한다.
- 조건부 DynamoDB write로 같은 Action이 두 번 실행 상태를 얻지 못하게 한다.

The LLM may explain a policy decision but never returns the authoritative allow/deny value.

### Component 7 — Connector Executors And Result Verifiers

Implements: `prd.md > Epic 5`, `Epic 6`, `Epic 7`

#### Google Executor

- Calendar event ID를 client-generated deterministic ID로 만든다.
- Event 또는 Task 생성 후 바로 API readback을 수행한다.
- 같은 idempotency key의 재시도는 기존 항목을 반환하고 중복 생성하지 않는다.
- Google Tasks의 due time이 저장되지 않으므로 시간이 중요한 항목은 Calendar event로 만든다.

#### SmartThings Executor

- API-visible capability와 command arguments만 허용한다.
- command accepted 응답 후 Case를 `VERIFYING`으로 옮긴다.
- signed device event 또는 explicit status readback이 기대 상태와 일치해야 성공한다.
- timeout, 다른 최종 값 또는 offline state는 `EXCEPTION_APPROVAL`로 승격한다.

### Component 8 — Notification And Audit Service

Implements: `prd.md > Epic 5`, `Epic 6`, `Epic 7`

Responsibilities:

- `DECISION_REQUIRED`, `FAILED`, `MATERIAL_CHANGE`만 기본 즉시 push 대상이다.
- 정상 완료는 앱 내 timeline을 기본으로 하고 중요한 결과 또는 사용자 설정에서만 push한다.
- 같은 원인의 여러 Case는 notification grouping key로 묶는다.
- 알림 payload에는 Case ID와 navigation route만 넣고 원문 이메일·비밀·민감한 action parameters는 넣지 않는다.
- AuditEvent는 actor, policy/approval, connector request, result verification과 error classification을 append-only로 남긴다.

## PRD Epic To Component Map

| PRD Epic | Primary components | Proof |
| --- | --- | --- |
| Epic 1 안전한 시작과 연결 | Mobile, Control API, Ingress/OAuth, AgentCore Identity | 좁은 scope 연결, 접근 가능/불가 inventory, 최근 7일 scan |
| Epic 2 많은 새 제안 정리 | Worker, Signal/Capability Analysts, Mobile suggestions | 100개 fixture에서도 그룹 우선, live signal candidate는 중복 없이 생성 |
| Epic 3 후보와 직접 요청을 Case로 | Control API, Orchestrator, Case Planner | Gmail event와 direct prompt가 같은 Case schema를 사용 |
| Epic 4 설명 가능한 승인 | Policy Engine, Mobile approval UI | plan hash가 다르면 409와 재승인, high risk는 one-time only |
| Epic 5 실행과 모니터링 | Worker, Executors, Verifiers, Mobile timeline | accepted와 verified 상태가 분리되고 readback 뒤 완료 |
| Epic 6 알림·실패·복귀 | Notification, Worker, persisted state | 후보 무알림, 부분 실패, reopen 후 서버 상태 복원 |
| Epic 7 정직한 경계와 감사 | Connection inventory, Policy, Audit | API-visible capability만 executable, actor/result trace 보존 |

## Data Model

### Identifiers And Concurrency

- Public IDs use ULID and are never connector secrets.
- Every mutable aggregate has integer `version`.
- Every mutation requires `expected_version`; mismatch returns `409 CONFLICT` with current summary.
- Times are UTC RFC 3339. Mobile renders local timezone.
- `schema_version` is present on queue events and agent outputs.

### DynamoDB Key Layout

Main table uses `PK`, `SK`, `GSI1PK`, `GSI1SK`.

| Entity | PK | SK | Purpose |
| --- | --- | --- | --- |
| UserProfile | `USER#<sub>` | `PROFILE` | locale, timezone, notification defaults |
| Connection | `USER#<sub>` | `CONNECTION#<provider>` | status, granted scopes, token reference, inventory summary |
| Preferences | `USER#<sub>` | `PREFERENCES` | pinned filters, suppression rules, scan scope, aggregated discovery preferences |
| DiscoveryFeedback | `USER#<sub>` | `FEEDBACK#<timestamp>#<id>` | append-only structured relevance feedback and outcome reference |
| Candidate | `USER#<sub>` | `CANDIDATE#<id>` | discovered proposal, group, evidence refs, confidence |
| CandidateGroup | `USER#<sub>` | `GROUP#<id>` | dynamic label, explanation and counts |
| Routine | `USER#<sub>` | `ROUTINE#<id>` | trigger, conditions, actions and activation state |
| Policy | `USER#<sub>` | `POLICY#<id>` | grant mode, action envelope, expiry, revocation |
| Case meta | `CASE#<id>` | `META` | owner, type, goal, status, priority, version |
| Evidence | `CASE#<id>` | `EVIDENCE#<id>` | connector reference, normalized facts, revision |
| Plan | `CASE#<id>` | `PLAN#<version>` | immutable plan body and hash |
| Approval | `CASE#<id>` | `APPROVAL#<id>` | actor, decision, plan hash, grant and expiry |
| Action | `CASE#<id>` | `ACTION#<id>` | connector mutation, status, idempotency and verification |
| AuditEvent | `CASE#<id>` | `EVENT#<timestamp>#<id>` | append-only timeline event |
| Message | `CASE#<id>` | `MESSAGE#<timestamp>#<id>` | Case-scoped chat message and author |

`GSI1` supports:

- active/history Case listing by `USER#<sub>#CASE#<bucket>` and priority/update time
- Candidate listing by `USER#<sub>#CANDIDATE#<status>#<group_id>` and discovery time
- Routine/Policy management by user and active state
- recent DiscoveryFeedback listing by user and time for transparent preference inspection/rebuild

The separate idempotency table stores `dedupe_key`, processing state, result reference and TTL. OAuth tokens and raw secrets are never stored in DynamoDB.

### Candidate Contract

Required fields:

- `candidate_id`, `user_id`, `source_type`, `outcome`, `summary`, `why_now`, `opportunity_type`
- `evidence_refs[]`, `confidence >= 0.70`, `risk`, `required_capabilities[]`
- `proposed_actions[1..3]`; each action matches one AVAILABLE capability operation and cites an invocation evidence ref
- `primary_group_id`, `tags[]`, `status`, `created_at`, `updated_at`
- `fingerprint`: normalized outcome + entity/date/resource signature for duplicate suppression
- SMS Candidate에 한해 `safety_state`, `safety_reasons[]`, 정규화된 `event_at`, sender class와 suspicious-feature flags를 추가한다. 원문 body와 전화번호 전체는 Candidate에 저장하지 않는다.

Candidate states:

```text
DISCOVERED -> GROUPED -> VISIBLE -> SELECTED -> CONVERTED
                           |           |
                           +-> HIDDEN  +-> VISIBLE
                           +-> EXPIRED
```

Low-confidence or actionless assessments never enter `VISIBLE`. They remain an internal abstention result and may be reconsidered only when new evidence or capability arrives.

### Discovery Feedback And Preference Contract

Discovery feedback is derived from existing authoritative mutations rather than asking the client to submit an arbitrary score.

Allowed feedback kinds:

- `APPROVED_VERIFIED`: approved with no material user correction, external result verified and not undone during the configured regret window
- `APPROVED_EDITED`: approved only after the user changed topic, target, timing, grouping or action feature
- `HIDDEN_ONCE`, `REDUCE_SIMILAR`, `DEFERRED`, `STOPPED`, `POLICY_REVOKED`, `UNDONE`

Each feedback event stores `candidate_id` or `case_id`, structured source/event/topic/time-horizon/risk/action features, feedback kind, plan/result reference, occurrence time and schema version. It does not copy raw email/SMS body, full sender address, complete phone number, secret, or sensitive Action parameter.

`Preferences` keeps a rebuildable aggregate containing feature weights, sample count, confidence, last-updated time and decay state. Candidate ranking combines grounded base relevance, user preference, freshness and a bounded diversity/exploration term. The aggregate may affect retrieval, primary group hints and order only; Policy matching, risk ceiling, plan hash, grant eligibility and executor authorization never read the preference score.

Repeated positive feedback may create a `ROUTINE_DISCOVERY` Candidate, but never an active Routine or standing Policy. The explanation surface identifies which structured preference raised or lowered the Candidate, and the user can exclude a feature class or reset the aggregate without deleting the append-only Case audit history.

### Case Contract

Required fields:

- `case_id`, `user_id`, `case_type`, `goal`, `summary`
- `status`, `risk`, `priority`, `version`
- `current_plan_version`, `current_plan_hash`
- `evidence_count`, `action_counts`, `decision_reason`
- `created_at`, `updated_at`, `last_verified_at`

Case states and allowed transitions:

```text
PREPARING
  -> DECISION_REQUIRED
  -> APPROVED
DECISION_REQUIRED
  -> APPROVED | PAUSED | STOPPED
APPROVED
  -> QUEUED
QUEUED
  -> RUNNING | FAILED | STOPPED
RUNNING
  -> VERIFYING | DECISION_REQUIRED | FAILED | PAUSED
VERIFYING
  -> COMPLETED | DECISION_REQUIRED | FAILED
FAILED
  -> QUEUED | STOPPED
PAUSED
  -> DECISION_REQUIRED | QUEUED | STOPPED
PERMISSION_REVOKED
  -> DECISION_REQUIRED | STOPPED
```

`COMPLETED` and `STOPPED` are terminal. Policy revocation moves a nonterminal Case to `PERMISSION_REVOKED` before the next external Action.

### Plan, Approval And Action Contracts

An immutable Plan contains ordered steps and proposed external Actions. A new material revision creates a new Plan version rather than editing the previous item.

`plan_hash` is SHA-256 over canonical JSON containing:

- Case ID and plan version
- evidence revision set
- each Action connector, target resource, verb and normalized parameters
- required OAuth scopes
- risk and reversibility

An Approval contains `decision`, `plan_version`, `plan_hash`, `actor_user_id`, `grant_mode`, optional `conditions`, `expires_at` and `created_at`.

Action states:

```text
PROPOSED -> APPROVED -> QUEUED -> RUNNING -> VERIFYING -> SUCCEEDED
     |          |          |         |           |
     +->BLOCKED +->CANCELLED         +->FAILED <-+
```

Every Action has a stable `idempotency_key` and `external_result_ref` when a real resource was created or changed.

### Routine And Policy Contracts

Routine states are `PROPOSED`, `ACTIVE`, `PAUSED`, `REVOKED`. `PROPOSED` never executes.

Policy grant modes:

- `ONCE`: one exact plan hash, consumed atomically
- `CONDITIONAL`: action envelope plus explicit target/time/state/impact constraints and expiry
- `STANDING`: reusable low/medium-risk action envelope with maximum impact and revocation

High-risk categories reject `CONDITIONAL` and `STANDING` at validation time.

## HTTP API Contracts

All `/v1/*` endpoints require Cognito JWT. All mutation requests carry `Idempotency-Key`; versioned mutations also carry `expected_version` in the body.

### Connections

| Method | Path | Request | Success response |
| --- | --- | --- | --- |
| GET | `/v1/connections` | — | `{connections:[ConnectionSummary]}` |
| POST | `/v1/connections/{provider}/authorize` | `{return_uri}` | `{authorization_url, flow_id, expires_at}` |
| POST | `/v1/connections/{provider}/scan` | `{lookback_days:1..30}` | `202 {connection:ConnectionSummary}` |
| DELETE | `/v1/connections/{provider}` | `{expected_version}` | `202 {status:"disconnecting"}` |
| GET | `/v1/connections/{provider}/inventory` | — | `{accessible[], unavailable[], checked_at, completeness}` |

Public OAuth routes:

- `GET /oauth/{provider}/return`: validates provider callback/session and completes token binding.
- `GET /oauth/{provider}/app-return`: exchanges one-time code for a deep link; it never puts an OAuth token in the URL.

`ConnectionSummary.discovery_revision`은 해당 연결의 최근 분석을 완료한 Worker가 적용한 action-ready Candidate 계약 revision이다. 분석 요청 시에는 0으로 유지하고, 현재 Worker가 scan 결과를 영속화할 때만 1로 올린다. 모바일은 `CONNECTED + scan_progress=100 + last_checked_at + discovery_revision>=1 + 성공한 Candidate 응답`을 모두 확인한 경우에만 `준비할 일 없음`을 표시한다. 이전 revision은 `POST /v1/connections/google/scan`으로 명시적 재분석을 요청하며, 재분석 중에는 `SCANNING` 상태와 진행률을 표시한다. 현재 구현의 scan 성공 응답은 즉시 갱신된 `ConnectionSummary`를 반환한다.

### Suggestions

| Method | Path | Request/Query | Success response |
| --- | --- | --- | --- |
| GET | `/v1/suggestion-groups` | filters, search, cursor | group summaries and counts |
| GET | `/v1/suggestions` | `group_id`, filters, cursor | paginated Candidate summaries |
| POST | `/v1/suggestions/{id}/feedback` | `{mode:HIDE_ONCE|REDUCE_SIMILAR|ADJUST_SCOPE}` | updated Candidate/policy preview |
| POST | `/v1/suggestions/convert` | `{candidate_ids[], expected_versions[]}` | `201 {case_id}` or merge preview |

The server derives additional DiscoveryFeedback from Candidate conversion, Case decision/edit, verified completion, stop, policy revocation and undo events. The client cannot use the feedback endpoint to assert `APPROVED_VERIFIED` or grant authority.

### Cases And Chat

| Method | Path | Request/Query | Success response |
| --- | --- | --- | --- |
| GET | `/v1/cases` | `bucket=active|history`, cursor | paginated Case cards |
| POST | `/v1/cases` | `{prompt}` or `{candidate_ids[]}` | `202 {case_id,status:"PREPARING"}` |
| GET | `/v1/cases/{case_id}` | optional event cursor | Case, current Plan, Actions and timeline |
| POST | `/v1/cases/{case_id}/messages` | `{text,expected_version}` | `202 {message_id,planning_job_id}` |
| POST | `/v1/cases/{case_id}/decision` | `{decision,plan_version,plan_hash,grant_mode,conditions?,expected_version}` | updated Case summary |
| POST | `/v1/cases/{case_id}/retry` | `{action_ids[],expected_version}` | `202 {status:"QUEUED"}` |
| POST | `/v1/cases/{case_id}/stop` | `{reason?,expected_version}` | updated Case summary |

Decision values are `APPROVE`, `REJECT`, `DEFER`, `STOP`. A stale hash or version returns `409 PLAN_CHANGED` with a safe change summary; the server never auto-approves the new plan.

### Policies And Routines

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/v1/policies` | active and revoked grants with recent usage |
| PATCH | `/v1/policies/{id}` | narrow, pause or change expiry; widening creates approval preview |
| DELETE | `/v1/policies/{id}` | revoke future use and pause affected Cases |
| GET | `/v1/routines` | proposed/active/paused routines |
| POST | `/v1/routines/{id}/activate` | approve exact Routine version and Policy envelope |
| POST | `/v1/routines/{id}/pause` | stop future triggers |

### Push Registration

- `PUT /v1/mobile/push-token`: `{expo_push_token, device_id, app_version, platform}`
- `DELETE /v1/mobile/push-token/{device_id}`

Push token is encrypted at rest and is not exposed by read APIs.

### Webhooks

- `POST /webhooks/google/pubsub`: validates Google OIDC; payload becomes `GMAIL_HISTORY_AVAILABLE`.
- `POST /webhooks/smartthings`: validates HTTP signature; payload becomes `SMARTTHINGS_DEVICE_EVENT` or lifecycle event.

Both return success only after durable queue enqueue. Invalid signatures return 401/403 and are never queued.

## Internal Event Contract

Every SQS message uses:

```json
{
  "schema_version": 1,
  "event_id": "01...",
  "event_type": "GMAIL_HISTORY_AVAILABLE",
  "user_id": "cognito-sub",
  "connector": "google",
  "occurred_at": "2026-08-23T00:00:00Z",
  "dedupe_key": "google:user:history-id",
  "trace_id": "01...",
  "payload": {}
}
```

Allowed initial event types:

- `INITIAL_SCAN_REQUESTED`
- `GMAIL_HISTORY_AVAILABLE`
- `DIRECT_REQUEST_RECEIVED`
- `CAPABILITY_SCAN_REQUESTED`
- `PLAN_APPROVED`
- `ACTION_EXECUTE_REQUESTED`
- `ACTION_VERIFY_REQUESTED`
- `GOOGLE_CONNECTION_REVOKED`
- `SMARTTHINGS_DEVICE_EVENT`
- `SMARTTHINGS_CONNECTION_DELETED`
- `SMS_MESSAGE_IMPORTED` (future, only after an approved Android input path; raw body is not placed on SQS)

## Agent Output Contracts

### CandidateProposal

- outcome, concise summary, why-now explanation and opportunity type
- evidence refs only, not copied secret/token values
- confidence of at least 0.70 with uncertainty reason
- one primary result group and minimal tags
- risk, required capabilities and one to three typed grounded Action proposals
- duplicate fingerprint inputs

### CasePlanProposal

- human-readable goal and explanation
- evidence revisions used
- ordered preparation steps
- external Action proposals with exact target and normalized parameters
- required scopes, risk, reversibility and verification method
- decision compression: one result-oriented question plus optional details

Pydantic validation failure triggers one schema-repair attempt. A second failure records `AGENT_OUTPUT_INVALID` and creates an Exception/Approval Case without external execution.

## File Structure

```text
QuietPilot/
├── apps/
│   └── mobile/
│       ├── app/
│       │   ├── _layout.tsx                  # providers, auth gate, notification/deep-link routing
│       │   ├── (auth)/                      # sign-in, sign-up and verification screens
│       │   ├── (tabs)/
│       │   │   ├── active.tsx               # 진행: decisions first, then running Cases
│       │   │   └── suggestions.tsx          # 새 제안: grouped/filterable candidates
│       │   ├── cases/[caseId].tsx           # Case detail, plan, timeline and scoped chat
│       │   ├── connections/index.tsx        # connector inventory and scopes
│       │   ├── policies/index.tsx           # one-time/conditional/standing permissions
│       │   └── oauth-return.tsx              # one-time app return result
│       ├── src/
│       │   ├── api/                          # typed HTTP client and query keys
│       │   ├── auth/                         # Amplify session adapter
│       │   ├── components/                   # cards, groups, status/risk and explanation UI
│       │   ├── features/cases/               # Case list/detail/decision behavior
│       │   ├── features/suggestions/         # grouping, filters and suppression
│       │   ├── features/connections/         # OAuth and capability inventory
│       │   ├── features/policies/            # grants, revocation and policy preview
│       │   ├── notifications/                # registration and deep-link mapping
│       │   └── theme/                        # calm control-room design tokens, light/dark modes
│       ├── app.config.ts                     # Android package, plugins, schemes and permissions
│       ├── eas.json                          # development build profile if cloud build is needed
│       └── package.json
├── services/
│   ├── control-api/
│   │   ├── src/handlers.py                   # authenticated HTTP API routes
│   │   ├── src/repositories.py               # DynamoDB aggregate reads/writes
│   │   ├── src/policy.py                     # deterministic authorization decisions
│   │   ├── src/plan_hash.py                  # canonicalization and SHA-256
│   │   └── pyproject.toml
│   ├── ingress/
│   │   ├── src/google_pubsub.py              # OIDC verification and queue enqueue
│   │   ├── src/smartthings_webhook.py        # signature/digest/replay verification
│   │   ├── src/oauth_return.py               # browser session binding and deep-link handoff
│   │   └── pyproject.toml
│   ├── worker/
│   │   ├── src/consumer.py                   # SQS batch processing and dispatch
│   │   ├── src/google_sync.py                # watch/history/full-sync recovery
│   │   ├── src/executors/google.py           # Calendar/Tasks mutations and readback
│   │   ├── src/executors/smartthings.py      # commands and verification scheduling
│   │   ├── src/notifications.py              # Expo Push delivery and grouping
│   │   └── pyproject.toml
│   └── agent/
│       ├── main.py                           # AgentCore Runtime entrypoint and ping
│       ├── quietpilot/orchestrator.py         # routing and final typed output
│       ├── quietpilot/agents/signal.py        # Gmail/Calendar specialist
│       ├── quietpilot/agents/capability.py    # SmartThings specialist
│       ├── quietpilot/agents/planner.py       # Case and ActionPlan specialist
│       ├── quietpilot/tools/read_context.py   # bounded read-only context tools
│       ├── quietpilot/tools/propose.py        # proposal writers, never side-effect tools
│       ├── quietpilot/models.py               # Pydantic agent contracts
│       ├── pyproject.toml
│       └── agentcore/agentcore.json           # CodeZip/runtime/Identity configuration
├── infra/
│   ├── bin/quietpilot.ts                      # CDK app and environment selection
│   ├── lib/auth-stack.ts                      # Cognito and app client
│   ├── lib/api-stack.ts                       # HTTP API, Lambda and routes
│   ├── lib/data-stack.ts                      # DynamoDB, queues and DLQs
│   ├── lib/schedule-stack.ts                  # Gmail renew/recovery schedules
│   ├── lib/observability-stack.ts             # logs, metrics, retention and alarms
│   └── test/                                  # CDK assertions
├── contracts/
│   ├── openapi.yaml                           # source of truth for mobile HTTP contracts
│   ├── events/                                # versioned internal queue JSON Schemas
│   └── agent/                                 # exported Candidate/Plan JSON Schemas
├── tests/
│   ├── contract/                              # OpenAPI/Pydantic/Zod compatibility checks
│   ├── integration/                           # deployed sandbox connector/API tests
│   └── fixtures/                              # UI and automated-test data only, never demo proof
├── docs/hackathon-build/                      # guided planning documents
└── tasks/                                     # task plans and correction lessons
```

## Core Data Flows

### Flow A — Connected Gmail Signal To Verified Google Action

1. Gmail sends a mailbox change to Google Pub/Sub.
2. Pub/Sub calls `/webhooks/google/pubsub` with OIDC JWT.
3. Ingress validates JWT and queues `GMAIL_HISTORY_AVAILABLE` with history ID.
4. Worker deduplicates, retrieves the Connection and calls Gmail `history.list`.
5. Worker fetches only changed messages needed for the authorized scan scope.
6. Normalized Evidence enters AgentCore under the verified user ID.
7. Action Discovery Planner reads bounded Evidence and capability inventory, then returns `SUPPRESS` or one action-ready Candidate. Greetings, newsletters and unsupported/ambiguous items stop here.
8. Deterministic validation checks confidence, evidence ownership, risk floor and exact AVAILABLE operations; only a passing Candidate appears under `새 제안` without push.
9. User sees the outcome and proposed work, then chooses `이대로 준비`, `내용 조정` or `필요 없음`. A prepared Candidate carries its original Actions into the Case.
10. User approves the plan hash once or under an allowed condition.
11. Policy Engine atomically validates and queues Action execution.
12. Google Executor creates the external resource with stable idempotency ID.
13. Readback confirms the resource; Action and Case become `SUCCEEDED`/`COMPLETED`.
14. Mobile refetch shows the timeline. Push occurs only if configured as an important completion.

### Flow B — Direct Delegation

1. User sends a prompt to `POST /v1/cases`.
2. Control API creates a `DIRECT_DELEGATION` Case in `PREPARING`.
3. Orchestrator gathers only connected, policy-allowed context.
4. Case Planner returns preparation steps, blocked connectors and proposed Actions.
5. The same approval, execution and verification path as Flow A runs.

### Flow C — Routine Discovery

1. Initial or scheduled capability scan reads API-visible SmartThings devices/status.
2. Capability Analyst creates grounded Routine Candidate(s) with trigger, condition, action and revocation.
3. Candidate remains inactive in `새 제안`.
4. Activation creates an immutable Routine version plus Policy preview.
5. Only explicit approval changes it to `ACTIVE`; future triggers create Cases rather than running invisibly.

### Flow D — Material Change Or Partial Failure

1. Executor detects changed target/parameters, missing scope, conflicting state or failed step.
2. Current Action stops before the next external mutation.
3. A new Plan version records completed, failed and remaining Actions.
4. Old approval becomes invalid because plan hash changed.
5. Case moves to `DECISION_REQUIRED` and sends one blocking notification.
6. User approves the changed plan, retries failed Actions, defers or stops.

### Flow E — Authorized SMS To Automatic Local Reminder

1. 사용자가 실제 지원되는 문자 입력 방식과 스캔 범위, 온디바이스 처리, 제외 조건을 확인하고 연결한다.
2. 온디바이스 전처리가 OTP·결제·단축 URL·과거 일정과 명백한 스팸을 먼저 표시한다.
3. Message Safety Analyst가 발신자 분류, 미래 날짜·시간, 사건 유형, 신뢰도, 불확실성과 안전 상태를 구조화한다.
4. 결정론적 policy engine이 `trusted sender + unambiguous future time + no suspicious URL/payment/OTP + not duplicate + active low-risk grant`를 모두 확인한다.
5. 조건이 하나라도 빠지면 `REVIEW` Candidate 또는 Exception/Approval Case가 되고 아무 알림도 자동 등록하지 않는다. `BLOCKED`는 새 제안 기본 목록에 올리지 않는다.
6. 조건이 모두 맞으면 mobile local-notification adapter가 안정적인 fingerprint로 취소 가능한 알림을 등록하고 OS readback을 기록한다.
7. Case는 readback 뒤에만 `COMPLETED`가 되며 정책·근거·등록 시간·취소 경로를 timeline에 남긴다.

첫 로컬 구현은 이 흐름을 fixture backend로만 재현하며 `externalSideEffects=false`다. 실제 SMS 읽기, Notification Listener, Calendar 쓰기, Clock alarm 또는 local notification은 실행하지 않는다.

## Background And Resume Behavior

- Gmail and SmartThings monitoring runs through provider webhooks, SQS and Scheduler even if the app is closed.
- Mobile app background execution is not required for discovery or external execution.
- Push is best effort. Force-stop, disabled channels or vendor power management can suppress delivery; pending work remains in `진행`.
- App resume invalidates active Case, Connection and notification queries and fetches server truth.
- OAuth browser return uses a short-lived one-time code; if the app was killed, reopening the app shows connection state from the server.
- Location/geofence is excluded from MVP. A later implementation must pass a separate S23 Ultra terminated-app reliability spike.
- SMS history inspection cannot depend on a permanent JavaScript background process. A selected native input path emits minimized local records; notification-listener mode observes posted/active notifications only and is not represented as historical inbox access.

## Error Strategy

| Failure | Classification | Behavior | Recovery proof |
| --- | --- | --- | --- |
| Pub/Sub duplicate | expected retry | same dedupe key returns stored result | one Candidate/Event only |
| Gmail history ID expired/404 | recoverable sync gap | bounded full sync, update baseline | no duplicate Case fingerprints |
| Google OAuth test token expired | connection revoked/expired | pause dependent Actions, reconnect CTA | resume from exact blocked Action |
| Agent output invalid twice | agent contract failure | no external action; Exception/Approval Case | invalid schema fixture test |
| Connection scan completed with an old discovery revision | stale discovery result | keep the connection, show per-connector reanalysis state, never show no-work | new scan reaches current revision before an empty result is accepted |
| Mobile and deployed API response contracts are incompatible | deployment compatibility error | keep the connection, show a per-connector failure state rather than update progress, never show no-work | deploy the compatible producer/consumer contract, then complete a current-revision scan |
| Gmail initial scan still has a next page or one batch failed validation | incomplete discovery | keep progress below 100, continue through SQS or retry the failed batch, never show no-work | every page is validated and the final page atomically certifies the current revision |
| Stale plan hash/version | material conflict | 409, show change summary | no mutation call observed |
| SQS partial batch failure | isolated retry | retry failed message only, DLQ after limit | successful siblings not repeated |
| Calendar/Tasks timeout after write | unknown external result | lookup by deterministic ID before retry | one external resource |
| SmartThings command accepted but no state event | verification timeout | explicit status readback, then exception | not marked complete on acceptance |
| SmartThings OAuth registration unavailable | external onboarding blocker | Google loop proceeds; PAT only disclosed demo fallback | no product OAuth claim |
| Push delivery unavailable | notification degradation | Case stays visible in `진행` | app reopen reveals decision |
| Android background/library bug | client defect | reproduce on S23, patch plugin/native/resume path | device regression test |
| SMS model marks suspicious text safe | safety-classification failure | deterministic risk flags override, block auto action, add redacted error class to evaluation set | zero automatic action in adversarial fixtures |
| SMS date is ambiguous or stale | extraction uncertainty | `REVIEW`, show competing interpretations, no reminder | explicit decision-required fixture |
| SMS permission/Play review unavailable | distribution blocker | selected-message import or future-notification path; disclose missing history | manifest contains no undeclared restricted permission |
| Local reminder registration unknown | unknown device result | lookup by stable fingerprint before retry | one reminder or a visible verification failure |

## Security And Privacy

- Cognito access token is verified at API Gateway; `sub` is never accepted from request body.
- Lambda Runtime invocation role is scoped to the exact AgentCore Runtime and user-invocation permission.
- AgentCore Identity or Secrets Manager holds external credentials. Tokens are excluded from DynamoDB, SQS, push and logs.
- Google Pub/Sub and SmartThings webhook authenticity is verified before durable enqueue.
- Raw connector data is minimized before model invocation and is not written to application logs.
- External text is untrusted content. It cannot change system policy, grant authority or choose hidden tool arguments.
- DynamoDB encryption at rest, TLS, log retention and least-privilege IAM are configured in CDK.
- Audit logs store references and result summaries, not OAuth tokens or complete email bodies.
- Google restricted-scope testing is limited to listed test users; public production readiness is not claimed.
- SMS raw bodies and full sender addresses remain on-device by default. Cloud logs, queues, push payloads, fine-tuning datasets and analytics contain neither complete message text nor complete phone numbers.
- Fine-tuning requires a separate consented/redacted dataset decision. User message history is never silently repurposed as training data.

## AI Usage

### Where AI Is Used

- Evidence extraction and uncertainty labeling
- Candidate generation and duplicate/group hints
- Related-signal grouping into a result-oriented Case
- Direct-request decomposition without exposing microtasks
- Routine proposals grounded in actual capabilities
- Human-readable approval and change explanations
- SMS spam/smishing risk, future-event extraction and calibrated uncertainty proposals
- structured approval-history preference summaries for Candidate retrieval, grouping hints and ranking explanations

### Where AI Is Not Authoritative

- authentication and user identity
- OAuth scope enforcement
- plan hash and version validation
- risk ceiling and grant-mode eligibility
- idempotency and state transitions
- external side-effect execution
- success/readback determination
- final SMS allow/block decision and automatic-reminder eligibility
- authorization changes based on personalized relevance or approval frequency

### Prompt And Evaluation Fixtures

Maintain a small redacted golden set covering:

- unrelated email that must yield no Candidate
- two related signals that should group once
- conflicting evidence that must request a decision
- malicious email instruction that must remain data
- missing connector that must separate preparation from blocked execution
- capability inventory containing API-inaccessible items
- material plan change that must invalidate approval
- benign appointment SMS, ambiguous multi-date SMS, expired event, duplicate reservation, OTP, payment request, shortened URL and adversarial smishing variants

Evaluation checks schema validity, evidence grounding, duplicate rate, correct Case type, risk under-classification and forbidden direct mutation attempts. The SMS slice additionally tracks malicious-message recall, benign false-block rate, exact date/time extraction, duplicate suppression, calibration and automatic-action precision. Personalized discovery tracks top-k verified-hit precision, verified approval without material edit, hide/suppression rate, time to decision, regret-window undo/revocation, diversity and cold-start performance. Deferred work is not counted as a negative label. A specialized fine-tune is considered only after a redacted representative set shows that prompt/rule/baseline-model improvements are insufficient; it is not assumed to be the first solution.

## Risks And Verification

### Verification Gate 1 — Mobile Shell

- Expo SDK 57 development build installs on `SM-S918N`.
- Cognito sign-up/sign-in/sign-out works.
- API call uses a real access token.
- push permission, push receipt and Case deep link work.
- force-stop limitation is documented; reopening restores server state.

### Verification Gate 2 — AgentCore

- local Strands contract tests pass.
- CodeZip deploys in `ap-northeast-2` with Linux ARM64 dependencies.
- configured model is accessible.
- Cognito-authorized Lambda invokes Runtime with separate user IDs.
- malformed model output cannot reach executor.

### Verification Gate 3 — Google Connection And Signal

- AgentCore Identity browser flow returns to the app without exposing a token.
- Gmail `watch` is active and expiration is stored.
- a real test email produces one authenticated Pub/Sub event and one Candidate.
- daily renewal and expired-history recovery paths are tested.

### Verification Gate 4 — Approval And Real Action

- exact Plan/Action is shown in mobile UI.
- stale or changed hash is rejected before connector call.
- approved Calendar event or Task is created once.
- API readback matches expected result before Case completion.
- audit timeline identifies approval, request and verified result.

### Verification Gate 5 — SmartThings

- current OAuth-In/API Access App creation is tried after the Google loop works.
- only API-visible air conditioners/capabilities are shown as controllable.
- one separately approved command executes during a controlled test.
- accepted and verified states are shown separately.
- if registration is blocked, the PAT fallback is labeled personal/demo-only and never called production account linking.

### Verification Gate 6 — SMS Safety And Local Reminder Expansion

- choose and document one real Android input path; selected share/import is the distribution-safe baseline, while full history requires verified default-handler/Assistant status or accepted Google Play device-automation permission declaration.
- prove raw text stays on-device and inspect logs/queues for absence of message bodies and full sender addresses.
- pass adversarial safety/date fixtures and threshold calibration before any automatic action test.
- show the exact standing low-risk reminder policy and its revocation path.
- on `SM-S918N`, grant notification permission, register one controlled local reminder, read back one stable identifier, retry without duplication, cancel it, and verify removal.
- do not add `READ_SMS`, request restricted access, or inspect the participant's real messages until this gate receives a fresh explicit privacy/permission decision.

### Architecture Self-Review

1. **Too many AWS services?** Cognito, HTTP API, Lambda, SQS, Scheduler and DynamoDB each cover a distinct required failure boundary. Step Functions, AppSync, Gateway, Policy and AgentCore Memory are deliberately excluded.
2. **Too many agents?** Three specialist roles are enough to demonstrate non-trivial Strands orchestration without creating one agent per API endpoint. Policy and execution stay deterministic.
3. **Two clouds for Gmail?** Google Cloud Pub/Sub is required by Gmail push. QuietPilot minimizes this surface to topic/subscription/authenticated push while business state remains in AWS.

No unresolved architecture choice blocks the checklist. External account availability remains a spike with an explicit fallback and honest claim boundary.

## Demo And Submission Flow

1. Open the installed Android app and sign in.
2. Show Google and SmartThings connection inventory with accessible/unavailable boundaries.
3. Send or reveal a real Gmail test signal and show progressive discovery without a Candidate push.
4. Show grouped `새 제안`, filters and one suppression action.
5. Create a Direct-Delegation Case and show it shares the same structure as the connected signal.
6. Open the approval card, explain evidence, exact external changes, risk, reversibility and grant mode.
7. Approve once, create a real Calendar/Tasks resource and show API readback before completion.
8. Show a material-change or partial-failure fixture moving the Case back to `결정 필요` without a duplicate action.
9. Show Routine Discovery from API-visible SmartThings capabilities.
10. With separate live consent, send one command and show acceptance versus verified device state.
11. End on the audit timeline and policy revocation controls.

The video may use deterministic seeded UI fixtures to reach error screens, but the Gmail, Google write/readback and selected SmartThings proof must be live integrations.

## Build Checklist Handoff

The build checklist must sequence work by proof, not by screen count:

1. local toolchain and monorepo skeleton
2. contracts, Case state machine, plan hash and policy unit tests
3. CDK data/auth/API foundation
4. mobile shell, Cognito and two-area navigation
5. AgentCore typed orchestrator and safe proposal tools
6. Google Identity, Gmail watch/Pub/Sub and initial scan
7. Candidate grouping and Case conversion UI
8. approval, Google executor, readback and audit timeline
9. notifications, failure/recovery and device regression checks
10. SmartThings OAuth/capability/command spike
11. repeatable demo, evidence capture and submission preparation

Each checklist task must include a current verification command or live readback. A mock-only result cannot complete a connector task.
