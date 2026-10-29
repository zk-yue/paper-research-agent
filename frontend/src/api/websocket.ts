import type { WsMessage } from './types'

export function subscribeWs(
  taskId: string,
  onMessage: (msg: WsMessage) => void,
): { close: () => void } {
  let stopped = false
  let failures = 0
  let socket: WebSocket
  let timer: ReturnType<typeof setTimeout> | undefined
  const connect = () => {
    if (stopped) return
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    socket = new WebSocket(
      `${proto}://${location.host}/ws/${encodeURIComponent(taskId)}`,
    )
    socket.onopen = () => {
      failures = 0
    }
    socket.onmessage = (event) => {
      if (stopped) return
      let message: WsMessage
      try {
        message = JSON.parse(event.data) as WsMessage
      } catch {
        console.warn('Ignoring malformed WebSocket message')
        return
      }
      // The server sends a fresh task snapshot on every connection.
      if (message.type === 'not_found') {
        stopped = true
        socket.close()
      }
      onMessage(message)
    }
    socket.onclose = () => {
      if (!stopped)
        timer = setTimeout(connect, Math.min(1000 * 2 ** failures++, 15000))
    }
  }
  connect()
  return {
    close: () => {
      stopped = true
      clearTimeout(timer)
      socket.close()
    },
  }
}
