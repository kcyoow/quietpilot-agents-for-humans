import {
  CfnOutput,
  LegacyStackSynthesizer,
  RemovalPolicy,
  Stack,
  type StackProps,
} from "aws-cdk-lib";
import {
  AccountRecovery,
  UserPool,
  UserPoolClient,
  UserPoolClientIdentityProvider,
  UserPoolEmail,
} from "aws-cdk-lib/aws-cognito";
import type { Construct } from "constructs";

export const CONTROL_API_SCOPE = "aws.cognito.signin.user.admin";

export interface AuthStackProps extends StackProps {
  readonly ephemeral: boolean;
}
export class AuthStack extends Stack {
  public readonly userPool: UserPool;
  public readonly userPoolClient: UserPoolClient;
  public readonly authorizationScope = CONTROL_API_SCOPE;

  public constructor(scope: Construct, id: string, props: AuthStackProps) {
    super(scope, id, {
      ...props,
      synthesizer: props.synthesizer ?? new LegacyStackSynthesizer(),
    });

    const removalPolicy = props.ephemeral
      ? RemovalPolicy.DESTROY
      : RemovalPolicy.RETAIN;

    this.userPool = new UserPool(this, "UserPool", {
      selfSignUpEnabled: true,
      signInAliases: { email: true },
      autoVerify: { email: true },
      email: UserPoolEmail.withCognito(),
      accountRecovery: AccountRecovery.EMAIL_ONLY,
      signInCaseSensitive: false,
      standardAttributes: {
        email: { mutable: true, required: true },
        fullname: { mutable: true, required: true },
      },
      passwordPolicy: {
        minLength: 8,
        requireDigits: true,
        requireLowercase: true,
        requireSymbols: true,
        requireUppercase: true,
      },
      removalPolicy,
      deletionProtection: !props.ephemeral,
    });

    this.userPoolClient = new UserPoolClient(this, "MobileClient", {
      userPool: this.userPool,
      generateSecret: false,
      authFlows: { userSrp: true },
      disableOAuth: true,
      preventUserExistenceErrors: true,
      enableTokenRevocation: true,
      supportedIdentityProviders: [UserPoolClientIdentityProvider.COGNITO],
    });

    new CfnOutput(this, "UserPoolId", {
      value: this.userPool.userPoolId,
    });
    new CfnOutput(this, "UserPoolClientId", {
      value: this.userPoolClient.userPoolClientId,
    });
  }
}
