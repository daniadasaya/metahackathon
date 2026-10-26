#!/usr/bin/env node
/**
 * Launcher for openapi-typescript that forces the CLI to load TypeScript 5.x
 * instead of the project's TypeScript 7.x (which lacks the legacy ts.factory API).
 *
 * Why this exists: openapi-typescript 7.13.0 declares `typescript: ^5.x` as a peer
 * and crashes on `ts.factory` when loaded with TS 7. The project template pins
 * `typescript@^7.0.2` for `tsc`. This wrapper shadows the `typescript` module ID to
 * the `openapi-typescript-ts5` devDependency (an npm alias for typescript@5.9.3) for
 * the duration of the generation, without affecting `npm run build`.
 */
const path = require('node:path');
const fs = require('node:fs');
const { pathToFileURL } = require('node:url');
const { spawnSync } = require('node:child_process');

const ts5Path = path.join(__dirname, 'node_modules', 'openapi-typescript-ts5', 'lib', 'typescript.js');
if (!fs.existsSync(ts5Path)) {
  console.error('gen-types: missing node_modules/openapi-typescript-ts5 — run npm install first.');
  process.exit(1);
}

// Register an ESM loader that redirects `import 'typescript'` to the TS 5 build.
const loaderPath = path.join(__dirname, 'gen-types-loader.mjs');
fs.writeFileSync(loaderPath, `
const ts5URL = ${JSON.stringify(pathToFileURL(ts5Path).href)};
export async function resolve(specifier, context, nextResolve) {
  if (specifier === 'typescript') {
    return { url: ts5URL, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}
`);

const cliPath = path.join(__dirname, 'node_modules', 'openapi-typescript', 'bin', 'cli.js');
let exitCode = 1;
try {
  const result = spawnSync(
    process.execPath,
    ['--experimental-loader', pathToFileURL(loaderPath).href, cliPath, ...process.argv.slice(2)],
    { stdio: 'inherit', cwd: process.cwd() }
  );
  exitCode = result.status ?? 1;
} finally {
  fs.unlinkSync(loaderPath);
}
process.exit(exitCode);
