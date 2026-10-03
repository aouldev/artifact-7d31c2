import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { locateChangedTests } from './commit_test_locator.mjs';

const __filename = fileURLToPath(import.meta.url);

const CODE_LIKE_EXTENSIONS = new Set([
  '.js',
  '.jsx',
  '.ts',
  '.tsx',
  '.mjs',
  '.cjs',
  '.mts',
  '.cts',
  '.vue',
  '.svelte',
]);
const FAILURE_NOTE_PATTERNS = [
  'command_failed',
  'test_command_exit_',
  'missing_vitest_binary',
  'missing_jest_binary',
  'unsupported_runner_family',
  'playwright_not_implemented_yet',
  'missing_binary',
  'no_artifact',
];
const VALID_COVERAGE_KINDS = new Set(['coverage-final', 'lcov', 'raw-v8', 'browser-raw']);
const HARD_COVERAGE_FAILURE_CATEGORIES = new Set([
  'missing_test_environment',
  'database_not_ready',
  'redis_not_ready',
  'jest_transform_mismatch',
  'missing_node_module',
  'test_bootstrap_failed',
]);
const FILE_AT_COMMIT_CACHE = new Map();
const SOURCE_SEMANTIC_CACHE = new Map();

function parseArgs(argv) {
  if (argv.length === 0) {
    throw new Error('Usage: node graph_pairer.mjs <trace-commit|pair-records|run> ...');
  }

  const subcommand = argv[0];
  const args = {
    subcommand,
    repo: null,
    commit: null,
    base: null,
    commits: [],
    commitsFile: null,
    revRange: null,
    recordsFile: null,
    outputDir: null,
    outputJson: null,
    detailsJson: null,
    recordsJson: null,
    windowHours: 12,
    skipInstall: false,
    forceReinstall: false,
    profileConfig: null,
    resumeRecords: null,
    coverageTimeoutMs: null,
    envPackageSubdir: null,
    autoResume: true,
    continueOnError: false,
  };

  for (let i = 1; i < argv.length; i += 1) {
    const token = argv[i];
    if (token === '--repo') {
      args.repo = argv[i + 1];
      i += 1;
    } else if (token === '--commit') {
      args.commit = argv[i + 1];
      i += 1;
    } else if (token === '--base') {
      args.base = argv[i + 1];
      i += 1;
    } else if (token === '--commits-file') {
      args.commitsFile = argv[i + 1];
      i += 1;
    } else if (token === '--rev-range') {
      args.revRange = argv[i + 1];
      i += 1;
    } else if (token === '--records-file') {
      args.recordsFile = argv[i + 1];
      i += 1;
    } else if (token === '--output-dir') {
      args.outputDir = argv[i + 1];
      i += 1;
    } else if (token === '--output-json') {
      args.outputJson = argv[i + 1];
      i += 1;
    } else if (token === '--details-json') {
      args.detailsJson = argv[i + 1];
      i += 1;
    } else if (token === '--records-json') {
      args.recordsJson = argv[i + 1];
      i += 1;
    } else if (token === '--window-hours') {
      args.windowHours = Number(argv[i + 1]);
      i += 1;
    } else if (token === '--skip-install') {
      args.skipInstall = true;
    } else if (token === '--force-reinstall') {
      args.forceReinstall = true;
    } else if (token === '--profile-config') {
      args.profileConfig = argv[i + 1];
      i += 1;
    } else if (token === '--resume-records') {
      args.resumeRecords = argv[i + 1];
      i += 1;
    } else if (token === '--coverage-timeout-ms') {
      args.coverageTimeoutMs = Number(argv[i + 1]);
      i += 1;
    } else if (token === '--env-package-subdir') {
      args.envPackageSubdir = argv[i + 1];
      i += 1;
    } else if (token === '--no-auto-resume') {
      args.autoResume = false;
    } else if (token === '--continue-on-error') {
      args.continueOnError = true;
    } else if (token === '--commit-list') {
      args.commits.push(...argv[i + 1].split(',').map((item) => item.trim()).filter(Boolean));
      i += 1;
    } else {
      throw new Error(`Unknown arg: ${token}`);
    }
  }

  if (!Number.isFinite(args.windowHours) || args.windowHours <= 0) {
    throw new Error(`Invalid --window-hours: ${args.windowHours}`);
  }

  if (subcommand === 'trace-commit') {
    if (!args.repo || !args.commit || !args.outputDir) {
      throw new Error(
        'Usage: node graph_pairer.mjs trace-commit --repo <repo> --commit <sha> --output-dir <dir> [--base <sha>] [--output-json <path>] [--skip-install] [--force-reinstall] [--profile-config <path>]',
      );
    }
  } else if (subcommand === 'pair-records') {
    if (!args.recordsFile) {
      throw new Error(
        'Usage: node graph_pairer.mjs pair-records --records-file <json> [--output-json <path>] [--details-json <path>] [--window-hours <hours>]',
      );
    }
  } else if (subcommand === 'run') {
    if (!args.repo || !args.outputDir) {
      throw new Error(
        'Usage: node graph_pairer.mjs run --repo <repo> --output-dir <dir> [--commit <sha>] [--commit-list <a,b>] [--commits-file <path>] [--rev-range <range>] [--output-json <path>] [--details-json <path>] [--records-json <path>] [--window-hours <hours>] [--skip-install] [--force-reinstall] [--profile-config <path>]',
      );
    }
  } else {
    throw new Error(`Unknown subcommand: ${subcommand}`);
  }

  return args;
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function sanitizeId(input) {
  return input.replace(/[^a-zA-Z0-9._-]+/g, '__');
}

function runGit(repoRoot, args, allowFailure = false) {
  try {
    return execFileSync('git', ['-C', repoRoot, ...args], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
      maxBuffer: 64 * 1024 * 1024,
    }).trimEnd();
  } catch (error) {
    if (allowFailure) {
      return null;
    }
    const stderr = error.stderr?.toString?.() ?? '';
    throw new Error(`git ${args.join(' ')} failed: ${stderr || error.message}`);
  }
}

function readFileAtCommit(repoRoot, ref, relPath) {
  const key = `${repoRoot}::${ref}::${relPath}`;
  if (FILE_AT_COMMIT_CACHE.has(key)) return FILE_AT_COMMIT_CACHE.get(key);
  const value = runGit(repoRoot, ['show', `${ref}:${relPath}`], true);
  FILE_AT_COMMIT_CACHE.set(key, value);
  return value;
}

