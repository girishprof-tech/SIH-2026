import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, Battery, Bot, X, Package, Activity, Radio, Layers, ShieldCheck, Box, Sliders } from 'lucide-react'
import { api, setApiOperatorRole } from './api'
import { useFleetSocket } from './hooks/useFleetSocket'
import { ControlBar } from './components/ControlBar'
import { RoleSelectionModal, type OperatorRole } from './components/RoleSelectionModal'
import { GridCanvas } from './components/GridCanvas'
import { FleetSidebar } from './components/FleetSidebar'
import { TaskPanel } from './components/TaskPanel'
import { ObstaclePanel } from './components/ObstaclePanel'
import { MetricsPanel } from './components/MetricsPanel'
import { LoadingScreen } from './components/LoadingScreen'
import { InventoryPanel } from './components/InventoryPanel'
import { TransferLogPanel } from './components/TransferLogPanel'
import { HaLowStatusWidget } from './components/HaLowStatusWidget'
import { RobotInspectorPanel } from './components/RobotInspectorPanel'
import { STATE_LABELS } from './state-meta'
import type {
  HaLowStatus,
  InventoryUpdateEvent,
  Metrics,
  Point,
  Robot,
  ShelfRecord,
  SimulationStatus,
  Task,
  TempObstacle,
  TickUpdate,
  World,
} from './types'

const emptyWorld: World = {
  width: 30,
  height: 30,
  static_obstacles: [],
  charging_stations: [],
  pickup_stations: [],
  dropoff_stations: [],
}

