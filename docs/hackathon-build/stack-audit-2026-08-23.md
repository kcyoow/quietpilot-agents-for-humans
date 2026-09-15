# QuietPilot 기술 스택 심층 감사 및 확정안

- 기준일: 2026-08-23 (Asia/Seoul)
- 승인 상태: 2026-08-23 참가자 확인으로 이 문서의 스택을 QuietPilot 구현 기준선으로 확정했다.
- 조사 방식: Exa 심층 검색 51개 질의에서 379개 후보 결과를 검토하고, 최종 판단에는 공식 문서·공식 SDK 저장소·공식 예제만 사용했다.
- 판단 등급:
  - `확정`: 공식 지원과 QuietPilot 요구가 일치하며 MVP에 채택한다.
  - `초기 스파이크`: 구조는 타당하지만 계정별 권한, 전환 중인 제품 표면, 실제 기기 동작을 짧은 수직 검증으로 먼저 증명해야 한다.
  - `MVP 제외`: 가능하더라도 첫 완주 루프의 신뢰도나 일정에 불리하다.

## 결론

기존 큰 방향은 맞다. 다만 그대로 채택하지 않고 다음처럼 좁혀 확정한다.

1. Android 앱은 Expo Go가 아니라 **Expo SDK 57 개발 빌드**로 만든다.
2. 사용자 요청과 외부 이벤트는 **Cognito → API Gateway HTTP API → Lambda → SQS → AgentCore Runtime** 경계로 통일한다.
3. AgentCore Runtime은 서울 리전에 두고, **Strands Python 에이전트**를 CodeZip으로 배포한다.
4. 첫 추론 모델은 AWS 프로모션 크레딧 범위와 실제 계정 호출 성공을 우선해 **Amazon Nova 2 Lite on Bedrock**으로 둔다. 서울 리전에서 `global.amazon.nova-2-lite-v1:0` 글로벌 추론 프로필을 호출하므로 데이터가 서울 단일 리전에만 머문다고 주장하지 않는다. 모델은 `QUIETPILOT_BEDROCK_MODEL_ID`로 명시적으로 바꿀 수 있지만, 어떤 모델도 Pydantic 검증·제한된 재시도·위험 하한·실행 권한 검사를 우회하지 않는다.
5. Gmail 감지는 Google Cloud Pub/Sub 푸시와 일일 `watch` 갱신, 주기적 재동기화를 함께 사용한다.
6. 첫 실제 쓰기 동작은 Google Calendar와 Google Tasks로 만들고, SmartThings는 등록 경로를 먼저 증명한 뒤 두 번째 실제 동작으로 붙인다.
7. SmartThings는 기능 자체가 아니라 **현재 OAuth 앱 등록 표면**이 위험 요소다. 신형 API Access App 문서는 등록을 아직 `coming soon`이라고 명시하고, 현행 CLI의 OAuth-In SmartApp 경로에는 2026년 생성 404 사례가 있다. 따라서 제품 스택에서는 유지하되 `초기 스파이크`로 분류한다.
8. 위치·지오펜싱, Drive/Docs, 기존 SmartThings 모바일 루틴 가져오기, 임의의 폰 시스템 제어는 MVP에서 제외한다.

이 확정은 “문제가 전혀 없을 것”이라는 뜻이 아니다. Android 백그라운드 실행·푸시·딥링크에서 실제 기기 버그가 나오면 S23 Ultra 재현을 기준으로 Expo config plugin, 네이티브 Android 코드 또는 서버 측 복구 흐름에서 수정한다. SmartThings 등록 표면은 외부 서비스이므로 QuietPilot이 그 서비스 자체를 고칠 수는 없지만, 모바일·Google 완주 경로를 먼저 만든 뒤 현재 지원되는 등록 경로를 직접 시험하고 우리 연동 코드나 우회 경로를 조정한다. 이 두 위험은 스택을 다시 미정으로 돌리는 사유가 아니라 초기 검증 작업이다.

## 확정 스택

