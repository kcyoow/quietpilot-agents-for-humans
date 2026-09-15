import { Duration, RemovalPolicy, Stack, type StackProps } from "aws-cdk-lib";
import type { IFunction } from "aws-cdk-lib/aws-lambda";
import {
  Schedule,
  ScheduleExpression,
  ScheduleTargetInput,
  TimeWindow,
} from "aws-cdk-lib/aws-scheduler";
import { LambdaInvoke } from "aws-cdk-lib/aws-scheduler-targets";
import { Queue, QueueEncryption } from "aws-cdk-lib/aws-sqs";
import type { Construct } from "constructs";

export interface ScheduleStackProps extends StackProps {
  readonly workerFunction: IFunction;
  readonly ephemeral: boolean;
  readonly googleMaintenanceEnabled?: boolean;
}
export class ScheduleStack extends Stack {
  public readonly schedulerDeadLetterQueue: Queue;
  public readonly gmailWatchRenewalSchedule: Schedule;
  public readonly recoverySweepSchedule: Schedule;

  public constructor(scope: Construct, id: string, props: ScheduleStackProps) {
    super(scope, id, props);

    this.schedulerDeadLetterQueue = new Queue(
      this,
      "SchedulerDeadLetterQueue",
      {
        encryption: QueueEncryption.SQS_MANAGED,
        enforceSSL: true,
        retentionPeriod: Duration.days(14),
        removalPolicy: props.ephemeral
          ? RemovalPolicy.DESTROY
          : RemovalPolicy.RETAIN,
      },
    );

    this.gmailWatchRenewalSchedule = new Schedule(
      this,
      "GmailWatchRenewalSchedule",
      {
        schedule: ScheduleExpression.rate(Duration.days(1)),
        enabled: props.googleMaintenanceEnabled ?? false,
        timeWindow: TimeWindow.off(),
        description: "Renew Gmail watch registrations before expiration",
        target: new LambdaInvoke(props.workerFunction, {
          deadLetterQueue: this.schedulerDeadLetterQueue,
          maxEventAge: Duration.hours(1),
          retryAttempts: 2,
          input: ScheduleTargetInput.fromObject({
            maintenance_task: "RENEW_GMAIL_WATCHES",
          }),
        }),
      },
    );

    this.recoverySweepSchedule = new Schedule(
      this,
      "ConnectionRecoverySweepSchedule",
      {
        schedule: ScheduleExpression.rate(Duration.hours(6)),
        enabled: props.googleMaintenanceEnabled ?? false,
        timeWindow: TimeWindow.off(),
        description: "Find connector sync gaps that require bounded recovery",
        target: new LambdaInvoke(props.workerFunction, {
          deadLetterQueue: this.schedulerDeadLetterQueue,
          maxEventAge: Duration.hours(1),
          retryAttempts: 2,
          input: ScheduleTargetInput.fromObject({
            maintenance_task: "RECOVER_CONNECTION_SYNC",
          }),
        }),
      },
    );
  }
}
