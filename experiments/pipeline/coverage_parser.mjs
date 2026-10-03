import fs from 'node:fs';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const require = createRequire(import.meta.url);
const SOURCE_MAP_CACHE = new Map();
const MODULE_ENTRY_CACHE = new Map();
const MODULE_INSTANCE_CACHE = new Map();
const SOURCE_MAP_CONSUMER_CACHE = new Map();
const BROWSER_GENERATED_FILE_REMAP_CACHE = new Map();
const GENERATED_FILE_URL_CACHE = new Map();
const GENERATED_FILE_BASENAME_CACHE = new Map();
const GENERATED_FILE_BASENAME_INDEX_CACHE = new Map();

function toPosix(relPath) {
  return relPath.split(path.sep).join('/');
}

function tryRealpath(inputPath) {
  try {
    return fs.realpathSync(inputPath);
  } catch {
    return path.resolve(inputPath);
  }
}

function getRepoRoots(caseConfig) {
  const roots = [caseConfig.repoRoot, caseConfig.executionRepoRoot]
    .filter(Boolean)
    .map((item) => tryRealpath(item));
  return [...new Set(roots)];
}

function normalizeRepoRelative(absPath, caseConfig) {
  const normalizedAbsPath = tryRealpath(absPath);
  for (const repoRoot of getRepoRoots(caseConfig)) {
    const rel = path.relative(repoRoot, normalizedAbsPath);
    if (!rel.startsWith('..') && !path.isAbsolute(rel)) {
      return toPosix(rel);
    }
  }
  return null;
}

function normalizeMaybeRelative(filePath, caseConfig) {
  if (path.isAbsolute(filePath)) {
    return normalizeRepoRelative(filePath, caseConfig);
  }

  const baseRoots = [
    caseConfig.coverageCwd,
    caseConfig.cwd,
    ...getRepoRoots(caseConfig),
  ]
    .filter(Boolean)
    .map((item) => path.resolve(item));
  const uniqueBaseRoots = [...new Set(baseRoots)];
  const existingMatches = [];
  const fallbackMatches = [];

  for (const baseRoot of uniqueBaseRoots) {
    const candidate = path.resolve(baseRoot, filePath);
    const rel = normalizeRepoRelative(candidate, caseConfig);
    if (!rel) continue;
    if (fs.existsSync(candidate)) {
      existingMatches.push(rel);
    }
    fallbackMatches.push(rel);
  }

  return existingMatches[0] ?? fallbackMatches[0] ?? null;
}

function isLocalHostName(hostname) {
  return hostname === '127.0.0.1' || hostname === 'localhost' || hostname === '::1' || hostname === '[::1]';
}

function isExternalHttpUrl(urlString) {
  try {
    const u = new URL(urlString);
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return false;
    return !isLocalHostName(u.hostname);
  } catch {
    return false;
  }
}

function decodeRepeatedly(value) {
  let decoded = value;
  for (let i = 0; i < 3; i += 1) {
    try {
      const next = decodeURIComponent(decoded);
      if (next === decoded) break;
      decoded = next;
    } catch {
      break;
    }
  }
  return decoded;
}

function normalizeBrowserScriptUrl(urlString) {
  const reactUrlMatch = /^about:\/\/React\/(?:Client|Server)\/(.+)$/u.exec(urlString);
  if (reactUrlMatch) {
    return decodeRepeatedly(reactUrlMatch[1]);
  }
  return urlString;
}

