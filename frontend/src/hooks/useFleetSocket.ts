import { useEffect, useRef, useState } from 'react'
import { wsUrl } from '../api'
import type {
  Conflict,
  HaLowStatus,
  Metrics,
  OrderInfo,
  Point,
  Robot,
  ShelfRecord,
  Task,
  TempObstacle,
} from '../types'

export type SocketStatus = 'connected' | 'reconnecting' | 'disconnected'

export interface FleetStore {
  tick: number
  timestamp_ms: number
  tick_ms?: number
  tickIntervalMs?: number
  lastTickArrival?: number
  lastTickNumber?: number
  robots: Map<string, Robot>
  robotsArray: Robot[]
  tasks: Task[]
  orders?: OrderInfo[]
  conflicts: Conflict[]
  obstacles: TempObstacle[]
  metrics?: Metrics
  inventory?: ShelfRecord[]
  sortation_chutes?: Record<string, { destination_zone: string; x: number; y: number }>
  fleet_status?: { running: boolean; mode: string; tick: number; armed_state?: string }
  halow_status?: HaLowStatus
  lastUpdated: number
}

function normalizeRobot(r: any, prev?: Robot): Robot {
  const robot_id: string = String(r.robot_id ?? r.id ?? prev?.robot_id ?? 'unknown')
  const position: Point = Array.isArray(r.position)
    ? { x: r.position[0], y: r.position[1] }
    : (r.position ?? (r.x !== undefined && r.y !== undefined ? { x: r.x, y: r.y } : (prev?.position ?? { x: 0, y: 0 })))
  const heading = r.heading ?? prev?.heading ?? 'NORTH'
  const robot_type = r.robot_type ?? prev?.robot_type ?? 'GOODS_TO_PERSON'
  const state = r.state ?? prev?.state ?? 'IDLE'
  const battery_pct = r.battery_pct ?? r.battery ?? prev?.battery_pct ?? 100
  const current_task_id = r.current_task_id !== undefined ? r.current_task_id : (prev?.current_task_id ?? null)
  const priority_score = r.priority_score !== undefined ? Number(r.priority_score) : (prev?.priority_score ?? 0)
  const last_updated_tick = r.tick ?? r.last_updated_tick ?? prev?.last_updated_tick ?? 0

  let path = prev?.path ?? []
  if (r.path && Array.isArray(r.path)) {
    path = r.path.map((p: any) => ({ x: p.x, y: p.y, t: p.t ?? 0 }))
  }

  return {
    robot_id,
    position,
    heading,
    robot_type,
    state,
    battery_pct,
    current_task_id,
    priority_score,
    last_updated_tick,
    path,
    carrying_pod_id: r.carrying_pod_id !== undefined ? r.carrying_pod_id : (prev?.carrying_pod_id ?? null),
    wait_ticks_so_far: r.wait_ticks_so_far ?? prev?.wait_ticks_so_far ?? 0,
    action: r.action ?? prev?.action ?? 'IDLE',
    conflict: r.conflict !== undefined ? r.conflict : (prev?.conflict ?? null),
    planner_latency_ms: r.planner_latency_ms ?? prev?.planner_latency_ms ?? 0,
    goal: r.goal ?? prev?.goal,
  }
}

