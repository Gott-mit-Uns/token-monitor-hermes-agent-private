'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { sharedDataDir } = require('../shared/config');

const TRANSIENT_RECORD_KEYS = new Set(['observedAt', 'receivedAt', 'updatedAt']);

function stableSemanticJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableSemanticJson).join(',')}]`;
  if (value && typeof value === 'object') {
    const entries = Object.keys(value)
      .filter((key) => !TRANSIENT_RECORD_KEYS.has(key))
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableSemanticJson(value[key])}`);
    return `{${entries.join(',')}}`;
  }
  return JSON.stringify(value);
}

function semanticRecordFingerprint(record) {
  return crypto.createHash('sha256').update(stableSemanticJson(record)).digest('hex');
}

function createDeduplicatingDelivery(options = {}) {
  const send = typeof options.send === 'function' ? options.send : async () => {};
  const now = typeof options.now === 'function' ? options.now : Date.now;
  const onSuccess = typeof options.onSuccess === 'function' ? options.onSuccess : null;
  const heartbeatMs = Math.max(1, Number(options.heartbeatMs) || 5 * 60 * 1000);
  let lastFingerprint = null;
  let lastSentAt = Number.NEGATIVE_INFINITY;

  async function deliver(record) {
    const fingerprint = semanticRecordFingerprint(record);
    const attemptedAt = now();
    const elapsed = attemptedAt - lastSentAt;
    const heartbeatDue = !Number.isFinite(elapsed) || elapsed < 0 || elapsed >= heartbeatMs;
    if (fingerprint === lastFingerprint && !heartbeatDue) {
      return { sent: false, duplicate: true, fingerprint };
    }

    const result = await send(record);
    const sentAt = now();
    lastFingerprint = fingerprint;
    lastSentAt = sentAt;
    onSuccess?.({ fingerprint, record, sentAt });
    return { sent: true, duplicate: false, fingerprint, result, sentAt };
  }

  return { deliver };
}

function agentSuccessPath(options = {}) {
  return options.path || path.join(sharedDataDir(options), 'last-success');
}

function writeAgentSuccess(options = {}) {
  const filePath = agentSuccessPath(options);
  const now = typeof options.now === 'function' ? options.now : Date.now;
  const writeFileSync = options.writeFileSync || fs.writeFileSync;
  const renameSync = options.renameSync || fs.renameSync;
  const mkdirSync = options.mkdirSync || fs.mkdirSync;
  const at = new Date(now()).toISOString();
  const tempPath = `${filePath}.${process.pid}.tmp`;
  mkdirSync(path.dirname(filePath), { recursive: true });
  writeFileSync(tempPath, `${at}\n`, { encoding: 'utf8', mode: 0o600 });
  renameSync(tempPath, filePath);
  return { at, filePath };
}

module.exports = {
  agentSuccessPath,
  createDeduplicatingDelivery,
  semanticRecordFingerprint,
  stableSemanticJson,
  writeAgentSuccess
};
