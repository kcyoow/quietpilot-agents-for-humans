import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const checkOnly = process.argv.includes('--check');
const temporary = mkdtempSync(path.join(tmpdir(), 'quietpilot-contracts-'));
const temporaryAgent = path.join(temporary, 'agent');
const temporaryGenerated = path.join(temporary, 'generated');
mkdirSync(temporaryAgent, { recursive: true });
mkdirSync(temporaryGenerated, { recursive: true });

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: root,
    encoding: 'utf8',
    env: process.env,
  });
  if (result.status !== 0) {
    throw new Error(`${command} failed:\n${result.stdout}${result.stderr}`);
  }
}

const bin = (name) => path.join(root, 'node_modules', '.bin', name);

try {
  run('uv', [
    'run',
    '--package',
    'quietpilot-agent',
    'python',
    'services/agent/scripts/export_schemas.py',
    '--output-dir',
    temporaryAgent,
  ]);
  run(bin('openapi-typescript'), [
    'contracts/openapi.yaml',
    '--output',
    path.join(temporaryGenerated, 'api.d.ts'),
  ]);

  const jsonSchemas = [
    ['contracts/events/event-envelope.schema.json', 'event-envelope.d.ts'],
    [path.join(temporaryAgent, 'candidate-proposal.schema.json'), 'candidate-proposal.d.ts'],
    [path.join(temporaryAgent, 'case-plan-proposal.schema.json'), 'case-plan-proposal.d.ts'],
  ];
  for (const [input, output] of jsonSchemas) {
    run(bin('json2ts'), [
      '--input',
      input,
      '--output',
      path.join(temporaryGenerated, output),
      '--style.singleQuote',
    ]);
  }

  const outputs = [
    ...Object.keys({
      'candidate-proposal.schema.json': true,
      'case-plan-proposal.schema.json': true,
    }).map((name) => [
      path.join(temporaryAgent, name),
      path.join(root, 'contracts', 'agent', name),
    ]),
    ...['api.d.ts', 'event-envelope.d.ts', 'candidate-proposal.d.ts', 'case-plan-proposal.d.ts'].map(
      (name) => [
        path.join(temporaryGenerated, name),
        path.join(root, 'contracts', 'generated', name),
      ],
    ),
  ];

  const stale = [];
  for (const [source, destination] of outputs) {
    const generated = readFileSync(source, 'utf8');
    if (checkOnly) {
      let current = '';
      try {
        current = readFileSync(destination, 'utf8');
      } catch {
        stale.push(path.relative(root, destination));
        continue;
      }
      if (current !== generated) {
        stale.push(path.relative(root, destination));
      }
      continue;
    }
    mkdirSync(path.dirname(destination), { recursive: true });
    writeFileSync(destination, generated, 'utf8');
  }

  if (stale.length > 0) {
    throw new Error(`Generated contracts are stale:\n${stale.join('\n')}`);
  }
  console.log(checkOnly ? 'Generated contracts are current.' : 'Generated contracts updated.');
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
