import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const requiredPaths = [
  'apps/mobile',
  'services/control-api/src/quietpilot_control_api',
  'services/ingress/src/quietpilot_ingress',
  'services/worker/src/quietpilot_worker',
  'services/agent/src/quietpilot_agent',
  'services/agent/agentcore',
  'infra',
  'contracts/events',
  'contracts/agent',
  'tests/contract',
  'tests/integration',
  'tests/fixtures',
  'docs/hackathon-build/spec.md',
  'docs/hackathon-build/checklist.md',
];

const missing = requiredPaths.filter((relativePath) =>
  !existsSync(path.join(root, relativePath)),
);

if (missing.length > 0) {
  throw new Error(`Missing workspace paths:\n${missing.join('\n')}`);
}

const [nodeMajor, nodeMinor] = process.versions.node.split('.').map(Number);
if (nodeMajor !== 22 || nodeMinor < 13) {
  throw new Error(`Expected Node 22.13+, got ${process.versions.node}`);
}

function run(label, command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: root,
    encoding: 'utf8',
    env: process.env,
    ...options,
  });
  const output = `${result.stdout ?? ''}${result.stderr ?? ''}`.trim();
  if (result.status !== 0) {
    throw new Error(`${label} failed: ${output || result.error?.message}`);
  }
  const firstLine = output.split('\n').find(Boolean) ?? 'ok';
  console.log(`✓ ${label}: ${firstLine}`);
  return output;
}

if (!process.env.JAVA_HOME) {
  throw new Error('JAVA_HOME is missing. Run: source scripts/project-env.sh');
}

const javaVersion = run('Java', 'java', ['-version']);
if (!/version "17\./.test(javaVersion)) {
  throw new Error(`Expected JDK 17, got: ${javaVersion}`);
}

const androidHome = process.env.ANDROID_HOME;
if (!androidHome) {
  throw new Error('ANDROID_HOME is missing. Run: source scripts/project-env.sh');
}

const androidArtifacts = [
  'platforms/android-36/android.jar',
  'build-tools/36.0.0/aapt2',
  'platform-tools/adb',
];
for (const artifact of androidArtifacts) {
  if (!existsSync(path.join(androidHome, artifact))) {
    throw new Error(`Missing Android SDK artifact: ${artifact}`);
  }
}

run('Python 3.12', 'uv', ['run', '--no-project', '--python', '3.12', 'python', '--version']);
run('uv', 'uv', ['--version']);
run('AWS CLI binary', 'aws', ['--version']);
run('AgentCore CLI', 'agentcore', ['--version']);
run('ADB', 'adb', ['version']);

console.log(`✓ Android SDK: ${androidHome}`);
console.log(`✓ Workspace paths: ${requiredPaths.length}`);
console.log('QuietPilot local workspace is ready.');
