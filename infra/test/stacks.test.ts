import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { App, LegacyStackSynthesizer } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";

import { ApiStack } from "../lib/api-stack.js";
import { AuthStack } from "../lib/auth-stack.js";
import { DataStack } from "../lib/data-stack.js";
import { ObservabilityStack } from "../lib/observability-stack.js";
import { ScheduleStack } from "../lib/schedule-stack.js";

type CfnResource = {
  readonly Type: string;
  readonly Properties?: Record<string, unknown>;
  readonly DeletionPolicy?: string;
  readonly UpdateReplacePolicy?: string;
};

function createFoundation(
  ephemeral = false,
  agentCoreRuntimeArn?: string,
  googlePubSubServiceAccountEmail?: string,
) {
  const app = new App({
    context: { "@aws-cdk/core:defaultCrossStackReferences": "strong" },
  });
  const auth = new AuthStack(app, "TestAuth", { ephemeral });
  const data = new DataStack(app, "TestData", { ephemeral });
  const api = new ApiStack(app, "TestApi", {
    auth,
    data,
    agentCoreRuntimeArn,
    googlePubSubServiceAccountEmail,
  });
  const schedule = new ScheduleStack(app, "TestSchedule", {
    workerFunction: api.workerFunction,
    ephemeral,
    googleMaintenanceEnabled: Boolean(googlePubSubServiceAccountEmail),
  });
  const observability = new ObservabilityStack(app, "TestObservability", {
    httpApi: api.httpApi,
    controlFunction: api.controlFunction,
    workerFunction: api.workerFunction,
    workQueue: data.workQueue,
    workDeadLetterQueue: data.workDeadLetterQueue,
    schedulerDeadLetterQueue: schedule.schedulerDeadLetterQueue,
  });

  return {
    authStack: auth,
    auth: Template.fromStack(auth),
    dataStack: data,
    data: Template.fromStack(data),
    api: Template.fromStack(api),
    schedule: Template.fromStack(schedule),
    observability: Template.fromStack(observability),
  };
}

const foundation = createFoundation();

function resourcesOf(template: Template, type: string): CfnResource[] {
  return Object.values(template.findResources(type)) as CfnResource[];
}

function statementsOf(policy: CfnResource): Record<string, unknown>[] {
  const document = policy.Properties?.PolicyDocument as
    | { Statement?: Record<string, unknown>[] }
    | undefined;
  return document?.Statement ?? [];
}

function literalStrings(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(literalStrings);
  if (value !== null && typeof value === "object") {
    return Object.values(value).flatMap(literalStrings);
  }
  return [];
}

describe("QuietPilot AuthStack", () => {
  it("creates an email User Pool and public SRP client", () => {
    foundation.auth.resourceCountIs("AWS::Cognito::UserPool", 1);
    foundation.auth.hasResourceProperties("AWS::Cognito::UserPool", {
      UsernameAttributes: ["email"],
      AutoVerifiedAttributes: ["email"],
      EmailConfiguration: { EmailSendingAccount: "COGNITO_DEFAULT" },
      DeletionProtection: "ACTIVE",
      Schema: Match.arrayWith([
        { Mutable: true, Name: "email", Required: true },
        { Mutable: true, Name: "name", Required: true },
      ]),
    });
    foundation.auth.hasResourceProperties("AWS::Cognito::UserPoolClient", {
      GenerateSecret: false,
      AllowedOAuthFlowsUserPoolClient: false,
      ExplicitAuthFlows: Match.arrayWith([
        "ALLOW_USER_SRP_AUTH",
        "ALLOW_REFRESH_TOKEN_AUTH",
      ]),
      EnableTokenRevocation: true,
    });
    const client = resourcesOf(
      foundation.auth,
      "AWS::Cognito::UserPoolClient",
    )[0]!;
    assert.equal(client.Properties?.CallbackURLs, undefined);
    assert.equal(client.Properties?.AllowedOAuthFlows, undefined);
    foundation.auth.resourceCountIs("AWS::Cognito::UserPoolResourceServer", 0);

    const userPool = resourcesOf(foundation.auth, "AWS::Cognito::UserPool")[0]!;
    assert.equal(userPool.DeletionPolicy, "Retain");
    assert.equal(userPool.UpdateReplacePolicy, "Retain");
  });

  it("does not require CDK bootstrap resources for the assetless auth stack", () => {
    const template = foundation.auth.toJSON();
    assert.ok(
      foundation.authStack.synthesizer instanceof LegacyStackSynthesizer,
    );
    assert.equal(template.Parameters?.BootstrapVersion, undefined);
    assert.equal(template.Rules?.CheckBootstrapVersion, undefined);
  });

  it("allows destructive User Pool cleanup only with ephemeral=true", () => {
    const ephemeral = createFoundation(true).auth;
    const userPool = resourcesOf(ephemeral, "AWS::Cognito::UserPool")[0]!;
    assert.equal(userPool.DeletionPolicy, "Delete");
    assert.equal(userPool.UpdateReplacePolicy, "Delete");
    assert.equal(userPool.Properties?.DeletionProtection, "INACTIVE");
  });
});

