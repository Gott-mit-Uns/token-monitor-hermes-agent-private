'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const { postAgentUsage } = require('../../src/agent/upload');
const { createOrderedSink } = require('../../src/shared/orderedSink');

test('deadline covers a stalled response body and releases the upload queue', async () => {
  let calls = 0;
  let signal;
  const sink = createOrderedSink({send: (summary) => postAgentUsage({
    timeoutMs: 20, url: 'http://fixture.invalid', summary,
    fetchFn: async (_, options) => {
      calls++;
      if (calls === 1) {
        signal = options.signal;
        return {ok: true, status: 200, json: () => new Promise(() => {})};
      }
      return {ok: true, status: 200, json: async () => ({ok: true})};
    }
  })});
  const first = sink.enqueue({}, 1);
  const second = sink.enqueue({}, 2);
  await assert.rejects(first, {name: 'TimeoutError'});
  await second;
  assert.equal(signal.aborted, true);
  assert.equal(calls, 2);
  sink.stop();
});

test('HTTP failure does not read or disclose the response body', async () => {
  await assert.rejects(postAgentUsage({url: 'http://fixture.invalid', summary: {},
    fetchFn: async () => ({ok: false, status: 503, text: () => {throw new Error('must not read');}})
  }), /Hub responded 503/);
});

test('aggregate-only upload preserves source and replaces old Hub session detail including retry', async () => {
  const { mergeDeviceRecord } = require('../../src/shared/usage');
  const summary = {deviceId: 'fixture', trackedClients: ['hermes'], projectsEnabled: false,
    today: {totalTokens: 10, totalCost: 1, sessions: {a: {client: 'hermes', totalTokens: 10}}},
    month: {totalTokens: 20, totalCost: 2, sessions: {a: {client: 'hermes', totalTokens: 10}, b: {client: 'hermes', totalTokens: 10}}},
    allTime: {totalTokens: 30}, history: {daily: [{date: '2026-10-03', totalTokens: 10, outputTokens: 4}]}};
  const before = JSON.stringify(summary);
  const bodies = [];
  await postAgentUsage({url: 'http://fixture.invalid', summary, sessionDetailsEnabled: false,
    fetchFn: async (_, options) => {
      bodies.push(JSON.parse(options.body));
      return bodies.length === 1 ? {status: 413, ok: false, arrayBuffer: async () => new ArrayBuffer(0)}
        : {status: 200, ok: true, json: async () => ({ok: true})};
    }});
  assert.equal(JSON.stringify(summary), before);
  assert.equal(bodies.length, 2);
  for (const body of bodies) {
    assert.deepEqual(body.today.sessions, {});
    assert.deepEqual(body.month.sessions, {});
    assert.deepEqual(body.sessionDetailsOmitted, {today: 1, month: 2});
    assert.equal(body.today.totalTokens, 10);
    assert.equal(body.month.totalCost, 2);
    assert.equal(body.allTime.totalTokens, 30);
    const merged = mergeDeviceRecord(mergeDeviceRecord(null, summary), body);
    assert.deepEqual(merged.periods.today.sessions, {});
    assert.deepEqual(merged.periods.month.sessions, {});
    assert.deepEqual(merged.sessionDetailsOmitted, {today: 1, month: 2});
  }
});

test('default upload retains session detail and full aggregates', async () => {
  let body;
  await postAgentUsage({url: 'http://fixture.invalid', summary: {today: {totalTokens: 10, sessions: {a: {totalTokens: 10}}}},
    fetchFn: async (_, options) => {body = JSON.parse(options.body); return {ok: true, status: 200, json: async () => ({ok: true})};}});
  assert.equal(body.today.sessions.a.totalTokens, 10);
  assert.equal(body.sessionDetailsOmitted, undefined);
});