| 영역 | 확정 선택 | 상태 | 이유와 경계 |
| --- | --- | --- | --- |
| 모바일 런타임 | Expo SDK `~57.0`의 최신 패치, React Native 0.86.x, React 19.2.x, TypeScript | 확정 | SDK 57은 Android compile/target SDK 36을 사용하고 Android 16 기기와 맞는다. Hermes 메모리 회귀 수정 때문에 최소 57.0.9 이상을 사용하며 실제 생성 시 최신 패치를 고정한다. |
| 모바일 실행 방식 | `expo-dev-client` 기반 프로젝트 전용 Android 개발 빌드 | 확정 | Expo Go 제약을 피하고 네이티브 라이브러리·config plugin·Kotlin 모듈을 추가할 수 있다. |
| 내비게이션·상태 | Expo Router, TanStack Query, React Context/로컬 상태 | 확정 | 서버 상태와 화면 상태를 분리하고 별도 전역 상태 라이브러리는 필요가 생길 때만 추가한다. |
| 모바일 인증 | `aws-amplify` v6 Auth + `@aws-amplify/react-native`, 커스텀 로그인 UI | 확정 | Cognito 세션 처리에 사용하되 Amplify UI의 기본 화면에 제품 디자인을 종속시키지 않는다. Expo Go는 지원하지 않지만 개발 빌드는 공식 지원 범위다. |
| 모바일 보안·연결 | `expo-secure-store`, `expo-web-browser`, `expo-linking` | 확정 | 로컬 세션 보조 저장, OAuth 브라우저 전환, HTTPS 반환 후 앱 딥링크에 사용한다. 외부 OAuth 토큰은 폰에 저장하지 않는다. |
| 알림 | `expo-notifications` + Expo Push Service | 확정 | Android 채널·권한·딥링크·액션을 지원하고 MVP 서버 구성이 direct FCM보다 단순하다. 알림 액션은 `Case 열기`, `나중에`만 허용한다. |
| IaC | AWS CDK v2, TypeScript | 확정 | 사용자의 TypeScript 경험을 활용하고 Cognito/API Gateway/Lambda/SQS/DynamoDB/EventBridge를 재현 가능하게 배포한다. AgentCore 리소스는 AgentCore CLI 설정과 배포로 관리한다. |
| 앱 인증 경계 | Cognito User Pool + API Gateway HTTP API JWT authorizer | 확정 | 모바일은 AgentCore를 직접 호출하지 않는다. API Gateway가 액세스 토큰을 검증하고 Lambda가 검증된 `sub`를 사용자 ID로 사용한다. |
| 제어 API·웹훅 | Python 3.12 Lambda + AWS Lambda Powertools | 확정 | HTTP API 라우팅, 구조화 로그, 메트릭, SQS 부분 실패, 멱등성 구현을 단순화한다. |
| 비동기 처리 | SQS 표준 큐 + DLQ, Lambda consumer, `ReportBatchItemFailures` | 확정 | 외부 웹훅을 빠르게 응답하고 중복·재시도를 안전하게 흡수한다. 모든 consumer는 at-least-once를 전제로 멱등하게 만든다. |
| 스케줄 | EventBridge Scheduler | 확정 | Gmail `watch` 일일 갱신, 누락 복구 동기화, 토큰·연결 상태 점검에 사용한다. Step Functions는 쓰지 않는다. |
| 영속 상태 | DynamoDB main table + idempotency table, TTL과 조건부 쓰기 | 확정 | Case·제안·정책·작업·감사 이벤트를 장기 보관하고 승인된 정확한 계획 버전만 실행한다. AgentCore 세션 메모리를 영속 상태로 사용하지 않는다. |
| 에이전트 | Strands Agents Python + Pydantic | 확정 | 연결별 분석기를 Agents-as-Tools로 구성하고 제안·계획을 타입 검증한다. 도구 annotation은 보안 경계로 사용하지 않는다. |
| 에이전트 호스팅 | Bedrock AgentCore Runtime, `ap-northeast-2`, Python 3.12 CodeZip | 확정 | 서울 리전 지원, 비동기 작업, 격리 세션, CloudWatch 관측성을 활용한다. CLI가 Linux ARM64 wheel 패키징을 처리하게 한다. |
| Runtime 호출 | Lambda의 IAM SigV4 호출 + 검증된 사용자 ID 전달 | 확정 | 모바일 요청과 Gmail/SmartThings 백그라운드 이벤트를 같은 신뢰 경계에서 호출할 수 있다. Runtime은 외부에 직접 노출하지 않고 호출 역할을 정확한 ARN과 사용자 호출 권한으로 제한한다. |
| 외부 토큰 | AgentCore Identity | Google은 확정, SmartThings는 초기 스파이크 | Google built-in OAuth provider를 사용한다. SmartThings는 custom OAuth2 + `CLIENT_SECRET_BASIC` 기술 호환은 확인됐지만 앱 생성 성공을 먼저 증명한다. |
| 추론 모델 | `global.amazon.nova-2-lite-v1:0` | 기본값 확정, 로컬 설정 완료 | 서울 source region에서 Global inference와 Converse 도구 호출을 사용한다. 한국어는 공식 최적화 언어에 포함된다. Strict structured output 지원에 의존하지 않고 기존 Pydantic 검증과 제한된 재시도를 유지한다. 같은 계정·리전의 최소 Converse 호출은 성공했지만 AgentCore 배포 증거는 아직 아니다. |
| 관측성 | CloudWatch Logs/Metrics + AgentCore OTEL traces | 확정 | 원문 이메일·토큰·모델 전체 입력을 로그에 남기지 않는다. Case ID, connector, latency, result code, policy decision만 기록한다. |
| Google 입력 | Gmail API `gmail.readonly`, Gmail `watch`, Google Cloud Pub/Sub authenticated push | 확정 | 본문 분석에는 metadata scope만으로 부족하다. Pub/Sub OIDC JWT의 서명·audience·service-account email을 검증한다. |
| Google 출력 | Calendar API + Tasks API | 확정 | Calendar 이벤트 ID를 사전 생성해 중복 쓰기를 막는다. Tasks의 due는 시간 정보를 버리므로 시간 알림은 Calendar로 만든다. |
| SmartThings | Devices/Commands/Subscriptions API + OAuth-In/API Access App + 서명 웹훅 | 초기 스파이크 | 읽기 capability는 실제 에어컨 2대로 증명됐다. 명령 수락은 완료가 아니며 이벤트 또는 상태 재조회로 결과를 확인한다. |