function eraseTypeOnlyImportSyntax(sourceText) {
  return sourceText
    .split(/\r?\n/)
    .map((line) => {
      const trimmed = line.trimStart();
      if (!/^(import|export)\b/.test(trimmed)) return line;
      const indent = line.slice(0, line.length - trimmed.length);
      const erased = trimmed
        .replace(/^import\s+type\s+/, 'import ')
        .replace(/^export\s+type\s+/, 'export ')
        .replace(/([,{]\s*)type\s+([A-Za-z_$][\w$]*(?:\s+as\s+[A-Za-z_$][\w$]*)?)/g, '$1$2');
      return `${indent}${erased}`;
    })
    .join('\n');
}

function normalizeSourceForNonSemanticCompare(sourceText) {
  return eraseTypeOnlyImportSyntax(sourceText).replace(/\r\n/g, '\n').trimEnd();
}

function diffIsWhitespaceOnly(repoRoot, base, commit, diffPaths) {
  const output = runGit(
    repoRoot,
    [
      'diff',
      '--ignore-all-space',
      '--ignore-blank-lines',
      '--ignore-space-at-eol',
      '--unified=0',
      base,
      commit,
      '--',
      ...diffPaths,
    ],
    true,
  );
  return output != null && output.trim() === '';
}

function diffChangedLines(diffText) {
  const lines = [];
  for (const line of diffText.split('\n')) {
    if (!line.startsWith('+') && !line.startsWith('-')) continue;
    if (line.startsWith('+++') || line.startsWith('---')) continue;
    lines.push(line.slice(1));
  }
  return lines;
}

function stripImportExportDeclarations(sourceText) {
  return sourceText
    .split(/\r?\n/)
    .filter((line) => {
      const trimmed = line.trimStart();
      if (!trimmed) return false;
      if (/^(import|export)\b/.test(trimmed)) return false;
      return true;
    })
    .join('\n')
    .replace(/\r\n/g, '\n')
    .trimEnd();
}

function isCommentOrBlankLine(line) {
  const trimmed = line.trim();
  return (
    trimmed === '' ||
    trimmed.startsWith('//') ||
    trimmed.startsWith('/*') ||
    trimmed.startsWith('*') ||
    trimmed.startsWith('*/')
  );
}

function diffIsCommentOnly(repoRoot, base, commit, diffPaths) {
  const output = runGit(
    repoRoot,
    ['diff', '--unified=0', base, commit, '--', ...diffPaths],
    true,
  );
  if (output == null) return false;
  const changedLines = diffChangedLines(output);
  return changedLines.length > 0 && changedLines.every((line) => isCommentOrBlankLine(line));
}

function analyzeSourceChangeSemantics(record, relPath, previousPath, gitStatus, diffPaths) {
  const normalizedStatus = (gitStatus ?? '')[0] ?? '';
  const paths = uniqSorted((diffPaths?.length ? diffPaths : [relPath, previousPath]).filter(Boolean));
  const key = `${record.repoRoot}::${record.base}::${record.commit}::${relPath}::${previousPath ?? ''}::${normalizedStatus}::${paths.join('|')}`;
  if (SOURCE_SEMANTIC_CACHE.has(key)) return SOURCE_SEMANTIC_CACHE.get(key);

  const result = (() => {
    if (!CODE_LIKE_EXTENSIONS.has(path.extname(relPath).toLowerCase())) {
      return { decision: 'keep', reason: 'unsupported_extension' };
    }
    if (!record.base || normalizedStatus === 'A' || normalizedStatus === 'D') {
      return { decision: 'keep', reason: 'added_or_deleted_keep_conservative' };
    }

    const beforePath = previousPath ?? relPath;
    const beforeText = readFileAtCommit(record.repoRoot, record.base, beforePath);
    const afterText = readFileAtCommit(record.repoRoot, record.commit, relPath);
    if (beforeText == null || afterText == null) {
      return { decision: 'keep', reason: 'source_unavailable_keep_conservative' };
    }

    if (
      normalizeSourceForNonSemanticCompare(beforeText) ===
      normalizeSourceForNonSemanticCompare(afterText)
    ) {
      return { decision: 'drop', reason: 'type_import_only_non_semantic' };
    }

    if (
      stripImportExportDeclarations(beforeText) === stripImportExportDeclarations(afterText)
    ) {
      return { decision: 'drop', reason: 'import_export_only_non_semantic' };
    }

    if (diffIsWhitespaceOnly(record.repoRoot, record.base, record.commit, paths)) {
      return { decision: 'drop', reason: 'whitespace_only_non_semantic' };
    }

    if (diffIsCommentOnly(record.repoRoot, record.base, record.commit, paths)) {
      return { decision: 'drop', reason: 'comment_only_non_semantic' };
    }

    return { decision: 'keep', reason: 'semantic_or_unknown' };
  })();

  SOURCE_SEMANTIC_CACHE.set(key, result);
  return result;
}

function readJson(jsonPath) {
  return JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
}

function writeJson(jsonPath, value) {
  ensureDir(path.dirname(jsonPath));
  const tempPath = `${jsonPath}.tmp-${process.pid}-${Date.now()}`;
  fs.writeFileSync(tempPath, `${JSON.stringify(value, null, 2)}\n`);
  fs.renameSync(tempPath, jsonPath);
}

function appendJsonl(jsonlPath, value) {
  ensureDir(path.dirname(jsonlPath));
  fs.appendFileSync(jsonlPath, `${JSON.stringify(value)}\n`);
}

function toRunStatusLabel(status) {
  if (status === 'running') return 'running';
  if (status === 'completed') return 'completed';
  if (status === 'failed') return 'failed';
  return 'unknown';
}

function writeRunEvent(outputDir, event) {
  appendJsonl(path.join(outputDir, 'progress_events.jsonl'), {
    schemaVersion: 1,
    timestamp: new Date().toISOString(),
    event: event.event,
    stage: event.stage ?? null,
    status: event.status ?? null,
    repository: event.repoName ?? null,
    commit: event.commit ?? null,
    processedCommitCount: event.processedCommitCount ?? null,
    totalCommitCount: event.totalCommitCount ?? null,
    sampleCount: event.sampleCount ?? null,
    positiveSamples: event.positiveSamples ?? null,
    negativeSamples: event.negativeSamples ?? null,
    unresolvedSamples: event.unresolvedSamples ?? null,
    message: event.message ?? null,
    error: event.error ?? null,
  });
}

function writeRunCheckpoint(outputDir, payload) {
  const statusLabel = toRunStatusLabel(payload.status);
  writeJson(path.join(outputDir, 'records.partial.json'), payload.traceRecords);
  writeJson(path.join(outputDir, 'trace_index.partial.json'), payload.traceIndex);
  writeJson(path.join(outputDir, 'run_progress.json'), {
    schemaVersion: 1,
    status: payload.status,
    statusLabel,
    processedCommitCount: payload.traceRecords.length,
    totalCommitCount: payload.totalCommitCount,
    lastCommit: payload.traceRecords.at(-1)?.commit ?? null,
    lastNewCommit: payload.lastNewCommit ?? null,
    resumedRecordCount: payload.resumedRecordCount ?? 0,
    updatedAt: new Date().toISOString(),
  });
}

function summarizePairing(pairing) {
  const diagnostics = buildPairingDiagnostics(pairing);
  return {
    schemaVersion: 1,
    repoRoot: pairing.repoRoot,
    windowHours: pairing.windowHours,
    traceRecordCount: pairing.traceRecordCount,
    stats: pairing.stats,
    labelCounts: {
      positive: pairing.stats.positiveSamples ?? 0,
      negative: pairing.stats.negativeSamples ?? 0,
      unresolved: pairing.stats.unresolvedSamples ?? 0,
    },
    prodNodeLabelCounts: diagnostics.prodNodeLabelCounts,
    rates: diagnostics.rates,
    warnings: diagnostics.warnings,
  };
}

function divideSafe(numerator, denominator) {
  if (!denominator) return 0;
  return numerator / denominator;
}

function buildPairingDiagnostics(pairing) {
  const samples = pairing.samples ?? [];
  const positiveSamples = samples.filter((sample) => sample.label === 1);
  const negativeSamples = samples.filter((sample) => sample.label === 0);
  const unresolvedSamples = pairing.unresolvedSamples ?? [];

  const positiveProdNodes = positiveSamples.reduce(
    (sum, sample) => sum + (sample.prod?.nodeCount ?? 0),
    0,
  );
  const negativeProdNodes = negativeSamples.reduce(
    (sum, sample) => sum + (sample.prod?.nodeCount ?? 0),
    0,
  );
  const unresolvedProdNodes = unresolvedSamples.reduce(
    (sum, sample) => sum + (sample.prod?.nodeCount ?? 0),
    0,
  );
  const totalEpisodeCount = positiveSamples.length + negativeSamples.length;
  const totalProdNodes = positiveProdNodes + negativeProdNodes + unresolvedProdNodes;
  const positiveSizes = positiveSamples
    .map((sample) => sample.prod?.nodeCount ?? 0)
    .sort((left, right) => left - right);
  const maxPositiveProdNodes = positiveSizes.at(-1) ?? 0;
  const largePositiveEpisodes = positiveSamples.filter((sample) => (sample.prod?.nodeCount ?? 0) > 100);

  const rates = {
    episodePositiveRate: divideSafe(positiveSamples.length, totalEpisodeCount),
    prodNodePositiveRate: divideSafe(positiveProdNodes, totalProdNodes),
    prodNodeNegativeRate: divideSafe(negativeProdNodes, totalProdNodes),
    prodNodeUnresolvedRate: divideSafe(unresolvedProdNodes, totalProdNodes),
  };

  const warnings = [];
  if (positiveSamples.length > 0 && rates.episodePositiveRate < 0.05 && rates.prodNodePositiveRate > 0.15) {
    warnings.push({
      type: 'aggregation-scale-mismatch',
      message:
        'Positive samples are counted as connected-component episodes, while negative samples are often isolated production nodes; episode positive rate can therefore be lower and should not be used alone as a data-quality signal.',
      recommendation: 'Report prodNodeLabelCounts alongside episode counts, or redefine negative aggregation before training.',
    });
  }
  if (largePositiveEpisodes.length > 0) {
    warnings.push({
      type: 'wide-component-merge-risk',
      message: `${largePositiveEpisodes.length} positive episode(s) cover more than 100 production nodes; the largest covers ${maxPositiveProdNodes}.`,
      recommendation: 'Down-weight or split very large connected components instead of treating them as clean gold labels.',
    });
  }
  if (unresolvedSamples.length > 0) {
    warnings.push({
      type: 'unresolved-samples',
      message: `${unresolvedSamples.length} unresolved episode(s) remain, usually because coverage evidence failed or was skipped.`,
      recommendation: 'Inspect unresolved.reasons to distinguish environment failures, runner failures, and coverage blind spots.',
    });
  }

  return {
    schemaVersion: 1,
    description: 'This diagnostic detects unusual proportions; episode counts and production-node counts must be interpreted separately.',
    episodeLabelCounts: {
      positive: positiveSamples.length,
      negative: negativeSamples.length,
      unresolved: unresolvedSamples.length,
    },
    prodNodeLabelCounts: {
      positive: positiveProdNodes,
      negative: negativeProdNodes,
      unresolved: unresolvedProdNodes,
      total: totalProdNodes,
    },
    rates,
    positiveEpisodeProdNodeSizes: positiveSizes,
    maxPositiveProdNodes,
    warnings,
  };
}

function writePairingSnapshot(outputDir, traceRecords, traceIndex, options = {}) {
  if (traceRecords.length === 0) return null;
  const details = pairTraceRecords(traceRecords, {
    windowHours: options.windowHours ?? 12,
    flushOpenWindow: options.final ?? false,
  });
  details.traceIndex = traceIndex;
  const result = toDatasetSamples(details);
  const suffix = options.final ? '' : '.partial';
  writeJson(path.join(outputDir, `pairing${suffix}.json`), result);
  writeJson(path.join(outputDir, `pairing_details${suffix}.json`), details);
  writeJson(path.join(outputDir, `summary${suffix}.json`), summarizePairing(details));
  writeJson(path.join(outputDir, `diagnostics${suffix}.json`), buildPairingDiagnostics(details));
  return { details, result };
}

function removeDirIfExists(dirPath) {
  if (fs.existsSync(dirPath)) {
    fs.rmSync(dirPath, { recursive: true, force: true });
  }
}

function resolveBaseCommit(repoRoot, commit, explicitBase) {
  if (explicitBase) return explicitBase;
  const parentLine = runGit(repoRoot, ['rev-list', '--parents', '-n', '1', commit]);
  const parts = parentLine.split(/\s+/).filter(Boolean);
  if (parts.length < 2) {
    throw new Error(`Commit ${commit} has no parent; please pass --base explicitly`);
  }
  return parts[1];
}

function listChangedFiles(repoRoot, base, commit) {
  const output = runGit(repoRoot, ['diff', '--name-status', '--find-renames', base, commit]);
  const files = [];
  for (const line of output.split('\n')) {
    if (!line.trim()) continue;
    const parts = line.split('\t');
    const statusToken = parts[0] ?? '';
    const status = statusToken[0];
    if (status === 'R' || status === 'C') {
      files.push({
        status,
        oldPath: parts[1] ?? null,
        path: parts[2] ?? null,
      });
      continue;
    }
    files.push({
      status,
      oldPath: null,
      path: parts[1] ?? null,
    });
  }
  return files;
}

function isTestPath(relPath) {
  if (!relPath) return false;
  const p = relPath.toLowerCase();
  if (/(^|\/)(__tests__|tests|test|playwright|cypress)(\/|$)/.test(p)) return true;
  return /\.(test|spec|e2e|integration-test)\.[a-z0-9]+$/.test(p);
}

function isProdLike(relPath) {
  if (!relPath) return false;
  const p = relPath.toLowerCase();
  const ext = path.extname(p);
  if (
    p.includes('/node_modules/') ||
    p.startsWith('node_modules/') ||
    p.includes('/coverage/') ||
    p.includes('/__tests__/') ||
    p.includes('/tests/') ||
    p.includes('/test/') ||
    p.includes('/specs/') ||
    p.includes('/e2e/') ||
    p.includes('/playwright/') ||
    p.includes('/cypress/') ||
    p.includes('/__mocks__/') ||
    p.includes('/mocks/')
  ) {
    return false;
  }
  if (
    p.endsWith('.test.ts') ||
    p.endsWith('.test.tsx') ||
    p.endsWith('.test.js') ||
    p.endsWith('.test.jsx') ||
    p.endsWith('.spec.ts') ||
    p.endsWith('.spec.tsx') ||
    p.endsWith('.spec.js') ||
    p.endsWith('.spec.jsx') ||
    p.endsWith('.stories.ts') ||
    p.endsWith('.stories.tsx') ||
    p.endsWith('.stories.js') ||
    p.endsWith('.stories.jsx') ||
    p.endsWith('.config.ts') ||
    p.endsWith('.config.js') ||
    p.endsWith('.config.mjs') ||
    p.endsWith('.config.cjs') ||
    p.endsWith('vitest.workspace.ts') ||
    p.endsWith('vitest.workspace.js') ||
    p.endsWith('vitest.workspace.mts') ||
    p.endsWith('vitest.workspace.mjs')
  ) {
    return false;
  }
  return CODE_LIKE_EXTENSIONS.has(ext) && !isTestPath(relPath);
}

function uniqSorted(items) {
  return [...new Set(items)].sort((a, b) => a.localeCompare(b));
}

function uniqInOrder(items) {
  return [...new Set(items)];
}

function getCommitMeta(repoRoot, commit) {
  const output = runGit(repoRoot, [
    'show',
    '-s',
    '--format=%H%n%an%n%aI%n%cI%n%s%n%P',
    commit,
  ]);
  const lines = output.split('\n');
  return {
    hash: lines[0] ?? commit,
    shortHash: (lines[0] ?? commit).slice(0, 8),
    authorName: lines[1] ?? '',
    authorDate: lines[2] ?? '',
    commitDate: lines[3] ?? '',
    subject: lines[4] ?? '',
    parentHashes: (lines[5] ?? '').split(/\s+/).filter(Boolean),
  };
}

function toTimestampMs(isoLike) {
  const value = Date.parse(isoLike);
  if (!Number.isFinite(value)) {
    throw new Error(`Invalid timestamp: ${isoLike}`);
  }
  return value;
}

function buildNodeId(kind, commit, relPath) {
  return `${kind}:${commit}:${relPath}`;
}

function buildProdChanges(diffEntries, commit) {
  return diffEntries
    .filter((entry) => isProdLike(entry.path ?? entry.oldPath))
    .map((entry) => {
      const relPath = entry.path ?? entry.oldPath;
      const previousPath = entry.oldPath ?? null;
      const diffPaths = uniqSorted([relPath, previousPath].filter(Boolean));
      return {
        nodeId: buildNodeId('prod', commit, relPath),
        commit,
        path: relPath,
        previousPath,
        gitStatus: entry.status,
        diffPaths,
        semantic: null,
      };
    });
}

function attachProdSemantics(record) {
  record.prodChanges = (record.prodChanges ?? []).map((change) => ({
    ...change,
    semantic:
      change.semantic ??
      analyzeSourceChangeSemantics(
        record,
        change.path,
        change.previousPath,
        change.gitStatus,
        change.diffPaths,
      ),
  }));
  return record;
}

function simplifyLocatorFiles(locator) {
  return locator.files.map((item) => ({
    nodeId: buildNodeId('test', locator.commit, item.path),
    path: item.path,
    previousPath: item.previousPath ?? null,
    gitStatus: item.gitStatus,
    includeForCoverage: item.includeForCoverage,
    runnerFamilyGuess: item.runnerFamilyGuess,
    targetExpression: item.targetExpression,
    semantic: item.semantic,
  }));
}

function isNonSemanticDecision(semantic) {
  return semantic?.decision === 'drop';
}

function analyzeTestChangeSemantics(record, testChange) {
  if (!testChange?.path) {
    return { decision: 'keep', reason: 'missing_test_path_keep_conservative' };
  }
  if (isNonSemanticDecision(testChange.semantic)) {
    return testChange.semantic;
  }
  return analyzeSourceChangeSemantics(
    record,
    testChange.path,
    testChange.previousPath,
    testChange.gitStatus,
    uniqSorted([testChange.path, testChange.previousPath].filter(Boolean)),
  );
}

function buildSemanticTestPathSet(record) {
  const paths = [];
  const excludedTestPaths = new Set(
    (record.coverage?.excludedTestFiles ?? [])
      .map((item) => (typeof item === 'string' ? item : item?.testFile))
      .filter(Boolean),
  );
  for (const item of record.testChanges ?? []) {
    if (!item.includeForCoverage) continue;
    if (excludedTestPaths.has(item.path)) continue;
    const semantic = analyzeTestChangeSemantics(record, item);
    if (isNonSemanticDecision(semantic)) continue;
    paths.push(item.path);
  }
  return new Set(paths);
}

async function buildCommitTraceRecord(options) {
  const repoRoot = path.resolve(options.repo);
  const outputDir = path.resolve(options.outputDir);
  ensureDir(outputDir);

  const meta = getCommitMeta(repoRoot, options.commit);
  const base = resolveBaseCommit(repoRoot, options.commit, options.base ?? null);
  const diffEntries = listChangedFiles(repoRoot, base, options.commit);
  const prodChanges = buildProdChanges(diffEntries, options.commit);
  const locator = locateChangedTests({
    repo: repoRoot,
    commit: options.commit,
    base,
  });

  let coverage = {
    status: 'skipped_no_semantic_tests',
    changedTestFileCount: locator.changedTestFileCount,
    selectedForCoverageCount: locator.selectedForCoverageCount,
    preparedEnv: null,
    edges: [],
  };

  if (locator.selectedForCoverageCount > 0) {
    const { buildTestProdMap } = await loadInternalTestProdMap();
    const mapDir = path.join(outputDir, 'coverage');
    const mapResult = await buildTestProdMap({
      repo: repoRoot,
      commit: options.commit,
      base,
      outputDir: mapDir,
      outputJson: path.join(mapDir, 'test_prod_map.json'),
      skipInstall: options.skipInstall ?? false,
        forceReinstall: options.forceReinstall ?? false,
        profileConfig: options.profileConfig ?? null,
        coverageTimeoutMs: options.coverageTimeoutMs ?? null,
        envPackageSubdir: options.envPackageSubdir ?? null,
      });
    coverage = {
      status: mapResult.coverageSkipped ? 'coverage_skipped' : 'collected',
      coverageSkipReason: mapResult.coverageSkipReason ?? null,
      changedTestFileCount: mapResult.changedTestFileCount,
      selectedForCoverageCount: mapResult.selectedForCoverageCount,
      originalSelectedForCoverageCount: mapResult.originalSelectedForCoverageCount ?? mapResult.selectedForCoverageCount,
      excludedTestFiles: mapResult.excludedTestFiles ?? [],
      preparedEnv: mapResult.preparedEnv ?? null,
      edges: mapResult.edges ?? [],
    };
  }

  const record = {
    schemaVersion: 1,
    repoRoot,
    commit: options.commit,
    base,
    commitMeta: meta,
    timestampMs: toTimestampMs(meta.authorDate),
    diffEntries,
    prodChanges,
    testChanges: simplifyLocatorFiles(locator),
    coverage,
  };
  attachProdSemantics(record);

  const recordPath = path.join(outputDir, 'trace_record.json');
  writeJson(recordPath, record);
  return { record, recordPath };
}

async function loadInternalTestProdMap() {
  const moduleUrl = new URL('../../_internal/construction_pipeline/commit_to_test_prod_map.mjs', import.meta.url);
  try {
    return await import(moduleUrl.href);
  } catch (error) {
    if (error?.code === 'ERR_MODULE_NOT_FOUND') {
      throw new Error(
        'The repository-specific coverage runner is not included in the public demo. ' +
          'Use the public pair-records command or provide the staging _internal construction pipeline.',
      );
    }
    throw error;
  }
}

function hashSampleId(payload) {
  return crypto.createHash('sha1').update(JSON.stringify(payload)).digest('hex').slice(0, 12);
}

function gitIsAncestor(repoRoot, ancestor, descendant) {
  if (ancestor === descendant) return true;
  try {
    execFileSync('git', ['-C', repoRoot, 'merge-base', '--is-ancestor', ancestor, descendant], {
      stdio: ['ignore', 'ignore', 'pipe'],
    });
    return true;
  } catch (error) {
    if (error.status === 1) return false;
    const stderr = error.stderr?.toString?.() ?? '';
    throw new Error(
      `git merge-base --is-ancestor failed for ${ancestor} and ${descendant}: ${stderr || error.message}`,
    );
  }
}

function resolveLinearCommitHistory(repoRoot, commits) {
  const resolved = uniqInOrder(
    commits.map((commit) => runGit(repoRoot, ['rev-parse', `${commit}^{commit}`])),
  );
  if (resolved.length === 0) {
    throw new Error('Cannot resolve an empty commit history');
  }

  const predecessorCounts = new Map(resolved.map((commit) => [commit, 0]));
  for (let leftIndex = 0; leftIndex < resolved.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < resolved.length; rightIndex += 1) {
      const left = resolved[leftIndex];
      const right = resolved[rightIndex];
      if (gitIsAncestor(repoRoot, left, right)) {
        predecessorCounts.set(right, predecessorCounts.get(right) + 1);
      } else if (gitIsAncestor(repoRoot, right, left)) {
        predecessorCounts.set(left, predecessorCounts.get(left) + 1);
      } else {
        throw new Error(`Commits ${left} and ${right} are not on one ancestry chain`);
      }
    }
  }

  const orderedCommits = [...resolved].sort(
    (left, right) => predecessorCounts.get(left) - predecessorCounts.get(right),
  );
  const expectedCounts = orderedCommits.map((_, index) => index);
  const actualCounts = orderedCommits.map((commit) => predecessorCounts.get(commit));
  if (!actualCounts.every((count, index) => count === expectedCounts[index])) {
    throw new Error(`Cannot derive a unique commit order for ${resolved.join(', ')}`);
  }

  const parentLine = runGit(repoRoot, [
    'rev-list',
    '--parents',
    '-n',
    '1',
    orderedCommits[0],
  ]);
  const parents = parentLine.split(/\s+/).filter(Boolean).slice(1);
  if (parents.length === 0) {
    throw new Error(`Earliest commit ${orderedCommits[0]} has no parent`);
  }
  return { orderedCommits, baseCommit: parents[0] };
}

