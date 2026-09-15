import * as path from "node:path";

import {
  ArnFormat,
  CfnOutput,
  Duration,
  RemovalPolicy,
  Stack,
  type StackProps,
} from "aws-cdk-lib";
import { AccessLogFormat } from "aws-cdk-lib/aws-apigateway";
import {
  HttpApi,
  HttpMethod,
  HttpStage,
  LogGroupLogDestination,
} from "aws-cdk-lib/aws-apigatewayv2";
import {
  HttpJwtAuthorizer,
  HttpUserPoolAuthorizer,
} from "aws-cdk-lib/aws-apigatewayv2-authorizers";
import { HttpLambdaIntegration } from "aws-cdk-lib/aws-apigatewayv2-integrations";
import {
  Effect,
  Policy,
  PolicyStatement,
  Role,
  ServicePrincipal,
} from "aws-cdk-lib/aws-iam";
import {
  ApplicationLogLevel,
  Architecture,
  Code,
  Function,
  LoggingFormat,
  RecursiveLoop,
  Runtime,
  SystemLogLevel,
} from "aws-cdk-lib/aws-lambda";
import { SqsEventSource } from "aws-cdk-lib/aws-lambda-event-sources";
import { LogGroup, RetentionDays } from "aws-cdk-lib/aws-logs";
import type { Construct } from "constructs";

import type { AuthStack } from "./auth-stack.js";
import type { DataStack } from "./data-stack.js";
import { WORKER_TIMEOUT_SECONDS } from "./runtime-config.js";

export interface ApiStackProps extends StackProps {
  readonly auth: AuthStack;
  readonly data: DataStack;
  readonly agentCoreRuntimeArn?: string;
  readonly googlePubSubServiceAccountEmail?: string;
}