export default function App() {
  const [loading, setLoading] = useState(true)
  const [world, setWorld] = useState(emptyWorld)
  const [robots, setRobots] = useState<Robot[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [obstacles, setObstacles] = useState<TempObstacle[]>([])
  const [inventory, setInventory] = useState<ShelfRecord[]>([])
  const [transferLogs, setTransferLogs] = useState<InventoryUpdateEvent[]>([])
  const [haLowStatus, setHaLowStatus] = useState<HaLowStatus>({
    connected: true,
    last_msg_timestamp_ms: Date.now(),
    throttle_utilization: 0.12,
    bitrate_kbps: 150,
    packets_received: 0,
  })
  const [activeTab, setActiveTab] = useState<'fleet' | 'inventory' | 'sync_log' | 'obstacles'>('fleet')
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [status, setStatus] = useState<SimulationStatus>({
    running: false,
    tick: 0,
    timestamp_ms: 0,
    fleet_size: 0,
    tick_ms: 500,
  })
  const [conflicts, setConflicts] = useState<TickUpdate['active_conflicts']>([])
  const [history, setHistory] = useState<
    Array<{ tick: number; process: number; planner: number; conflicts: number; replans: number }>
  >([])
  const [selected, setSelected] = useState<string | null>(null)
  const [filter, setFilter] = useState('ALL')
  const [chaos, setChaos] = useState(false)
  const [loss, setLoss] = useState(0)
  const [busy, setBusy] = useState(false)
  const [showMeshLinks, setShowMeshLinks] = useState(true)
  const [toast, setToast] = useState<string | null>(null)
  const [fleetMode, setFleetMode] = useState<string>('Autonomous (10 AMRs)')
  const [lastSyncedTick, setLastSyncedTick] = useState<number>(0)
  const [cameraFollow, setCameraFollow] = useState<boolean>(true)

  const [operatorRole, setOperatorRole] = useState<OperatorRole | null>(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('sih_operator_role')
      if (saved === 'IMPORT' || saved === 'EXPORT' || saved === 'AUTHORITY') {
        setApiOperatorRole(saved)
        return saved as OperatorRole
      }
    }
    return null
  })
  const [showRoleModal, setShowRoleModal] = useState<boolean>(false)

  const handleSelectRole = (role: OperatorRole) => {
    setOperatorRole(role)
    setApiOperatorRole(role)
    try {
      localStorage.setItem('sih_operator_role', role)
    } catch {
      // ignore
    }
    setShowRoleModal(false)
    setToast(
      `Active station: ${
        role === 'AUTHORITY'
          ? 'Authority Station (Full Control)'
          : role === 'IMPORT'
          ? 'Import Station (Inbound)'
          : 'Export Station (Outbound)'
      }`
    )
  }

  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('sih_theme')
      if (saved === 'light' || saved === 'dark') return saved
    }
    return 'dark'
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    try {
      localStorage.setItem('sih_theme', theme)
    } catch {
      // ignore
    }
  }, [theme])

  const [isFullscreen, setIsFullscreen] = useState(false)

  const handleToggleFullscreen = useCallback(() => {
    if (!document.fullscreenElement) {
      if (document.documentElement.requestFullscreen) {
        document.documentElement.requestFullscreen().catch(() => {
          setIsFullscreen((prev) => !prev)
        })
      } else {
        setIsFullscreen((prev) => !prev)
      }
    } else {
      if (document.exitFullscreen) {
        document.exitFullscreen().catch(() => {
          setIsFullscreen(false)
        })
      } else {
        setIsFullscreen(false)
      }
    }
  }, [])

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(Boolean(document.fullscreenElement))
    }
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isFullscreen) {
        if (document.fullscreenElement) {
          document.exitFullscreen?.().catch(() => undefined)
        }
        setIsFullscreen(false)
      } else if (
        (e.key === 'f' || e.key === 'F') &&
        !['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as HTMLElement)?.tagName)
      ) {
        handleToggleFullscreen()
      }
    }
    document.addEventListener('fullscreenchange', handleFullscreenChange)
    document.addEventListener('webkitfullscreenchange', handleFullscreenChange)
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('fullscreenchange', handleFullscreenChange)
      document.removeEventListener('webkitfullscreenchange', handleFullscreenChange)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [isFullscreen, handleToggleFullscreen])

  const toggleTheme = () => {
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'))
  }

  const { status: socket, skippedTicks } = useFleetSocket((update: any) => {
    if (update.type === 'INVENTORY_SYNC') {
      const newEvent: InventoryUpdateEvent = {
        id: `SYNC-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        shelf_id: update.shelf_id || 'UNKNOWN',
        source_robot_id: update.last_audited_by || 'AMR-NODE',
        source: update.source || 'audit_scan',
        channel: update.channel || 'HALOW',
        tick: update.last_audited_tick || status.tick,
        box_count: update.current_box_count || 0,
        confidence: update.confidence ?? 1.0,
        sku_manifest: update.sku_manifest || {},
        timestamp_ms: update.timestamp_ms || Date.now(),
      }
      setTransferLogs((prev) => [newEvent, ...prev].slice(0, 100))

      setInventory((prev) => {
        const idx = prev.findIndex((s) => s.shelf_id === update.shelf_id)
        if (idx >= 0) {
          const updated = [...prev]
          updated[idx] = {
            ...updated[idx],
            current_box_count: update.current_box_count ?? updated[idx].current_box_count,
            sku_manifest: update.sku_manifest ?? updated[idx].sku_manifest,
            confidence: update.confidence ?? updated[idx].confidence,
            last_audited_tick: update.last_audited_tick ?? updated[idx].last_audited_tick,
            last_audited_by: update.last_audited_by ?? updated[idx].last_audited_by,
          }
          return updated
        }
        return prev
      })

      setHaLowStatus((prev) => ({
        ...prev,
        last_msg_timestamp_ms: Date.now(),
        packets_received: prev.packets_received + 1,
      }))
      return
    }

    if (update.robots) setRobots(update.robots)
    if (update.active_conflicts) setConflicts(update.active_conflicts)
    if (update.temporary_obstacles) setObstacles(update.temporary_obstacles)
    if (update.tick !== undefined) setLastSyncedTick(update.tick)
    setStatus((old) => ({ ...old, tick: update.tick, timestamp_ms: update.timestamp_ms }))
    if (update.tasks) setTasks(update.tasks)
    if (update.metrics) setMetrics(update.metrics)
    if (update.inventory) setInventory(update.inventory)
    if (update.sortation_chutes) {
      setWorld((prev) => ({ ...prev, sortation_chutes: update.sortation_chutes }))
    }
    if (update.halow_status) {
      setHaLowStatus(update.halow_status)
    }
    if (update.fleet_status) {
      setStatus((old) => ({
        ...old,
        running: update.fleet_status!.running,
        tick: update.tick,
      }))
      if (update.fleet_status.mode) {
        setFleetMode(update.fleet_status.mode)
      }
    }
    setHistory((old) =>
      [
        ...old,
        {
          tick: update.tick,
          process: update.metrics?.last_tick_processing_ms ?? metrics?.last_tick_processing_ms ?? 0,
          planner: update.metrics?.planner_latency_ms ?? metrics?.planner_latency_ms ?? 0,
          conflicts: (update.active_conflicts || []).length,
          replans: update.metrics?.replans ?? metrics?.replans ?? 0,
        },
      ].slice(-50)
    )
  })

  const run = async <T,>(action: () => Promise<T>, success?: string): Promise<T> => {
    setBusy(true)
    try {
      const result = await action()
      if (success) setToast(success)
      return result
    } catch (error) {
      setToast(error instanceof Error ? error.message : 'Request failed')
      throw error
    } finally {
      setBusy(false)
    }
  }

  const handleLoadingComplete = useCallback(() => {
    setLoading(false)
  }, [])

  useEffect(() => {
    const minLoadTimer = window.setTimeout(() => {
      setLoading(false)
    }, 5200)

    // Reset simulation to clean initial state on page load / reload
    api.simulation('reset')
      .catch(() => undefined)
      .finally(() => {
        void Promise.all([
          api.world().then(setWorld),
          api.robots().then(setRobots),
          api.tasks().then(setTasks),
          api.obstacles().then(setObstacles),
          api.status().then(setStatus),
          api.inventory().then((inv) => setInventory(inv.shelves)).catch(() => undefined),
          api.health().then((h) => {
            if (h.fleet_mode) setFleetMode(h.fleet_mode)
          }),
          api.chaosStatus().then((snapshot) => {
            setChaos(snapshot.enabled)
            setLoss(snapshot.packet_loss_pct)
          }),
        ]).catch((error) => setToast(error instanceof Error ? error.message : 'Backend unavailable'))
      })

    // Polling restricted to external human toggles (chaos mode) and backend health/mode detection.
    // All active simulation telemetry (robots, tasks, obstacles, metrics, status) is delivered synchronously per tick over WebSocket.
    const timer = window.setInterval(() => {
      api.chaosStatus()
        .then((snapshot) => {
          setChaos(snapshot.enabled)
          setLoss(snapshot.packet_loss_pct)
        })
        .catch(() => undefined)
      api.health()
        .then((h) => {
          if (h.fleet_mode) setFleetMode(h.fleet_mode)
        })
        .catch(() => undefined)
    }, 5000)

    return () => {
      window.clearTimeout(minLoadTimer)
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(null), 5000)
    return () => window.clearTimeout(timer)
  }, [toast])

  const chosen = robots.find((robot) => robot.robot_id === selected)
  const selectCell = (point: Point) => {
    if (!busy) setToast(`Map coordinate selected: (${point.x}, ${point.y})`)
  }
  const pause = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms))

  const runDemo = async () => {
    setBusy(true)
    setToast('Demo scenario started: fleet coordination sequence running')
    try {
      if (!status.running) await api.simulation('start')
      const jobs = [
        { job_type: 'fetch_item' as const, item_id: 'DEMO-FETCH-01', urgency: 5 },
        { job_type: 'sort_batch' as const, zone: 'SORTING_ZONE', urgency: 4 },
        { job_type: 'audit_checkpoint' as const, urgency: 2 },
        { job_type: 'fetch_item' as const, item_id: 'DEMO-FETCH-02', urgency: 3 },
        { job_type: 'sort_batch' as const, zone: 'SORTING_ZONE', urgency: 5 },
        { job_type: 'audit_checkpoint' as const, urgency: 1 },
        { job_type: 'fetch_item' as const, item_id: 'DEMO-FETCH-03', urgency: 4 },
        { job_type: 'sort_batch' as const, zone: 'SORTING_ZONE', urgency: 3 },
        { job_type: 'audit_checkpoint' as const, urgency: 2 },
      ]
      for (const [index, job] of jobs.entries()) {
        if (index > 0) await pause(3500)
        try {
          await api.submitJob(job)
        } catch (error) {
          setToast(
            error instanceof Error ? `Demo job ${index + 1} skipped: ${error.message}` : `Demo job ${index + 1} skipped`
          )
        }
        if (index === 1)
          await api.addObstacle({
            obstacle_id: `DEMO-OBSTACLE-${Date.now()}`,
            x: 1,
            y: 10,
            duration_ticks: 40,
          })
        if (index === 3) await api.chaos(15)
      }
      setToast('Demo scenario complete: jobs, obstacle, and resilience event deployed')
    } catch (error) {
      setToast(error instanceof Error ? `Demo stopped: ${error.message}` : 'Demo scenario stopped')
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return <LoadingScreen theme={theme} durationMs={5000} onComplete={handleLoadingComplete} />
  }

  return (
    <div className="app-shell" data-theme={theme}>
      <ControlBar
        running={status.running}
        tick={status.tick}
        lastSyncedTick={lastSyncedTick}
        fleetMode={fleetMode}
        timestamp={status.timestamp_ms}
        socket={socket}
        skipped={skippedTicks}
        chaos={chaos}
        loss={loss}
        busy={busy}
        showMeshLinks={showMeshLinks}
        onToggleMeshLinks={() => setShowMeshLinks((prev) => !prev)}
        theme={theme}
        onToggleTheme={toggleTheme}
        isFullscreen={isFullscreen}
        onToggleFullscreen={handleToggleFullscreen}
        operatorRole={operatorRole ?? 'AUTHORITY'}
        onOpenRoleModal={() => setShowRoleModal(true)}
        onAction={(action) => {
          if (action === 'start') {
            setStatus((prev) => ({ ...prev, running: true }))
          } else if (action === 'pause' || action === 'reset') {
            setStatus((prev) => ({ ...prev, running: false }))
          }
          return run(() => api.simulation(action), `${action.charAt(0).toUpperCase() + action.slice(1)} command accepted`).then(
            () => {
              if (action === 'reset') {
                setSelected(null)
                void Promise.all([
                  api.world().then(setWorld),
                  api.robots().then(setRobots),
                  api.tasks().then(setTasks),
                  api.obstacles().then(setObstacles),
                  api.status().then(setStatus),
                ])
              } else {
                void api.status().then(setStatus)
              }
            }
          )
        }}
        onChaos={(enabled, value) => {
          setChaos(enabled)
          setLoss(value)
          void run(
            () => api.chaos(enabled ? value : 0),
            enabled ? `Chaos mode enabled at ${value}%` : 'Chaos mode disabled'
          )
        }}
        onDemo={() => void runDemo()}
      />

      {socket !== 'connected' && (
        <div className={`connection-alert-banner ${socket}`} role="alert">
          <AlertCircle size={16} />
          <span>
            {socket === 'reconnecting'
              ? 'Mesh telemetry stream interrupted — reconnecting to local peer UDP orchestrator...'
              : 'Mesh telemetry offline — backend disconnected. AMR nodes continuing autonomous decentralized execution.'}
          </span>
          <span className="reconnect-hint">Last authoritative sync: Tick #{lastSyncedTick}</span>
        </div>
      )}

      <main className="dashboard">
        <section className="map-column">
          <GridCanvas
            world={world}
            robots={robots}
            tasks={tasks}
            conflicts={conflicts}
            obstacles={obstacles}
            tick={status.tick}
            selected={selected}
            showMeshLinks={showMeshLinks}
            theme={theme}
            isFullscreen={isFullscreen}
            onToggleFullscreen={handleToggleFullscreen}
            onRobot={setSelected ? (robot) => setSelected(robot.robot_id) : () => undefined}
            onCell={selectCell}
            cameraFollow={cameraFollow}
            onToggleCameraFollow={() => setCameraFollow((prev) => !prev)}
          />
          <div className="map-footer">
            <span>
              <i className="legend-dot idle" /> Idle
            </span>
            <span>
              <i className="legend-dot route" /> En route
            </span>
            <span>
              <i className="legend-dot conflict" /> Negotiating
            </span>
            <span>
              <i className="legend-dot charge" /> Charging
            </span>
            <span>
              <i className="legend-dot mesh" /> P2P Mesh Links
            </span>
            <span className="map-hint">Click robot to inspect, click cell to target, scroll to zoom</span>
          </div>
          <MetricsPanel metrics={metrics} history={history} robots={robots} theme={theme} />
        </section>

        <section className="side-column">
          <nav className="side-tabs-nav" aria-label="Dashboard Views">
            <button
              className={`side-tab-btn ${activeTab === 'fleet' ? 'active' : ''}`}
              onClick={() => setActiveTab('fleet')}
              title="Fleet Status & Mission Queue"
            >
              <Bot size={13} />
              <span>Fleet & Tasks</span>
            </button>
            <button
              className={`side-tab-btn ${activeTab === 'inventory' ? 'active' : ''}`}
              onClick={() => setActiveTab('inventory')}
              title="Decentralized Inventory Ledger"
            >
              <Package size={13} />
              <span>Inventory</span>
            </button>
            <button
              className={`side-tab-btn ${activeTab === 'sync_log' ? 'active' : ''}`}
              onClick={() => setActiveTab('sync_log')}
              title="Dual-Channel Sync & Audit Feed"
            >
              <Activity size={13} />
              <span>Sync Log</span>
            </button>
            <button
              className={`side-tab-btn ${activeTab === 'obstacles' ? 'active' : ''}`}
              onClick={() => setActiveTab('obstacles')}
              title="Temporary Dynamic Hazards"
            >
              <Sliders size={13} />
              <span>Hazards</span>
            </button>
          </nav>

          {activeTab === 'fleet' && (
            <>
              <FleetSidebar
                robots={robots}
                selected={selected}
                filter={filter}
                onFilter={setFilter}
                onSelect={(robot) => setSelected(robot.robot_id)}
                operatorRole={operatorRole ?? 'AUTHORITY'}
              />
              <TaskPanel
                tasks={tasks}
                busy={busy}
                operatorRole={operatorRole ?? 'AUTHORITY'}
                onJob={(body) =>
                  run(() => api.submitJob(body), 'Mission queued').then((res) => {
                    void api.tasks().then(setTasks)
                    return res
                  })
                }
              />
            </>
          )}

          {activeTab === 'inventory' && (
            <>
              <HaLowStatusWidget status={haLowStatus} currentTick={status.tick} />
              <InventoryPanel
                inventory={inventory}
                currentTick={status.tick}
                onSelectShelf={(shelfId) => {
                  setToast(`Selected Shelf Pod: ${shelfId}`)
                }}
              />
            </>
          )}

          {activeTab === 'sync_log' && (
            <>
              <HaLowStatusWidget status={haLowStatus} currentTick={status.tick} />
              <TransferLogPanel
                logs={transferLogs}
                onClearLogs={() => setTransferLogs([])}
              />
            </>
          )}

          {activeTab === 'obstacles' && (
            <ObstaclePanel
              obstacles={obstacles}
              tick={status.tick}
              busy={busy}
              onAdd={(body) =>
                run(() => api.addObstacle(body), 'Temporary obstacle deployed').then(() =>
                  api.obstacles().then(setObstacles)
                )
              }
              onRemove={(id) =>
                run(() => api.removeObstacle(id), 'Obstacle removed').then(() =>
                  api.obstacles().then(setObstacles)
                )
              }
            />
          )}
        </section>
      </main>

      {chosen && (
        <RobotInspectorPanel
          robot={chosen}
          tick={status.tick}
          cameraFollow={cameraFollow}
          onToggleCameraFollow={() => setCameraFollow((prev) => !prev)}
          onClose={() => setSelected(null)}
          onEStop={(robotId) => {
            void run(() => api.eStop(robotId), `Emergency stop dispatched to ${robotId}`).then(() => {
              void api.robots().then(setRobots)
            })
          }}
          onReset={(robotId) => {
            void run(() => api.resetRobot(robotId), `State reset dispatched to ${robotId}`).then(() => {
              void api.robots().then(setRobots)
            })
          }}
        />
      )}

      {toast && (
        <div className="toast" role="status">
          <AlertCircle size={16} />
          <span>{toast}</span>
          <button onClick={() => setToast(null)} aria-label="Dismiss notification">
            <X size={14} />
          </button>
        </div>
      )}

      <RoleSelectionModal
        isOpen={showRoleModal || operatorRole === null}
        currentRole={operatorRole}
        onSelectRole={handleSelectRole}
        onClose={operatorRole !== null ? () => setShowRoleModal(false) : undefined}
      />

      <footer className="system-footer">
        <span>{socket === 'connected' ? 'Connected to fleet coordinator' : 'Awaiting fleet link'}</span>
        <span>Tick interval {status.tick_ms} ms</span>
      </footer>
    </div>
  )
}