function getCumulativeDiff(repoRoot, baseCommit, targetCommit, files) {
  if (!files || files.length === 0) return '';
  try {
    return execFileSync(
      'git',
      [
        '-C',
        repoRoot,
        'diff',
        '--binary',
        '--full-index',
        '--no-ext-diff',
        baseCommit,
        targetCommit,
        '--',
        ...files,
      ],
      {
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'pipe'],
        maxBuffer: 64 * 1024 * 1024,
      },
    );
  } catch (error) {
    const stderr = error.stderr?.toString?.() ?? '';
    throw new Error(`git diff failed for ${baseCommit}..${targetCommit}: ${stderr || error.message}`);
  }
}

function historyForNodes(nodes) {
  if (nodes.length === 0) return null;
  const repoRoot = nodes[0].repoRoot;
  if (nodes.some((node) => node.repoRoot !== repoRoot)) {
    throw new Error('One episode cannot contain nodes from multiple repositories');
  }
  return {
    repoRoot,
    ...resolveLinearCommitHistory(repoRoot, nodes.map((node) => node.commit)),
  };
}

function buildCumulativeDiffFromNodes(nodes, baseCommit, orderedCommits) {
  if (nodes.length === 0) return '';
  const files = uniqSorted(
    nodes.flatMap((node) => node.diffPaths ?? [node.path]).filter(Boolean),
  );
  return getCumulativeDiff(nodes[0].repoRoot, baseCommit, orderedCommits.at(-1), files);
}

