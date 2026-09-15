import {
  CfnOutput,
  Duration,
  RemovalPolicy,
  Stack,
  type StackProps,
} from "aws-cdk-lib";
import {
  AttributeType,
  BillingMode,
  ProjectionType,
  Table,
  TableEncryption,
} from "aws-cdk-lib/aws-dynamodb";
import { Queue, QueueEncryption } from "aws-cdk-lib/aws-sqs";
import type { Construct } from "constructs";

import { WORK_QUEUE_VISIBILITY_TIMEOUT_SECONDS } from "./runtime-config.js";

export interface DataStackProps extends StackProps {
  readonly ephemeral: boolean;
}
export class DataStack extends Stack {
  public readonly mainTable: Table;
  public readonly idempotencyTable: Table;
  public readonly workQueue: Queue;
  public readonly workDeadLetterQueue: Queue;

  public constructor(scope: Construct, id: string, props: DataStackProps) {
    const { ephemeral, ...stackProps } = props;
    super(scope, id, {
      ...stackProps,
      terminationProtection: !ephemeral,
    });

    const mainRemovalPolicy = ephemeral
      ? RemovalPolicy.DESTROY
      : RemovalPolicy.RETAIN;

    this.mainTable = new Table(this, "MainTable", {
      partitionKey: { name: "PK", type: AttributeType.STRING },
      sortKey: { name: "SK", type: AttributeType.STRING },
      timeToLiveAttribute: "expiresAt",
      billingMode: BillingMode.PAY_PER_REQUEST,
      encryption: TableEncryption.AWS_MANAGED,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: true,
      },
      deletionProtection: !ephemeral,
      removalPolicy: mainRemovalPolicy,
    });
    this.mainTable.addGlobalSecondaryIndex({
      indexName: "GSI1",
      partitionKey: { name: "GSI1PK", type: AttributeType.STRING },
      sortKey: { name: "GSI1SK", type: AttributeType.STRING },
      projectionType: ProjectionType.ALL,
    });
    this.mainTable.addGlobalSecondaryIndex({
      indexName: "GSI2",
      partitionKey: { name: "GSI2PK", type: AttributeType.STRING },
      sortKey: { name: "GSI2SK", type: AttributeType.STRING },
      projectionType: ProjectionType.ALL,
    });

    this.idempotencyTable = new Table(this, "IdempotencyTable", {
      partitionKey: { name: "subject", type: AttributeType.STRING },
      sortKey: { name: "idempotencyKey", type: AttributeType.STRING },
      timeToLiveAttribute: "expiresAt",
      billingMode: BillingMode.PAY_PER_REQUEST,
      encryption: TableEncryption.AWS_MANAGED,
      removalPolicy: mainRemovalPolicy,
    });

    this.workDeadLetterQueue = new Queue(this, "WorkDeadLetterQueue", {
      encryption: QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
      retentionPeriod: Duration.days(14),
      removalPolicy: mainRemovalPolicy,
    });
    this.workQueue = new Queue(this, "WorkQueue", {
      encryption: QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
      retentionPeriod: Duration.days(4),
      visibilityTimeout: Duration.seconds(
        WORK_QUEUE_VISIBILITY_TIMEOUT_SECONDS,
      ),
      removalPolicy: mainRemovalPolicy,
      deadLetterQueue: {
        queue: this.workDeadLetterQueue,
        maxReceiveCount: 5,
      },
    });

    new CfnOutput(this, "MainTableName", {
      value: this.mainTable.tableName,
    });
    new CfnOutput(this, "WorkQueueUrl", {
      value: this.workQueue.queueUrl,
    });
  }
}
