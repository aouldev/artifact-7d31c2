#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

function log(message) {
  console.error('[vendure-dashboard-playwright-runner] ' + message);
}

function findRepoRoot(startDir) {
  let current = path.resolve(startDir);
  while (current && current !== path.dirname(current)) {
    if (fs.existsSync(path.join(current, 'packages', 'dashboard', 'e2e', 'playwright.config.ts'))) {
      return current;
    }
    current = path.dirname(current);
  }
  return null;
}

function run(command, args, options) {
  log('running ' + [command, ...args].join(' ') + ' in ' + options.cwd);
  const result = spawnSync(command, args, {
    cwd: options.cwd,
    env: options.env || process.env,
    stdio: 'inherit',
    timeout: options.timeout || Number(process.env.REPO_TEST_EVOLUTION_PLAYWRIGHT_TIMEOUT_MS || 1800000),
  });
  if (result.error) {
    console.error(result.error.stack || result.error.message || String(result.error));
    return result.status || 1;
  }
  return result.status == null ? 1 : result.status;
}

function patchAuthenticatedRoute(repoRoot) {
  const routePath = path.join(repoRoot, 'packages', 'dashboard', 'src', 'app', 'routes', '_authenticated.tsx');
  if (!fs.existsSync(routePath)) return { patched: false, reason: 'missing_route_file' };
  const source = fs.readFileSync(routePath, 'utf8');
  const fromImport = "import { AUTHENTICATED_ROUTE_PREFIX } from '@/vdb/constants.js';\n";
  const fromCall = 'createFileRoute(AUTHENTICATED_ROUTE_PREFIX)';
  const toCall = "createFileRoute('/_authenticated')";
  if (!source.includes(fromCall)) return { patched: false, reason: 'route_already_literal_or_changed' };
  fs.writeFileSync(routePath, source.replace(fromImport, '').replace(fromCall, toCall));
  return { patched: true, routePath };
}

function patchHttpProxyLoggerType(repoRoot) {
  const filePath = path.join(repoRoot, 'packages', 'core', 'src', 'plugin', 'plugin-utils.ts');
  if (!fs.existsSync(filePath)) return { patched: false, reason: 'missing_plugin_utils' };
  const source = fs.readFileSync(filePath, 'utf8');
  if (!source.includes('logger: {')) return { patched: false, reason: 'logger_block_missing' };
  if (source.includes('logger: ({')) return { patched: false, reason: 'already_cast' };
  fs.writeFileSync(filePath, source.replace('logger: {', 'logger: ({').replace(/\n        },\n    }\);/, '\n        } as any),\n    });'));
  return { patched: true, filePath };
}


function hasBcryptNativeBinding(repoRoot) {
  const bindingRoot = path.join(repoRoot, 'node_modules', 'bcrypt', 'lib', 'binding');
  if (!fs.existsSync(bindingRoot)) return false;
  const stack = [bindingRoot];
  while (stack.length > 0) {
    const current = stack.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const entryPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(entryPath);
      } else if (entry.name === 'bcrypt_lib.node') {
        return true;
      }
    }
  }
  return false;
}

function copyStaticCoreFiles(repoRoot) {
  const coreRoot = path.join(repoRoot, 'packages', 'core');
  const srcRoot = path.join(coreRoot, 'src');
  const distRoot = path.join(coreRoot, 'dist');
  const copied = [];
  function copyRecursive(relativeDir, predicate) {
    const sourceDir = path.join(srcRoot, relativeDir);
    if (!fs.existsSync(sourceDir)) return;
    const stack = [sourceDir];
    while (stack.length > 0) {
      const current = stack.pop();
      for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
        const sourcePath = path.join(current, entry.name);
        if (entry.isDirectory()) {
          stack.push(sourcePath);
          continue;
        }
        if (!predicate(sourcePath)) continue;
        const relativePath = path.relative(srcRoot, sourcePath);
        const targetPath = path.join(distRoot, relativePath);
        fs.mkdirSync(path.dirname(targetPath), { recursive: true });
        fs.copyFileSync(sourcePath, targetPath);
        copied.push(relativePath);
      }
    }
  }
  copyRecursive('', filePath => filePath.endsWith('.graphql'));
  copyRecursive('i18n/messages', () => true);
  return copied.length;
}