function extractComponent(startNodeId, graph) {
  const visited = new Set();
  const queue = [startNodeId];
  while (queue.length > 0) {
    const nodeId = queue.shift();
    if (!nodeId || visited.has(nodeId)) continue;
    const node = graph.nodes.get(nodeId);
    if (!node || node.consumed) continue;
    visited.add(nodeId);
    for (const neighborId of graph.adjacency.get(nodeId) ?? []) {
      const neighbor = graph.nodes.get(neighborId);
      if (!neighbor || neighbor.consumed || visited.has(neighborId)) continue;
      queue.push(neighborId);
    }
  }

  const prodNodes = [];
  const testNodes = [];
  for (const nodeId of visited) {
    const node = graph.nodes.get(nodeId);
    if (!node) continue;
    if (node.kind === 'prod') {
      prodNodes.push(node);
    } else if (node.kind === 'test') {
      testNodes.push(node);
    }
  }

  const edgePairs = [];
  for (const nodeId of visited) {
    const neighbors = graph.adjacency.get(nodeId) ?? new Set();
    for (const neighborId of neighbors) {
      if (!visited.has(neighborId) || nodeId >= neighborId) continue;
      edgePairs.push([nodeId, neighborId]);
    }
  }

  prodNodes.sort((a, b) => a.timestampMs - b.timestampMs || a.path.localeCompare(b.path));
  testNodes.sort((a, b) => a.timestampMs - b.timestampMs || a.path.localeCompare(b.path));

  return {
    nodeIds: [...visited].sort((a, b) => a.localeCompare(b)),
    prodNodes,
    testNodes,
    edgePairs,
  };
}