describe("QuietPilot DataStack", () => {
  it("protects the default stack and disables protection only for ephemeral mode", () => {
    assert.equal(foundation.dataStack.terminationProtection, true);
    assert.equal(createFoundation(true).dataStack.terminationProtection, false);
  });

  it("creates the retained encrypted main table with PK/SK and two bounded GSIs", () => {
    const tables = resourcesOf(foundation.data, "AWS::DynamoDB::Table");
    assert.equal(tables.length, 2);
    const main = tables.find((table) =>
      JSON.stringify(table.Properties?.KeySchema).includes('"PK"'),
    );
    assert.ok(main);
    assert.equal(main.DeletionPolicy, "Retain");
    assert.deepEqual(main.Properties?.BillingMode, "PAY_PER_REQUEST");
    assert.deepEqual(main.Properties?.SSESpecification, { SSEEnabled: true });
    assert.deepEqual(main.Properties?.PointInTimeRecoverySpecification, {
      PointInTimeRecoveryEnabled: true,
    });
    assert.deepEqual(main.Properties?.TimeToLiveSpecification, {
      AttributeName: "expiresAt",
      Enabled: true,
    });
    assert.equal(main.Properties?.DeletionProtectionEnabled, true);
    assert.deepEqual(main.Properties?.KeySchema, [
      { AttributeName: "PK", KeyType: "HASH" },
      { AttributeName: "SK", KeyType: "RANGE" },
    ]);
    assert.deepEqual(main.Properties?.GlobalSecondaryIndexes, [
      {
        IndexName: "GSI1",
        KeySchema: [
          { AttributeName: "GSI1PK", KeyType: "HASH" },
          { AttributeName: "GSI1SK", KeyType: "RANGE" },
        ],
        Projection: { ProjectionType: "ALL" },
      },
      {
        IndexName: "GSI2",
        KeySchema: [
          { AttributeName: "GSI2PK", KeyType: "HASH" },
          { AttributeName: "GSI2SK", KeyType: "RANGE" },
        ],
        Projection: { ProjectionType: "ALL" },
      },
    ]);
  });

  it("creates a subject/idempotencyKey table with TTL", () => {
    foundation.data.hasResourceProperties("AWS::DynamoDB::Table", {
      KeySchema: [
        { AttributeName: "subject", KeyType: "HASH" },
        { AttributeName: "idempotencyKey", KeyType: "RANGE" },
      ],
      TimeToLiveSpecification: {
        AttributeName: "expiresAt",
        Enabled: true,
      },
    });
    const idempotency = resourcesOf(
      foundation.data,
      "AWS::DynamoDB::Table",
    ).find((table) =>
      JSON.stringify(table.Properties?.KeySchema).includes('"subject"'),
    );
    assert.ok(idempotency);
    assert.equal(idempotency.DeletionPolicy, "Retain");
    assert.equal(idempotency.UpdateReplacePolicy, "Retain");
    assert.equal(idempotency.Properties?.BillingMode, "PAY_PER_REQUEST");
    assert.deepEqual(idempotency.Properties?.SSESpecification, {
      SSEEnabled: true,
    });
  });

  it("creates SSE-SQS work/DLQ queues with SSL, redrive and visibility", () => {
    foundation.data.resourceCountIs("AWS::SQS::Queue", 2);
    foundation.data.hasResourceProperties("AWS::SQS::Queue", {
      SqsManagedSseEnabled: true,
      MessageRetentionPeriod: 345600,
      VisibilityTimeout: 720,
      RedrivePolicy: {
        deadLetterTargetArn: Match.anyValue(),
        maxReceiveCount: 5,
      },
    });
    foundation.data.hasResourceProperties("AWS::SQS::Queue", {
      SqsManagedSseEnabled: true,
      MessageRetentionPeriod: 1209600,
    });
    for (const queue of resourcesOf(foundation.data, "AWS::SQS::Queue")) {
      assert.equal(queue.DeletionPolicy, "Retain");
      assert.equal(queue.UpdateReplacePolicy, "Retain");
    }

    const queuePolicies = resourcesOf(foundation.data, "AWS::SQS::QueuePolicy");
    assert.equal(queuePolicies.length, 2);
    for (const policy of queuePolicies) {
      const deny = statementsOf(policy)[0]!;
      assert.equal(deny.Effect, "Deny");
      assert.deepEqual(deny.Condition, {
        Bool: { "aws:SecureTransport": "false" },
      });
    }
  });

  it("allows destructive main-table cleanup only with ephemeral=true", () => {
    const ephemeral = createFoundation(true).data;
    const main = resourcesOf(ephemeral, "AWS::DynamoDB::Table").find((table) =>
      JSON.stringify(table.Properties?.KeySchema).includes('"PK"'),
    );
    assert.ok(main);
    assert.equal(main.DeletionPolicy, "Delete");
    assert.equal(main.UpdateReplacePolicy, "Delete");
    assert.equal(main.Properties?.DeletionProtectionEnabled, false);
    for (const table of resourcesOf(ephemeral, "AWS::DynamoDB::Table")) {
      assert.equal(table.DeletionPolicy, "Delete");
      assert.equal(table.UpdateReplacePolicy, "Delete");
    }
    for (const queue of resourcesOf(ephemeral, "AWS::SQS::Queue")) {
      assert.equal(queue.DeletionPolicy, "Delete");
      assert.equal(queue.UpdateReplacePolicy, "Delete");
    }
  });
});

