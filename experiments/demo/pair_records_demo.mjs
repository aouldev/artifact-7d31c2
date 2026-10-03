import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const demoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'repotem-pair-demo-'));
const repo = path.join(workDir, 'repo');
const recordsPath = path.join(workDir, 'records.json');
const outputPath = path.join(workDir, 'pairing.json');

function git(...args) {
  return execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8' }).trim();
}

function write(relativePath, content) {
  const target = path.join(repo, relativePath);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, content, 'utf8');
}

function record(commit, timestamp, productionPath, withTest, baseCommit, firstCommit) {
  const edge = {
    testFile: 'tests/a.test.js',
    coveredProdFiles: ['src/a.js', 'src/b.js'],
    coverageKind: 'coverage-final',
    coverageStatus: 'collected',
    runnerFamily: 'vitest',
    notes: [],
  };
  return {
    repoRoot: repo,
    commit,
    base: timestamp === 1 ? baseCommit : firstCommit,
    commitMeta: {
      shortHash: commit.slice(0, 8),
      authorDate: `2026-01-01T00:00:0${timestamp}Z`,
    },
    timestampMs: timestamp * 1000,
    prodChanges: [
      {
        nodeId: `prod:${commit}:${productionPath}`,
        path: productionPath,
        previousPath: null,
        diffPaths: [productionPath],
        gitStatus: 'M',
        semantic: { decision: 'keep', reason: 'demo' },
      },
    ],
    coverage: {
      selectedForCoverageCount: withTest ? 1 : 0,
      edges: withTest ? [edge] : [],
    },
  };
}

function main() {
  let base;
  let first;
  try {
  fs.mkdirSync(repo, { recursive: true });
  git('init');
  git('config', 'user.name', 'RepoTEM Demo');
  git('config', 'user.email', 'demo@example.invalid');
  write('src/a.js', 'a0\n');
  write('src/b.js', 'b0\n');
  write('tests/a.test.js', 't0\n');
  git('add', '.');
  git('commit', '-m', 'base');
  base = git('rev-parse', 'HEAD');

  write('src/a.js', 'a1\n');
  git('add', '.');
  git('commit', '-m', 'production change');
  first = git('rev-parse', 'HEAD');

  write('src/b.js', 'b1\n');
  write('tests/a.test.js', 't1\n');
  git('add', '.');
  git('commit', '-m', 'test maintenance');
  const second = git('rev-parse', 'HEAD');

  fs.writeFileSync(recordsPath, JSON.stringify([
    record(second, 2, 'src/b.js', true, base, first),
    record(first, 1, 'src/a.js', false, base, first),
  ], null, 2));

  execFileSync(process.execPath, [
    path.join(demoRoot, 'pipeline/graph_pairer.mjs'),
    'pair-records',
    '--records-file',
    recordsPath,
    '--output-json',
    outputPath,
  ], { stdio: 'inherit' });

  const episodes = JSON.parse(fs.readFileSync(outputPath, 'utf8'));
  console.error(`Demo complete: ${episodes.length} episode(s) written.`);
  } finally {
    fs.rmSync(workDir, { recursive: true, force: true });
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))) {
  main();
}