function markConsumed(nodes) {
  for (const node of nodes) {
    node.consumed = true;
  }
}

function summarizeNodes(nodes, orderedCommits = null) {
  return {
    nodeCount: nodes.length,
    commits: orderedCommits ?? uniqInOrder(nodes.map((node) => node.commit)),
    files: uniqSorted(nodes.map((node) => node.path)),
  };
}

function buildPositiveSample(component, context) {
  const prodHistory = historyForNodes(component.prodNodes);
  const testHistory = historyForNodes(component.testNodes);
  const prodSummary = summarizeNodes(component.prodNodes, prodHistory.orderedCommits);
  const testSummary = summarizeNodes(component.testNodes, testHistory.orderedCommits);
  const prodMergedDiff = buildCumulativeDiffFromNodes(
    component.prodNodes,
    prodHistory.baseCommit,
    prodHistory.orderedCommits,
  );
  const testMergedDiff = buildCumulativeDiffFromNodes(
    component.testNodes,
    prodHistory.baseCommit,
    testHistory.orderedCommits,
  );
  const sampleId = hashSampleId({
    label: 1,
    settlementCommit: context.settlementCommit,
    prodNodeIds: component.prodNodes.map((node) => node.id),
    testNodeIds: component.testNodes.map((node) => node.id),
  });

  return {
    sampleId,
    label: 1,
    baseCommit: prodHistory.baseCommit,
    settlement: {
      anchorCommit: context.settlementCommit,
      triggerCommit: context.triggerCommit,
      reason: context.reason,
      windowHours: context.windowHours,
    },
    prod: {
      ...prodSummary,
      nodes: component.prodNodes.map((node) => ({
        id: node.id,
        commit: node.commit,
        path: node.path,
        previousPath: node.previousPath,
        gitStatus: node.gitStatus,
      })),
      mergedDiff: prodMergedDiff,
    },
    test: {
      ...testSummary,
      nodes: component.testNodes.map((node) => ({
        id: node.id,
        commit: node.commit,
        path: node.path,
        previousPath: node.previousPath,
        gitStatus: node.gitStatus,
        coverageKind: node.coverageKind ?? null,
        runnerFamily: node.runnerFamily ?? null,
      })),
      mergedDiff: testMergedDiff,
    },
    graph: {
      componentNodeCount: component.nodeIds.length,
      componentEdgeCount: component.edgePairs.length,
      edges: component.edgePairs.map(([left, right]) => ({ from: left, to: right })),
    },
  };
}

function buildNegativeSample(prodNode, context) {
  const prodHistory = historyForNodes([prodNode]);
  const sampleId = hashSampleId({
    label: 0,
    settlementCommit: context.settlementCommit,
    prodNodeId: prodNode.id,
  });

  return {
    sampleId,
    label: 0,
    baseCommit: prodHistory.baseCommit,
    settlement: {
      anchorCommit: context.settlementCommit,
      triggerCommit: context.triggerCommit,
      reason: context.reason,
      windowHours: context.windowHours,
    },
    prod: {
      nodeCount: 1,
      commits: [prodNode.commit],
      files: [prodNode.path],
      nodes: [
        {
          id: prodNode.id,
          commit: prodNode.commit,
          path: prodNode.path,
          previousPath: prodNode.previousPath,
          gitStatus: prodNode.gitStatus,
        },
      ],
      mergedDiff: buildCumulativeDiffFromNodes(
        [prodNode],
        prodHistory.baseCommit,
        prodHistory.orderedCommits,
      ),
    },
    test: {
      nodeCount: 0,
      commits: [],
      files: [],
      nodes: [],
      mergedDiff: '',
    },
    graph: {
      componentNodeCount: 1,
      componentEdgeCount: 0,
      edges: [],
    },
  };
}

function buildUnresolvedSample(prodNode, context, ambiguity) {
  const prodHistory = historyForNodes([prodNode]);
  const sampleId = hashSampleId({
    label: 'unresolved',
    settlementCommit: context.settlementCommit,
    prodNodeId: prodNode.id,
    reasons: ambiguity.reasons,
  });

  return {
    sampleId,
    status: 'unresolved',
    baseCommit: prodHistory.baseCommit,
    settlement: {
      anchorCommit: context.settlementCommit,
      triggerCommit: context.triggerCommit,
      reason: context.reason,
      windowHours: context.windowHours,
    },
    unresolved: ambiguity,
    prod: {
      nodeCount: 1,
      commits: [prodNode.commit],
      files: [prodNode.path],
      nodes: [
        {
          id: prodNode.id,
          commit: prodNode.commit,
          path: prodNode.path,
          previousPath: prodNode.previousPath,
          gitStatus: prodNode.gitStatus,
        },
      ],
      mergedDiff: buildCumulativeDiffFromNodes(
        [prodNode],
        prodHistory.baseCommit,
        prodHistory.orderedCommits,
      ),
    },
    test: {
      nodeCount: 0,
      commits: [],
      files: [],
      nodes: [],
      mergedDiff: '',
    },
    graph: {
      componentNodeCount: 1,
      componentEdgeCount: 0,
      edges: [],
    },
  };
}

function noteSignalsFailure(note) {
  return FAILURE_NOTE_PATTERNS.some((pattern) => note.includes(pattern));
}

function edgeHasCoveredProdFiles(edge) {
  return (edge.coveredProdFileCount ?? edge.coveredProdFiles?.length ?? 0) > 0;
}

function edgeHasHardCoverageFailure(edge) {
  const category = edge?.failureClassification?.category ?? null;
  return category ? HARD_COVERAGE_FAILURE_CATEGORIES.has(category) : false;
}

function edgeHasOnlySoftTestFailure(edge) {
  const notes = Array.isArray(edge?.notes) ? edge.notes : [];
  const hasTestExitNote = notes.some((note) => String(note).startsWith('test_command_exit_'));
  const status = String(edge?.coverageStatus ?? '');
  const category = edge?.failureClassification?.category ?? null;
  return (
    edgeHasCoveredProdFiles(edge) &&
    !edgeHasHardCoverageFailure(edge) &&
    (hasTestExitNote ||
      status.endsWith('_with_test_failures') ||
      category === 'test_assertions_failed' ||
      category === 'command_failed')
  );
}

function isValidCoverageEdge(edge) {
  if (!edge) return false;
  if (!VALID_COVERAGE_KINDS.has(edge.coverageKind ?? '')) return false;
  if (!edge.runnerFamily || edge.runnerFamily === 'unknown' || edge.runnerFamily === 'other') {
    return false;
  }
  if (!edgeHasCoveredProdFiles(edge)) return false;
  if (edgeHasHardCoverageFailure(edge)) return false;
  if (edgeHasOnlySoftTestFailure(edge)) return true;
  const notes = Array.isArray(edge.notes) ? edge.notes : [];
  if (notes.some((note) => noteSignalsFailure(String(note)))) {
    return false;
  }
  return true;
}

function comparableStem(relPath, isTest = false) {
  const baseName = path.basename(relPath).toLowerCase();
  const withoutTestSuffix = isTest
    ? baseName.replace(/\.(test|spec|e2e|integration-test)\.[a-z0-9]+$/, '')
    : baseName;
  return withoutTestSuffix.replace(/\.[a-z0-9]+$/, '');
}

function sharedDirectoryPrefixLength(leftPath, rightPath) {
  const leftDirs = path.posix.dirname(leftPath).toLowerCase().split('/').filter(Boolean);
  const rightDirs = path.posix.dirname(rightPath).toLowerCase().split('/').filter(Boolean);
  let count = 0;
  while (count < leftDirs.length && count < rightDirs.length && leftDirs[count] === rightDirs[count]) {
    count += 1;
  }
  return count;
}

function testPathCouldTargetProd(prodPath, testPath) {
  if (!prodPath || !testPath) return false;
  const prodStem = comparableStem(prodPath, false);
  const testStem = comparableStem(testPath, true);
  if (prodStem && testStem && prodStem === testStem) return true;
  if (prodStem.length >= 4 && testStem.includes(prodStem)) return true;
  if (testStem.length >= 4 && prodStem.includes(testStem)) return true;
  const sharedPrefixLength = sharedDirectoryPrefixLength(prodPath, testPath);
  return sharedPrefixLength >= 3 && (prodStem === testStem || testPath.toLowerCase().includes(prodStem));
}