function normalizeWebpackSourcePath(rawSource) {
  if (!rawSource.startsWith('webpack://') && !rawSource.startsWith('turbopack://')) return null;
  let sourcePath;
  try {
    const parsed = new URL(rawSource);
    sourcePath = `${parsed.hostname}${parsed.pathname}`;
  } catch {
    sourcePath = rawSource.replace(/^(?:webpack|turbopack):\/\//u, '');
  }
  sourcePath = decodeRepeatedly(sourcePath)
    .replace(/^_N_E\//u, '')
    .replace(/^webpack\//u, '')
    .replace(/^\/?\[project\]\//u, '')
    .replace(/^\/+/, '')
    .replace(/^\.\//u, '');
  while (sourcePath.startsWith('../')) sourcePath = sourcePath.slice(3);
  return sourcePath || null;
}

function isProdLike(relPath) {
  const p = relPath.toLowerCase();
  const ext = path.extname(p);
  const codeLikeExts = new Set([
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
  if (
    p.startsWith('node_modules/') ||
    p.includes('/node_modules/') ||
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
    p.endsWith('vitest.workspace.mjs') ||
    p.endsWith('setupvitest.ts') ||
    p.endsWith('setupvitest.js') ||
    p.endsWith('setupvitest.mts') ||
    p.endsWith('setupvitest.mjs') ||
    p.endsWith('vitestsetup.ts') ||
    p.endsWith('vitestsetup.js') ||
    p.endsWith('vitestsetup.mts') ||
    p.endsWith('vitestsetup.mjs')
  ) {
    return false;
  }
  if (!codeLikeExts.has(ext)) {
    return false;
  }
  return true;
}

function uniqSorted(items) {
  return [...new Set(items)].sort((a, b) => a.localeCompare(b));
}

function hasCoverageHit(entry) {
  if (entry?.s && Object.values(entry.s).some((count) => count > 0)) return true;
  if (entry?.f && Object.values(entry.f).some((count) => count > 0)) return true;
  if (entry?.b && Object.values(entry.b).some((branchCounts) => Array.isArray(branchCounts) && branchCounts.some((count) => count > 0))) return true;
  return false;
}

function parseCoverageFinal(caseConfig) {
  const raw = JSON.parse(fs.readFileSync(caseConfig.artifact, 'utf8'));
  const covered = [];
  for (const [filePath, entry] of Object.entries(raw)) {
    if (!hasCoverageHit(entry)) continue;
    const rel = normalizeRepoRelative(filePath, caseConfig);
    if (!rel) continue;
    if (!isProdLike(rel)) continue;
    covered.push(rel);
  }
  return {
    status: covered.length > 0 ? 'native' : 'failed',
    coveredProdFiles: uniqSorted(covered),
    notes: [],
  };
}

function parseLcov(caseConfig) {
  const lines = fs.readFileSync(caseConfig.artifact, 'utf8').split(/\r?\n/);
  const covered = new Set();
  let current = null;
  let hit = false;
  for (const line of lines) {
    if (line.startsWith('SF:')) {
      current = line.slice(3);
      hit = false;
      continue;
    }
    if (line.startsWith('DA:') && current) {
      const count = Number(line.split(',')[1] ?? 0);
      if (count > 0) hit = true;
      continue;
    }
    if (line === 'end_of_record' && current) {
      if (hit) {
        const rel = normalizeMaybeRelative(current, caseConfig);
        if (rel && isProdLike(rel)) covered.add(rel);
      }
      current = null;
      hit = false;
    }
  }
  return {
    status: covered.size > 0 ? 'native' : 'failed',
    coveredProdFiles: uniqSorted([...covered]),
    notes: [],
  };
}

function hasExecutedRange(rawEntry) {
  for (const fn of rawEntry.functions ?? []) {
    for (const range of fn.ranges ?? []) {
      if ((range.count ?? 0) > 0) return true;
    }
  }
  return false;
}

function buildRawEntryCoverageCacheKey(generatedFile, rawEntry) {
  const hash = crypto.createHash('sha1');
  for (const fn of rawEntry.functions ?? []) {
    for (const range of fn.ranges ?? []) {
      if ((range.count ?? 0) <= 0) continue;
      hash.update(`${range.startOffset ?? 0}:${range.endOffset ?? 0}:${range.count ?? 0};`);
    }
  }
  return `${fs.realpathSync(generatedFile)}:${hash.digest('hex')}`;
}

function findFileByBaseName(rootDir, baseName) {
  const cacheKey = `${rootDir}\0${baseName}`;
  if (GENERATED_FILE_BASENAME_CACHE.has(cacheKey)) {
    return GENERATED_FILE_BASENAME_CACHE.get(cacheKey);
  }
  let index = GENERATED_FILE_BASENAME_INDEX_CACHE.get(rootDir);
  if (!index) {
    index = new Map();
    const stack = [rootDir];
    while (stack.length) {
      const current = stack.pop();
      if (!current || !fs.existsSync(current)) continue;
      let entries;
      try {
        entries = fs.readdirSync(current, { withFileTypes: true });
      } catch {
        continue;
      }
      for (const entry of entries) {
        const fullPath = path.join(current, entry.name);
        if (entry.isDirectory()) {
          stack.push(fullPath);
        } else if (entry.isFile() && !index.has(entry.name)) {
          index.set(entry.name, fullPath);
        }
      }
    }
    GENERATED_FILE_BASENAME_INDEX_CACHE.set(rootDir, index);
  }
  const match = index.get(baseName) ?? null;
  GENERATED_FILE_BASENAME_CACHE.set(cacheKey, match);
  return match;
}

function findGeneratedFileForUrl(urlString, buildRoots = []) {
  const cacheKey = JSON.stringify([urlString, buildRoots]);
  if (GENERATED_FILE_URL_CACHE.has(cacheKey)) {
    return GENERATED_FILE_URL_CACHE.get(cacheKey);
  }
  try {
    const u = new URL(urlString);
    const decodedPathname = u.pathname
      .split('/')
      .map((part) => decodeRepeatedly(part))
      .join('/');
    const candidatePathnames = [decodedPathname.replace(/^\/+/, '')];
    const nextStaticMatch = decodedPathname.match(/^\/_next\/static\/(.+)$/u);
    if (nextStaticMatch) {
      candidatePathnames.push(nextStaticMatch[1]);
      candidatePathnames.push(path.posix.join('static', nextStaticMatch[1]));
    }
    const baseName = path.basename(decodedPathname);
    for (const root of buildRoots) {
      for (const candidatePathname of candidatePathnames) {
        const direct = path.join(root, candidatePathname);
        if (fs.existsSync(direct) && fs.statSync(direct).isFile()) {
          GENERATED_FILE_URL_CACHE.set(cacheKey, direct);
          return direct;
        }
      }
      if (!baseName) continue;
      const candidate = findFileByBaseName(root, baseName);
      if (candidate) {
        GENERATED_FILE_URL_CACHE.set(cacheKey, candidate);
        return candidate;
      }
    }
  } catch {
    GENERATED_FILE_URL_CACHE.set(cacheKey, null);
    return null;
  }
  GENERATED_FILE_URL_CACHE.set(cacheKey, null);
  return null;
}

function parseSourceMapReference(generatedFile) {
  if (SOURCE_MAP_CACHE.has(generatedFile)) {
    return SOURCE_MAP_CACHE.get(generatedFile);
  }
  let mapJson = null;
  try {
    const content = fs.readFileSync(generatedFile, 'utf8');
    const refs = [
      ...content.matchAll(/\/\/# sourceMappingURL=(.+)$/gm),
      ...content.matchAll(/\/\*# sourceMappingURL=(.+?)\s*\*\//gm),
    ]
      .map((match) => match[1].trim())
      .filter(Boolean);
    if (refs.length === 0) {
      SOURCE_MAP_CACHE.set(generatedFile, null);
      return null;
    }
    for (let i = refs.length - 1; i >= 0; i -= 1) {
      const ref = refs[i];
      if (ref.startsWith('data:')) {
        const base64Match = ref.match(/base64,([A-Za-z0-9+/=]+)$/);
        if (base64Match) {
          mapJson = JSON.parse(Buffer.from(base64Match[1], 'base64').toString('utf8'));
          break;
        }
      } else {
        const mapPath = path.resolve(path.dirname(generatedFile), ref);
        if (fs.existsSync(mapPath)) {
          mapJson = JSON.parse(fs.readFileSync(mapPath, 'utf8'));
          break;
        }
      }
    }
  } catch {
    mapJson = null;
  }
  SOURCE_MAP_CACHE.set(generatedFile, mapJson);
  return mapJson;
}

function findModuleEntry(moduleName, searchRoots = []) {
  const cacheKey = `${moduleName}::${searchRoots.join('|')}`;
  if (MODULE_ENTRY_CACHE.has(cacheKey)) return MODULE_ENTRY_CACHE.get(cacheKey);

  const candidateSuffixes = [
    path.join('node_modules', '.pnpm', 'node_modules', moduleName, 'package.json'),
    path.join('node_modules', moduleName, 'package.json'),
  ];

  for (const root of searchRoots) {
    for (const suffix of candidateSuffixes) {
      const pkgPath = path.join(root, suffix);
      if (fs.existsSync(pkgPath)) {
        const pkgJson = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
        const mainFile = pkgJson.main || 'index.js';
        const entry = path.resolve(path.dirname(pkgPath), mainFile);
        MODULE_ENTRY_CACHE.set(cacheKey, entry);
        return entry;
      }
    }

    const pnpmDir = path.join(root, 'node_modules', '.pnpm');
    if (fs.existsSync(pnpmDir)) {
      const escapedName = moduleName.replace('/', '+');
      const entryPrefix = moduleName.startsWith('@') ? escapedName : moduleName;
      for (const entryName of fs.readdirSync(pnpmDir)) {
        if (!entryName.startsWith(entryPrefix + '@')) continue;
        const pkgPath = path.join(pnpmDir, entryName, 'node_modules', moduleName, 'package.json');
        if (!fs.existsSync(pkgPath)) continue;
        const pkgJson = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
        const mainFile = pkgJson.main || 'index.js';
        const entry = path.resolve(path.dirname(pkgPath), mainFile);
        MODULE_ENTRY_CACHE.set(cacheKey, entry);
        return entry;
      }
    }
  }

  MODULE_ENTRY_CACHE.set(cacheKey, null);
  return null;
}

function loadModuleFromRoots(moduleName, searchRoots = []) {
  const cacheKey = `${moduleName}::${searchRoots.join('|')}`;
  if (MODULE_INSTANCE_CACHE.has(cacheKey)) return MODULE_INSTANCE_CACHE.get(cacheKey);
  const entry = findModuleEntry(moduleName, searchRoots);
  if (!entry) {
    MODULE_INSTANCE_CACHE.set(cacheKey, null);
    return null;
  }
  const loaded = require(entry);
  MODULE_INSTANCE_CACHE.set(cacheKey, loaded);
  return loaded;
}

function getSourceMapConsumerClass(searchRoots = []) {
  const mod = loadModuleFromRoots('source-map-js', searchRoots);
  return mod?.SourceMapConsumer ?? null;
}

function getV8ToIstanbulFactory(searchRoots = []) {
  const mod = loadModuleFromRoots('v8-to-istanbul', searchRoots);
  return typeof mod === 'function' ? mod : null;
}

function getSourceMapConsumer(generatedFile, searchRoots = []) {
  const realPath = fs.realpathSync(generatedFile);
  if (SOURCE_MAP_CONSUMER_CACHE.has(realPath)) {
    return SOURCE_MAP_CONSUMER_CACHE.get(realPath);
  }

  const mapJson = parseSourceMapReference(realPath);
  const SourceMapConsumer = getSourceMapConsumerClass(searchRoots);
  if (!mapJson || !SourceMapConsumer) {
    SOURCE_MAP_CONSUMER_CACHE.set(realPath, null);
    return null;
  }

  const consumer = new SourceMapConsumer(mapJson);
  SOURCE_MAP_CONSUMER_CACHE.set(realPath, consumer);
  return consumer;
}

function shouldRecurseIntoGenerated(relPath, absPath) {
  const lower = relPath.toLowerCase();
  const ext = path.extname(absPath).toLowerCase();
  if (!['.js', '.mjs', '.cjs'].includes(ext)) return false;
  return lower.includes('/dist/') || lower.includes('/build/') || lower.includes('/.next/');
}

function inferNextAppRoot(generatedFile) {
  const marker = `${path.sep}.next${path.sep}`;
  const index = generatedFile.indexOf(marker);
  return index >= 0 ? generatedFile.slice(0, index) : null;
}

function resolveMapSourceAbsolute(rawSource, generatedFile, sourceRoot = '', caseConfig = null) {
  if (!rawSource || rawSource.startsWith('http://') || rawSource.startsWith('https://')) {
    return null;
  }
  const webpackSourcePath = normalizeWebpackSourcePath(rawSource);
  if (webpackSourcePath) {
    const nextAppRoot = inferNextAppRoot(generatedFile);
    const candidateRoots = [
      ...(nextAppRoot ? [nextAppRoot] : []),
      ...getRepoRoots(caseConfig ?? {}),
      path.resolve(path.dirname(generatedFile), '..', '..', '..'),
    ];
    for (const root of candidateRoots) {
      const candidate = path.resolve(root, webpackSourcePath);
      if (fs.existsSync(candidate)) return candidate;
    }
    return path.resolve(candidateRoots[0] ?? path.dirname(generatedFile), webpackSourcePath);
  }
  if (rawSource.startsWith('file://')) {
    return fileURLToPath(rawSource);
  }
  if (path.isAbsolute(rawSource)) return rawSource;
  return path.resolve(path.dirname(generatedFile), sourceRoot, rawSource);
}

function getSourceMapSources(mapJson) {
  const directSources = Array.isArray(mapJson.sources) ? mapJson.sources : [];
  const sectionSources = Array.isArray(mapJson.sections)
    ? mapJson.sections.flatMap((section) => getSourceMapSources(section.map ?? {}))
    : [];
  return [...directSources, ...sectionSources];
}

function collectCoveredSourcesFromMap(mapJson, generatedFile, repoRoot, seenGenerated = new Set()) {
  const sourceRoot = mapJson.sourceRoot ? String(mapJson.sourceRoot) : '';
  const results = [];
  for (const rawSource of getSourceMapSources(mapJson)) {
    const resolved = resolveMapSourceAbsolute(rawSource, generatedFile, sourceRoot, repoRoot);
    if (!resolved) continue;
    results.push(...expandRepoOwnedSource(resolved, repoRoot, seenGenerated));
  }
  return uniqSorted(results);
}

function collectRepoSourcesFromExecutedGenerated(generatedFile, caseConfig) {
  const mapJson = parseSourceMapReference(generatedFile);
  if (!mapJson) return [];
  const sourceRoot = mapJson.sourceRoot ? String(mapJson.sourceRoot) : '';
  const results = [];
  for (const rawSource of getSourceMapSources(mapJson)) {
    const resolved = resolveMapSourceAbsolute(rawSource, generatedFile, sourceRoot, caseConfig);
    if (!resolved) continue;
    const rel = normalizeRepoRelative(resolved, caseConfig);
    if (rel && isProdLike(rel)) results.push(rel);
  }
  return uniqSorted(results);
}

function expandRepoOwnedSource(absPath, caseConfig, seenGenerated = new Set()) {
  const rel = normalizeRepoRelative(absPath, caseConfig);
  if (!rel || !isProdLike(rel)) return [];
  if (!fs.existsSync(absPath) || !fs.statSync(absPath).isFile()) return [rel];
  if (!shouldRecurseIntoGenerated(rel, absPath)) return [rel];

  const realPath = fs.realpathSync(absPath);
  if (seenGenerated.has(realPath)) return [rel];

  const mapJson = parseSourceMapReference(realPath);
  if (!mapJson) return [rel];

  seenGenerated.add(realPath);
  const deeper = collectCoveredSourcesFromMap(mapJson, realPath, caseConfig, seenGenerated);
  seenGenerated.delete(realPath);
  return deeper.length > 0 ? deeper : [rel];
}

function extractCoveredLocationsFromIstanbul(fileCoverage) {
  const locations = [];
  for (const [statementId, count] of Object.entries(fileCoverage.s ?? {})) {
    if (count > 0) {
      const loc = fileCoverage.statementMap?.[statementId]?.start;
      if (loc?.line != null && loc?.column != null) {
        locations.push({ line: loc.line, column: loc.column });
      }
    }
  }
  for (const [fnId, count] of Object.entries(fileCoverage.f ?? {})) {
    if (count > 0) {
      const loc = fileCoverage.fnMap?.[fnId]?.loc?.start;
      if (loc?.line != null && loc?.column != null) {
        locations.push({ line: loc.line, column: loc.column });
      }
    }
  }
  for (const [branchId, counts] of Object.entries(fileCoverage.b ?? {})) {
    const branchCounts = Array.isArray(counts) ? counts : [];
    const branchLocs = fileCoverage.branchMap?.[branchId]?.locations ?? [];
    for (let i = 0; i < branchCounts.length; i += 1) {
      if (branchCounts[i] > 0) {
        const loc = branchLocs[i]?.start;
        if (loc?.line != null && loc?.column != null) {
          locations.push({ line: loc.line, column: loc.column });
        }
      }
    }
  }
  return locations;
}

function remapCoveredLocationsThroughConsumer(consumer, generatedFile, caseConfig, coveredLocations, searchRoots, seenGenerated = new Set()) {
  const SourceMapConsumer = getSourceMapConsumerClass(searchRoots);
  const results = new Set();
  if (!consumer || !SourceMapConsumer) return [];

  for (const loc of coveredLocations) {
    const column = Math.max(0, loc.column ?? 0);
    const original = consumer.originalPositionFor({
      line: loc.line,
      column,
      bias: SourceMapConsumer.GREATEST_LOWER_BOUND,
    });
    if (!original?.source) continue;
    const resolved = resolveMapSourceAbsolute(original.source, generatedFile, consumer.sourceRoot || '', caseConfig);
    if (!resolved) continue;
    const deeper = expandRepoOwnedSource(resolved, caseConfig, seenGenerated);
    for (const rel of deeper) results.add(rel);
  }

  return uniqSorted([...results]);
}

function buildLineStartOffsets(sourceText) {
  const offsets = [0];
  for (let index = 0; index < sourceText.length; index += 1) {
    if (sourceText[index] === '\n') offsets.push(index + 1);
  }
  return offsets;
}

function offsetToLocation(lineStarts, offset) {
  const safeOffset = Math.max(0, offset ?? 0);
  let low = 0;
  let high = lineStarts.length - 1;
  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    if (lineStarts[mid] <= safeOffset) {
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }
  const lineIndex = Math.max(0, high);
  return {
    line: lineIndex + 1,
    column: safeOffset - lineStarts[lineIndex],
  };
}

function extractCoveredLocationsFromRawEntry(generatedSource, rawEntry) {
  const lineStarts = buildLineStartOffsets(generatedSource);
  const offsets = new Set();
  for (const fn of rawEntry.functions ?? []) {
    for (const range of fn.ranges ?? []) {
      if ((range.count ?? 0) <= 0) continue;
      const startOffset = Math.max(0, range.startOffset ?? 0);
      const endOffset = Math.max(startOffset, range.endOffset ?? startOffset);
      offsets.add(startOffset);
      if (endOffset > startOffset) {
        offsets.add(Math.max(0, endOffset - 1));
      }
      const span = endOffset - startOffset;
      if (span > 16) {
        offsets.add(startOffset + Math.floor(span / 2));
      }
    }
  }
  return [...offsets].map((offset) => offsetToLocation(lineStarts, offset));
}

function remapBrowserEntryWithOffsets(entry, generatedFile, generatedSource, caseConfig, searchRoots) {
  const notes = ['missing_v8_to_istanbul'];
  const rel = normalizeRepoRelative(generatedFile, caseConfig);
  if (rel && isProdLike(rel) && !shouldRecurseIntoGenerated(rel, generatedFile)) {
    return {
      coveredProdFiles: [rel],
      notes: [...notes, `direct_source:${rel}`],
    };
  }

  if (rel?.includes('/.next/server/')) {
    const sources = collectRepoSourcesFromExecutedGenerated(generatedFile, caseConfig);
    if (sources.length > 0) {
      return {
        coveredProdFiles: sources,
        notes: [...notes, 'next_server_sourcemap_sources'],
      };
    }
  }

  const consumer = getSourceMapConsumer(generatedFile, searchRoots);
  if (!consumer) {
    return {
      coveredProdFiles: [],
      notes: [...notes, `missing_sourcemap:${generatedFile}`],
    };
  }

  const locations = extractCoveredLocationsFromRawEntry(generatedSource, entry);
  const remapped = remapCoveredLocationsThroughConsumer(
    consumer,
    generatedFile,
    caseConfig,
    locations,
    searchRoots,
  );
  return {
    coveredProdFiles: remapped,
    notes: remapped.length > 0 ? [...notes, 'fallback_offset_remap'] : [...notes, `empty_offset_remap:${rel ?? generatedFile}`],
  };
}

async function remapBrowserEntryWithV8ToIstanbul(entry, generatedFile, caseConfig) {
  const searchRoots = [...getRepoRoots(caseConfig), PROJECT_ROOT];
  const generatedSource = fs.readFileSync(generatedFile, 'utf8');
  if (caseConfig.kind === 'browser-raw' && process.env.REPO_TEST_EVOLUTION_PRECISE_BROWSER_REMAP !== '1') {
    return remapBrowserEntryWithOffsets(entry, generatedFile, generatedSource, caseConfig, searchRoots);
  }

  if (caseConfig.kind === 'browser-raw' && process.env.REPO_TEST_EVOLUTION_PRECISE_BROWSER_REMAP !== '1') {
    return remapBrowserEntryWithOffsets(entry, generatedFile, generatedSource, caseConfig, searchRoots);
  }

  const v8toIstanbul = getV8ToIstanbulFactory(searchRoots);
  if (!v8toIstanbul) {
    return remapBrowserEntryWithOffsets(entry, generatedFile, generatedSource, caseConfig, searchRoots);
  }

  const converter = v8toIstanbul(generatedFile, 0, { source: generatedSource });
  await converter.load();
  converter.applyCoverage(entry.functions ?? []);
  const istanbul = converter.toIstanbul();

  const covered = new Set();
  const notes = [];

  for (const [filePath, fileCoverage] of Object.entries(istanbul)) {
    const absPath = path.resolve(filePath);
    const rel = normalizeRepoRelative(absPath, caseConfig);
    if (!rel || !isProdLike(rel)) continue;

    if (!shouldRecurseIntoGenerated(rel, absPath)) {
      covered.add(rel);
      continue;
    }

    const consumer = getSourceMapConsumer(absPath, searchRoots);
    if (!consumer) {
      covered.add(rel);
      notes.push(`stopped_at_generated:${rel}`);
      continue;
    }

    const locations = extractCoveredLocationsFromIstanbul(fileCoverage);
    const remapped = remapCoveredLocationsThroughConsumer(
      consumer,
      absPath,
      caseConfig,
      locations,
      searchRoots,
    );

    if (remapped.length === 0) {
      covered.add(rel);
      notes.push(`empty_recursive_remap:${rel}`);
      continue;
    }

    for (const item of remapped) covered.add(item);
  }

  return {
    coveredProdFiles: uniqSorted([...covered]),
    notes: uniqSorted(notes),
  };
}

async function parseRawV8Like(caseConfig) {
  const artifacts = Array.isArray(caseConfig.artifacts) && caseConfig.artifacts.length > 0
    ? caseConfig.artifacts
    : [caseConfig.artifact];
  const files = artifacts.flatMap((artifact) => (
    fs.statSync(artifact).isDirectory()
      ? listFilesRecursive(artifact).filter((filePath) => filePath.endsWith('.json'))
      : [artifact]
  )).sort();
  const covered = new Set();
  const notes = [];
  const ignoredExternalUrls = new Set();
  let remappedCount = 0;
  let failedRemapCount = 0;

  for (const filePath of files) {
    const raw = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    const entries = Array.isArray(raw) ? raw : raw.result ?? [];
    for (const entry of entries) {
      const rawUrlString = entry?.url ?? '';
      const urlString = normalizeBrowserScriptUrl(rawUrlString);
      if (!urlString || !hasExecutedRange(entry)) continue;
      if (urlString.startsWith('node:')) continue;
      if (isExternalHttpUrl(urlString)) {
        ignoredExternalUrls.add(urlString);
        continue;
      }

      if (urlString.startsWith('file://')) {
        const absPath = fileURLToPath(urlString);
        const remapped = expandRepoOwnedSource(absPath, caseConfig);
        for (const rel of remapped) covered.add(rel);
        continue;
      }

      if (path.isAbsolute(urlString)) {
        const remapped = expandRepoOwnedSource(urlString, caseConfig);
        for (const rel of remapped) covered.add(rel);
        continue;
      }

      const generatedFile = findGeneratedFileForUrl(urlString, caseConfig.buildRoots ?? []);
      if (!generatedFile) {
        notes.push(`unresolved_local_script:${urlString}`);
        continue;
      }

      const generatedRel = normalizeRepoRelative(generatedFile, caseConfig);
      if (caseConfig.kind === 'browser-raw' && generatedRel?.includes('/.next/static/chunks/')) {
        const basename = path.basename(generatedFile);
        if (!basename.includes('turbopack') && !/^[A-Za-z0-9_.~-]{1,24}\.js$/u.test(basename)) {
          notes.push(`skipped_large_browser_chunk:${generatedRel}`);
          continue;
        }
      }

      if (caseConfig.kind === 'browser-raw') {
        const cacheKey = buildRawEntryCoverageCacheKey(generatedFile, entry);
        let browserRemap = BROWSER_GENERATED_FILE_REMAP_CACHE.get(cacheKey);
        if (!browserRemap) {
          browserRemap = await remapBrowserEntryWithV8ToIstanbul(entry, generatedFile, caseConfig);
          BROWSER_GENERATED_FILE_REMAP_CACHE.set(cacheKey, browserRemap);
        }
        if (browserRemap.coveredProdFiles.length === 0) {
          failedRemapCount += 1;
          for (const note of browserRemap.notes) notes.push(note);
          continue;
        }
        remappedCount += 1;
        for (const rel of browserRemap.coveredProdFiles) covered.add(rel);
        for (const note of browserRemap.notes) notes.push(note);
        continue;
      }

      const mapJson = parseSourceMapReference(generatedFile);
      if (!mapJson) {
        failedRemapCount += 1;
        notes.push(`missing_sourcemap:${generatedFile}`);
        continue;
      }

      const remapped = collectCoveredSourcesFromMap(mapJson, generatedFile, caseConfig, new Set());
      if (remapped.length === 0) {
        failedRemapCount += 1;
        notes.push(`empty_remap:${generatedFile}`);
        continue;
      }

      remappedCount += 1;
      for (const rel of remapped) covered.add(rel);
    }
  }

  const coveredProdFiles = uniqSorted([...covered]);
  let status = 'failed';
  if (coveredProdFiles.length > 0 && remappedCount > 0) status = 'remapped';
  if (coveredProdFiles.length > 0 && remappedCount === 0 && failedRemapCount === 0) status = 'native';

  return {
    status,
    coveredProdFiles,
    ignoredExternalUrlCount: ignoredExternalUrls.size,
    notes: uniqSorted(notes),
  };
}

function listFilesRecursive(rootDir) {
  if (!fs.existsSync(rootDir)) return [];
  const results = [];
  const stack = [rootDir];
  while (stack.length > 0) {
    const current = stack.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) stack.push(fullPath);
      else results.push(fullPath);
    }
  }
  return results.sort();
}

export async function parseCoverageArtifact(caseConfig) {
  let result;
  if (caseConfig.kind === 'coverage-final') {
    result = parseCoverageFinal(caseConfig);
  } else if (caseConfig.kind === 'lcov') {
    result = parseLcov(caseConfig);
  } else if (caseConfig.kind === 'raw-v8' || caseConfig.kind === 'browser-raw') {
    result = await parseRawV8Like(caseConfig);
  } else {
    throw new Error(`Unsupported kind: ${caseConfig.kind}`);
  }

  return {
    kind: caseConfig.kind,
    artifact: caseConfig.artifact ?? caseConfig.artifacts,
    repoRoot: caseConfig.repoRoot,
    status: result.status,
    coveredProdFileCount: result.coveredProdFiles.length,
    coveredProdFiles: result.coveredProdFiles,
    ignoredExternalUrlCount: result.ignoredExternalUrlCount ?? 0,
    notes: result.notes ?? [],
  };
}