function writeBrowserCoverageFixture(repoRoot) {
  const fixturePath = path.join(repoRoot, 'packages', 'dashboard', 'e2e', 'repo-test-evolution-browser-fixture.ts');
  const source = `import { test as base, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const coverageDir = process.env.REPO_TEST_EVOLUTION_BROWSER_COVERAGE_DIR;
let counter = 0;

function sanitizeId(input: string) {
    return String(input || 'unknown').replace(/[^a-zA-Z0-9._-]+/g, '_').slice(0, 120);
}

function writeDiagnostic(event: string, data: Record<string, unknown> = {}) {
    if (!coverageDir) return;
    fs.mkdirSync(coverageDir, { recursive: true });
    fs.appendFileSync(path.join(coverageDir, 'diagnostics.jsonl'), JSON.stringify({ event, ...data, at: new Date().toISOString() }) + '\\n');
}

async function startCoverageForPage(page: Page, sessions: Array<{ client: any }>) {
    try {
        const client = await page.context().newCDPSession(page);
        await client.send('Profiler.enable');
        await client.send('Profiler.startPreciseCoverage', { callCount: true, detailed: true });
        sessions.push({ client });
        writeDiagnostic('start_ok', { url: page.url() });
    } catch (error: any) {
        writeDiagnostic('start_failed', { message: error?.message ?? String(error) });
    }
}

async function stopCoverageSessions(sessions: Array<{ client: any }>, testInfo: any) {
    if (!coverageDir) return;
    fs.mkdirSync(coverageDir, { recursive: true });
    for (const session of sessions) {
        try {
            const payload = await session.client.send('Profiler.takePreciseCoverage');
            const titlePath = typeof testInfo?.titlePath === 'function' ? testInfo.titlePath().join('__') : testInfo?.title;
            const filename = ['coverage', process.pid, Date.now(), counter++, sanitizeId(titlePath)].join('-') + '.json';
            fs.writeFileSync(path.join(coverageDir, filename), JSON.stringify(payload) + '\\n');
            await session.client.send('Profiler.stopPreciseCoverage').catch(() => {});
            await session.client.send('Profiler.disable').catch(() => {});
            await session.client.detach().catch(() => {});
            writeDiagnostic('stop_ok', { file: filename });
        } catch (error: any) {
            writeDiagnostic('stop_failed', { message: error?.message ?? String(error) });
        }
    }
}

export const test = base.extend({
    page: async ({ page }, use, testInfo) => {
        const sessions: Array<{ client: any }> = [];
        const onPage = (newPage: Page) => void startCoverageForPage(newPage, sessions);
        await startCoverageForPage(page, sessions);
        page.context().on('page', onPage);
        try {
            await use(page);
        } finally {
            page.context().off('page', onPage);
            await stopCoverageSessions(sessions, testInfo);
        }
    },
});

export { expect, type Page };
`;
  fs.writeFileSync(fixturePath, source);
  return fixturePath;
}