function summarizeRecordEvidence(record) {
  const semanticTestPathSet = buildSemanticTestPathSet(record);
  const selectedForCoverageCount = semanticTestPathSet.size;
  const preparedEnv = record.coverage?.preparedEnv ?? null;
  const installStatus = preparedEnv?.installStatus ?? null;
  const edges = Array.isArray(record.coverage?.edges) ? record.coverage.edges : [];
  const selectedTestFiles = (record.testChanges ?? [])
    .filter((item) => item.includeForCoverage && semanticTestPathSet.has(item.path))
    .map((item) => item.path)
    .filter(Boolean);
  const reasons = [];
  const recordLevelReasons = [];
  const ambiguousTestReasons = new Map();
  const addRecordLevelReason = (reason) => {
    if (!reason) return;
    reasons.push(reason);
    recordLevelReasons.push(reason);
  };
  const addEdgeReason = (edge, reason) => {
    if (!reason) return;
    reasons.push(reason);
    if (!edge?.testFile) return;
    const existing = ambiguousTestReasons.get(edge.testFile) ?? [];
    existing.push(reason);
    ambiguousTestReasons.set(edge.testFile, existing);
  };

  if (selectedForCoverageCount === 0) {
    return {
      hasSemanticTests: false,
      isAmbiguous: false,
      hasAnyValidCoverageAttempt: false,
      selectedTestFiles,
      recordLevelReasons,
      ambiguousTests: [],
      reasons,
    };
  }

  if (record.coverage?.status === 'coverage_skipped') {
    addRecordLevelReason(
      record.coverage?.coverageSkipReason ??
        record.coverage?.preparedEnv?.installFailureCategory ??
        'coverage_skipped',
    );
  }
  if (
    installStatus &&
    installStatus !== 'installed' &&
    installStatus !== 'reused' &&
    installStatus !== 'linked_from_source_repo'
  ) {
    addRecordLevelReason(`install_${installStatus}`);
  }
  if (edges.length === 0) {
    addRecordLevelReason('no_coverage_edges');
  }

  let hasAnyValidCoverageAttempt = false;
  let hasAnyInvalidCoverageAttempt = false;
  let hasAnyBlockingFailureClassification = false;
  for (const edge of edges) {
    if (edge.failureClassification?.category && edgeHasHardCoverageFailure(edge)) {
      hasAnyBlockingFailureClassification = true;
      addEdgeReason(edge, edge.failureClassification.category);
      for (const item of edge.failureClassification.summary ?? []) {
        addEdgeReason(edge, String(item));
      }
    }
    if (isValidCoverageEdge(edge)) {
      hasAnyValidCoverageAttempt = true;
    } else {
      hasAnyInvalidCoverageAttempt = true;
      addEdgeReason(
        edge,
        `edge_${edge.runnerFamily ?? 'unknown'}_${edge.coverageKind ?? 'none'}_${edge.coverageStatus ?? 'unknown'}`,
      );
      for (const note of edge.notes ?? []) {
        if (noteSignalsFailure(String(note))) {
          addEdgeReason(edge, String(note));
        }
      }
      if (edge.failureClassification?.category) {
        addEdgeReason(edge, edge.failureClassification.category);
      }
    }
  }

  if (selectedForCoverageCount > 0 && !hasAnyValidCoverageAttempt && ambiguousTestReasons.size === 0) {
    for (const testFile of selectedTestFiles) {
      ambiguousTestReasons.set(testFile, ['no_valid_coverage_attempt']);
    }
    reasons.push('no_valid_coverage_attempt');
  }

  const dedupedReasons = uniqSorted(reasons);
  const dedupedRecordLevelReasons = uniqSorted(recordLevelReasons);
  const ambiguousTests = [...ambiguousTestReasons.entries()]
    .map(([testFile, testReasons]) => ({
      testFile,
      reasons: uniqSorted(testReasons),
    }))
    .sort((left, right) => left.testFile.localeCompare(right.testFile));
  return {
    hasSemanticTests: selectedForCoverageCount > 0,
    hasAnyValidCoverageAttempt,
    hasAnyInvalidCoverageAttempt,
    selectedTestFiles,
    recordLevelReasons: dedupedRecordLevelReasons,
    ambiguousTests,
    isAmbiguous:
      dedupedRecordLevelReasons.length > 0 ||
      ambiguousTests.length > 0 ||
      hasAnyBlockingFailureClassification,
    reasons: dedupedReasons,
  };
}

function collectAmbiguityForProd(graph, prodNode) {
  const blockingCommits = [];
  for (const commitId of graph.activeCommitOrder) {
    const commitState = graph.commits.get(commitId);
    if (!commitState || commitState.timestampMs < prodNode.timestampMs) continue;
    if (!commitState.evidence?.isAmbiguous) continue;
    const ambiguousEntries =
      commitState.evidence.recordLevelReasons?.length > 0
        ? (commitState.evidence.selectedTestFiles ?? []).map((testFile) => ({
            testFile,
            reasons: commitState.evidence.recordLevelReasons,
          }))
        : commitState.evidence.ambiguousTests ?? [];
    const matchingEntries = ambiguousEntries.filter((item) =>
      testPathCouldTargetProd(prodNode.path, item.testFile),
    );
    const matchingTests = matchingEntries.map((item) => item.testFile);
    if (matchingTests.length === 0) continue;
    blockingCommits.push({
      commit: commitId,
      matchingTests: uniqSorted(matchingTests),
      reasons: uniqSorted(matchingEntries.flatMap((item) => item.reasons ?? [])),
    });
  }

  return {
    reasons: uniqSorted(blockingCommits.flatMap((item) => item.reasons)),
    blockingCommits,
  };
}

function createGraphContext(traceRecords, windowHours) {
  return {
    traceRecords,
    windowHours,
    windowMs: windowHours * 60 * 60 * 1000,
    nodes: new Map(),
    adjacency: new Map(),
    prodPathIndex: new Map(),
    commits: new Map(),
    activeCommitOrder: [],
    samples: [],
    unresolvedSamples: [],
    stats: {
      positiveSamples: 0,
      negativeSamples: 0,
      unresolvedSamples: 0,
      totalProdNodes: 0,
    totalTestEdgesSeen: 0,
    totalTestNodesLinked: 0,
      totalProdNodesDroppedAsNonSemantic: 0,
      commitsProcessed: 0,
      commitsSettled: 0,
    },
  };
}

function addNode(graph, node) {
  graph.nodes.set(node.id, node);
  graph.adjacency.set(node.id, new Set());
  if (node.kind === 'prod') {
    if (!graph.prodPathIndex.has(node.path)) {
      graph.prodPathIndex.set(node.path, new Set());
    }
    graph.prodPathIndex.get(node.path).add(node.id);
    graph.stats.totalProdNodes += 1;
  }
}

function addEdge(graph, leftId, rightId) {
  graph.adjacency.get(leftId)?.add(rightId);
  graph.adjacency.get(rightId)?.add(leftId);
}

function registerCommitState(graph, record) {
  graph.commits.set(record.commit, {
    commit: record.commit,
    timestampMs: record.timestampMs,
    authorDate: record.commitMeta.authorDate,
    evidence: summarizeRecordEvidence(record),
    prodNodeIds: [],
    testNodeIds: [],
  });
  graph.activeCommitOrder.push(record.commit);
}

function addProdNodesFromRecord(graph, record) {
  const commitState = graph.commits.get(record.commit);
  for (const change of record.prodChanges) {
    const semantic =
      change.semantic ??
      analyzeSourceChangeSemantics(
        record,
        change.path,
        change.previousPath,
        change.gitStatus,
        change.diffPaths,
      );
    if (semantic.decision === 'drop') {
      graph.stats.totalProdNodesDroppedAsNonSemantic += 1;
      continue;
    }
    const node = {
      id: change.nodeId,
      kind: 'prod',
      repoRoot: record.repoRoot,
      commit: record.commit,
      shortHash: record.commitMeta.shortHash,
      authorDate: record.commitMeta.authorDate,
      timestampMs: record.timestampMs,
      path: change.path,
      previousPath: change.previousPath,
      diffPaths: change.diffPaths,
      gitStatus: change.gitStatus,
      semantic,
      consumed: false,
    };
    addNode(graph, node);
    commitState.prodNodeIds.push(node.id);
  }
}

