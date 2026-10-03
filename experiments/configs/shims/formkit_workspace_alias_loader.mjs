import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const repoRoot = process.env.REPO_TEST_EVOLUTION_REPO_ROOT
  ? path.resolve(process.env.REPO_TEST_EVOLUTION_REPO_ROOT)
  : null;

const workspacePackages = new Set([
  'addons',
  'cli',
  'core',
  'dev',
  'i18n',
  'icons',
  'inputs',
  'observer',
  'rules',
  'tailwindcss',
  'themes',
  'utils',
  'validation',
  'vue',
  'zod',
]);

function workspaceEntry(packageName) {
  if (!repoRoot || !workspacePackages.has(packageName)) return null;
  const packageDir = path.join(repoRoot, 'packages', packageName);
  for (const relPath of ['dist/index.mjs', 'dist/index.dev.mjs']) {
    const candidate = path.join(packageDir, relPath);
    if (fs.existsSync(candidate)) return candidate;
  }
  return null;
}

export async function resolve(specifier, context, nextResolve) {
  const match = /^@formkit\/([^/]+)$/.exec(specifier);
  if (match) {
    const entry = workspaceEntry(match[1]);
    if (entry) {
      return {
        url: pathToFileURL(entry).href,
        shortCircuit: true,
      };
    }
  }
  return nextResolve(specifier, context);
}
