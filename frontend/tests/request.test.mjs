import assert from 'node:assert/strict';
import { afterEach, test, mock } from 'node:test';
import { ApiError, request } from '../src/api/request.ts';

afterEach(() => mock.restoreAll());

test('serializes JSON and returns the response', async () => {
  mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/tasks');
    assert.equal(options.headers.get('Content-Type'), 'application/json');
    assert.deepEqual(JSON.parse(options.body), { keywords: ['触觉'] });
    return new Response('{"task_id":"abc"}', { status: 201 });
  });
  assert.deepEqual(await request('/tasks', { method: 'POST', body: { keywords: ['触觉'] } }), { task_id: 'abc' });
});

test('surfaces backend error details, including DELETE errors', async () => {
  mock.method(globalThis, 'fetch', async () => new Response('{"detail":"Task not found"}', { status: 404 }));
  await assert.rejects(request('/tasks/missing', { method: 'DELETE' }), error => error instanceof ApiError && error.status === 404 && error.message === 'Task not found');
});

test('formats validation arrays without object-to-string errors', async () => {
  mock.method(globalThis, 'fetch', async () => new Response('{"detail":[{"msg":"keywords required"},{"msg":"invalid year"}]}', { status: 422 }));
  await assert.rejects(request('/tasks'), /keywords required; invalid year/);
});

test('handles non-JSON errors and empty success bodies', async () => {
  mock.method(globalThis, 'fetch', async () => new Response('<html>Bad gateway</html>', { status: 502 }));
  await assert.rejects(request('/tasks'), /HTTP 502/);
  mock.restoreAll();
  mock.method(globalThis, 'fetch', async () => new Response(null, { status: 204 }));
  assert.equal(await request('/tasks'), undefined);
});

test('rejects invalid JSON on successful responses', async () => {
  mock.method(globalThis, 'fetch', async () => new Response('<html>SPA fallback</html>', { status: 200 }));
  await assert.rejects(request('/tasks'), /无效的 JSON/);
});
