import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, Battery, Bot, X } from 'lucide-react'
import { api } from './api'
import { useFleetSocket } from './hooks/useFleetSocket'
import { ControlBar } from './components/ControlBar'
import { GridCanvas } from './components/GridCanvas'
import { FleetSidebar } from './components/FleetSidebar'
import { TaskPanel } from './components/TaskPanel'
import { ObstaclePanel } from './components/ObstaclePanel'
import { MetricsPanel } from './components/MetricsPanel'
import { LoadingScreen } from './components/LoadingScreen'
import { STATE_LABELS } from './state-meta'
import type { Metrics, Point, Robot, SimulationStatus, Task, TempObstacle, TickUpdate, World } from './types'

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

  const { status: socket, skippedTicks } = useFleetSocket((update) => {
    setRobots(update.robots)
    setConflicts(update.active_conflicts)
    setObstacles(update.temporary_obstacles)
    setLastSyncedTick(update.tick)
    setStatus((old) => ({ ...old, tick: update.tick, timestamp_ms: update.timestamp_ms }))
    if (update.tasks) setTasks(update.tasks)
    if (update.metrics) setMetrics(update.metrics)
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
          conflicts: update.active_conflicts.length,
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

    void Promise.all([
      api.world().then(setWorld),
      api.robots().then(setRobots),
      api.tasks().then(setTasks),
      api.obstacles().then(setObstacles),
      api.status().then(setStatus),
      api.health().then((h) => {
        if (h.fleet_mode) setFleetMode(h.fleet_mode)
      }),
      api.chaosStatus().then((snapshot) => {
        setChaos(snapshot.enabled)
        setLoss(snapshot.packet_loss_pct)
      }),
    ]).catch((error) => setToast(error instanceof Error ? error.message : 'Backend unavailable'))

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
        onAction={(action) =>
          run(() => api.simulation(action), `${action.charAt(0).toUpperCase() + action.slice(1)} command accepted`).then(
            () => api.status().then(setStatus)
          )
        }
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
          <FleetSidebar
            robots={robots}
            selected={selected}
            filter={filter}
            onFilter={setFilter}
            onSelect={(robot) => setSelected(robot.robot_id)}
          />
          <TaskPanel
            tasks={tasks}
            busy={busy}
            onJob={(body) =>
              run(() => api.submitJob(body), 'Mission queued').then((res) => {
                void api.tasks().then(setTasks)
                return res
              })
            }
          />
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
        </section>
      </main>

      {chosen && (
        <aside className="robot-detail panel">
          <button className="close-detail" onClick={() => setSelected(null)} aria-label="Close robot details">
            <X size={15} />
          </button>
          <h2>
            <Bot size={18} /> {chosen.robot_id}
          </h2>
          <div>
            <span className={`state-badge ${chosen.state.toLowerCase()}`}>
              {STATE_LABELS[chosen.state] ?? chosen.state.replace(/_/g, ' ')}
            </span>
          </div>
          <div className="detail-battery">
            <span>
              <Battery size={14} /> Battery
            </span>
            <strong className={chosen.battery_pct < 20 ? 'battery-low' : ''}>
              {chosen.battery_pct.toFixed(1)}%
            </strong>
            <div>
              <i style={{ width: `${chosen.battery_pct}%` }} />
            </div>
          </div>
          <dl>
            <dt>Position</dt>
            <dd>
              ({chosen.position.x}, {chosen.position.y}), {chosen.heading.toLowerCase()}
            </dd>
            <dt>Current task</dt>
            <dd>{chosen.current_task_id ?? 'Unassigned'}</dd>
            <dt>Priority score</dt>
            <dd>{chosen.priority_score.toFixed(2)}</dd>
            <dt>Path nodes</dt>
            <dd>{chosen.path.length} reserved</dd>
          </dl>
        </aside>
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

      <footer className="system-footer">
        <span>{socket === 'connected' ? 'Connected to fleet coordinator' : 'Awaiting fleet link'}</span>
        <span>Tick interval {status.tick_ms} ms</span>
      </footer>
    </div>
  )
}