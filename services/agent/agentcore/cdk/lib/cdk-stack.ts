import { AgentCoreApplication, type AgentCoreProjectSpec } from '@aws/agentcore-cdk';
import { ArnFormat, Stack, type StackProps } from 'aws-cdk-lib';
import { PolicyStatement } from 'aws-cdk-lib/aws-iam';
import type { Construct } from 'constructs';

const GOOGLE_PROVIDER_NAME = 'quietpilot-google';
const RUNTIME_NAME = 'QuietPilotAgent';

export interface QuietPilotAgentCoreStackProps extends StackProps {
  readonly spec: AgentCoreProjectSpec;
}

export class QuietPilotAgentCoreStack extends Stack {
  public constructor(scope: Construct, id: string, props: QuietPilotAgentCoreStackProps) {
    super(scope, id, props);
    const application = new AgentCoreApplication(this, 'Application', { spec: props.spec });
    const environment = application.environments.get(RUNTIME_NAME);
    if (!environment) {
      throw new Error('QuietPilotAgent runtime is missing from the AgentCore project spec');
    }

    const tokenVaultArn = this.formatArn({
      service: 'bedrock-agentcore',
      resource: 'token-vault',
      resourceName: 'default',
    });
    const identityDirectoryArn = this.formatArn({
      service: 'bedrock-agentcore',
      resource: 'workload-identity-directory',
      resourceName: 'default',
    });
    const workloadIdentityArn = this.formatArn({
      service: 'bedrock-agentcore',
      resource: 'workload-identity-directory',
      resourceName: `default/workload-identity/${props.spec.name}_${RUNTIME_NAME}-*`,
    });
    const credentialProviderArn = this.formatArn({
      service: 'bedrock-agentcore',
      resource: 'token-vault',
      resourceName: `default/oauth2credentialprovider/${GOOGLE_PROVIDER_NAME}`,
    });
    const credentialSecretArn = this.formatArn({
      service: 'secretsmanager',
      resource: 'secret',
      resourceName: `bedrock-agentcore-identity!default/oauth2/${GOOGLE_PROVIDER_NAME}-*`,
      arnFormat: ArnFormat.COLON_RESOURCE_NAME,
    });
    environment.runtime.addToPolicy(
      new PolicyStatement({
        actions: ['bedrock-agentcore:GetResourceOauth2Token'],
        resources: [
          credentialProviderArn,
          tokenVaultArn,
          workloadIdentityArn,
          identityDirectoryArn,
        ],
      }),
    );
    environment.runtime.addToPolicy(
      new PolicyStatement({
        actions: ['secretsmanager:GetSecretValue'],
        resources: [credentialSecretArn],
      }),
    );
    environment.runtime.addEnvironmentVariable(
      'QUIETPILOT_GOOGLE_PROVIDER_NAME',
      GOOGLE_PROVIDER_NAME,
    );

    const gmailTopicName = process.env.QUIETPILOT_GMAIL_TOPIC_NAME?.trim();
    if (gmailTopicName) {
      environment.runtime.addEnvironmentVariable('QUIETPILOT_GMAIL_TOPIC_NAME', gmailTopicName);
    }
  }
}