## 실제 실행 흐름

```text
Gmail Pub/Sub / SmartThings webhook / 직접 사용자 요청
  -> API Gateway 수신 Lambda
  -> 서명·JWT·중복 검증
  -> SQS
  -> worker Lambda
  -> AgentCore Runtime(Strands)가 제안/계획 생성
  -> DynamoDB에 Candidate 또는 Case 저장
  -> 필요 시 Expo push
  -> 사용자가 앱에서 승인
  -> Lambda가 승인 plan_hash·정책·멱등성 재검증
  -> Google/SmartThings 도구 실행
  -> webhook 또는 API readback으로 결과 확인
  -> Case timeline과 audit event 갱신
```

AgentCore Runtime 세션은 기본 15분 유휴 종료, microVM 최대 8시간이므로 일주일 이상 지속되는 Routine 상태를 맡기지 않는다. Routine과 Case 상태는 DynamoDB에 있고, 각 이벤트가 새 Runtime 호출을 시작하거나 기존 session ID를 재사용한다.

## 보안·승인 계약

- LLM은 `propose`만 한다. 권한 부여, 위험 등급, 승인 유효성, 실제 side effect 허용은 결정론적 Python 정책 코드가 판정한다.
- 승인 대상은 `target + action + normalized parameters + required scopes + evidence revision`의 해시로 고정한다.
- 대상, 파라미터, 권한, 위험 또는 근거가 바뀌면 기존 승인을 무효화한다.
- Google/SmartThings 원문은 필요한 최소 범위만 모델에 전달하고, 로그에는 원문과 토큰을 남기지 않는다.
- 외부 입력 안의 “이전 지시를 무시하고 실행하라” 같은 문장은 데이터로만 취급한다. 메일 본문이 도구 권한을 만들 수 없다.
- SQS, Gmail history, SmartThings event ID, Calendar client event ID를 각각 멱등성 키에 포함한다.
- SmartThings 명령은 응답 200만으로 완료 처리하지 않는다. 이벤트 또는 readback 상태가 기대값과 일치해야 `completed`가 된다.
- 푸시는 best effort다. Android force-stop이나 채널 차단 시 전달되지 않을 수 있으므로 모든 승인 대기는 앱의 `진행` 영역에 남는다.

