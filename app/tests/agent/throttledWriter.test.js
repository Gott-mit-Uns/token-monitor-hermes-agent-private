'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const { createThrottledWriter } = require('../../src/agent/throttledWriter');

function harness() {
  let now = 0;
  let timerId = 0;
  const timers = new Map();
  const writes = [];
  const writer = createThrottledWriter({
    intervalMs: 300,
    now: () => now,
    write: (value) => writes.push(value),
    setTimeout(callback, delay) {
      const id = ++timerId;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimeout: (id) => timers.delete(id)
  });
  return {
    advance: (ms) => { now += ms; },
    fire() {
      const next = timers.entries().next().value;
      if (!next) return;
      timers.delete(next[0]);
      next[1].callback();
    },
    timers,
    writer,
    writes
  };
}

test('coalesces repeated updates into the latest periodic write', () => {
  const h = harness();
  h.writer.update({ revision: 1 });
  h.writer.update({ revision: 2 });
  assert.equal(h.writes.length, 0);
  assert.equal(h.timers.size, 1);
  h.advance(300);
  h.fire();
  assert.deepEqual(h.writes, [{ revision: 2 }]);
  assert.equal(h.writer.status().dirty, false);
});

test('flush persists the latest value immediately and cancels the timer', () => {
  const h = harness();
  h.writer.update('latest');
  assert.equal(h.writer.flush(), true);
  assert.deepEqual(h.writes, ['latest']);
  assert.equal(h.timers.size, 0);
  assert.equal(h.writer.flush(), false);
});
