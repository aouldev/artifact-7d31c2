import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const TEST_NAME_PLACEHOLDER = '__TEST_NAME__';
const TEST_ROOT_NAMES = new Set(['describe', 'it', 'test']);
const CODE_EXTENSIONS = new Set([
  '.js',
  '.jsx',
  '.ts',
  '.tsx',
  '.mjs',
  '.cjs',
  '.mts',
  '.cts',
]);
const RUNNER_CONFIG_FILES = {
  jest: [
    '.jestconfig.json',
    'jest.config.js',
    'jest.config.cjs',
    'jest.config.mjs',
    'jest.config.ts',
    'jest.config.cts',
    'jest.config.mts',
  ],
  vitest: [
    'vitest.config.js',
    'vitest.config.cjs',
    'vitest.config.mjs',
    'vitest.config.ts',
    'vitest.config.cts',
    'vitest.config.mts',
  ],
  playwright: [
    'playwright.config.js',
    'playwright.config.cjs',
    'playwright.config.mjs',
    'playwright.config.ts',
  ],
  other: [
    'cypress.config.js',
    'cypress.config.cjs',
    'cypress.config.mjs',
    'cypress.config.ts',
  ],
};
const JSON_AT_COMMIT_CACHE = new Map();
const FILE_AT_COMMIT_CACHE = new Map();

function parseArgs(argv) {
  const args = {
    repo: null,
    commit: null,
    base: null,
    output: null,
  };
  for (let i = 0; i < argv.length; i += 1) {
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
    } else if (token === '--output') {
      args.output = argv[i + 1];
      i += 1;
    } else {
      throw new Error(`Unknown arg: ${token}`);
    }
  }
  if (!args.repo || !args.commit) {
    throw new Error('Usage: node commit_test_locator.mjs --repo <repo> --commit <sha> [--base <sha>] [--output <path>]');
  }
  return args;
}

function runGit(repoRoot, args, allowFailure = false) {
  try {
    return execFileSync('git', ['-C', repoRoot, ...args], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
    }).trimEnd();
  } catch (error) {
    if (allowFailure) {
      return null;
    }
    const stderr = error.stderr?.toString?.() ?? '';
    throw new Error(`git ${args.join(' ')} failed: ${stderr || error.message}`);
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
  if (/(^|\/)(playwright|cypress)(\/|$)/.test(p)) return true;
  if (/(^|\/)(__tests__|tests)(\/|$)/.test(p)) return true;
  if (/(?:^|[._-])(?:test|spec)\.[a-z0-9]+$/.test(p)) return true;
  return /\.(test|spec|e2e|integration-test)\.[a-z0-9]+$/.test(p);
}

function isSupportedCodeFile(relPath) {
  return CODE_EXTENSIONS.has(path.extname(relPath).toLowerCase());
}

function readFileAtCommit(repoRoot, ref, relPath) {
  const value = runGit(repoRoot, ['show', `${ref}:${relPath}`], true);
  return value;
}

function toPosixPath(relPath) {
  return relPath.split(path.sep).join('/');
}

function safeParseJson(rawText) {
  try {
    return JSON.parse(rawText);
  } catch {
    return null;
  }
}

function readJsonAtCommit(repoRoot, commit, relPath) {
  const key = `${repoRoot}::${commit}::${relPath}`;
  if (JSON_AT_COMMIT_CACHE.has(key)) {
    return JSON_AT_COMMIT_CACHE.get(key);
  }
  const rawText = readFileAtCommit(repoRoot, commit, relPath);
  const parsed = rawText == null ? null : safeParseJson(rawText);
  JSON_AT_COMMIT_CACHE.set(key, parsed);
  return parsed;
}

function fileExistsAtCommit(repoRoot, commit, relPath) {
  const key = `${repoRoot}::${commit}::${relPath}`;
  if (FILE_AT_COMMIT_CACHE.has(key)) {
    return FILE_AT_COMMIT_CACHE.get(key);
  }
  const exists = readFileAtCommit(repoRoot, commit, relPath) != null;
  FILE_AT_COMMIT_CACHE.set(key, exists);
  return exists;
}

function listAncestorDirs(relPath) {
  const normalized = toPosixPath(relPath);
  const dirs = [];
  let current = path.posix.dirname(normalized);
  while (true) {
    dirs.push(current === '.' ? '' : current);
    if (!current || current === '.') break;
    const parent = path.posix.dirname(current);
    current = parent === current ? '' : parent;
    if (!current) {
      dirs.push('');
      break;
    }
  }
  return [...new Set(dirs)];
}