## 초기 스파이크 순서

스택 자체를 더 토론하기보다 다음 다섯 개를 순서대로 실제 증명한다.

1. **모바일 빌드 스파이크**: Expo SDK 57 앱을 S23 Ultra에 설치하고 Cognito 로그인, API 호출, push 수신, 딥링크 복귀를 확인한다.
2. **AgentCore 스파이크**: 서울 리전 CodeZip 배포, Nova 2 Lite 모델 접근, Pydantic 구조화 출력, Lambda SigV4 `runtimeUserId` 호출과 사용자 분리를 확인한다.
3. **Google Identity 스파이크**: 모바일 브라우저에서 AgentCore Identity 3LO를 시작하고 HTTPS return handler를 거쳐 앱으로 복귀한 뒤 Gmail 읽기와 Calendar 쓰기를 수행한다.
4. **Gmail 이벤트 스파이크**: Gmail `watch` → authenticated Pub/Sub push → history fetch → 중복 없는 Candidate 생성을 확인한다.
5. **SmartThings 스파이크**: OAuth-In SmartApp 또는 새 API Access App을 실제 생성한다. 성공하면 AgentCore custom provider와 구독 웹훅까지 연결한다. 생성 경로가 막히면 해커톤 데모는 24시간 PAT를 Secrets Manager에 당일 주입하는 개인 계정 한정 폴백으로 실행하고, 이를 제품용 OAuth라고 표현하지 않는다.

## MVP 제외

- Android 지오펜싱과 백그라운드 위치: 종료 앱 재시작 불가, Android 16 foreground-service/job 제한, Samsung 절전 차이 때문에 첫 신뢰성 증명에서 제외한다.
- Google Drive/Docs: Calendar/Tasks 완주 후 확장한다.
- 기존 SmartThings 모바일 Routine 가져오기: 공개 Rules API와 모바일 Routine이 같은 표면이 아니므로 지원한다고 주장하지 않는다.
- AgentCore Gateway, Policy, Memory, Browser, Code Interpreter: 첫 버전의 필수 문제가 아니며 서비스 수와 권한 표면만 넓힌다.
- Step Functions, AppSync, OpenSearch, RDS: 현재 Case 흐름에는 필요하지 않다.
- direct FCM: Expo Push Service가 요구를 충족하는 동안 추가하지 않는다.
- iOS, 임의 Wi-Fi/DND/접근성/Knox 제어, 결제·삭제·최종 제출 자동 승인.
- 불특정 다수 대상 Gmail 공개 출시: Restricted scope 검증과, 서버가 해당 데이터를 처리할 때 필요한 보안 심사를 통과하기 전에는 해커톤 테스트 사용자만 지원한다.

## 현재 로컬 환경 점검

