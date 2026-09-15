#!/usr/bin/env node

import { App } from "aws-cdk-lib";

import { ApiStack } from "../lib/api-stack.js";
import { AuthStack } from "../lib/auth-stack.js";
import { DataStack } from "../lib/data-stack.js";
import { ObservabilityStack } from "../lib/observability-stack.js";
import { ScheduleStack } from "../lib/schedule-stack.js";

const app = new App();
const ephemeralContext = app.node.tryGetContext("ephemeral");
const ephemeral = ephemeralContext === true || ephemeralContext === "true";
const runtimeArnContext = app.node.tryGetContext("agentCoreRuntimeArn");
if (runtimeArnContext !== undefined && typeof runtimeArnContext !== "string") {
  throw new TypeError("agentCoreRuntimeArn context must be a string");
}
const agentCoreRuntimeArn = runtimeArnContext?.trim() || undefined;
const googlePubSubEmailContext = app.node.tryGetContext(
  "googlePubSubServiceAccountEmail",
);
if (
  googlePubSubEmailContext !== undefined &&
  typeof googlePubSubEmailContext !== "string"
) {
  throw new TypeError(
    "googlePubSubServiceAccountEmail context must be a string",
  );
}
const googlePubSubServiceAccountEmail =
  googlePubSubEmailContext?.trim() || undefined;

const auth = new AuthStack(app, "QuietPilotAuth", { ephemeral });
const data = new DataStack(app, "QuietPilotData", { ephemeral });
const api = new ApiStack(app, "QuietPilotApi", {
  auth,
  data,
  agentCoreRuntimeArn,
  googlePubSubServiceAccountEmail,
});
const schedule = new ScheduleStack(app, "QuietPilotSchedule", {
  workerFunction: api.workerFunction,
  ephemeral,
  googleMaintenanceEnabled: Boolean(googlePubSubServiceAccountEmail),
});

new ObservabilityStack(app, "QuietPilotObservability", {
  httpApi: api.httpApi,
  controlFunction: api.controlFunction,
  workerFunction: api.workerFunction,
  workQueue: data.workQueue,
  workDeadLetterQueue: data.workDeadLetterQueue,
  schedulerDeadLetterQueue: schedule.schedulerDeadLetterQueue,
});
