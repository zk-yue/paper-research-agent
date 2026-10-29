import test from 'node:test'
import assert from 'node:assert/strict'
import { subscribeWs } from '../src/api/websocket.ts'

function setup(t) {
  const originals = Object.fromEntries(['WebSocket', 'location', 'setTimeout', 'clearTimeout'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  const sockets = [], timers = new Map(), delays = []
  let sequence = 0
  class Socket {
    constructor(url) { this.url = url; sockets.push(this) }
    close() { this.closed = true; this.onclose?.() }
    receive(message) { this.onmessage({ data: JSON.stringify(message) }) }
  }
  Object.assign(globalThis, {
    WebSocket: Socket,
    location: { protocol: 'https:', host: 'localhost:5173' },
    setTimeout(callback, delay) { const id = ++sequence; timers.set(id, callback); delays.push(delay); return id },
    clearTimeout(id) { timers.delete(id) },
  })
  t.after(() => { for (const [key, descriptor] of Object.entries(originals)) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key] } })
  return { sockets, timers, delays, fire() { const [id, callback] = timers.entries().next().value; timers.delete(id); callback() } }
}

test('websocket retries with backoff, accepts a fresh snapshot and stops on cleanup', t => {
  const state = setup(t), messages = []
  const subscription = subscribeWs('test task', message => messages.push(message))
  assert.equal(state.sockets[0].url, 'wss://localhost:5173/ws/test%20task')
  state.sockets[0].close()
  assert.deepEqual(state.delays, [1000])
  state.fire()
  state.sockets[1].close()
  assert.deepEqual(state.delays, [1000, 2000])
  state.fire()
  state.sockets[2].onopen()
  state.sockets[2].receive({ type: 'init', task: { id: 'test task' } })
  assert.equal(messages[0].task.id, 'test task')
  state.sockets[2].close()
  assert.deepEqual(state.delays, [1000, 2000, 1000])
  subscription.close()
  assert.equal(state.timers.size, 0)
})
test('missing tasks do not reconnect forever or deliver messages after cleanup', t => {
  const state = setup(t), messages = []
  const subscription = subscribeWs('missing', message => messages.push(message))
  state.sockets[0].receive({ type: 'not_found' })
  assert.equal(state.timers.size, 0)
  assert.equal(state.sockets[0].closed, true)
  subscription.close()
  state.sockets[0].receive({ type: 'ping' })
  assert.deepEqual(messages, [{ type: 'not_found' }])
})