function getUnconsumedProdMatches(graph, coveredProdFiles) {
  const matchedIds = new Set();
  for (const coveredPath of coveredProdFiles) {
    for (const prodNodeId of graph.prodPathIndex.get(coveredPath) ?? []) {
      const prodNode = graph.nodes.get(prodNodeId);
      if (!prodNode || prodNode.consumed) continue;
      matchedIds.add(prodNodeId);
    }
  }
  return [...matchedIds].sort((a, b) => a.localeCompare(b));
}

function addTestEdgesFromRecord(graph, record) {
  const commitState = graph.commits.get(record.commit);
  for (const edge of record.coverage.edges ?? []) {
    graph.stats.totalTestEdgesSeen += 1;
    if (!isValidCoverageEdge(edge)) continue;
    const coveredProdFiles = uniqSorted(edge.coveredProdFiles ?? []);
    if (coveredProdFiles.length === 0) continue;
    const matchedProdNodeIds = getUnconsumedProdMatches(graph, coveredProdFiles);
    if (matchedProdNodeIds.length === 0) continue;

    const nodeId = buildNodeId('test', record.commit, edge.testFile);
    let testNode = graph.nodes.get(nodeId);
    if (!testNode) {
      testNode = {
        id: nodeId,
        kind: 'test',
        repoRoot: record.repoRoot,
        commit: record.commit,
        shortHash: record.commitMeta.shortHash,
        authorDate: record.commitMeta.authorDate,
        timestampMs: record.timestampMs,
        path: edge.testFile,
        previousPath: null,
        diffPaths: [edge.testFile],
        gitStatus: 'M',
        coverageKind: edge.coverageKind ?? null,
        coverageStatus: edge.coverageStatus ?? null,
        runnerFamily: edge.runnerFamily ?? null,
        coverageConfidence: edgeHasOnlySoftTestFailure(edge) ? 'low' : 'normal',
        consumed: false,
      };
      addNode(graph, testNode);
      commitState.testNodeIds.push(nodeId);
      graph.stats.totalTestNodesLinked += 1;
    }

    for (const prodNodeId of matchedProdNodeIds) {
      addEdge(graph, nodeId, prodNodeId);
    }
  }
}

function settleCommit(graph, commit, reason, triggerCommit) {
  const commitState = graph.commits.get(commit);
  if (!commitState) return;

  for (const prodNodeId of commitState.prodNodeIds) {
    const prodNode = graph.nodes.get(prodNodeId);
    if (!prodNode || prodNode.consumed) continue;

    const component = extractComponent(prodNodeId, graph);
    if (component.testNodes.length === 0) {
      const ambiguity = collectAmbiguityForProd(graph, prodNode);
      if (ambiguity.reasons.length > 0) {
        const sample = buildUnresolvedSample(prodNode, {
          settlementCommit: commit,
          triggerCommit,
          reason,
          windowHours: graph.windowHours,
        }, ambiguity);
        graph.unresolvedSamples.push(sample);
        graph.stats.unresolvedSamples += 1;
        prodNode.consumed = true;
        continue;
      }
      const sample = buildNegativeSample(prodNode, {
        settlementCommit: commit,
        triggerCommit,
        reason,
        windowHours: graph.windowHours,
      });
      graph.samples.push(sample);
      graph.stats.negativeSamples += 1;
      prodNode.consumed = true;
      continue;
    }

    const sample = buildPositiveSample(component, {
      settlementCommit: commit,
      triggerCommit,
      reason,
      windowHours: graph.windowHours,
    });
    graph.samples.push(sample);
    graph.stats.positiveSamples += 1;
    markConsumed([...component.prodNodes, ...component.testNodes]);
  }

  graph.stats.commitsSettled += 1;
  graph.activeCommitOrder = graph.activeCommitOrder.filter((item) => item !== commit);
}

function settleExpiredBefore(graph, nextTimestampMs, nextCommit) {
  while (graph.activeCommitOrder.length > 0) {
    const oldestCommit = graph.activeCommitOrder[0];
    const oldestState = graph.commits.get(oldestCommit);
    if (!oldestState) {
      graph.activeCommitOrder.shift();
      continue;
    }
    if (nextTimestampMs - oldestState.timestampMs <= graph.windowMs) {
      break;
    }
    settleCommit(graph, oldestCommit, 'window_expired', nextCommit);
  }
}

function pairTraceRecords(records, options = {}) {
  const withIndex = records.map((record, index) => ({ record, index }));
  withIndex.sort((left, right) => {
    const delta = left.record.timestampMs - right.record.timestampMs;
    if (delta !== 0) return delta;
    return left.index - right.index;
  });

  const sortedRecords = withIndex.map((item) => item.record);
  const graph = createGraphContext(sortedRecords, options.windowHours ?? 12);

  for (const record of sortedRecords) {
    settleExpiredBefore(graph, record.timestampMs, record.commit);
    registerCommitState(graph, record);
    addProdNodesFromRecord(graph, record);
    addTestEdgesFromRecord(graph, record);
    graph.stats.commitsProcessed += 1;
  }

  const flushOpenWindow = options.flushOpenWindow ?? true;
  if (flushOpenWindow) {
    while (graph.activeCommitOrder.length > 0) {
      settleCommit(graph, graph.activeCommitOrder[0], 'final_flush', null);
    }
  }

  return {
    schemaVersion: 1,
    repoRoot: sortedRecords[0]?.repoRoot ?? null,
    windowHours: graph.windowHours,
    traceRecordCount: sortedRecords.length,
    commitOrder: sortedRecords.map((record) => ({
      commit: record.commit,
      authorDate: record.commitMeta.authorDate,
      subject: record.commitMeta.subject,
    })),
    stats: {
      ...graph.stats,
      sampleCount: graph.samples.length,
      unresolvedSampleCount: graph.unresolvedSamples.length,
      activeCommitCount: graph.activeCommitOrder.length,
    },
    partial: !flushOpenWindow,
    activeCommitOrder: graph.activeCommitOrder,
    samples: graph.samples,
    unresolvedSamples: graph.unresolvedSamples,
  };
}

function toDatasetSample(sample, repoRoot) {
  return {
    sample_id: sample.sampleId,
    repo: repoRoot ? path.basename(repoRoot) : null,
    label: sample.label,
    base_commit: sample.baseCommit,
    prod_commits: sample.prod?.commits ?? [],
    test_commits: sample.test?.commits ?? [],
    prod_files: sample.prod?.files ?? [],
    test_files: sample.test?.files ?? [],
    prod_diff: sample.prod?.mergedDiff ?? '',
    test_diff: sample.test?.mergedDiff ?? '',
  };
}

function toDatasetSamples(pairing) {
  return pairing.samples.map((sample) => toDatasetSample(sample, pairing.repoRoot));
}

function loadCommitsFromFile(filePath) {
  const raw = fs.readFileSync(filePath, 'utf8');
  const trimmed = raw.trim();
  if (!trimmed) return [];
  if (trimmed.startsWith('[') || trimmed.startsWith('{')) {
    const parsed = JSON.parse(trimmed);
    if (Array.isArray(parsed)) {
      return parsed.map((item) => String(item).trim()).filter(Boolean);
    }
    if (Array.isArray(parsed.commits)) {
      return parsed.commits.map((item) => String(item).trim()).filter(Boolean);
    }
    throw new Error(`Unsupported commit file JSON shape: ${filePath}`);
  }
  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function resolveCommitList(repoRoot, args) {
  const commits = [];
  if (args.commit) commits.push(args.commit);
  if (args.commits.length > 0) commits.push(...args.commits);
  if (args.commitsFile) commits.push(...loadCommitsFromFile(args.commitsFile));
  if (args.revRange) {
    const listed = runGit(repoRoot, ['rev-list', '--reverse', args.revRange])
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);
    commits.push(...listed);
  }
  return [...new Set(commits)].filter((commit) => {
    const line = runGit(repoRoot, ['rev-list', '--parents', '-n', '1', commit], true);
    return Boolean(line && line.trim().split(/\s+/).length > 1);
  });
}

function loadAutoResumeRecords(outputDir, commits, args) {
  const explicitResumePath = args.resumeRecords ? path.resolve(args.resumeRecords) : null;
  const autoResumePath = path.join(outputDir, 'records.partial.json');
  const resumePath =
    explicitResumePath ??
    (args.autoResume && fs.existsSync(autoResumePath) ? autoResumePath : null);
  if (!resumePath) {
    return {
      records: [],
      resumePath: null,
    };
  }

  const parsed = readJson(resumePath);
  if (!Array.isArray(parsed)) {
    throw new Error(`Unsupported resume records shape: ${resumePath}`);
  }
  const commitSet = new Set(commits);
  return {
    records: parsed.filter((record) => commitSet.has(record.commit)),
    resumePath,
  };
}

