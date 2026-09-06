import { useEffect, useRef, useState } from 'react'
import { wsUrl } from '../api'
import type { TickUpdate } from '../types'

export type SocketStatus = 'connected' | 'reconnecting' | 'disconnected'

export function useFleetSocket(onTick: (tick: TickUpdate) => void) {
  const [status, setStatus] = useState<SocketStatus>('disconnected')
  const [skippedTicks, setSkippedTicks] = useState(0)
  const callback = useRef(onTick)
  const lastTick = useRef<number | null>(null)
  callback.current = onTick

  useEffect(() => {
    let socket: WebSocket | undefined
    let retry = 0
    let timer: number | undefined
    let disposed = false
    const connect = () => {
      if (disposed) return
      setStatus(retry ? 'reconnecting' : 'disconnected')
      socket = new WebSocket(wsUrl())
      socket.onopen = () => { retry = 0; setStatus('connected') }
      socket.onmessage = event => {
        try {
          const update = JSON.parse(event.data) as TickUpdate
          if (update.type && update.type !== 'TICK_UPDATE') return
          if (lastTick.current !== null && update.tick > lastTick.current + 1) setSkippedTicks(update.tick - lastTick.current - 1)
          lastTick.current = update.tick
          callback.current(update)
        } catch { /* Ignore malformed telemetry frames. */ }
      }
      socket.onclose = () => {
        if (disposed) return
        setStatus('reconnecting')
        const delay = Math.min(1000 * 2 ** retry, 10000)
        retry += 1
        timer = window.setTimeout(connect, delay)
      }
      socket.onerror = () => socket?.close()
    }
    connect()
    return () => { disposed = true; if (timer) window.clearTimeout(timer); socket?.close() }
  }, [])

  return { status, skippedTicks }
}