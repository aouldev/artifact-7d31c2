const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');

const COVERAGE_DIR = process.env.REPO_TEST_EVOLUTION_BROWSER_COVERAGE_DIR;
let counter = 0;

function sanitizeId(input) {
  return String(input || 'unknown').replace(/[^a-zA-Z0-9._-]+/g, '_').slice(0, 120);
}

function writeJsonLine(filePath, value) {
  if (!COVERAGE_DIR) return;
  fs.mkdirSync(COVERAGE_DIR, { recursive: true });
  fs.appendFileSync(filePath, `${JSON.stringify(value)}\n`);
}

function writeDiagnostic(event, data = {}) {
  writeJsonLine(path.join(COVERAGE_DIR, 'diagnostics.jsonl'), {
    event,
    ...data,
    at: new Date().toISOString(),
  });
}

function writeCoverage(testInfo, payload) {
  if (!COVERAGE_DIR || !payload || !Array.isArray(payload.result)) return;
  fs.mkdirSync(COVERAGE_DIR, { recursive: true });
  const titlePath = typeof testInfo?.titlePath === 'function'
    ? testInfo.titlePath().join('__')
    : testInfo?.title;
  const filename = [
    'coverage',
    process.pid,
    Date.now(),
    counter++,
    sanitizeId(titlePath),
  ].join('-');
  fs.writeFileSync(path.join(COVERAGE_DIR, `${filename}.json`), `${JSON.stringify(payload)}\n`);
}

async function startCoverageForPage(page, sessions) {
  try {
    const client = await page.context().newCDPSession(page);
    await client.send('Profiler.enable');
    await client.send('Profiler.startPreciseCoverage', {
      callCount: true,
      detailed: true,
    });
    sessions.push({ client });
  } catch (error) {
    writeDiagnostic('start_failed', { message: error?.message ?? String(error) });
  }
}

async function stopCoverageSessions(sessions, testInfo) {
  for (const session of sessions) {
    try {
      const payload = await session.client.send('Profiler.takePreciseCoverage');
      writeCoverage(testInfo, payload);
      await session.client.send('Profiler.stopPreciseCoverage').catch(() => {});
      await session.client.send('Profiler.disable').catch(() => {});
      await session.client.detach().catch(() => {});
    } catch (error) {
      writeDiagnostic('stop_failed', { message: error?.message ?? String(error) });
    }
  }
}

function patchPlaywrightTest(mod) {
  if (!COVERAGE_DIR || !mod || mod.__repoTestEvolutionBrowserCoveragePatched) return mod;
  if (!mod.test || typeof mod.test.extend !== 'function') return mod;

  const baseTest = mod.test;
  const coverageTest = baseTest.extend({
    page: async ({ page }, use, testInfo) => {
      const sessions = [];
      const onPage = (newPage) => {
        startCoverageForPage(newPage, sessions);
      };

      await startCoverageForPage(page, sessions);
      if (typeof page.context().on === 'function') {
        page.context().on('page', onPage);
      }

      try {
        await use(page);
      } finally {
        if (typeof page.context().off === 'function') {
          page.context().off('page', onPage);
        }
        await stopCoverageSessions(sessions, testInfo);
      }
    },
  });

  mod.test = coverageTest;
  mod.__repoTestEvolutionBrowserCoveragePatched = true;
  writeDiagnostic('patched_playwright_test');
  return mod;
}

if (COVERAGE_DIR && !global.__repoTestEvolutionPlaywrightCoverageShimInstalled) {
  global.__repoTestEvolutionPlaywrightCoverageShimInstalled = true;
  const originalLoad = Module._load;
  Module._load = function patchedLoad(request, parent, isMain) {
    const loaded = originalLoad.apply(this, arguments);
    if (request === '@playwright/test' || request.endsWith('/@playwright/test')) {
      return patchPlaywrightTest(loaded);
    }
    return loaded;
  };
}
