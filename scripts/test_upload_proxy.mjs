// Run after `cd frontend && npm ci && npm run build`:
// node scripts/test_upload_proxy.mjs
// Uses only loopback servers and synthetic files; no real credentials or cases.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer, request } from 'node:http';
import { fileURLToPath } from 'node:url';

const frontend = fileURLToPath(new URL('../frontend/', import.meta.url));
const boundary = 'fm-synthetic-upload';
const part = Buffer.alloc(50 * 1024 * 1024, 'x');
const body = Buffer.concat([
  ...[1, 2].flatMap(index => [
    Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="files"; filename="fixture-${index}.txt"\r\nContent-Type: text/plain\r\n\r\n`),
    part, Buffer.from('\r\n')
  ]),
  Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="attachment_type"\r\n\r\nmedical_record\r\n--${boundary}--\r\n`)
]);
const expectedHash = createHash('sha256').update(body).digest('hex');
const upstream = createServer(async (req, res) => {
  let size = 0;
  const hash = createHash('sha256');
  for await (const chunk of req) { size += chunk.length; hash.update(chunk); }
  res.setHeader('Content-Type', 'application/json');
  res.end(JSON.stringify({ size, sha256: hash.digest('hex'), authorization: req.headers.authorization }));
});
upstream.listen(0, '127.0.0.1');
await once(upstream, 'listening');
const reservation = createServer();
reservation.listen(0, '127.0.0.1');
await once(reservation, 'listening');
const port = reservation.address().port;
await new Promise(resolve => reservation.close(resolve));
const origin = `http://127.0.0.1:${port}`;
const server = spawn(process.execPath, ['node_modules/next/dist/bin/next', 'start', '-H', '127.0.0.1', '-p', String(port)], {
  cwd: frontend,
  windowsHide: true,
  env: { ...process.env, INTERNAL_API_BASE_URL: `http://127.0.0.1:${upstream.address().port}`, FM_JOOLUN_EMBED_BASE_URL: origin },
  stdio: ['ignore', 'pipe', 'pipe']
});
let output = '';
server.stdout.on('data', chunk => { output += chunk; });
server.stderr.on('data', chunk => { output += chunk; });
try {
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    if (server.exitCode !== null) throw new Error(`Next.js exited: ${output}`);
    try {
      const response = await fetch(`${origin}/api/internal/health`, { signal: AbortSignal.timeout(1000) });
      if (response.ok) { ready = true; break; }
    } catch { /* Wait for the local server to start. */ }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  assert.ok(ready, `Next.js did not become ready: ${output}`);
  const received = await new Promise((resolve, reject) => {
    const req = request(`${origin}/api/v2/cases/synthetic/attachments`, {
      method: 'POST', headers: {
        Origin: origin, 'Sec-Fetch-Site': 'same-origin',
        Cookie: 'fm_session=synthetic; Admin-Token=synthetic-java',
        'Content-Type': `multipart/form-data; boundary=${boundary}`, 'Content-Length': body.length
      }
    }, res => {
      let json = '';
      res.on('data', chunk => { json += chunk; });
      res.on('end', () => {
        try { assert.equal(res.statusCode, 200, json); resolve(JSON.parse(json)); }
        catch (error) { reject(error); }
      });
      res.on('error', reject);
    });
    req.setTimeout(30000, () => req.destroy(new Error('Upload proxy timed out')));
    req.on('error', reject);
    req.end(body);
  });
  assert.deepEqual(received, { size: body.length, sha256: expectedHash, authorization: 'Bearer synthetic' });
  assert.doesNotMatch(output, /Request body exceeded/);
  console.log('PASS: production Next.js forwards 100 MiB multipart intact and converts Cookie to Bearer');
} finally {
  if (server.exitCode === null) { server.kill(); await once(server, 'exit'); }
  upstream.closeAllConnections();
  await new Promise(resolve => upstream.close(resolve));
}
