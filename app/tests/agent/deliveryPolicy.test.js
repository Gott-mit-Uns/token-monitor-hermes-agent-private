'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const {
  createDeduplicatingDelivery,
  semanticRecordFingerprint,
  writeAgentSuccess
} = require('../../src/agent/deliveryPolicy');

function record(tokens, updatedAt = '2026-08-08T00:00:00.000Z') {
  return {
    deviceId: 'nas-hermes',
    updatedAt,
    clientHealth: { observedAt: updatedAt, overall: 'healthy' },
    today: { totalTokens: tokens }
  };
}

test('semantic fingerprint ignores transport clocks but retains usage changes', () => {
  assert.equal(semanticRecordFingerprint(record(10, 'first')), semanticRecordFingerprint(record(10, 'second')));
  assert.notEqual(semanticRecordFingerprint(record(10)), semanticRecordFingerprint(record(11)));
});

test('deduplicates unchanged records while retaining a bounded heartbeat', async () => {
  let now = 1_000;
  const sent = [];
  const successes = [];
  const delivery = createDeduplicatingDelivery({
    heartbeatMs: 300,
    now: () => now,
    send: async (value) => sent.push(value),
    onSuccess: (value) => successes.push(value)
  });

  assert.equal((await delivery.deliver(record(10, 'first'))).sent, true);
  now += 100;
  assert.equal((await delivery.deliver(record(10, 'second'))).duplicate, true);
  now += 200;
  assert.equal((await delivery.deliver(record(10, 'third'))).sent, true);
  now += 10;
  assert.equal((await delivery.deliver(record(11, 'fourth'))).sent, true);
  assert.equal(sent.length, 3);
  assert.equal(successes.length, 3);
});

test('failed delivery remains eligible for retry', async () => {
  let attempts = 0;
  const delivery = createDeduplicatingDelivery({
    send: async () => {
      attempts += 1;
      if (attempts === 1) throw new Error('offline');
    }
  });

  await assert.rejects(delivery.deliver(record(10)), /offline/);
  assert.equal((await delivery.deliver(record(10))).sent, true);
  assert.equal(attempts, 2);
});

test('writes the success heartbeat atomically', () => {
  const calls = [];
  const result = writeAgentSuccess({
    path: '/config/Token Monitor/last-success',
    now: () => Date.parse('2026-08-08T00:00:00.000Z'),
    mkdirSync: (...args) => calls.push(['mkdir', ...args]),
    writeFileSync: (...args) => calls.push(['write', ...args]),
    renameSync: (...args) => calls.push(['rename', ...args])
  });

  assert.equal(result.at, '2026-08-08T00:00:00.000Z');
  assert.equal(calls[0][0], 'mkdir');
  assert.match(calls[1][1], /last-success\.\d+\.tmp$/);
  assert.equal(calls[2][2], '/config/Token Monitor/last-success');
});

test('deduplication retains date boundaries and session detail changes', () => {
  const first = { ...record(10), periodWindows: {today: {key: '2026-09-28'}}, allTime: {sessions: {a: {totalTokens: 10}}}};
  const nextDay = {...first, periodWindows: {today: {key: '2026-09-29'}}};
  const nextSession = {...first, allTime: {sessions: {b: {totalTokens: 10}}}};
  assert.notEqual(semanticRecordFingerprint(first), semanticRecordFingerprint(nextDay));
  assert.notEqual(semanticRecordFingerprint(first), semanticRecordFingerprint(nextSession));
});

test('collection health clocks do not defeat deduplication while failures remain visible', () => {
  const first = {...record(10), clientHealth:{clients:{hermes:{collection:{state:'ok', lastAttemptAt:'a', lastSuccessAt:'a'}}}}};
  const repeated = {...first, clientHealth:{clients:{hermes:{collection:{state:'ok', lastAttemptAt:'b', lastSuccessAt:'b'}}}}};
  const failed = {...first, clientHealth:{clients:{hermes:{collection:{state:'failed', lastAttemptAt:'b', lastSuccessAt:'a'}}}}};
  assert.equal(semanticRecordFingerprint(first), semanticRecordFingerprint(repeated));
  assert.notEqual(semanticRecordFingerprint(first), semanticRecordFingerprint(failed));
});