function addRunnerScore(scores, runner, weight) {
  scores[runner] = (scores[runner] ?? 0) + weight;
}

function scorePackageJsonSignals(pkg, proximityWeight, scores) {
  if (!pkg || typeof pkg !== 'object') return;

  const scripts = Object.values(pkg.scripts ?? {}).join('\n');
  if (/\bvitest\b/.test(scripts)) addRunnerScore(scores, 'vitest', proximityWeight + 2);
  if (/\bjest\b/.test(scripts)) addRunnerScore(scores, 'jest', proximityWeight + 2);
  if (/\bplaywright\b/.test(scripts)) addRunnerScore(scores, 'playwright', proximityWeight + 2);
  if (/\bcypress\b/.test(scripts)) addRunnerScore(scores, 'other', proximityWeight + 2);

  const deps = {
    ...(pkg.dependencies ?? {}),
    ...(pkg.devDependencies ?? {}),
    ...(pkg.peerDependencies ?? {}),
  };
  if (deps.vitest || deps['@vitest/ui']) addRunnerScore(scores, 'vitest', proximityWeight + 1);
  if (deps.jest || deps['jest-cli'] || deps['ts-jest'] || deps['babel-jest'] || deps['@types/jest']) {
    addRunnerScore(scores, 'jest', proximityWeight + 1);
  }
  if (deps['@playwright/test']) addRunnerScore(scores, 'playwright', proximityWeight + 1);
  if (deps.cypress) addRunnerScore(scores, 'other', proximityWeight + 1);
}

function scoreConfigSignals(repoRoot, commit, relPath, scores) {
  const dirs = listAncestorDirs(relPath);
  for (let index = 0; index < dirs.length; index += 1) {
    const dir = dirs[index];
    const proximityWeight = Math.max(1, 4 - index);
    for (const [runner, configNames] of Object.entries(RUNNER_CONFIG_FILES)) {
      if (
        configNames.some((name) =>
          fileExistsAtCommit(repoRoot, commit, dir ? `${dir}/${name}` : name),
        )
      ) {
        addRunnerScore(scores, runner, proximityWeight);
      }
    }
  }
}

function detectRunnerFromNearestConfig(repoRoot, commit, relPath, candidateRunners) {
  const dirs = listAncestorDirs(relPath);
  for (const dir of dirs) {
    for (const runner of candidateRunners) {
      const configNames = RUNNER_CONFIG_FILES[runner] ?? [];
      if (
        configNames.some((name) =>
          fileExistsAtCommit(repoRoot, commit, dir ? `${dir}/${name}` : name),
        )
      ) {
        return runner;
      }
    }
  }
  return null;
}

function detectRunnerFromNearestPackage(repoRoot, commit, relPath, candidateRunners) {
  const dirs = listAncestorDirs(relPath);
  for (const dir of dirs) {
    const packageJsonPath = dir ? `${dir}/package.json` : 'package.json';
    const pkg = readJsonAtCommit(repoRoot, commit, packageJsonPath);
    if (!pkg) continue;
    const scripts = Object.values(pkg.scripts ?? {}).join('\n');
    const deps = {
      ...(pkg.dependencies ?? {}),
      ...(pkg.devDependencies ?? {}),
      ...(pkg.peerDependencies ?? {}),
    };
    if (candidateRunners.includes('vitest') && (/\bvitest\b/.test(scripts) || deps.vitest || deps['@vitest/ui'])) {
      return 'vitest';
    }
    if (
      candidateRunners.includes('jest') &&
      (/\bjest\b/.test(scripts) || deps.jest || deps['jest-cli'] || deps['ts-jest'] || deps['babel-jest'])
    ) {
      return 'jest';
    }
  }
  return null;
}

function scorePackageHierarchy(repoRoot, commit, relPath, scores) {
  const dirs = listAncestorDirs(relPath);
  for (let index = 0; index < dirs.length; index += 1) {
    const dir = dirs[index];
    const packageJsonPath = dir ? `${dir}/package.json` : 'package.json';
    const pkg = readJsonAtCommit(repoRoot, commit, packageJsonPath);
    if (!pkg) continue;
    const proximityWeight = Math.max(1, 5 - index);
    scorePackageJsonSignals(pkg, proximityWeight, scores);
  }
}

