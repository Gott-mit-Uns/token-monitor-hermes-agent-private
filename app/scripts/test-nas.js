'use strict';
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const agentTests = fs.readdirSync('tests/agent').filter(name => name.endsWith('.test.js')).map(name => `tests/agent/${name}`);
const sharedTests = ['orderedSink', 'deviceState', 'deviceRuntime', 'syncPayload', 'sessionUsageArchive', 'collectorPeriodWindows'];
const result = spawnSync(process.execPath, ['--test', ...agentTests, ...sharedTests.map(name => `tests/shared/${name}.test.js`)], { stdio: 'inherit' });
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