function buildTraceIndexItem(record, recordPath = null) {
  return {
    commit: record.commit,
    traceRecordPath: recordPath,
    authorDate: record.commitMeta.authorDate,
    subject: record.commitMeta.subject,
    prodChangeCount: record.prodChanges.length,
    semanticTestCount: record.coverage.selectedForCoverageCount,
    coverageStatus: record.coverage.status,
    coverageSkipReason: record.coverage.coverageSkipReason ?? null,
  };
}

function buildFailedTraceRecord(repoRoot, commit, error, args) {
  const meta = getCommitMeta(repoRoot, commit);
  const base = resolveBaseCommit(repoRoot, commit, args.base ?? null);
  const diffEntries = listChangedFiles(repoRoot, base, commit);
  const prodChanges = buildProdChanges(diffEntries, commit);
  return {
    schemaVersion: 1,
    repoRoot,
    commit,
    base,
    commitMeta: meta,
    timestampMs: toTimestampMs(meta.authorDate),
    diffEntries,
    prodChanges,
    testChanges: [],
    coverage: {
      status: 'coverage_skipped',
      changedTestFileCount: 0,
      selectedForCoverageCount: 0,
      preparedEnv: {
        installStatus: 'failed',
        installFailureCategory: 'trace_error',
        installFailureSummary: [error.message],
      },
      edges: [],
    },
    traceError: {
      message: error.message,
      stack: error.stack ?? null,
    },
  };
}

async function runTraceCommit(args) {
  const { record, recordPath } = await buildCommitTraceRecord(args);
  if (args.outputJson) {
    writeJson(path.resolve(args.outputJson), record);
  }
  process.stdout.write(`${JSON.stringify({ recordPath, record }, null, 2)}\n`);
}

function runPairRecords(args) {
  const parsed = readJson(path.resolve(args.recordsFile));
  const records = Array.isArray(parsed) ? parsed : parsed.records;
  if (!Array.isArray(records)) {
    throw new Error(`Unsupported records file shape: ${args.recordsFile}`);
  }
  const details = pairTraceRecords(records, { windowHours: args.windowHours });
  const result = toDatasetSamples(details);
  if (args.outputJson) {
    writeJson(path.resolve(args.outputJson), result);
  }
  if (args.detailsJson) {
    writeJson(path.resolve(args.detailsJson), details);
  }
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

async function runEndToEnd(args) {
  const repoRoot = path.resolve(args.repo);
  const outputDir = path.resolve(args.outputDir);
  ensureDir(outputDir);
  const repoName = path.basename(repoRoot);

  const commits = resolveCommitList(repoRoot, args);
  if (commits.length === 0) {
    throw new Error('No commits resolved for run subcommand');
  }

  const { records: resumedRecords, resumePath } = loadAutoResumeRecords(outputDir, commits, args);
  writeRunEvent(outputDir, {
    event: 'start',
    stage: 'run',
    status: 'running',
    repoName,
    processedCommitCount: resumedRecords.length,
    totalCommitCount: commits.length,
    message: resumePath ? `Resumed from existing records: ${resumePath}` : 'No resumable records found; starting from scratch',
  });

  const commitSet = new Set(commits);
  const traceRecords = resumedRecords.filter((record) => commitSet.has(record.commit));
  const completedCommits = new Set(traceRecords.map((record) => record.commit));
  const traceIndex = traceRecords.map((record) => buildTraceIndexItem(record));
  const resumedRecordCount = traceRecords.length;
  const tempBaseDir = fs.mkdtempSync(path.join(os.tmpdir(), 'graph_pairer_run_'));

  try {
    for (const commit of commits) {
      if (completedCommits.has(commit)) {
        continue;
      }
      const commitDir = path.join(tempBaseDir, 'commit_traces', sanitizeId(commit));
      let record;
      let recordPath = null;
      try {
        const built = await buildCommitTraceRecord({
          repo: repoRoot,
          commit,
          base: args.base ?? null,
          outputDir: commitDir,
          skipInstall: args.skipInstall,
          forceReinstall: args.forceReinstall,
          profileConfig: args.profileConfig ?? null,
          coverageTimeoutMs: args.coverageTimeoutMs ?? null,
          envPackageSubdir: args.envPackageSubdir ?? null,
        });
        record = built.record;
        recordPath = built.recordPath;
      } catch (error) {
        writeRunEvent(outputDir, {
          event: 'commit_failed',
          stage: 'trace-commit',
          status: args.continueOnError ? 'continued' : 'failed',
          repoName,
          commit,
          processedCommitCount: traceRecords.length,
          totalCommitCount: commits.length,
          error: error.message,
        });
        if (!args.continueOnError) {
          throw error;
        }
        record = buildFailedTraceRecord(repoRoot, commit, error, args);
      }
      traceRecords.push(record);
      completedCommits.add(commit);
      traceIndex.push(buildTraceIndexItem(record, recordPath));
      writeRunCheckpoint(outputDir, {
        status: 'running',
        traceRecords,
        traceIndex,
        totalCommitCount: commits.length,
        lastNewCommit: commit,
        resumedRecordCount,
      });
      writePairingSnapshot(outputDir, traceRecords, traceIndex, {
        windowHours: args.windowHours,
        final: false,
      });
      const partialSummary = readJson(path.join(outputDir, 'summary.partial.json'));
      writeRunEvent(outputDir, {
          event: 'commit_completed',
        stage: 'trace-commit',
        status: 'running',
        repoName,
        commit,
        processedCommitCount: traceRecords.length,
        totalCommitCount: commits.length,
        sampleCount: partialSummary.stats?.sampleCount ?? null,
        positiveSamples: partialSummary.labelCounts?.positive ?? null,
        negativeSamples: partialSummary.labelCounts?.negative ?? null,
        unresolvedSamples: partialSummary.labelCounts?.unresolved ?? null,
        message: `Completed ${traceRecords.length}/${commits.length}`,
      });
    }

    const details = pairTraceRecords(traceRecords, { windowHours: args.windowHours });
    details.traceIndex = traceIndex;
    const result = toDatasetSamples(details);

    const outputJsonPath = path.resolve(args.outputJson ?? path.join(outputDir, 'pairing.json'));
    const detailsJsonPath = path.resolve(args.detailsJson ?? path.join(outputDir, 'pairing_details.json'));
    writeJson(outputJsonPath, result);
    writeJson(detailsJsonPath, details);
    writeJson(path.join(outputDir, 'summary.json'), summarizePairing(details));
    writeJson(path.join(outputDir, 'diagnostics.json'), buildPairingDiagnostics(details));
    if (args.recordsJson) {
      writeJson(path.resolve(args.recordsJson), traceRecords);
    }
    writeRunCheckpoint(outputDir, {
      status: 'completed',
      traceRecords,
      traceIndex,
      totalCommitCount: commits.length,
      lastNewCommit: traceRecords.at(-1)?.commit ?? null,
      resumedRecordCount,
    });
    writeRunEvent(outputDir, {
      event: 'completed',
      stage: 'run',
      status: 'completed',
      repoName,
      processedCommitCount: traceRecords.length,
      totalCommitCount: commits.length,
      sampleCount: result.length,
      positiveSamples: details.stats.positiveSamples,
      negativeSamples: details.stats.negativeSamples,
      unresolvedSamples: details.stats.unresolvedSamples,
      message: 'Batch completed',
    });

    process.stdout.write(
      `${JSON.stringify(
        {
          outputJsonPath,
          detailsJsonPath,
          recordsJsonPath: args.recordsJson ? path.resolve(args.recordsJson) : null,
          sampleCount: result.length,
        },
        null,
        2,
      )}\n`,
    );
  } finally {
    removeDirIfExists(tempBaseDir);
  }
}

async function main() {
  if (process.argv[2] === '--help') {
    console.log('Usage: node graph_pairer.mjs <trace-commit|pair-records|run> ...');
    return;
  }
  const args = parseArgs(process.argv.slice(2));
  if (args.subcommand === 'trace-commit') {
    await runTraceCommit(args);
    return;
  }
  if (args.subcommand === 'pair-records') {
    runPairRecords(args);
    return;
  }
  if (args.subcommand === 'run') {
    await runEndToEnd(args);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === __filename) {
  await main();
}

export { buildCommitTraceRecord, pairTraceRecords, runEndToEnd, toDatasetSamples };