function chooseBestRunner(scores) {
  const ranked = Object.entries(scores)
    .filter(([, score]) => score > 0)
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]));
  if (ranked.length === 0) return 'unknown';
  if (ranked.length > 1 && ranked[0][1] === ranked[1][1]) return 'unknown';
  return ranked[0][0];
}

function getRepoRequire(repoRoot) {
  return createRequire(path.join(repoRoot, 'package.json'));
}

function loadTypeScript(repoRoot) {
  const repoRequire = getRepoRequire(repoRoot);
  const tsPath = repoRequire.resolve('typescript');
  return repoRequire(tsPath);
}

function detectScriptKind(ts, relPath) {
  const ext = path.extname(relPath).toLowerCase();
  if (ext === '.tsx') return ts.ScriptKind.TSX;
  if (ext === '.jsx') return ts.ScriptKind.JSX;
  if (ext === '.js' || ext === '.mjs' || ext === '.cjs') return ts.ScriptKind.JS;
  return ts.ScriptKind.TS;
}

function getTestRootName(ts, expr) {
  if (!expr) return null;
  if (ts.isIdentifier(expr) && TEST_ROOT_NAMES.has(expr.text)) {
    return expr.text;
  }
  if (ts.isPropertyAccessExpression(expr) || ts.isElementAccessExpression(expr)) {
    return getTestRootName(ts, expr.expression);
  }
  if (ts.isCallExpression(expr)) {
    return getTestRootName(ts, expr.expression);
  }
  if (ts.isParenthesizedExpression(expr)) {
    return getTestRootName(ts, expr.expression);
  }
  return null;
}

function isIgnorableTestNameArg(ts, node) {
  return (
    ts.isStringLiteral(node) ||
    ts.isNoSubstitutionTemplateLiteral(node) ||
    ts.isTemplateExpression(node)
  );
}