| 항목 | 현재 상태 | 조치 |
| --- | --- | --- |
| Node | 22.14.0 | Expo SDK 57 최소 22.13.x를 충족한다. |
| npm | 10.9.2 | 사용 가능하다. |
| Python | 3.12.4 | Lambda와 AgentCore Python 3.12에 맞는다. |
| Java | Oracle JDK 21 | React Native는 JDK 17을 권장하고 더 높은 버전에서 문제가 날 수 있으므로 JDK 17을 별도로 설치·고정한다. |
| Android Studio / SDK | 설치 또는 기본 SDK 경로가 확인되지 않음 | Android Studio, Platform 36, Build-Tools, Command-line Tools를 설치하고 `ANDROID_HOME`을 설정한다. EAS 개발 빌드는 임시 우회일 뿐 로컬 도구도 준비한다. |
| adb | `/opt/homebrew/bin/adb` | 설치됨. 이번 점검 시 기기는 연결되어 있지 않았다. |
| Watchman | 없음 | Expo SDK 56 이상에서는 필수가 아니므로 설치하지 않는다. |
| AWS CLI | 없음 | AWS CLI v2와 SSO/자격 증명 구성을 준비한다. |
| Docker | 28.5.1 | Lambda 의존성 번들링이나 CodeZip 폴백에 사용 가능하다. |
| SmartThings CLI | 없음 | Node 24 요구가 있으므로 현재 Node를 바꾸기보다 Homebrew macOS 바이너리를 사용할 수 있다. |
| Git | 현재 폴더는 Git 저장소가 아님 | 구현 시작 전 초기화 여부는 별도 사용자 결정이며 이번 감사에서는 변경하지 않았다. |

## 확인된 제약과 남은 불확실성

- **Google**: 해커톤 테스트 모드에서는 검증 없이 지정 test user로 가능하지만, 외부 앱의 테스트 authorization/refresh token은 비기본 scope에서 7일 만료될 수 있다. 데모 직전에 재연동 절차를 점검한다.
- **Gmail**: `watch`는 최소 7일마다 갱신해야 하고 Google은 매일 갱신을 권장한다. 알림은 지연·유실될 수 있어 history 동기화와 404 시 full sync가 필요하다.
- **SmartThings**: OAuth protocol과 AgentCore custom provider의 `CLIENT_SECRET_BASIC`은 맞지만, 현재 개발자 등록 표면이 전환 중이다. 이것이 가장 큰 외부 일정 위험이다.
- 공식 CLI 저장소의 404 이슈는 한 개발자의 실패 보고이지 전체 계정에서 항상 실패한다는 증명은 아니다. 반대로 신형 콘솔 등록이 모든 계정에서 열렸다는 공식 확인도 없으므로, 두 신호를 합쳐 `불가`가 아니라 `초기 스파이크`로 판정했다.
- **모델**: Nova 2 Lite는 서울 단일 리전 inference가 아니라 global inference다. 단일 리전 데이터 경계가 필요해지면 배포 전에 모델을 다시 선택해야 한다. 한국어가 공식 최적화 언어이더라도 QuietPilot의 말투·날짜 해석·도구 인자 정확도는 별도 평가셋으로 검증해야 한다.
- **AgentCore**: CodeZip은 Linux ARM64 의존성을 요구한다. 로컬 macOS wheel을 그대로 zip하면 실패한다. AgentCore CLI/uv cross-platform packaging을 사용한다.
- **Expo**: 네이티브 기능은 막히지 않지만 네이티브 패키지를 추가할 때마다 개발 빌드를 다시 만들어야 한다. Android 운영체제의 force-stop·백그라운드 정책은 우회할 수 없다.

## 핵심 1차 출처

### Expo / React Native / Android

