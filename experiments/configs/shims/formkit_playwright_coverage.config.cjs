const { defineConfig, devices } = require('@playwright/test');
const path = require('node:path');

const repoRoot = process.env.REPO_TEST_EVOLUTION_REPO_ROOT || process.cwd();
const quotedRepoRoot = JSON.stringify(repoRoot);

module.exports = defineConfig({
  testDir: path.join(repoRoot, 'e2e'),
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  reporter: 'line',
  use: {
    baseURL: 'http://127.0.0.1:8787',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        headless: true,
        launchOptions: {
          args: ['--js-flags=--expose-gc'],
        },
      },
    },
  ],
  webServer: [
    {
      command: `cd ${quotedRepoRoot} && ./node_modules/.bin/vite preview --config ./examples/vite.config.ts --port 8787 --host 127.0.0.1`,
      url: 'http://127.0.0.1:8787',
      reuseExistingServer: true,
    },
    {
      command: `cd ${quotedRepoRoot} && node --expose-gc e2e/servers/formKitMemoryServer.mjs`,
      url: 'http://localhost:8686',
      reuseExistingServer: true,
    },
    {
      command: `cd ${quotedRepoRoot} && node --expose-gc e2e/servers/vueMemoryServer.mjs`,
      url: 'http://localhost:8585',
      reuseExistingServer: true,
    },
  ],
});
