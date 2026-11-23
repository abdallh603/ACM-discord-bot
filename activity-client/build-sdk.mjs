import {build} from 'esbuild';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {createHash} from 'node:crypto';

const here = path.dirname(fileURLToPath(import.meta.url));
const output = path.resolve(here, '../just_bot/activity_web/vendor');
await mkdir(output, {recursive: true});
const result = await build({absWorkingDir: here, entryPoints: [path.join(here, 'sdk-entry.js')],
  outfile: path.join(output, 'discord-sdk.js'), bundle: true, minify: true, format: 'esm', platform: 'browser',
  target: ['chrome108'], legalComments: 'eof', metafile: true, write: false});
const packages = new Map();
for (const input of Object.keys(result.metafile.inputs)) {
  let folder = path.dirname(path.resolve(here, input));
  const root = path.join(here, 'node_modules');
  while (folder.startsWith(root)) {
    try {
      const pkg = JSON.parse(await readFile(path.join(folder, 'package.json'), 'utf8'));
      if (pkg.name) { packages.set(pkg.name, {folder, pkg}); break; }
    } catch {}
    folder = path.dirname(folder);
  }
}
const notices = [];
for (const {folder, pkg} of packages.values()) {
  let license = '';
  for (const name of ['LICENSE', 'LICENSE.md', 'LICENSE.txt', 'LICENCE', 'LICENCE.md', 'LICENCE.txt', 'LICENSE-MIT', 'license', 'license.md', 'license.txt']) {
    try { license = await readFile(path.join(folder, name), 'utf8'); break; } catch {}
  }
  if (!license) throw new Error(`No license file found for bundled dependency ${pkg.name}; inspect it before distributing.`);
  notices.push(`${pkg.name} ${pkg.version}\n${'='.repeat(60)}\n${license}`);
}
await writeFile(path.join(output, 'discord-sdk-license.txt'), notices.join('\n\n'));
for (const file of result.outputFiles) await writeFile(file.path, file.contents);
await writeFile(path.join(output, 'sdk-build.json'), JSON.stringify({
  sdk_version: packages.get('@discord/embedded-app-sdk')?.pkg.version,
  sha256: createHash('sha256').update(result.outputFiles[0].contents).digest('hex'),
  dependencies: [...packages.values()].map(({pkg}) => ({name: pkg.name, version: pkg.version})),
}, null, 2));
console.log('Built the official Discord SDK for the Activity. No credentials are included in the browser bundle.');
