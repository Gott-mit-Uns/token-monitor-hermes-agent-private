'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const {
  createDeviceIdMigration,
  readPersistedDeviceId,
  writePersistedDeviceId
} = require('../../src/agent/deviceIdMigration');

test('first successful post records the current ID without deleting a device', async () => {
  const requests = [];
  const writes = [];
  const migration = createDeviceIdMigration({
    currentDeviceId: 'Hermes-NAS',
    hubUrl: 'http://hub.test/',
    fetch: async (...args) => { requests.push(args); return { ok: true }; },
    readPersistedDeviceId: () => '',
    writePersistedDeviceId: (value) => writes.push(value)
  });

  assert.deepEqual(await migration.migrateAfterSuccessfulPost(), { migrated: false, previousDeviceId: '' });
  assert.deepEqual(requests, []);
  assert.deepEqual(writes, ['Hermes-NAS']);
});

test('changed ID is retired after the new ID posts successfully', async () => {
  const requests = [];
  const writes = [];
  const logs = [];
  const migration = createDeviceIdMigration({
    currentDeviceId: 'Hermes/NAS new',
    hubUrl: 'http://hub.test/',
    secret: 'test-secret',
    fetch: async (...args) => { requests.push(args); return { ok: true }; },
    readPersistedDeviceId: () => 'NAS Hermes',
    writePersistedDeviceId: (value) => writes.push(value),
    logger: (message) => logs.push(message)
  });

  assert.deepEqual(await migration.migrateAfterSuccessfulPost(), { migrated: true, previousDeviceId: 'NAS Hermes' });
  assert.equal(requests[0][0], 'http://hub.test/api/devices/NAS%20Hermes');
  assert.deepEqual(requests[0][1], {
    method: 'DELETE',
    headers: { authorization: 'Bearer test-secret' }
  });
  assert.deepEqual(writes, ['Hermes/NAS new']);
  assert.deepEqual(logs, ['[device-id] migrated NAS Hermes -> Hermes/NAS new']);
});

test('failed retirement is retried and never commits the new ID early', async () => {
  let attempts = 0;
  const writes = [];
  const migration = createDeviceIdMigration({
    currentDeviceId: 'Hermes-NAS',
    hubUrl: 'http://hub.test',
    fetch: async () => {
      attempts += 1;
      if (attempts === 1) return { ok: false, status: 503, text: async () => 'unavailable' };
      return { ok: true };
    },
    readPersistedDeviceId: () => 'NAS Hermes',
    writePersistedDeviceId: (value) => writes.push(value),
    logger: () => {}
  });

  await assert.rejects(migration.migrateAfterSuccessfulPost(), /Hub responded 503: unavailable/);
  assert.deepEqual(writes, []);
  assert.deepEqual(await migration.migrateAfterSuccessfulPost(), { migrated: true, previousDeviceId: 'NAS Hermes' });
  assert.equal(attempts, 2);
  assert.deepEqual(writes, ['Hermes-NAS']);
});

test('matching persisted ID is a no-op across repeated deliveries', async () => {
  let reads = 0;
  const migration = createDeviceIdMigration({
    currentDeviceId: 'Hermes-NAS',
    hubUrl: 'http://hub.test',
    fetch: async () => { throw new Error('must not delete'); },
    readPersistedDeviceId: () => { reads += 1; return 'Hermes-NAS'; },
    writePersistedDeviceId: () => { throw new Error('must not rewrite'); }
  });

  await migration.migrateAfterSuccessfulPost();
  await migration.migrateAfterSuccessfulPost();
  assert.equal(reads, 1);
});

test('device ID state helpers tolerate a missing file and write atomically', () => {
  assert.equal(readPersistedDeviceId({
    path: '/config/agent-device-id',
    readFileSync: () => { const error = new Error('missing'); error.code = 'ENOENT'; throw error; }
  }), '');

  const calls = [];
  writePersistedDeviceId('Hermes-NAS', {
    path: '/config/agent-device-id',
    mkdirSync: (...args) => calls.push(['mkdir', ...args]),
    writeFileSync: (...args) => calls.push(['write', ...args]),
    renameSync: (...args) => calls.push(['rename', ...args])
  });
  assert.deepEqual(calls[0], ['mkdir', '/config', { recursive: true }]);
  assert.match(calls[1][1], /agent-device-id\.\d+\.tmp$/);
  assert.equal(calls[1][2], 'Hermes-NAS\n');
  assert.deepEqual(calls[1][3], { encoding: 'utf8', mode: 0o600 });
  assert.equal(calls[2][2], '/config/agent-device-id');
});
