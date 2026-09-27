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