- [Expo SDK 57 reference](https://docs.expo.dev/versions/latest/)
- [Expo SDK 57 release notes](https://expo.dev/changelog/sdk-57)
- [Development builds introduction](https://docs.expo.dev/develop/development-builds/introduction/)
- [Customize with native code](https://docs.expo.dev/workflow/customizing/)
- [Expo notifications SDK 57](https://docs.expo.dev/versions/v57.0.0/sdk/notifications/)
- [Expo Android development environment](https://docs.expo.dev/get-started/set-up-your-environment?mode=development-build)
- [React Native environment and JDK recommendation](https://reactnative.dev/docs/set-up-your-environment)
- [Android 16 background behavior changes](https://developer.android.com/about/versions/16/behavior-changes-all)

### AWS / Strands / AgentCore

- [AgentCore supported Regions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html)
- [AgentCore Python direct code deployment](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy-python.html)
- [AgentCore asynchronous and long-running agents](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-long-run.html)
- [AgentCore Runtime inbound OAuth/IAM](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html)
- [AgentCore custom OAuth provider](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-add-oauth-client-custom.html)
- [AgentCore OAuth client authentication methods](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/client-auth-methods.html)
- [AgentCore OAuth return session binding](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/oauth2-authorization-url-session-binding.html)
- [GPT-5.6 Luna model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-56-luna.html)
- [GPT-5.6 cross-Region inference launch](https://aws.amazon.com/blogs/machine-learning/introducing-cross-region-inference-for-openai-gpt-5-6-models-on-amazon-bedrock/)
- [Nova 2 Lite model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-2-lite.html)
- [Strands Amazon Bedrock provider](https://strandsagents.com/docs/user-guide/concepts/model-providers/amazon-bedrock/)
- [Strands structured output](https://strandsagents.com/docs/user-guide/concepts/agents/structured-output/)
- [Strands hooks](https://strandsagents.com/docs/user-guide/concepts/agents/hooks/)
- [API Gateway HTTP API JWT authorizer](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html)
- [Lambda queue/stream at-least-once behavior](https://docs.aws.amazon.com/lambda/latest/dg/invocation-eventsourcemapping.html)
- [Powertools SQS batch processing](https://docs.powertools.aws.dev/lambda/python/latest/utilities/batch/)
- [DynamoDB conditional/transactional idempotency](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/BestPractices_ConditionalBatchUpdate.html)

### Google

- [Gmail push notifications](https://developers.google.com/workspace/gmail/api/guides/push)
- [Gmail synchronization](https://developers.google.com/gmail/api/guides/sync)
- [Authenticated Pub/Sub push](https://cloud.google.com/pubsub/docs/authenticate-push-subscriptions)
- [Gmail OAuth scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [Restricted-scope production verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)
- [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy)
- [Calendar event creation](https://developers.google.com/calendar/api/guides/create-events)
- [Google Tasks resource](https://developers.google.com/workspace/tasks/reference/rest/v1/tasks)

### SmartThings

- [SmartThings API authorization and permissions](https://developer.smartthings.com/docs/getting-started/authorization-and-permissions)
- [API Access App setup and current registration warning](https://developer.smartthings.com/docs/service-integrations/app-setup)
- [SmartThings OAuth flow](https://developer.smartthings.com/docs/service-integrations/oauth)
- [SmartThings token management](https://developer.smartthings.com/docs/service-integrations/token-management)
- [SmartThings webhook events](https://developer.smartthings.com/docs/service-integrations/webhook-events)
- [Official API app minimal example](https://github.com/SmartThingsCommunity/api-app-minimal-example-js)
- [Official SmartThings CLI](https://github.com/SmartThingsCommunity/smartthings-cli)
- [Current CLI OAuth app creation 404 report](https://github.com/SmartThingsCommunity/smartthings-cli/issues/836)

## 2026-08-24 Addendum — Android SMS 일정 발견과 자동 알림

### 결론

- 기능 방향은 채택한다: 허용된 문자에서 미래 생활 일정을 찾고, 스팸·스미싱·OTP·결제·의심 링크·중복을 제외한 뒤, 기존 저위험 정책과 정확히 일치하는 경우에만 취소 가능한 로컬 알림을 자동 등록한다.
- `READ_SMS`는 일반적인 runtime permission처럼 바로 추가하지 않는다. Android는 이를 dangerous이면서 hard-restricted permission으로 정의하고, Google Play는 기본 SMS/Assistant handler 또는 심사·승인된 예외를 요구한다.
- Google Play 정책 표에는 `Device automation`이 예외 후보로 존재하지만 case-by-case review 대상이다. QuietPilot이 이 예외를 받을 것이라고 가정하지 않는다.
- QuietPilot을 기본 SMS 앱으로 만드는 것은 현재 범위가 아니다. 기본 SMS handler는 실제 SMS 송수신 역할을 충족해야 하므로 일정 추출 기능을 위해 메시지 앱 전체를 대체하는 것은 과도한 제품 확장이다.
- 공개 배포 기준선은 사용자가 선택한 문자를 share/import하는 방식이다. Notification Listener는 사용자가 special access를 부여한 뒤 새로 게시되거나 아직 활성 상태인 알림을 볼 수 있지만, 삭제된 과거 SMS inbox를 복원하는 수단으로 표현하지 않는다.
- 알림 결과는 먼저 QuietPilot의 취소 가능한 local notification으로 만든다. Android 13+에서는 `POST_NOTIFICATIONS` runtime permission이 필요하다. 일반 일정 알림은 exact alarm이 필요하다고 가정하지 않으며, 정확한 시각 중단이 핵심인 경우에만 Alarms & reminders 특별 접근을 별도 검토한다.
- 시스템 Clock에 등록할 때는 `ACTION_SET_ALARM`과 `SET_ALARM` intent가 있고 `EXTRA_SKIP_UI`로 중간 UI 생략을 요청할 수 있지만 수신 Clock 앱 구현에 의존한다. 제품 기본값은 자체 local reminder이고 Clock/Calendar 쓰기는 별도 Action과 정책으로 유지한다.
- Calendar provider 직접 쓰기는 `WRITE_CALENDAR`가 필요하다. 사용자 확인 UI가 있는 insert Intent와 무인 직접 insert를 구분한다.

### 안전 분석 구조

```text
authorized local message input
  -> deterministic prefilter (OTP/payment/URL/expired/duplicate)
  -> Message Safety Analyst (sender class, spam/smishing, event/date, uncertainty)
  -> deterministic automation gate
  -> REVIEW/BLOCKED or policy-bound local reminder
  -> OS readback + audit + cancellable identifier
```

- 모델은 `ELIGIBLE`, `REVIEW`, `BLOCKED`와 근거를 제안하지만 권한을 만들지 않는다.
- 모델과 규칙이 충돌하거나 날짜·시간대가 모호하면 자동 등록하지 않는다.
- 원문은 기본 온디바이스 처리이며 cloud에는 최소 정규화 metadata만 전달한다. 원문·전체 전화번호를 로그, queue, push, analytics 또는 fine-tuning dataset에 넣지 않는다.
- 초기 기준선은 규칙 + 범용 모델 + 보수적 threshold다. 파인튜닝은 동의·익명화된 대표 평가셋에서 malicious recall, benign false-block rate, 날짜/시간 exact match, calibration, duplicate suppression과 auto-action precision을 측정한 뒤 baseline 부족이 증명될 때만 진행한다.

### 구현 상태와 중지점

- 현재 구현은 `externalSideEffects=false`인 로컬 frontend prototype fixture뿐이다.
- `READ_SMS`, Notification Listener, `POST_NOTIFICATIONS`, Calendar/Clock permission 또는 실제 알림 등록 코드는 이번 addendum에서 추가하지 않는다.
- 실제 SMS 접근은 입력 경로, Google Play 배포 전략, 개인정보 고지와 실기기 시험을 참가자가 별도로 승인한 뒤 진행한다.

### Android / Google Play 1차 출처

- [Google Play SMS and Call Log permissions policy](https://support.google.com/googleplay/android-developer/answer/10208820?hl=en)
- [Android default handler permission guide](https://developer.android.com/guide/topics/permissions/default-handlers)
- [Android Manifest.permission reference](https://developer.android.com/reference/android/Manifest.permission)
- [NotificationListenerService reference](https://developer.android.com/reference/android/service/notification/NotificationListenerService)
- [Android notification runtime permission](https://developer.android.com/develop/ui/compose/notifications/notification-permission)
- [Android alarm scheduling guidance](https://developer.android.com/develop/background-work/services/alarms)
- [AlarmClock intent reference](https://developer.android.com/reference/android/provider/AlarmClock)
- [Calendar provider overview](https://developer.android.com/identity/providers/calendar-provider)
