'use strict';
const { postSyncPayload } = require('../shared/syncPayload');

async function postAgentUsage({ fetchFn, url, headers, summary, logger, timeoutMs = 30000, sessionDetailsEnabled = true }) {
  const controller = new AbortController();
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => {
      const error = new Error('Hub upload deadline exceeded');
      error.name = 'TimeoutError';
      controller.abort(error);
      reject(error);
    }, Math.max(1, timeoutMs));
  });
  try {
    return await Promise.race([deadline, (async () => {
      const { response } = await postSyncPayload(fetchFn, url, {
        headers, summary, logger, signal: controller.signal, omitSessionDetails: !sessionDetailsEnabled
      });
      // Avoid logging response bodies that might contain private data.
      if (!response.ok) throw new Error(`Hub responded ${response.status}`);
      return response.json();
    })()]);
  } finally {
    clearTimeout(timer);
  }
}
module.exports = { postAgentUsage };
