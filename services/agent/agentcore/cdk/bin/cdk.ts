#!/usr/bin/env node

import { ConfigIO, type AgentCoreProjectSpec } from '@aws/agentcore-cdk';
import { App } from 'aws-cdk-lib';
import * as path from 'node:path';

import { QuietPilotAgentCoreStack } from '../lib/cdk-stack';

function stackName(projectName: string, targetName: string): string {
  const safeProject = projectName.replace(/_/g, '-');
  const safeTarget = targetName.replace(/_/g, '-');
  return `AgentCore-${safeProject}-${safeTarget}`;
}

async function main(): Promise<void> {
  const configRoot = path.resolve(process.cwd(), '..');
  const config = new ConfigIO({ baseDir: configRoot });
  const spec = (await config.readProjectSpec()) as AgentCoreProjectSpec;
  const targets = await config.readAWSDeploymentTargets();
  if (targets.length === 0) {
    throw new Error('agentcore/aws-targets.json must contain a deployment target');
  }

  const app = new App();
  for (const target of targets) {
    new QuietPilotAgentCoreStack(app, stackName(spec.name, target.name), {
      env: { account: target.account, region: target.region },
      terminationProtection: true,
      spec,
      tags: {
        Project: 'QuietPilot',
        Purpose: 'AgentsForHumansHackathon',
      },
    });
  }
  app.synth();
}

void main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`AgentCore CDK synthesis failed: ${message}`);
  process.exitCode = 1;
});
