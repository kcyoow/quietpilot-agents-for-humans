import { Duration, Stack, type StackProps } from "aws-cdk-lib";
import {
  Alarm,
  ComparisonOperator,
  TreatMissingData,
} from "aws-cdk-lib/aws-cloudwatch";
import type { IHttpApi } from "aws-cdk-lib/aws-apigatewayv2";
import type { IFunction } from "aws-cdk-lib/aws-lambda";
import type { IQueue } from "aws-cdk-lib/aws-sqs";
import type { Construct } from "constructs";

export interface ObservabilityStackProps extends StackProps {
  readonly httpApi: IHttpApi;
  readonly controlFunction: IFunction;
  readonly workerFunction: IFunction;
  readonly workQueue: IQueue;
  readonly workDeadLetterQueue: IQueue;
  readonly schedulerDeadLetterQueue: IQueue;
}
function failureAlarm(
  scope: Construct,
  id: string,
  metric: ReturnType<IFunction["metricErrors"]>,
): Alarm {
  return new Alarm(scope, id, {
    metric,
    threshold: 1,
    evaluationPeriods: 1,
    comparisonOperator: ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
    treatMissingData: TreatMissingData.NOT_BREACHING,
  });
}

export class ObservabilityStack extends Stack {
  public constructor(
    scope: Construct,
    id: string,
    props: ObservabilityStackProps,
  ) {
    super(scope, id, props);

    failureAlarm(
      this,
      "HttpApiServerErrorsAlarm",
      props.httpApi.metricServerError({ period: Duration.minutes(5) }),
    );

    for (const [name, fn] of [
      ["Control", props.controlFunction],
      ["Worker", props.workerFunction],
    ] as const) {
      failureAlarm(
        this,
        `${name}FunctionErrorsAlarm`,
        fn.metricErrors({ period: Duration.minutes(5) }),
      );
      failureAlarm(
        this,
        `${name}FunctionThrottlesAlarm`,
        fn.metricThrottles({ period: Duration.minutes(5) }),
      );
    }

    new Alarm(this, "WorkQueueAgeAlarm", {
      metric: props.workQueue.metricApproximateAgeOfOldestMessage({
        period: Duration.minutes(5),
      }),
      threshold: 300,
      evaluationPeriods: 1,
      comparisonOperator: ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: TreatMissingData.NOT_BREACHING,
    });

    for (const [name, queue] of [
      ["Work", props.workDeadLetterQueue],
      ["Scheduler", props.schedulerDeadLetterQueue],
    ] as const) {
      new Alarm(this, `${name}DeadLetterQueueAlarm`, {
        metric: queue.metricApproximateNumberOfMessagesVisible({
          period: Duration.minutes(5),
        }),
        threshold: 1,
        evaluationPeriods: 1,
        comparisonOperator:
          ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData: TreatMissingData.NOT_BREACHING,
      });
    }
  }
}
