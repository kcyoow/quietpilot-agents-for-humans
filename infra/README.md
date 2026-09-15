# AWS infrastructure workspace

This workspace defines the local-only, pre-credit AWS foundation for QuietPilot.
It is deliberately environment agnostic: no account, region, credentials, context
lookup, deployment, or remote AWS call is required to test and synthesize it.

## Stacks

- `QuietPilotAuth`: email Cognito User Pool and public SRP mobile client
- `QuietPilotData`: DynamoDB main/idempotency tables and encrypted work queue/DLQ
- `QuietPilotApi`: authenticated HTTP API, Python 3.12 ARM64 Lambdas and SQS source
- `QuietPilotSchedule`: disabled Gmail renewal and recovery schedules with a DLQ
- `QuietPilotObservability`: API, Lambda, queue-age and DLQ CloudWatch alarms

Only the authenticated `ANY /v1` and `ANY /v1/{proxy+}` routes exist. Public
OAuth and webhook routes are intentionally deferred until their signature and
session-binding implementations are ready.

The routes require Cognito's `aws.cognito.signin.user.admin` access-token scope.
AWS documents this as the scope issued by `InitiateAuth`/SRP, so the API accepts
the public SRP client's access token while rejecting an ID token at the scope
check. A custom API scope would require the separate OAuth token endpoint flow.

## Local verification

From the repository root:

```sh
npm run typecheck -w quietpilot-infra
npm test -w quietpilot-infra
npm run synth -w quietpilot-infra
```

The synth command includes `--no-lookups`. It only produces CloudFormation and
Lambda asset manifests; it does not authenticate to AWS or create resources.

By default the Cognito User Pool, both DynamoDB tables, the work queue/DLQ and
the Scheduler DLQ are retained. The User Pool and main table also have deletion
protection. For a deliberately disposable sandbox deployment, pass
`-c ephemeral=true`; that changes those stateful resources to `DESTROY` and
disables deletion protection on the User Pool and main table. Never use that
context against valued data without a separate destructive-action review.

## Cloud gate

Do not run `aws login`, `cdk deploy`, invoke Bedrock/AgentCore, or otherwise use
chargeable AWS services until credits are verified or personal spend is
explicitly approved. Before that gate, local typecheck, assertions and synth are
the allowed proof surface.
