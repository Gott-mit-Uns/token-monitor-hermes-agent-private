'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { assess } = require('../../src/agent/nasHealth');
test('offline Hub does not mark local collection as stalled', () => {
  assert.deepEqual(assess({ collectedAt: 1000000, uploadedAt: 1 }, 1000100, 900000), { collection: 'ok', upload: 'stale' });
});
test('stalled collector and missing timestamps are unhealthy', () => {
  assert.equal(assess({ collectedAt: 1 }, 1000100, 900000).collection, 'stale');
  assert.equal(assess({}, 1000100, 900000).collection, 'stale');
});
