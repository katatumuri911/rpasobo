import * as asar from 'file:///C:/Users/nukon/AppData/Local/npm-cache/_npx/4b0e2640fe917ac8/node_modules/@electron/asar/lib/asar.js';
import fs from 'fs';
import path from 'path';
import { execSync } from 'child_process';

const bakAsar = 'C:/Users/nukon/AppData/Local/Programs/deepai-code-desktop/resources/server.asar.bak';
const liveAsar = 'C:/Users/nukon/AppData/Local/Programs/deepai-code-desktop/resources/server.asar';
const tmpDir = 'C:/Users/nukon/RPasobou/tmp_server_asar';
const destAsar = 'C:/Users/nukon/RPasobou/server.asar.built';

console.log('0. Extracting pristine original files from server.asar.bak...');
const list = asar.listPackage(bakAsar);
const pRuntime = list.find(x => x.includes('runtime-DL3Dibca.js')).replace(/^[\\\/]+/, '');
const pTrv = list.find(x => x.includes('ThreadRouteView-D5J-hl3X.js')).replace(/^[\\\/]+/, '');

const origRuntime = asar.extractFile(bakAsar, pRuntime).toString('utf8');
const origTrv = asar.extractFile(bakAsar, pTrv).toString('utf8');

fs.writeFileSync('runtime-DL3Dibca.orig.js', origRuntime, 'utf8');
fs.writeFileSync('ThreadRouteView-D5J-hl3X.orig.js', origTrv, 'utf8');
console.log('  ✔ Pristine orig files saved.');

console.log('1. Running patch scripts...');
execSync('node tools/history_autoload/patch_runtime.mjs', { stdio: 'inherit' });
execSync('node tools/history_autoload/patch_threadrouteview.mjs', { stdio: 'inherit' });

console.log('2. Unpacking server.asar to tmp_server_asar...');
if (fs.existsSync(tmpDir)) {
  fs.rmSync(tmpDir, { recursive: true, force: true });
}
asar.extractAll(liveAsar, tmpDir);

console.log('3. Copying patched files into tmp_server_asar...');
const targetTRV = path.join(tmpDir, pTrv);
const targetRuntime = path.join(tmpDir, pRuntime);

fs.copyFileSync('ThreadRouteView-D5J-hl3X.patched.js', targetTRV);
fs.copyFileSync('runtime-DL3Dibca.patched.js', targetRuntime);

console.log('4. Repacking tmp_server_asar to server.asar.built...');
const start = Date.now();
await asar.createPackageWithOptions(tmpDir, destAsar, { unpack: '*.{node,dll,exe,so}' });
console.log('  Repacked in', (Date.now() - start) / 1000, 'seconds');

console.log('5. Verifying repacked archive...');
const builtList = asar.listPackage(destAsar, { isPack: true });
const unpacked = builtList.filter(x => x.startsWith('unpack'));
console.log('  Unpacked native files count:', unpacked.length);
if (unpacked.length !== 26) {
  throw new Error(`Expected 26 unpacked files, got ${unpacked.length}`);
}

const trvInAsar = asar.extractFile(destAsar, pTrv).toString('utf8');
const runtimeInAsar = asar.extractFile(destAsar, pRuntime).toString('utf8');

if (!trvInAsar.includes('__HIST_TOAST_ID')) throw new Error('TRV in asar missing __HIST_TOAST_ID');
if (!runtimeInAsar.includes('emptyPageStreak')) throw new Error('Runtime in asar missing emptyPageStreak');

console.log('  ✔ ThreadRouteView inside server.asar.built has patched code');
console.log('  ✔ runtime inside server.asar.built has patched code');

console.log('6. Installing server.asar.built ->', liveAsar);
fs.copyFileSync(destAsar, liveAsar);
console.log('  ✔ Successfully installed patched server.asar into DeepAI Code desktop!');

// Clean up tmp_server_asar
fs.rmSync(tmpDir, { recursive: true, force: true });
console.log('7. Cleaned up tmp directory.');
