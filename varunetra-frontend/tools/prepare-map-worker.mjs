import { copyFile, mkdir, readdir, readFile, rm } from 'node:fs/promises';

// MapLibre 6's module worker imports a sibling shared module. Self-host both
// from the installed version, rather than relying on a CDN or bundler-relative URL.
const root = new URL('../', import.meta.url);
const source = new URL('node_modules/maplibre-gl/', root);
const { version } = JSON.parse(await readFile(new URL('package.json', source), 'utf8'));
const vendor = new URL('public/vendor/maplibre/', root);
const target = new URL(`${version}/`, vendor);
await mkdir(target, { recursive: true });
for (const file of ['maplibre-gl-worker.mjs', 'maplibre-gl-shared.mjs', '../LICENSE.txt']) {
  await copyFile(new URL(`dist/${file}`, source), new URL(file.split('/').at(-1), target));
}

for (const entry of await readdir(vendor, { withFileTypes: true })) {
  if (entry.isDirectory() && entry.name !== version) {
    await rm(new URL(`${entry.name}/`, vendor), { recursive: true, force: true });
    console.log(`prepare-map-worker: pruned stale maplibre ${entry.name}`);
  }
}