describe("QuietPilot ApiStack", () => {
  const runtimeArn =
    "arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:runtime/quietpilot_agent-abcdefghij";

  it("uses a Cognito JWT authorizer and protects every /v1 route with scope", () => {
    const authorizers = resourcesOf(
      foundation.api,
      "AWS::ApiGatewayV2::Authorizer",
    );
    assert.equal(authorizers.length, 1);
    assert.equal(authorizers[0]!.Properties?.AuthorizerType, "JWT");
    assert.deepEqual(authorizers[0]!.Properties?.IdentitySource, [
      "$request.header.Authorization",
    ]);
    const jwt = authorizers[0]!.Properties?.JwtConfiguration as {
      Audience: unknown[];
      Issuer: unknown;
    };
    assert.equal(jwt.Audience.length, 1);
    assert.match(JSON.stringify(jwt.Audience[0]), /MobileClient/);
    const issuer = JSON.stringify(jwt.Issuer);
    assert.match(issuer, /https:\/\/cognito-idp\./);
    assert.match(issuer, /AWS::Region/);
    assert.match(issuer, /\.amazonaws\.com\//);
    assert.match(issuer, /UserPool/);

    const routes = resourcesOf(foundation.api, "AWS::ApiGatewayV2::Route");
    assert.equal(routes.length, 3);
    const protectedRoutes = routes.filter((route) =>
      String(route.Properties?.RouteKey).startsWith("ANY /v1"),
    );
    assert.deepEqual(
      protectedRoutes.map((route) => route.Properties?.RouteKey).sort(),
      ["ANY /v1", "ANY /v1/{proxy+}"],
    );
    for (const route of protectedRoutes) {
      assert.equal(route.Properties?.AuthorizationType, "JWT");
      assert.deepEqual(route.Properties?.AuthorizationScopes, [
        "aws.cognito.signin.user.admin",
      ]);
      assert.ok(route.Properties?.AuthorizerId);
    }
    const callback = routes.find(
      (route) => route.Properties?.RouteKey === "GET /oauth/google/callback",
    );
    assert.ok(callback);
    assert.equal(callback.Properties?.AuthorizationType, "NONE");
    assert.equal(callback.Properties?.AuthorizerId, undefined);
  });

  it("keeps Google Pub/Sub ingress absent unless an exact service account is configured", () => {
    const routes = resourcesOf(foundation.api, "AWS::ApiGatewayV2::Route");
    assert.equal(
      routes.some(
        (route) =>
          route.Properties?.RouteKey === "POST /webhooks/google/pubsub",
      ),
      false,
    );
    const ingress = resourcesOf(foundation.api, "AWS::Lambda::Function").find(
      (resource) =>
        resource.Properties?.Handler === "quietpilot_ingress.handlers.handler",
    );
    assert.ok(ingress);
    const environment = ingress.Properties?.Environment as {
      Variables: Record<string, unknown>;
    };
    assert.equal(environment.Variables.WORK_QUEUE_URL, undefined);
    assert.equal(
      environment.Variables.GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL,
      undefined,
    );
  });

  it("adds authenticated Google Pub/Sub ingress with only query and queue permissions", () => {
    const serviceAccount =
      "quietpilot-push@quietpilot-demo.iam.gserviceaccount.com";
    const configured = createFoundation(false, undefined, serviceAccount).api;
    const authorizers = resourcesOf(
      configured,
      "AWS::ApiGatewayV2::Authorizer",
    );
    assert.equal(authorizers.length, 2);
    const googleAuthorizer = authorizers.find(
      (authorizer) => authorizer.Properties?.Name === "GooglePubSubAuthorizer",
    );
    assert.ok(googleAuthorizer);
    const jwt = googleAuthorizer.Properties?.JwtConfiguration as {
      Audience: unknown[];
      Issuer: unknown;
    };
    assert.equal(jwt.Issuer, "https://accounts.google.com");
    assert.equal(jwt.Audience.length, 1);
    assert.match(JSON.stringify(jwt.Audience[0]), /\/webhooks\/google\/pubsub/);

    const pubsubRoute = resourcesOf(
      configured,
      "AWS::ApiGatewayV2::Route",
    ).find(
      (route) => route.Properties?.RouteKey === "POST /webhooks/google/pubsub",
    );
    assert.ok(pubsubRoute);
    assert.equal(pubsubRoute.Properties?.AuthorizationType, "JWT");
    assert.ok(pubsubRoute.Properties?.AuthorizerId);

    const ingress = resourcesOf(configured, "AWS::Lambda::Function").find(
      (resource) =>
        resource.Properties?.Handler === "quietpilot_ingress.handlers.handler",
    );
    assert.ok(ingress);
    const environment = ingress.Properties?.Environment as {
      Variables: Record<string, unknown>;
    };
    assert.equal(
      environment.Variables.GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL,
      serviceAccount,
    );
    assert.ok(environment.Variables.WORK_QUEUE_URL);

    const ingressPolicy = resourcesOf(configured, "AWS::IAM::Policy").find(
      (policy) =>
        statementsOf(policy).some((statement) =>
          literalStrings(statement.Action).includes("dynamodb:Query"),
        ) &&
        statementsOf(policy).some((statement) =>
          literalStrings(statement.Action).includes("sqs:SendMessage"),
        ) &&
        statementsOf(policy).every(
          (statement) =>
            !literalStrings(statement.Action).includes("dynamodb:UpdateItem"),
        ),
    );
    assert.ok(ingressPolicy);
    const statements = statementsOf(ingressPolicy);
    const query = statements.find((statement) =>
      literalStrings(statement.Action).includes("dynamodb:Query"),
    );
    assert.ok(query);
    assert.match(JSON.stringify(query.Resource), /index\/GSI1/);
    const send = statements.find((statement) =>
      literalStrings(statement.Action).includes("sqs:SendMessage"),
    );
    assert.ok(send);
    assert.doesNotMatch(JSON.stringify(send.Resource), /\*/);

    const sendStatements = resourcesOf(configured, "AWS::IAM::Policy")
      .flatMap(statementsOf)
      .filter((statement) =>
        literalStrings(statement.Action).includes("sqs:SendMessage"),
      );
    assert.equal(sendStatements.length, 3);
    for (const statement of sendStatements) {
      assert.doesNotMatch(JSON.stringify(statement.Resource), /\*/);
    }
  });

  it("rejects broad or malformed Google Pub/Sub identities", () => {
    for (const invalid of [
      "*",
      "person@example.com",
      "quietpilot-push@*.iam.gserviceaccount.com",
    ]) {
      assert.throws(
        () => createFoundation(false, undefined, invalid),
        /must be one exact Google Cloud service account email/,
      );
    }
  });

  it("configures only Python 3.12 ARM64 functions with JSON logs", () => {
    const functions = resourcesOf(foundation.api, "AWS::Lambda::Function");
    assert.equal(functions.length, 3);
    assert.deepEqual(functions.map((fn) => fn.Properties?.Handler).sort(), [
      "quietpilot_control_api.handlers.handler",
      "quietpilot_ingress.handlers.handler",
      "quietpilot_worker.consumer.handler",
    ]);
    for (const fn of functions) {
      assert.equal(fn.Properties?.Runtime, "python3.12");
      assert.deepEqual(fn.Properties?.Architectures, ["arm64"]);
      assert.equal(fn.Properties?.ReservedConcurrentExecutions, undefined);
      const logging = fn.Properties?.LoggingConfig as Record<string, unknown>;
      assert.ok(logging);
      assert.equal(logging.LogFormat, "JSON");
      assert.ok(logging.LogGroup);
    }
    const worker = functions.find(
      (fn) => fn.Properties?.Handler === "quietpilot_worker.consumer.handler",
    );
    assert.equal(worker?.Properties?.Timeout, 120);
    assert.equal(worker?.Properties?.RecursiveLoop, "Allow");
    for (const fn of functions.filter((item) => item !== worker)) {
      assert.equal(fn.Properties?.RecursiveLoop, undefined);
    }

    foundation.api.resourceCountIs("AWS::Logs::LogGroup", 4);
    for (const logGroup of resourcesOf(foundation.api, "AWS::Logs::LogGroup")) {
      assert.equal(logGroup.Properties?.RetentionInDays, 7);
    }
  });

  it("uses an explicit access-log allowlist on the default stage", () => {
    const stage = resourcesOf(foundation.api, "AWS::ApiGatewayV2::Stage")[0]!;
    assert.equal(stage.Properties?.StageName, "$default");
    assert.equal(stage.Properties?.AutoDeploy, true);
    const accessLogs = stage.Properties?.AccessLogSettings as Record<
      string,
      unknown
    >;
    const format = JSON.parse(String(accessLogs.Format)) as Record<
      string,
      unknown
    >;
    assert.deepEqual(Object.keys(format).sort(), [
      "requestId",
      "responseLength",
      "routeKey",
      "status",
    ]);
    assert.doesNotMatch(
      JSON.stringify(format),
      /authorizer|token|claims|body/i,
    );
  });

  it("enables SQS partial batch failure reporting", () => {
    foundation.api.hasResourceProperties("AWS::Lambda::EventSourceMapping", {
      BatchSize: 10,
      FunctionResponseTypes: ["ReportBatchItemFailures"],
    });
  });

  it("uses custom Lambda roles and no unconstrained IAM allow", () => {
    const roles = resourcesOf(foundation.api, "AWS::IAM::Role");
    assert.equal(roles.length, 3);
    for (const role of roles) {
      assert.equal(role.Properties?.ManagedPolicyArns, undefined);
      const trust = role.Properties?.AssumeRolePolicyDocument as {
        Statement: Record<string, unknown>[];
      };
      assert.equal(
        (trust.Statement[0]!.Principal as { Service: string }).Service,
        "lambda.amazonaws.com",
      );
    }

    for (const policy of resourcesOf(foundation.api, "AWS::IAM::Policy")) {
      for (const statement of statementsOf(policy)) {
        if (statement.Effect !== "Allow") continue;
        assert.notEqual(statement.Action, "*");
        assert.notEqual(statement.Resource, "*");
        assert.equal(literalStrings(statement.Action).includes("*"), false);
      }
    }
  });

  it("fails closed when no AgentCore Runtime ARN is configured", () => {
    const controlFunction = resourcesOf(
      foundation.api,
      "AWS::Lambda::Function",
    ).find(
      (resource) =>
        resource.Properties?.Handler ===
        "quietpilot_control_api.handlers.handler",
    );
    assert.ok(controlFunction);
    const environment = controlFunction.Properties?.Environment as {
      Variables: Record<string, unknown>;
    };
    assert.equal(environment.Variables.AGENTCORE_RUNTIME_ARN, undefined);

    const actions = resourcesOf(foundation.api, "AWS::IAM::Policy")
      .flatMap(statementsOf)
      .flatMap((statement) => literalStrings(statement.Action));
    assert.equal(
      actions.includes("bedrock-agentcore:InvokeAgentRuntime"),
      false,
    );
    assert.equal(
      actions.includes("bedrock-agentcore:InvokeAgentRuntimeForUser"),
      false,
    );

    const outputs = (foundation.api.toJSON() as { Outputs?: object }).Outputs;
    assert.equal(outputs && "AgentCoreRuntimeArn" in outputs, false);
  });

  it("grants delegated per-user invocation on only the exact Runtime and DEFAULT endpoint", () => {
    const configured = createFoundation(false, runtimeArn).api;
    const controlFunction = resourcesOf(
      configured,
      "AWS::Lambda::Function",
    ).find(
      (resource) =>
        resource.Properties?.Handler ===
        "quietpilot_control_api.handlers.handler",
    );
    assert.ok(controlFunction);
    const environment = controlFunction.Properties?.Environment as {
      Variables: Record<string, unknown>;
    };
    assert.equal(environment.Variables.AGENTCORE_RUNTIME_ARN, runtimeArn);

    const invokeStatements = resourcesOf(configured, "AWS::IAM::Policy")
      .flatMap(statementsOf)
      .filter((statement) =>
        literalStrings(statement.Action).includes(
          "bedrock-agentcore:InvokeAgentRuntime",
        ),
      );
    assert.equal(invokeStatements.length, 2);
    for (const statement of invokeStatements) {
      assert.deepEqual(statement.Resource, [
        runtimeArn,
        `${runtimeArn}/runtime-endpoint/DEFAULT`,
      ]);
      assert.deepEqual(literalStrings(statement.Action).sort(), [
        "bedrock-agentcore:InvokeAgentRuntime",
        "bedrock-agentcore:InvokeAgentRuntimeForUser",
      ]);
    }

    const completeStatement = resourcesOf(configured, "AWS::IAM::Policy")
      .flatMap(statementsOf)
      .find((statement) =>
        literalStrings(statement.Action).includes(
          "bedrock-agentcore:CompleteResourceTokenAuth",
        ),
      );
    assert.ok(completeStatement);
    const completeResources = completeStatement.Resource as unknown[];
    assert.equal(completeResources.length, 4);
    const serializedCompleteResources = JSON.stringify(completeResources);
    assert.match(
      serializedCompleteResources,
      /token-vault\/default\/oauth2credentialprovider\/quietpilot-google/,
    );
    assert.match(
      serializedCompleteResources,
      /workload-identity-directory\/default\/workload-identity\/quietpilot_agent-abcdefghij/,
    );

    const controlSecretStatements = resourcesOf(configured, "AWS::IAM::Policy")
      .flatMap(statementsOf)
      .filter(
        (statement) =>
          literalStrings(statement.Action).includes(
            "secretsmanager:GetSecretValue",
          ) &&
          JSON.stringify(statement.Resource).includes(
            "bedrock-agentcore-identity!default/oauth2/quietpilot-google-*",
          ),
      );
    assert.equal(controlSecretStatements.length, 1);
    const serializedControlSecret = JSON.stringify(
      controlSecretStatements[0]!.Resource,
    );
    assert.match(
      serializedControlSecret,
      /secret:bedrock-agentcore-identity!default\/oauth2\/quietpilot-google-\*/,
    );
    assert.doesNotMatch(serializedControlSecret, /secret:\*/);

    const outputs = (
      configured.toJSON() as {
        Outputs: Record<string, { Value: unknown }>;
      }
    ).Outputs;
    assert.equal(outputs.AgentCoreRuntimeArn?.Value, runtimeArn);
  });

  it("rejects wildcard or non-Runtime AgentCore ARNs", () => {
    assert.throws(
      () =>
        createFoundation(
          false,
          "arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:runtime/*",
        ),
      /must be one exact Amazon Bedrock AgentCore Runtime ARN/,
    );
    assert.throws(
      () =>
        createFoundation(
          false,
          "arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:gateway/example",
        ),
      /must be one exact Amazon Bedrock AgentCore Runtime ARN/,
    );
  });
});

describe("QuietPilot ScheduleStack", () => {
  it("creates two disabled stable Scheduler schedules with bounded retries", () => {
    const schedules = resourcesOf(
      foundation.schedule,
      "AWS::Scheduler::Schedule",
    );
    assert.equal(schedules.length, 2);
    for (const schedule of schedules) {
      assert.equal(schedule.Properties?.State, "DISABLED");
      assert.deepEqual(schedule.Properties?.FlexibleTimeWindow, {
        Mode: "OFF",
      });
      const target = schedule.Properties?.Target as Record<string, unknown>;
      assert.deepEqual(target.RetryPolicy, {
        MaximumEventAgeInSeconds: 3600,
        MaximumRetryAttempts: 2,
      });
      assert.ok(target.DeadLetterConfig);
    }
    assert.deepEqual(
      schedules
        .map((schedule) => {
          const target = schedule.Properties?.Target as Record<string, unknown>;
          return JSON.parse(String(target.Input)) as Record<string, unknown>;
        })
        .sort((left, right) =>
          String(left.maintenance_task).localeCompare(
            String(right.maintenance_task),
          ),
        ),
      [
        { maintenance_task: "RECOVER_CONNECTION_SYNC" },
        { maintenance_task: "RENEW_GMAIL_WATCHES" },
      ],
    );
  });

  it("enables both maintenance schedules only with configured Google ingress", () => {
    const configured = createFoundation(
      false,
      undefined,
      "quietpilot-push@quietpilot-demo.iam.gserviceaccount.com",
    ).schedule;
    const schedules = resourcesOf(configured, "AWS::Scheduler::Schedule");
    assert.equal(schedules.length, 2);
    for (const schedule of schedules) {
      assert.equal(schedule.Properties?.State, "ENABLED");
    }
  });

  it("uses a separate SSE-SQS, SSL-enforced scheduler DLQ", () => {
    foundation.schedule.resourceCountIs("AWS::SQS::Queue", 1);
    foundation.schedule.hasResourceProperties("AWS::SQS::Queue", {
      SqsManagedSseEnabled: true,
      MessageRetentionPeriod: 1209600,
    });
    const queue = resourcesOf(foundation.schedule, "AWS::SQS::Queue")[0]!;
    assert.equal(queue.DeletionPolicy, "Retain");
    assert.equal(queue.UpdateReplacePolicy, "Retain");
    const policy = resourcesOf(
      foundation.schedule,
      "AWS::SQS::QueuePolicy",
    )[0]!;
    assert.deepEqual(statementsOf(policy)[0]!.Condition, {
      Bool: { "aws:SecureTransport": "false" },
    });
  });

  it("allows scheduler DLQ cleanup only with ephemeral=true", () => {
    const queue = resourcesOf(
      createFoundation(true).schedule,
      "AWS::SQS::Queue",
    )[0]!;
    assert.equal(queue.DeletionPolicy, "Delete");
    assert.equal(queue.UpdateReplacePolicy, "Delete");
  });

  it("does not grant a wildcard IAM action or wildcard resource", () => {
    for (const policy of resourcesOf(foundation.schedule, "AWS::IAM::Policy")) {
      for (const statement of statementsOf(policy)) {
        assert.equal(literalStrings(statement.Action).includes("*"), false);
        assert.notEqual(statement.Resource, "*");
      }
    }
  });
});

describe("QuietPilot ObservabilityStack", () => {
  it("alarms on API 5xx, Lambda errors/throttles, queue age and both DLQs", () => {
    const alarms = resourcesOf(
      foundation.observability,
      "AWS::CloudWatch::Alarm",
    );
    assert.equal(alarms.length, 8);
    const metricNames = alarms
      .map((alarm) => String(alarm.Properties?.MetricName))
      .sort();
    assert.deepEqual(metricNames, [
      "5xx",
      "ApproximateAgeOfOldestMessage",
      "ApproximateNumberOfMessagesVisible",
      "ApproximateNumberOfMessagesVisible",
      "Errors",
      "Errors",
      "Throttles",
      "Throttles",
    ]);
    for (const alarm of alarms) {
      assert.equal(alarm.Properties?.TreatMissingData, "notBreaching");
    }
  });
});
