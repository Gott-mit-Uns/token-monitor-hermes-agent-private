'use strict';

function createThrottledWriter(options = {}) {
  if (typeof options.write !== 'function') throw new TypeError('write must be a function');
  const now = typeof options.now === 'function' ? options.now : Date.now;
  const setTimer = options.setTimeout || setTimeout;
  const clearTimer = options.clearTimeout || clearTimeout;
  const onError = typeof options.onError === 'function' ? options.onError : null;
  const intervalMs = Math.max(1, Number(options.intervalMs) || 5 * 60 * 1000);
  let lastWriteAt = now();
  let pendingValue;
  let dirty = false;
  let timer = null;

  function clearPendingTimer() {
    if (timer === null) return;
    clearTimer(timer);
    timer = null;
  }

  function schedule() {
    if (!dirty || timer !== null) return;
    const elapsed = now() - lastWriteAt;
    const delay = elapsed < 0 ? intervalMs : Math.max(0, intervalMs - elapsed);
    timer = setTimer(() => {
      timer = null;
      persist(false);
    }, delay);
    timer?.unref?.();
  }

  function persist(throwOnError) {
    if (!dirty) return false;
    clearPendingTimer();
    try {
      options.write(pendingValue);
      dirty = false;
      lastWriteAt = now();
      return true;
    } catch (error) {
      // Avoid a tight retry loop if the filesystem remains unavailable.
      lastWriteAt = now();
      onError?.(error);
      schedule();
      if (throwOnError) throw error;
      return false;
    }
  }

  function update(value) {
    pendingValue = value;
    dirty = true;
    if (now() - lastWriteAt >= intervalMs) persist(false);
    else schedule();
  }

  function flush() {
    return persist(true);
  }

  function stop() {
    clearPendingTimer();
    return flush();
  }

  function status() {
    return { dirty, lastWriteAt, scheduled: timer !== null };
  }

  return { flush, status, stop, update };
}

module.exports = {
  createThrottledWriter
};
