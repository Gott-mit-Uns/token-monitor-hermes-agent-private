'use strict';
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const upstream = String(require(path.join(root, 'app/package.json')).version);
const nasVersion = fs.readFileSync(path.join(root, 'nas-version.txt'), 'utf8').trim();
const match = /^v(\d+\.\d+\.\d+)-(\d{2})$/.exec(nasVersion);
if (!match || match[1] !== upstream || Number(match[2]) < 1) {
  console.error('NAS version must be v<official package version>-<two-digit revision starting at 01>.');
  process.exit(1);
}
const dockerfile = fs.readFileSync(path.join(root, 'Dockerfile'), 'utf8');
if (!dockerfile.includes(`ARG BUILD_VERSION=${nasVersion}`)) {
  console.error('Dockerfile default version does not match nas-version.txt.');
  process.exit(1);
}
console.log(nasVersion);
