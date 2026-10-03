const fs = require('node:fs');
const path = require('node:path');

function findRepoRoot(startDir) {
  let current = path.resolve(startDir);
  while (current && current !== path.dirname(current)) {
    if (fs.existsSync(path.join(current, 'packages', 'core', 'package.json'))) return current;
    current = path.dirname(current);
  }
  return null;
}

const repoRoot = findRepoRoot(process.cwd());
if (repoRoot) {
  const distDir = path.join(repoRoot, 'packages', 'core', 'dist');
  fs.mkdirSync(distDir, { recursive: true });
  fs.writeFileSync(
    path.join(distDir, 'index.js'),
    `const generatedTypes = require('@vendure/common/lib/generated-types');
exports.AdjustmentType = generatedTypes.AdjustmentType;
exports.AssetType = generatedTypes.AssetType;
exports.CurrencyCode = generatedTypes.CurrencyCode;
exports.LanguageCode = generatedTypes.LanguageCode;
exports.Permission = generatedTypes.Permission;
exports.VENDURE_ADMIN_API_TYPE_PATHS = [];
exports.VendureConfig = undefined;
let currentConfig = {};
exports.resetConfig = () => { currentConfig = {}; };
exports.setConfig = async (config) => { currentConfig = config || {}; return currentConfig; };
exports.getConfig = () => currentConfig;
exports.runPluginConfigurations = async (config) => config || currentConfig || {};
exports.getFinalVendureSchema = async () => \`
  scalar JSON
  scalar DateTime
  enum LanguageCode { en de es fr }
  type Query { _empty: String }
  type Product { id: ID }
  type Mutation { createProduct(input: CreateProductInput!): Product }
  input ProductTranslationInput { id: ID languageCode: LanguageCode name: String slug: String description: String }
  input CreateProductInput { translations: [ProductTranslationInput!]! featuredAssetId: ID }
\`;
exports.PluginCommonModule = class PluginCommonModule {};
exports.VendurePlugin = () => (target) => target;
`,
  );
  fs.writeFileSync(
    path.join(distDir, 'index.d.ts'),
    `export { AdjustmentType, AssetType, CurrencyCode, LanguageCode, Permission } from '@vendure/common/lib/generated-types';
export declare const VENDURE_ADMIN_API_TYPE_PATHS: string[];
export type VendureConfig = any;
export declare function resetConfig(): void;
export declare function setConfig(config: any): Promise<any>;
export declare function getConfig(): any;
export declare function runPluginConfigurations(config: any): Promise<any>;
export declare function getFinalVendureSchema(): Promise<string>;
export declare class PluginCommonModule {}
export declare function VendurePlugin(): ClassDecorator;
`,
  );
}