export function useFleetSocket(
  onUiSync?: (store: FleetStore) => void,
  onInventorySync?: (update: any) => void
) {
  const [status, setStatus] = useState<SocketStatus>('disconnected')
  const [skippedTicks, setSkippedTicks] = useState(0)

  const storeRef = useRef<FleetStore>({
    tick: 0,
    timestamp_ms: Date.now(),
    robots: new Map(),
    robotsArray: [],
    tasks: [],
    conflicts: [],
    obstacles: [],
    lastUpdated: performance.now(),
  })

  const onUiSyncRef = useRef(onUiSync)
  onUiSyncRef.current = onUiSync
  const onInventorySyncRef = useRef(onInventorySync)
  onInventorySyncRef.current = onInventorySync
  const lastTick = useRef<number | null>(null)

  // 1-2 Hz timer (500ms = 2 Hz): Throttled UI state synchronization
  useEffect(() => {
    const timer = setInterval(() => {
      if (onUiSyncRef.current && storeRef.current.robotsArray.length > 0) {
        onUiSyncRef.current(storeRef.current)
      }
    }, 500)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    let socket: WebSocket | undefined
    let retry = 0
    let timer: number | undefined
    let disposed = false

    const connect = () => {
      if (disposed) return
      setStatus(retry ? 'reconnecting' : 'disconnected')
      socket = new WebSocket(wsUrl())
      socket.onopen = () => {
        retry = 0
        setStatus('connected')
      }

      socket.onmessage = (event) => {
        try {
          const update = JSON.parse(event.data) as any
          if (!update || !update.type) return

          if (update.type === 'INVENTORY_SYNC') {
            onInventorySyncRef.current?.(update)
            return
          }

          if (update.type === 'MAP_LAUNCHED') {
            const store = storeRef.current
            store.robots.clear()
            store.robotsArray = []
            store.tasks = []
            store.orders = []
            store.conflicts = []
            store.obstacles = []
            lastTick.current = null
            onUiSyncRef.current?.(store)
            return
          }

          if (update.type !== 'TICK_UPDATE' && update.type !== 'TICK_DELTA') {
            return
          }

          const store = storeRef.current
          const currentTick = Number(update.tick ?? store.tick)
          if (lastTick.current !== null && currentTick > lastTick.current + 1) {
            setSkippedTicks(currentTick - lastTick.current - 1)
          }
          lastTick.current = currentTick

          const nowPerf = performance.now()
          if (store.lastTickArrival && currentTick > (store.lastTickNumber ?? 0)) {
            const delta = nowPerf - store.lastTickArrival
            if (delta >= 40 && delta <= 5000) {
              store.tickIntervalMs = delta
            }
          }
          store.lastTickArrival = nowPerf
          store.lastTickNumber = currentTick

          if (update.tick_ms) {
            store.tick_ms = update.tick_ms
            if (!store.tickIntervalMs) store.tickIntervalMs = update.tick_ms
          } else if (update.metrics?.tick_ms_configured) {
            store.tick_ms = update.metrics.tick_ms_configured
            if (!store.tickIntervalMs) store.tickIntervalMs = update.metrics.tick_ms_configured
          } else if (!store.tickIntervalMs) {
            store.tickIntervalMs = 500
          }

          store.tick = currentTick
          store.timestamp_ms = update.timestamp_ms ?? Date.now()
          store.lastUpdated = nowPerf

          if (update.type === 'TICK_UPDATE') {
            // Full Baseline State on client handshake / reconnect
            store.robots.clear()
            if (update.robots && Array.isArray(update.robots)) {
              for (const r of update.robots) {
                const norm = normalizeRobot({ ...r, tick: currentTick })
                store.robots.set(norm.robot_id, norm)
              }
            }
            store.robotsArray = Array.from(store.robots.values())

            if (update.tasks) store.tasks = update.tasks
            if (update.orders) store.orders = update.orders
            store.conflicts = update.active_conflicts ?? []
            store.obstacles = update.temporary_obstacles ?? []
            if (update.metrics) store.metrics = update.metrics
            if (update.inventory) store.inventory = update.inventory
            if (update.sortation_chutes) store.sortation_chutes = update.sortation_chutes
            if (update.fleet_status) store.fleet_status = update.fleet_status
            if (update.halow_status) store.halow_status = update.halow_status

            // Trigger immediate UI sync on baseline connection so user doesn't wait 500ms
            onUiSyncRef.current?.(store)
          } else if (update.type === 'TICK_DELTA') {
            // Compact Delta: Patch only changed robots & entities
            if (update.robots && Array.isArray(update.robots)) {
              for (const r of update.robots) {
                const rid = r.robot_id ?? r.id
                if (!rid) continue
                const existing = store.robots.get(rid)
                const norm = normalizeRobot({ ...r, tick: currentTick }, existing)
                store.robots.set(rid, norm)
              }
              store.robotsArray = Array.from(store.robots.values())
            }

            if (update.tasks) store.tasks = update.tasks
            if (update.orders) store.orders = update.orders
            if (update.temporary_obstacles) store.obstacles = update.temporary_obstacles
            if (update.active_conflicts !== undefined) store.conflicts = update.active_conflicts
            if (update.metrics) store.metrics = update.metrics
            if (update.inventory) store.inventory = update.inventory
            if (update.fleet_status) store.fleet_status = update.fleet_status
            if (update.halow_status) store.halow_status = update.halow_status
          }
        } catch {
          /* Ignore malformed telemetry frames */
        }
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
    return () => {
      disposed = true
      if (timer) window.clearTimeout(timer)
      socket?.close()
    }
  }, [])

  return { status, skippedTicks, storeRef }
}