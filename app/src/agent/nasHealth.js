'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { pidFilePath } = require('../shared/config');
const statusPath = () => path.join(path.dirname(pidFilePath()), 'nas-health.json');
function mark(kind) {
  let state = {};
  try { state = JSON.parse(fs.readFileSync(statusPath(), 'utf8')); } catch {}
  state[kind] = Date.now();
  fs.mkdirSync(path.dirname(statusPath()), { recursive: true });
  const temp = statusPath() + '.tmp';
  fs.writeFileSync(temp, JSON.stringify(state), { mode: 0o600 });
  fs.renameSync(temp, statusPath());
}
function assess(state, now, maxAge) {
  return {
    collection: Number.isFinite(state.collectedAt) && now - state.collectedAt <= maxAge ? 'ok' : 'stale',
    upload: Number.isFinite(state.uploadedAt) && now - state.uploadedAt <= maxAge ? 'ok' : 'stale'
  };
}
if (require.main === module) {
  try {
    process.kill(Number(fs.readFileSync(pidFilePath(), 'utf8')), 0);
    fs.accessSync(path.join(process.env.HERMES_HOME || '/hermes', 'state.db'), fs.constants.R_OK);
    const state = JSON.parse(fs.readFileSync(statusPath(), 'utf8'));
    const status = assess(state, Date.now(), Math.max(900000, 3 * Number(process.env.TOKEN_MONITOR_INTERVAL_MS || 300000)));
    console.log(JSON.stringify(status));
    // A sleeping Hub should be visible, but must not cause restart loops.
    process.exitCode = status.collection === 'ok' ? 0 : 1;
  } catch {
    console.log('local collection unavailable');
    process.exitCode = 1;
  }
}
module.exports = { mark, assess };