const AGENTCORE_RUNTIME_ARN_PATTERN =
  /^arn:[a-z0-9-]+:bedrock-agentcore:[a-z0-9-]+:[0-9]{12}:runtime\/[^/*?\s]+$/;
const GOOGLE_SERVICE_ACCOUNT_EMAIL_PATTERN =
  /^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$/;

function exactAgentCoreRuntimeArn(runtimeArn: string): string {
  const normalized = runtimeArn.trim();
  if (!AGENTCORE_RUNTIME_ARN_PATTERN.test(normalized)) {
    throw new Error(
      "agentCoreRuntimeArn must be one exact Amazon Bedrock AgentCore Runtime ARN",
    );
  }
  return normalized;
}

function exactGoogleServiceAccountEmail(email: string): string {
  const normalized = email.trim().toLowerCase();
  if (!GOOGLE_SERVICE_ACCOUNT_EMAIL_PATTERN.test(normalized)) {
    throw new Error(
      "googlePubSubServiceAccountEmail must be one exact Google Cloud service account email",
    );
  }
  return normalized;
}

function logWritePolicy(logGroup: LogGroup): PolicyStatement {
  return new PolicyStatement({
    effect: Effect.ALLOW,
    actions: ["logs:CreateLogStream", "logs:PutLogEvents"],
    resources: [`${logGroup.logGroupArn}:*`],
  });
}

export class ApiStack extends Stack {
  public readonly httpApi: HttpApi;
  public readonly controlFunction: Function;
  public readonly workerFunction: Function;

  public constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);

    const agentCoreRuntimeArn = props.agentCoreRuntimeArn
      ? exactAgentCoreRuntimeArn(props.agentCoreRuntimeArn)
      : undefined;
    const googlePubSubServiceAccountEmail =
      props.googlePubSubServiceAccountEmail !== undefined
        ? exactGoogleServiceAccountEmail(props.googlePubSubServiceAccountEmail)
        : undefined;

    const controlLogGroup = new LogGroup(this, "ControlFunctionLogs", {
      retention: RetentionDays.ONE_WEEK,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const workerLogGroup = new LogGroup(this, "WorkerFunctionLogs", {
      retention: RetentionDays.ONE_WEEK,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const ingressLogGroup = new LogGroup(this, "IngressFunctionLogs", {
      retention: RetentionDays.ONE_WEEK,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const accessLogGroup = new LogGroup(this, "HttpApiAccessLogs", {
      retention: RetentionDays.ONE_WEEK,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const controlRole = new Role(this, "ControlFunctionRole", {
      assumedBy: new ServicePrincipal("lambda.amazonaws.com"),
      description:
        "Least-privilege execution role for the QuietPilot control API",
    });
    const workerRole = new Role(this, "WorkerFunctionRole", {
      assumedBy: new ServicePrincipal("lambda.amazonaws.com"),
      description:
        "Least-privilege execution role for the QuietPilot queue worker",
    });
    const ingressRole = new Role(this, "IngressFunctionRole", {
      assumedBy: new ServicePrincipal("lambda.amazonaws.com"),
      description:
        "Least-privilege execution role for token-free provider callbacks",
    });
    const ingressPolicyStatements = [
      logWritePolicy(ingressLogGroup),
      new PolicyStatement({
        actions: ["dynamodb:GetItem", "dynamodb:PutItem"],
        resources: [props.data.mainTable.tableArn],
      }),
    ];
    if (googlePubSubServiceAccountEmail) {
      ingressPolicyStatements.push(
        new PolicyStatement({
          actions: ["dynamodb:Query"],
          resources: [`${props.data.mainTable.tableArn}/index/GSI1`],
        }),
        new PolicyStatement({
          actions: ["sqs:SendMessage"],
          resources: [props.data.workQueue.queueArn],
        }),
      );
    }
    new Policy(this, "IngressFunctionPolicy", {
      roles: [ingressRole],
      statements: ingressPolicyStatements,
    });

    this.httpApi = new HttpApi(this, "HttpApi", {
      apiName: "QuietPilot control API",
      createDefaultStage: false,
    });
    const googleOauthReturnUrl = `${this.httpApi.apiEndpoint}/oauth/google/callback`;

    const controlPolicyStatements = [
      logWritePolicy(controlLogGroup),
      new PolicyStatement({
        actions: [
          "dynamodb:ConditionCheckItem",
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:Query",
          "dynamodb:TransactWriteItems",
          "dynamodb:UpdateItem",
        ],
        resources: [
          props.data.mainTable.tableArn,
          `${props.data.mainTable.tableArn}/index/GSI1`,
          props.data.idempotencyTable.tableArn,
        ],
      }),
      new PolicyStatement({
        actions: ["sqs:SendMessage"],
        resources: [props.data.workQueue.queueArn],
      }),
    ];
    if (agentCoreRuntimeArn) {
      const runtimeId = agentCoreRuntimeArn.split("/", 2)[1];
      if (!runtimeId) {
        throw new Error("agentCoreRuntimeArn must contain a Runtime ID");
      }
      controlPolicyStatements.push(
        new PolicyStatement({
          actions: [
            "bedrock-agentcore:InvokeAgentRuntime",
            "bedrock-agentcore:InvokeAgentRuntimeForUser",
          ],
          resources: [
            agentCoreRuntimeArn,
            `${agentCoreRuntimeArn}/runtime-endpoint/DEFAULT`,
          ],
        }),
        new PolicyStatement({
          actions: ["bedrock-agentcore:CompleteResourceTokenAuth"],
          resources: [
            this.formatArn({
              service: "bedrock-agentcore",
              resource: "token-vault",
              resourceName:
                "default/oauth2credentialprovider/quietpilot-google",
            }),
            this.formatArn({
              service: "bedrock-agentcore",
              resource: "token-vault",
              resourceName: "default",
            }),
            this.formatArn({
              service: "bedrock-agentcore",
              resource: "workload-identity-directory",
              resourceName: `default/workload-identity/${runtimeId}`,
            }),
            this.formatArn({
              service: "bedrock-agentcore",
              resource: "workload-identity-directory",
              resourceName: "default",
            }),
          ],
        }),
        new PolicyStatement({
          actions: ["secretsmanager:GetSecretValue"],
          resources: [
            this.formatArn({
              service: "secretsmanager",
              resource: "secret",
              resourceName:
                "bedrock-agentcore-identity!default/oauth2/quietpilot-google-*",
              arnFormat: ArnFormat.COLON_RESOURCE_NAME,
            }),
          ],
        }),
      );
    }
    new Policy(this, "ControlFunctionPolicy", {
      roles: [controlRole],
      statements: controlPolicyStatements,
    });

    const workerPolicyStatements = [
      logWritePolicy(workerLogGroup),
      new PolicyStatement({
        actions: [
          "dynamodb:ConditionCheckItem",
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:Query",
          "dynamodb:TransactWriteItems",
          "dynamodb:UpdateItem",
        ],
        resources: [
          props.data.mainTable.tableArn,
          `${props.data.mainTable.tableArn}/index/GSI1`,
          ...(googlePubSubServiceAccountEmail
            ? [`${props.data.mainTable.tableArn}/index/GSI2`]
            : []),
          props.data.idempotencyTable.tableArn,
        ],
      }),
    ];
    if (googlePubSubServiceAccountEmail) {
      workerPolicyStatements.push(
        new PolicyStatement({
          actions: ["sqs:SendMessage"],
          resources: [props.data.workQueue.queueArn],
        }),
      );
    }
    if (agentCoreRuntimeArn) {
      workerPolicyStatements.push(
        new PolicyStatement({
          actions: [
            "bedrock-agentcore:InvokeAgentRuntime",
            "bedrock-agentcore:InvokeAgentRuntimeForUser",
          ],
          resources: [
            agentCoreRuntimeArn,
            `${agentCoreRuntimeArn}/runtime-endpoint/DEFAULT`,
          ],
        }),
      );
    }
    new Policy(this, "WorkerFunctionPolicy", {
      roles: [workerRole],
      statements: workerPolicyStatements,
    });

    const controlEnvironment: Record<string, string> = {
      MAIN_TABLE_NAME: props.data.mainTable.tableName,
      IDEMPOTENCY_TABLE_NAME: props.data.idempotencyTable.tableName,
      WORK_QUEUE_URL: props.data.workQueue.queueUrl,
      GOOGLE_OAUTH_RETURN_URL: googleOauthReturnUrl,
    };
    if (agentCoreRuntimeArn) {
      controlEnvironment.AGENTCORE_RUNTIME_ARN = agentCoreRuntimeArn;
    }
    const workerEnvironment: Record<string, string> = {
      MAIN_TABLE_NAME: props.data.mainTable.tableName,
      IDEMPOTENCY_TABLE_NAME: props.data.idempotencyTable.tableName,
      WORK_QUEUE_URL: props.data.workQueue.queueUrl,
    };
    if (agentCoreRuntimeArn) {
      workerEnvironment.AGENTCORE_RUNTIME_ARN = agentCoreRuntimeArn;
    }

    this.controlFunction = new Function(this, "ControlFunction", {
      runtime: Runtime.PYTHON_3_12,
      architecture: Architecture.ARM_64,
      handler: "quietpilot_control_api.handlers.handler",
      code: Code.fromAsset(
        path.resolve(__dirname, "../../services/control-api/src"),
        { exclude: ["**/__pycache__/**", "**/*.pyc"] },
      ),
      role: controlRole,
      timeout: Duration.seconds(29),
      memorySize: 512,
      logGroup: controlLogGroup,
      loggingFormat: LoggingFormat.JSON,
      applicationLogLevelV2: ApplicationLogLevel.INFO,
      systemLogLevelV2: SystemLogLevel.WARN,
      environment: controlEnvironment,
    });

    this.workerFunction = new Function(this, "WorkerFunction", {
      runtime: Runtime.PYTHON_3_12,
      architecture: Architecture.ARM_64,
      handler: "quietpilot_worker.consumer.handler",
      code: Code.fromAsset(
        path.resolve(__dirname, "../../services/worker/src"),
        {
          exclude: ["**/__pycache__/**", "**/*.pyc"],
        },
      ),
      role: workerRole,
      timeout: Duration.seconds(WORKER_TIMEOUT_SECONDS),
      memorySize: 1024,
      logGroup: workerLogGroup,
      loggingFormat: LoggingFormat.JSON,
      applicationLogLevelV2: ApplicationLogLevel.INFO,
      systemLogLevelV2: SystemLogLevel.WARN,
      recursiveLoop: RecursiveLoop.ALLOW,
      environment: workerEnvironment,
    });
    this.workerFunction.addEventSource(
      new SqsEventSource(props.data.workQueue, {
        batchSize: 10,
        reportBatchItemFailures: true,
      }),
    );

    const ingressEnvironment: Record<string, string> = {
      MAIN_TABLE_NAME: props.data.mainTable.tableName,
    };
    if (googlePubSubServiceAccountEmail) {
      ingressEnvironment.WORK_QUEUE_URL = props.data.workQueue.queueUrl;
      ingressEnvironment.GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL =
        googlePubSubServiceAccountEmail;
    }
    const ingressFunction = new Function(this, "IngressFunction", {
      runtime: Runtime.PYTHON_3_12,
      architecture: Architecture.ARM_64,
      handler: "quietpilot_ingress.handlers.handler",
      code: Code.fromAsset(
        path.resolve(__dirname, "../../services/ingress/src"),
        {
          exclude: ["**/__pycache__/**", "**/*.pyc"],
        },
      ),
      role: ingressRole,
      timeout: Duration.seconds(5),
      memorySize: 256,
      logGroup: ingressLogGroup,
      loggingFormat: LoggingFormat.JSON,
      applicationLogLevelV2: ApplicationLogLevel.INFO,
      systemLogLevelV2: SystemLogLevel.WARN,
      environment: ingressEnvironment,
    });

    const authorizer = new HttpUserPoolAuthorizer(
      "ControlApiAuthorizer",
      props.auth.userPool,
      { userPoolClients: [props.auth.userPoolClient] },
    );
    const defaultStage = new HttpStage(this, "DefaultStage", {
      httpApi: this.httpApi,
      stageName: "$default",
      autoDeploy: true,
      accessLogSettings: {
        destination: new LogGroupLogDestination(accessLogGroup),
        format: AccessLogFormat.custom(
          JSON.stringify({
            requestId: "$context.requestId",
            routeKey: "$context.routeKey",
            status: "$context.status",
            responseLength: "$context.responseLength",
          }),
        ),
      },
    });

    const controlIntegration = new HttpLambdaIntegration(
      "ControlIntegration",
      this.controlFunction,
    );
    for (const pathPattern of ["/v1", "/v1/{proxy+}"]) {
      this.httpApi.addRoutes({
        path: pathPattern,
        methods: [HttpMethod.ANY],
        integration: controlIntegration,
        authorizer,
        authorizationScopes: [props.auth.authorizationScope],
      });
    }
    this.httpApi.addRoutes({
      path: "/oauth/google/callback",
      methods: [HttpMethod.GET],
      integration: new HttpLambdaIntegration(
        "GoogleOauthCallbackIntegration",
        ingressFunction,
      ),
    });
    if (googlePubSubServiceAccountEmail) {
      const googlePubSubEndpoint = `${this.httpApi.apiEndpoint}/webhooks/google/pubsub`;
      const googlePubSubAuthorizer = new HttpJwtAuthorizer(
        "GooglePubSubAuthorizer",
        "https://accounts.google.com",
        { jwtAudience: [googlePubSubEndpoint] },
      );
      this.httpApi.addRoutes({
        path: "/webhooks/google/pubsub",
        methods: [HttpMethod.POST],
        integration: new HttpLambdaIntegration(
          "GooglePubSubIntegration",
          ingressFunction,
        ),
        authorizer: googlePubSubAuthorizer,
      });
      new CfnOutput(this, "GooglePubSubEndpoint", {
        value: googlePubSubEndpoint,
      });
      new CfnOutput(this, "GooglePubSubAudience", {
        value: googlePubSubEndpoint,
      });
    }

    new CfnOutput(this, "ControlApiUrl", { value: defaultStage.url });
    new CfnOutput(this, "GoogleOauthReturnUrl", {
      value: googleOauthReturnUrl,
    });
    if (agentCoreRuntimeArn) {
      new CfnOutput(this, "AgentCoreRuntimeArn", {
        value: agentCoreRuntimeArn,
        description: "AgentCore Runtime configured for the control API",
      });
    }
  }
}