function normalizeTestSourceText(ts, sourceText, relPath) {
  const scriptKind = detectScriptKind(ts, relPath);
  const sourceFile = ts.createSourceFile(
    relPath,
    sourceText,
    ts.ScriptTarget.Latest,
    false,
    scriptKind,
  );

  let testUnitCount = 0;
  const transformer = (context) => {
    const visit = (node) => {
      if (ts.isCallExpression(node)) {
        const testRootName = getTestRootName(ts, node.expression);
        if (testRootName) {
          testUnitCount += 1;
          if (node.arguments.length > 0 && isIgnorableTestNameArg(ts, node.arguments[0])) {
            const nextArgs = [...node.arguments];
            nextArgs[0] = ts.factory.createStringLiteral(TEST_NAME_PLACEHOLDER);
            return ts.visitEachChild(
              ts.factory.updateCallExpression(
                node,
                node.expression,
                node.typeArguments,
                nextArgs,
              ),
              visit,
              context,
            );
          }
        }
      }
      return ts.visitEachChild(node, visit, context);
    };
    return (node) => ts.visitNode(node, visit);
  };

  const transformed = ts.transform(sourceFile, [transformer]);
  const printer = ts.createPrinter({
    removeComments: true,
    newLine: ts.NewLineKind.LineFeed,
  });
  const normalized = printer.printFile(transformed.transformed[0]);
  transformed.dispose();

  return {
    normalizedText: normalized.trim(),
    testUnitCount,
    parseDiagnosticCount: sourceFile.parseDiagnostics.length,
  };
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

function roughTestUnitCount(sourceText) {
  if (!sourceText) return 0;
  const withoutComments = sourceText
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1');
  const matches = withoutComments.match(
    /(?:^|[^\w$])(?:describe|it|test)(?:\.(?:only|skip|todo|each|concurrent))*\s*\(/g,
  );
  return matches?.length ?? 0;
}

function analyzeSemanticChange(repoRoot, relPath, beforeText, afterText, gitStatus) {
  if (!isSupportedCodeFile(relPath)) {
    return {
      decision: 'keep',
      reason: 'unsupported_extension',
      normalizedEquivalent: null,
      beforeTestUnitCount: null,
      afterTestUnitCount: null,
    };
  }

  let ts;
  try {
    ts = loadTypeScript(repoRoot);
  } catch (error) {
    const beforeTestUnitCount = roughTestUnitCount(beforeText);
    const afterTestUnitCount = roughTestUnitCount(afterText);
    if (afterTestUnitCount === 0) {
      return {
        decision: 'drop',
        reason: 'no_test_units_after_text_scan',
        normalizedEquivalent: null,
        beforeTestUnitCount,
        afterTestUnitCount,
        parserError: error.message,
      };
    }
    return {
      decision: 'keep',
      reason: 'missing_typescript_parser',
      normalizedEquivalent: null,
      beforeTestUnitCount,
      afterTestUnitCount,
      parserError: error.message,
    };
  }

  try {
    const beforeInfo = beforeText == null ? null : normalizeTestSourceText(ts, beforeText, relPath);
    const afterInfo = afterText == null ? null : normalizeTestSourceText(ts, afterText, relPath);

    if ((afterInfo?.testUnitCount ?? 0) === 0) {
      return {
        decision: 'drop',
        reason: 'no_test_units_after_parse',
        normalizedEquivalent: null,
        beforeTestUnitCount: beforeInfo?.testUnitCount ?? 0,
        afterTestUnitCount: afterInfo?.testUnitCount ?? 0,
      };
    }

    if (gitStatus === 'A' || beforeInfo == null) {
      return {
        decision: 'keep',
        reason: 'file_added',
        normalizedEquivalent: false,
        beforeTestUnitCount: beforeInfo?.testUnitCount ?? 0,
        afterTestUnitCount: afterInfo?.testUnitCount ?? 0,
      };
    }

    const beforeTypeErasedInfo = normalizeTestSourceText(
      ts,
      eraseTypeOnlyImportSyntax(beforeText),
      relPath,
    );
    const afterTypeErasedInfo = normalizeTestSourceText(
      ts,
      eraseTypeOnlyImportSyntax(afterText),
      relPath,
    );
    if (beforeTypeErasedInfo.normalizedText === afterTypeErasedInfo.normalizedText) {
      return {
        decision: 'drop',
        reason: 'type_import_only_non_semantic',
        normalizedEquivalent: true,
        beforeTestUnitCount: beforeInfo.testUnitCount,
        afterTestUnitCount: afterInfo.testUnitCount,
        parseDiagnosticCount:
          (beforeInfo.parseDiagnosticCount ?? 0) + (afterInfo.parseDiagnosticCount ?? 0),
      };
    }

    if (stripImportExportDeclarations(beforeText) === stripImportExportDeclarations(afterText)) {
      return {
        decision: 'drop',
        reason: 'import_export_only_non_semantic',
        normalizedEquivalent: true,
        beforeTestUnitCount: beforeInfo.testUnitCount,
        afterTestUnitCount: afterInfo.testUnitCount,
        parseDiagnosticCount:
          (beforeInfo.parseDiagnosticCount ?? 0) + (afterInfo.parseDiagnosticCount ?? 0),
      };
    }

    const normalizedEquivalent = beforeInfo.normalizedText === afterInfo.normalizedText;
    return {
      decision: normalizedEquivalent ? 'drop' : 'keep',
      reason: normalizedEquivalent ? 'ast_equivalent_non_semantic' : 'ast_changed',
      normalizedEquivalent,
      beforeTestUnitCount: beforeInfo.testUnitCount,
      afterTestUnitCount: afterInfo.testUnitCount,
      parseDiagnosticCount: (beforeInfo.parseDiagnosticCount ?? 0) + (afterInfo.parseDiagnosticCount ?? 0),
    };
  } catch (error) {
    return {
      decision: 'keep',
      reason: 'parse_error_keep_conservative',
      normalizedEquivalent: null,
      beforeTestUnitCount: null,
      afterTestUnitCount: null,
      parserError: error.message,
    };
  }
}

function guessRunnerFamily(repoRoot, commit, relPath, afterText) {
  const p = relPath.toLowerCase();
  if (
    p.startsWith('e2e-playwright/') ||
    p.includes('/e2e-playwright/') ||
    p.includes('/playwright/') ||
    p.startsWith('e2e/') ||
    p.includes('/e2e/') ||
    /\.(e2e)\.[a-z0-9]+$/.test(p)
  ) return 'playwright';
  if (p.includes('/cypress/')) return 'other';
  const text = afterText ?? '';
  if (/\bfrom\s+['"]@playwright\/test['"]/.test(text)) return 'playwright';
  if (/\bfrom\s+['"]cypress['"]|\bcy\./.test(text)) return 'other';
  if (/\bfrom\s+['"]vitest['"]|\bvi\./.test(text)) return 'vitest';
  if (/\bfrom\s+['"]@jest\/globals['"]|\bjest\./.test(text)) return 'jest';
  if (/(?:^|[._-])(?:test|spec)\.[a-z0-9]+$/.test(p)) {
    const configuredRunner = detectRunnerFromNearestConfig(repoRoot, commit, relPath, ['vitest', 'jest']);
    if (configuredRunner) return configuredRunner;
    const packageRunner = detectRunnerFromNearestPackage(repoRoot, commit, relPath, ['vitest', 'jest']);
    if (packageRunner) return packageRunner;
  }

  const scores = {
    jest: 0,
    vitest: 0,
    playwright: 0,
    other: 0,
  };
  scorePackageHierarchy(repoRoot, commit, relPath, scores);
  scoreConfigSignals(repoRoot, commit, relPath, scores);
  return chooseBestRunner(scores);
}

function buildTargetExpression(relPath) {
  return relPath;
}

export function compareNormalizedTestSources(repoRoot, relPath, beforeText, afterText) {
  return analyzeSemanticChange(repoRoot, relPath, beforeText, afterText, 'M');
}

function analyzeChangedTestFile(repoRoot, base, commit, entry) {
  const relPath = entry.path;
  const beforePath = entry.oldPath ?? relPath;

  if (!isSupportedCodeFile(relPath)) {
    return {
      path: relPath,
      previousPath: beforePath === relPath ? null : beforePath,
      gitStatus: entry.status,
      includeForCoverage: false,
      runnerFamilyGuess: 'unknown',
      targetExpression: null,
      semantic: {
        decision: 'drop',
        reason: 'unsupported_non_code_test_artifact',
        normalizedEquivalent: null,
        beforeTestUnitCount: null,
        afterTestUnitCount: null,
      },
    };
  }

  if (entry.status === 'D') {
    return {
      path: relPath,
      previousPath: beforePath,
      gitStatus: entry.status,
      includeForCoverage: false,
      runnerFamilyGuess: 'unknown',
      targetExpression: null,
      semantic: {
        decision: 'drop',
        reason: 'deleted_test_file',
        normalizedEquivalent: null,
        beforeTestUnitCount: null,
        afterTestUnitCount: null,
      },
    };
  }

  const beforeText = entry.status === 'A' ? null : readFileAtCommit(repoRoot, base, beforePath);
  const afterText = readFileAtCommit(repoRoot, commit, relPath);
  const semantic = analyzeSemanticChange(repoRoot, relPath, beforeText, afterText, entry.status);
  const runnerFamilyGuess = guessRunnerFamily(repoRoot, commit, relPath, afterText);
  const includeForCoverage = semantic.decision !== 'drop';

  return {
    path: relPath,
    previousPath: beforePath === relPath ? null : beforePath,
    gitStatus: entry.status,
    includeForCoverage,
    runnerFamilyGuess,
    targetExpression: includeForCoverage ? buildTargetExpression(relPath) : null,
    semantic,
  };
}

export function locateChangedTests(options) {
  const repoRoot = path.resolve(options.repo);
  const commit = options.commit;
  const base = resolveBaseCommit(repoRoot, commit, options.base);
  const diffEntries = listChangedFiles(repoRoot, base, commit);
  const changedTestEntries = diffEntries.filter((entry) => isTestPath(entry.path) || isTestPath(entry.oldPath));
  const files = changedTestEntries.map((entry) => analyzeChangedTestFile(repoRoot, base, commit, entry));

  return {
    repoRoot,
    commit,
    base,
    changedTestFileCount: changedTestEntries.length,
    selectedForCoverageCount: files.filter((item) => item.includeForCoverage).length,
    files,
  };
}

function main() {
  if (process.argv[2] === '--help') {
    console.log('Usage: node commit_test_locator.mjs --repo <repo> --commit <sha> [--base <sha>] [--output <path>]');
    return;
  }
  const args = parseArgs(process.argv.slice(2));
  const result = locateChangedTests(args);

  const rendered = JSON.stringify(result, null, 2);
  if (args.output) {
    const fs = createRequire(import.meta.url)('node:fs');
    fs.writeFileSync(path.resolve(args.output), `${rendered}\n`);
  } else {
    process.stdout.write(`${rendered}\n`);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === __filename) {
  main();
}
