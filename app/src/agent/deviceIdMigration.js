'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { sharedDataDir } = require('../shared/config');

function deviceIdStatePath(options = {}) {
  return options.path || path.join(sharedDataDir(options), 'agent-device-id');
}

function readPersistedDeviceId(options = {}) {
  const filePath = deviceIdStatePath(options);
  const readFileSync = options.readFileSync || fs.readFileSync;
  try {
    return String(readFileSync(filePath, 'utf8')).trim();
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    return '';
  }
}

function writePersistedDeviceId(deviceId, options = {}) {
  const filePath = deviceIdStatePath(options);
  const mkdirSync = options.mkdirSync || fs.mkdirSync;
  const writeFileSync = options.writeFileSync || fs.writeFileSync;
  const renameSync = options.renameSync || fs.renameSync;
  const tempPath = `${filePath}.${process.pid}.tmp`;
  mkdirSync(path.dirname(filePath), { recursive: true });
  writeFileSync(tempPath, `${deviceId}\n`, { encoding: 'utf8', mode: 0o600 });
  renameSync(tempPath, filePath);
  return filePath;
}

function createDeviceIdMigration(options = {}) {
  const currentDeviceId = String(options.currentDeviceId || '').trim();
  const hubUrl = String(options.hubUrl || '').replace(/\/$/, '');
  const secret = String(options.secret || '').trim();
  const request = options.fetch || globalThis.fetch;
  const read = options.readPersistedDeviceId || (() => readPersistedDeviceId(options));
  const write = options.writePersistedDeviceId || ((value) => writePersistedDeviceId(value, options));
  const logger = typeof options.logger === 'function' ? options.logger : console.log;
  let persistedDeviceId;
  let migrationPromise = null;

  if (!currentDeviceId) throw new Error('Current device ID must not be empty.');
  if (!hubUrl) throw new Error('Hub URL must not be empty.');
  if (typeof request !== 'function') throw new Error('fetch must be a function.');

  async function migrateAfterSuccessfulPost() {
    if (persistedDeviceId === currentDeviceId) return { migrated: false, previousDeviceId: currentDeviceId };
    if (migrationPromise) return migrationPromise;

    migrationPromise = (async () => {
      if (persistedDeviceId === undefined) persistedDeviceId = String(read() || '').trim();
      const previousDeviceId = persistedDeviceId;
      if (previousDeviceId === currentDeviceId) {
        return { migrated: false, previousDeviceId };
      }
      if (previousDeviceId && previousDeviceId !== currentDeviceId) {
        const response = await request(`${hubUrl}/api/devices/${encodeURIComponent(previousDeviceId)}`, {
          method: 'DELETE',
          headers: secret ? { authorization: `Bearer ${secret}` } : {}
        });
        if (!response.ok) {
          const detail = typeof response.text === 'function' ? (await response.text()).slice(0, 300) : '';
          throw new Error(`Could not retire previous device ID ${previousDeviceId}: Hub responded ${response.status}${detail ? `: ${detail}` : ''}`);
        }
      }

      write(currentDeviceId);
      persistedDeviceId = currentDeviceId;
      if (previousDeviceId && previousDeviceId !== currentDeviceId) {
        logger(`[device-id] migrated ${previousDeviceId} -> ${currentDeviceId}`);
        return { migrated: true, previousDeviceId };
      }
      return { migrated: false, previousDeviceId };
    })();

    try {
      return await migrationPromise;
    } finally {
      migrationPromise = null;
    }
  }

  return { migrateAfterSuccessfulPost };
}

module.exports = {
  createDeviceIdMigration,
  deviceIdStatePath,
  readPersistedDeviceId,
  writePersistedDeviceId
};