function patchAssetForCoverage(repoRoot, assetPath) {
  const fixturePath = writeBrowserCoverageFixture(repoRoot);
  const absoluteAssetPath = path.join(repoRoot, assetPath);
  if (!fs.existsSync(absoluteAssetPath)) return { patched: false, reason: 'missing_asset', assetPath, fixturePath };
  const source = fs.readFileSync(absoluteAssetPath, 'utf8');
  if (!source.includes("from '@playwright/test'")) return { patched: false, reason: 'no_playwright_import', assetPath, fixturePath };
  const relativeFixture = path.relative(path.dirname(absoluteAssetPath), fixturePath).split(path.sep).join('/').replace(/\.ts$/, '.js');
  const importPath = relativeFixture.startsWith('.') ? relativeFixture : './' + relativeFixture;
  fs.writeFileSync(absoluteAssetPath, source.replace(/from ['"]@playwright\/test['"]/g, `from '${importPath}'`));
  return { patched: true, assetPath, fixturePath, importPath };
}

function ensureVendureBuilds(repoRoot, env) {
  const commonLib = path.join(repoRoot, 'packages', 'common', 'lib');
  if (!fs.existsSync(commonLib)) {
    const status = run('npm', ['run', 'build'], { cwd: path.join(repoRoot, 'packages', 'common'), env });
    if (status !== 0) return status;
  } else {
    log('common build exists; skipping');
  }

  if (!hasBcryptNativeBinding(repoRoot)) {
    const status = run('npm', ['rebuild', 'bcrypt'], { cwd: repoRoot, env });
    if (status !== 0) return status;
  } else {
    log('bcrypt native binding exists; skipping rebuild');
  }

  const coreDistIndex = path.join(repoRoot, 'packages', 'core', 'dist', 'index.js');
  const coreCliIndex = path.join(repoRoot, 'packages', 'core', 'cli', 'index.js');
  const coreSchemaDir = path.join(repoRoot, 'packages', 'core', 'dist', 'api', 'schema', 'admin-api');
  const needsCoreBuild = !fs.existsSync(coreDistIndex) || !fs.existsSync(coreCliIndex) || !fs.existsSync(coreSchemaDir);
  if (needsCoreBuild) {
    const status = run('npm', ['run', 'build'], { cwd: path.join(repoRoot, 'packages', 'core'), env });
    if (status !== 0) return status;
  } else {
    log('core dist/cli build exists; skipping');
  }
  const copiedStatic = copyStaticCoreFiles(repoRoot);
  log('copied core static files: ' + copiedStatic);

  const testingIndex = path.join(repoRoot, 'packages', 'testing', 'lib', 'index.js');
  if (!fs.existsSync(testingIndex)) {
    const status = run('npm', ['run', 'build'], { cwd: path.join(repoRoot, 'packages', 'testing'), env });
    if (status !== 0) return status;
  } else {
    log('testing build exists; skipping');
  }
  return 0;
}

function main() {
  const assetPath = process.argv[2];
  if (!assetPath) {
    console.error('Usage: vendure_dashboard_playwright_runner.cjs <assetPath>');
    process.exit(2);
  }
  const repoRoot = findRepoRoot(process.cwd());
  if (!repoRoot) {
    console.error('Unable to locate Vendure repo root from cwd: ' + process.cwd());
    process.exit(2);
  }
  log('route patch: ' + JSON.stringify(patchAuthenticatedRoute(repoRoot)));
  log('proxy logger patch: ' + JSON.stringify(patchHttpProxyLoggerType(repoRoot)));
  log('coverage fixture patch: ' + JSON.stringify(patchAssetForCoverage(repoRoot, assetPath)));

  const env = {
    ...process.env,
    CI: '1',
    VITE_TEST_PORT: process.env.VITE_TEST_PORT || '5174',
  };
  const buildStatus = ensureVendureBuilds(repoRoot, env);
  if (buildStatus !== 0) process.exit(buildStatus);

  const installStatus = run('npm', ['exec', '--', 'playwright', 'install', 'chromium'], { cwd: repoRoot, env });
  if (installStatus !== 0) process.exit(installStatus);

  const configPath = path.join(repoRoot, 'packages', 'dashboard', 'e2e', 'playwright.config.ts');
  const args = [
    'exec',
    '--',
    'playwright',
    'test',
    '--config',
    configPath,
    assetPath,
    '--project=chromium',
    '--workers=1',
    '--retries=0',
    '--reporter=line',
  ];
  process.exit(run('npm', args, { cwd: repoRoot, env }));
}

main();
