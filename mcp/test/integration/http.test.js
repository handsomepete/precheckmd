'use strict';

/**
 * Integration tests: spin up the real server.js as a child process (no
 * mocking of express/auth/routing) and hit it over real HTTP, the same way
 * a client actually would. Covers: auth enforcement on every data-bearing
 * route (including the POST /mcp fix — see SECURITY.md), path-traversal
 * rejection on GET /briefs/:filename, and the happy path of serving a
 * brief end to end.
 *
 * No real YNAB/Linear/HA/Gmail credentials are used or needed — BEARER_TOKEN
 * is the only env var set, so those integrations simply aren't configured
 * (calls to tools needing them would fail, but nothing here exercises them).
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const SERVER_PATH = path.join(__dirname, '..', '..', 'server.js');
const BEARER_TOKEN = 'test-token-do-not-use-in-prod';
const READY_TIMEOUT_MS = 10_000;

function authHeaders(token = BEARER_TOKEN) {
  return { Authorization: `Bearer ${token}` };
}

async function waitForReady(baseUrl, deadline) {
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${baseUrl}/`);
      if (res.ok) return;
    } catch (_) {
      // not listening yet
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`server at ${baseUrl} did not become ready in time`);
}

async function startServer({ port, briefsDir }) {
  const child = spawn(process.execPath, [SERVER_PATH], {
    cwd: path.join(__dirname, '..', '..'),
    env: {
      ...process.env,
      PORT: String(port),
      BEARER_TOKEN,
      BRIEFS_DIR: briefsDir,
      // Deliberately no YNAB/LINEAR/HA/GMAIL/AIRTABLE credentials.
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const baseUrl = `http://127.0.0.1:${port}`;
  await waitForReady(baseUrl, Date.now() + READY_TIMEOUT_MS);
  return { child, baseUrl };
}

function stopServer(child) {
  return new Promise((resolve) => {
    child.once('exit', resolve);
    child.kill('SIGTERM');
    setTimeout(() => { try { child.kill('SIGKILL'); } catch (_) {} }, 2000).unref();
  });
}

function randomPort() {
  return 20000 + Math.floor(Math.random() * 20000);
}

test('auth + traversal + happy-path brief serving, end to end', async (t) => {
  const briefsDir = fs.mkdtempSync(path.join(os.tmpdir(), 'briefs-http-test-'));
  fs.writeFileSync(path.join(briefsDir, '2026-07-08-morning.md'), '# Morning Brief\n\nAll good.');
  // Plant a secret one directory above briefsDir, same as the unit-level traversal test.
  const secretPath = path.join(briefsDir, '..', 'http-test-secret.txt');
  fs.writeFileSync(secretPath, 'top secret — should never be servable');

  const { child, baseUrl } = await startServer({ port: randomPort(), briefsDir });

  t.after(async () => {
    await stopServer(child);
    fs.rmSync(briefsDir, { recursive: true, force: true });
    fs.rmSync(secretPath, { force: true });
  });

  await t.test('GET / is public (no auth required)', async () => {
    const res = await fetch(`${baseUrl}/`);
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.equal(body.name, 'homeos-mcp');
    assert.ok(body.tools.includes('get_upcoming_bills'));
    assert.ok(body.tools.includes('get_property_status'));
    assert.ok(body.tools.includes('get_linear_focus'));
  });

  await t.test('GET /health requires auth', async () => {
    const noAuth = await fetch(`${baseUrl}/health`);
    assert.equal(noAuth.status, 401);

    const wrongAuth = await fetch(`${baseUrl}/health`, { headers: authHeaders('wrong-token') });
    assert.equal(wrongAuth.status, 401);

    const withAuth = await fetch(`${baseUrl}/health`, { headers: authHeaders() });
    assert.equal(withAuth.status, 200);
    const body = await withAuth.json();
    assert.ok('status' in body);
  });

  await t.test('GET /briefs requires auth and lists seeded briefs', async () => {
    const noAuth = await fetch(`${baseUrl}/briefs`);
    assert.equal(noAuth.status, 401);

    const withAuth = await fetch(`${baseUrl}/briefs`, { headers: authHeaders() });
    assert.equal(withAuth.status, 200);
    const body = await withAuth.json();
    assert.deepEqual(body.briefs, ['2026-07-08-morning.md']);
  });

  await t.test('GET /briefs/:filename requires auth and serves the real file on the happy path', async () => {
    const noAuth = await fetch(`${baseUrl}/briefs/2026-07-08-morning.md`);
    assert.equal(noAuth.status, 401);

    const withAuth = await fetch(`${baseUrl}/briefs/2026-07-08-morning.md`, { headers: authHeaders() });
    assert.equal(withAuth.status, 200);
    assert.match(withAuth.headers.get('content-type') || '', /text\/markdown/);
    const text = await withAuth.text();
    assert.equal(text, '# Morning Brief\n\nAll good.');
  });

  await t.test('GET /briefs/:filename blocks every traversal shape, even with valid auth', async () => {
    const attempts = [
      '../../http-test-secret.txt',
      '..%2f..%2fhttp-test-secret.txt',
      '..%2f..%2fetc%2fpasswd',
      '2026-07-08-morning.md/../../http-test-secret.txt',
      '%2e%2e%2f%2e%2e%2fhttp-test-secret.txt',
      '2026-07-08-brief.md',   // previously-accepted generic type, now rejected — see SECURITY.md
      '2026-07-08-morning.md.evil',
    ];

    for (const attempt of attempts) {
      const res = await fetch(`${baseUrl}/briefs/${attempt}`, { headers: authHeaders() });
      assert.notEqual(res.status, 200, `expected non-200 for traversal attempt: ${attempt}`);
      const body = await res.text();
      assert.ok(
        !body.includes('top secret'),
        `response body leaked the planted secret for attempt: ${attempt}`
      );
    }
  });

  await t.test('POST /mcp requires auth (regression test for the fixed auth gap)', async () => {
    const rpcBody = JSON.stringify({
      jsonrpc: '2.0', id: 1, method: 'tools/call',
      params: { name: 'get_health', arguments: {} },
    });

    const noAuth = await fetch(`${baseUrl}/mcp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
      body: rpcBody,
    });
    assert.equal(noAuth.status, 401);
  });

  await t.test('POST /mcp with valid auth executes a tool call end to end', async () => {
    const rpcBody = JSON.stringify({
      jsonrpc: '2.0', id: 2, method: 'tools/call',
      params: { name: 'get_health', arguments: {} },
    });

    const res = await fetch(`${baseUrl}/mcp`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json, text/event-stream',
        ...authHeaders(),
      },
      body: rpcBody,
    });
    assert.equal(res.status, 200);
    const raw = await res.text();
    // Streamable HTTP transport returns an SSE-framed body; the JSON-RPC
    // payload is on the "data:" line.
    const dataLine = raw.split('\n').find((l) => l.startsWith('data:'));
    assert.ok(dataLine, `expected an SSE data line in response, got: ${raw}`);
    const rpcResponse = JSON.parse(dataLine.slice('data:'.length).trim());
    assert.equal(rpcResponse.jsonrpc, '2.0');
    assert.equal(rpcResponse.id, 2);
    const toolResult = JSON.parse(rpcResponse.result.content[0].text);
    assert.ok('status' in toolResult);
  });
});
