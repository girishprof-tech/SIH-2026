import { useCallback, useEffect, useState, useMemo } from 'react'
import { AlertCircle, Battery, Bot, X, Package, Activity, Radio, Layers, ShieldCheck, Box, Sliders, AlertTriangle, RefreshCw, ShoppingCart } from 'lucide-react'
import { api, setApiOperatorRole, API_BASE } from './api'
import { useFleetSocket, type FleetStore } from './hooks/useFleetSocket'
import { ControlBar } from './components/ControlBar'
import { RoleSelectionModal, type OperatorRole } from './components/RoleSelectionModal'
import { GridCanvas } from './components/GridCanvas'
import { FleetSidebar } from './components/FleetSidebar'
import { TaskPanel } from './components/TaskPanel'
import { OrdersPanel } from './components/OrdersPanel'
import { ObstaclePanel } from './components/ObstaclePanel'
import { MetricsPanel } from './components/MetricsPanel'
import { LoadingScreen } from './components/LoadingScreen'
import { InventoryPanel } from './components/InventoryPanel'
import { TransferLogPanel } from './components/TransferLogPanel'
import { HaLowStatusWidget } from './components/HaLowStatusWidget'
import { RobotInspectorPanel } from './components/RobotInspectorPanel'
import { LaunchScreen } from './components/LaunchScreen'
import { WarehouseMapEditor } from './components/WarehouseMapEditor'
import { STATE_LABELS } from './state-meta'
import type {
  HaLowStatus,
  InventoryUpdateEvent,
  Metrics,
  OrderInfo,
  Point,
  Robot,
  ShelfRecord,
  SimulationStatus,
  Task,
  TempObstacle,
  TickUpdate,
  World,
  WarehouseMap,
  MapPreset,
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
  const [orders, setOrders] = useState<OrderInfo[]>([])
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
  const [activeTab, setActiveTab] = useState<'fleet' | 'orders' | 'inventory' | 'sync_log' | 'obstacles'>('fleet')
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
  const [armedState, setArmedState] = useState<string>('ARMED — waiting for tasks')

  // Map launch and editor state
  const [launchChoiceMade, setLaunchChoiceMade] = useState(false)
  const [showMapEditor, setShowMapEditor] = useState(false)
  const [activeMap, setActiveMap] = useState<WarehouseMap | null>(null)
  const [simSpeed, setSimSpeed] = useState<number>(1.0)

  useEffect(() => {
    api.mapCurrent().then((m) => {
      if (m) setActiveMap(m)
    }).catch(() => {})
  }, [])

  const handleUseBuiltIn = useCallback(async () => {
    await api.mapLaunch('test_map.json')
    const [w, r, t, o, m] = await Promise.all([
      api.world(),
      api.robots(),
      api.tasks(),
      api.getOrders().catch(() => []),
      api.mapCurrent().catch(() => null),
    ])
    setWorld(w)
    setRobots(r)
    setTasks(t)
    setOrders(o || [])
    if (m) setActiveMap(m)
    setLaunchChoiceMade(true)
    setToast('Standard warehouse fleet launched & armed — zero motion until tasked')
  }, [])

  const handleSelectPreset = useCallback(async (preset: MapPreset) => {
    await api.mapLaunch(preset.filename)
    const [w, r, t, o, m] = await Promise.all([
      api.world(),
      api.robots(),
      api.tasks(),
      api.getOrders().catch(() => []),
      api.mapCurrent().catch(() => null),
    ])
    setWorld(w)
    setRobots(r)
    setTasks(t)
    setOrders(o || [])
    if (m) setActiveMap(m)
    setLaunchChoiceMade(true)
    setToast(`Preset '${preset.name}' launched & armed — zero motion until tasked`)
  }, [])

  const handleLaunchFromEditor = useCallback(async (launchedMap: WarehouseMap) => {
    const [w, r, t, o] = await Promise.all([api.world(), api.robots(), api.tasks(), api.getOrders().catch(() => [])])
    setWorld(w)
    setRobots(r)
    setTasks(t)
    setOrders(o || [])
    setActiveMap(launchedMap)
    setShowMapEditor(false)
    setLaunchChoiceMade(true)
    setToast(`Custom map '${launchedMap.name}' launched & armed — zero motion until tasked`)
  }, [])

  const handleSetSpeed = useCallback(async (newSpeed: number) => {
    setSimSpeed(newSpeed)
    try {
      await api.setSimulationSpeed(newSpeed)
      setToast(`Simulation speed scaled to ${newSpeed}x`)
    } catch (err) {
      console.error('Failed to set simulation speed:', err)
    }
  }, [])

  const handleChangeMap = useCallback(() => {
    setLaunchChoiceMade(false)
    setToast('Select or edit a warehouse map to launch')
  }, [])

  const robotCounts = useMemo(() => {
    let g2p = 0
    let sorting = 0
    let audit = 0
    const list = (Array.isArray(robots) ? robots : Object.values(robots || {})) as Robot[]
    for (const r of list) {
      const t = String(r.robot_type || (r as any).type || 'GOODS_TO_PERSON').toUpperCase()
      if (t.includes('SORT')) sorting++
      else if (t.includes('AUDIT') || t.includes('SCAN')) audit++
      else g2p++
    }
    return { g2p, sorting, audit, total: list.length }
  }, [robots])

  const activeMapName = activeMap?.name || (world as any)?.name || 'Standard 30x30 Test Warehouse'
  const gridDimensions = {
    width: activeMap?.grid?.width || world?.width || 30,
    height: activeMap?.grid?.height || world?.height || 30,
  }

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

  const handleInventorySync = useCallback((update: any) => {
    const newEvent: InventoryUpdateEvent = {
      id: `SYNC-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      shelf_id: update.shelf_id || 'UNKNOWN',
      source_robot_id: update.last_audited_by || 'AMR-NODE',
      source: update.source || 'audit_scan',
      channel: update.channel || 'HALOW',
      tick: update.last_audited_tick || 0,
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
  }, [])

  const handleUiSync = useCallback((store: FleetStore) => {
    setRobots(store.robotsArray)
    if (store.conflicts) setConflicts(store.conflicts)
    if (store.obstacles) setObstacles(store.obstacles)
    setLastSyncedTick(store.tick)
    setStatus((old) => ({ ...old, tick: store.tick, timestamp_ms: store.timestamp_ms }))
    if (store.tasks) setTasks(store.tasks)
    if (store.orders) setOrders(store.orders)
    if (store.metrics) setMetrics(store.metrics)
    if (store.inventory) setInventory(store.inventory)
    if (store.sortation_chutes) {
      setWorld((prev) => ({ ...prev, sortation_chutes: store.sortation_chutes }))
    }
    if (store.halow_status) {
      setHaLowStatus(store.halow_status)
    }
    if (store.fleet_status) {
      setStatus((old) => ({
        ...old,
        running: store.fleet_status!.running,
        tick: store.tick,
      }))
      if (store.fleet_status.mode) {
        setFleetMode(store.fleet_status.mode)
      }
      if (store.fleet_status.armed_state) {
        setArmedState(store.fleet_status.armed_state)
      } else {
        const hasActive =
          (store.tasks || []).some((t) => t.status !== 'COMPLETED' && t.status !== 'FAILED' && t.status !== 'UNCLAIMED') ||
          Array.from(store.robots.values()).some((r) => r.current_task_id != null || ['EN_ROUTE_PICKUP', 'PICKING', 'EN_ROUTE_DROPOFF', 'DROPPING', 'LIFTING', 'LOWERING'].includes(r.state))
        setArmedState(hasActive ? 'RUNNING' : 'ARMED — waiting for tasks')
      }
    }
    setHistory((old) =>
      [
        ...old,
        {
          tick: store.tick,
          process: store.metrics?.last_tick_processing_ms ?? 0,
          planner: store.metrics?.planner_latency_ms ?? 0,
          conflicts: (store.conflicts || []).length,
          replans: store.metrics?.replans ?? 0,
        },
      ].slice(-50)
    )
  }, [])

  const { status: socket, skippedTicks, storeRef } = useFleetSocket(handleUiSync, handleInventorySync)

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

  const [backendConnected, setBackendConnected] = useState<boolean>(true)
  const [isRetrying, setIsRetrying] = useState<boolean>(false)
  const [unfinishedRecoveryCount, setUnfinishedRecoveryCount] = useState<number>(0)
  const [showRecoveryBanner, setShowRecoveryBanner] = useState<boolean>(true)

  const handleResumeRecovery = async () => {
    try {
      const res = await api.recoveryResume()
      setToast(`Resumed ${res.resumed_count} jobs from previous session (${res.discarded_count} discarded)`)
      setUnfinishedRecoveryCount(0)
      setShowRecoveryBanner(false)
      const t = await api.tasks()
      setTasks(t)
    } catch (e: any) {
      setToast(`Failed to resume jobs: ${e.message}`)
    }
  }

  const handleDiscardRecovery = async () => {
    try {
      await api.recoveryDiscard()
      setToast('Discarded unfinished jobs from previous session')
      setUnfinishedRecoveryCount(0)
      setShowRecoveryBanner(false)
    } catch (e: any) {
      setToast(`Failed to discard jobs: ${e.message}`)
    }
  }

  const checkHealthAndConnect = useCallback(async () => {
    try {
      const h = await api.health()
      if (h && (h.status === 'ok' || h.status === 'degraded')) {
        setBackendConnected(true)
        if (h.fleet_mode) setFleetMode(h.fleet_mode)
        return true
      }
      setBackendConnected(false)
      return false
    } catch {
      setBackendConnected(false)
      return false
    }
  }, [])

  const handleLoadingComplete = useCallback(() => {
    setLoading(false)
  }, [])

  useEffect(() => {
    checkHealthAndConnect()

    void Promise.all([
      api.world().then(setWorld),
      api.robots().then(setRobots),
      api.tasks().then(setTasks),
      api.getOrders().then(setOrders).catch(() => undefined),
      api.obstacles().then(setObstacles),
      api.status().then(setStatus),
      api.inventory().then((inv) => setInventory(inv.shelves)).catch(() => undefined),
      api.health().then((h) => {
        if (h.fleet_mode) setFleetMode(h.fleet_mode)
      }),
      api.recoveryPending().then((res) => {
        if (res && res.count > 0) {
          setUnfinishedRecoveryCount(res.count)
          setShowRecoveryBanner(true)
        }
      }).catch(() => undefined),
      api.chaosStatus().then((snapshot) => {
        setChaos(snapshot.enabled)
        setLoss(snapshot.packet_loss_pct)
      }),
    ]).catch((error) => setToast(error instanceof Error ? error.message : 'Backend unavailable'))

    // Polling restricted to external human toggles (chaos mode) and backend health/mode detection.
    const timer = window.setInterval(() => {
      checkHealthAndConnect()
      api.chaosStatus()
        .then((snapshot) => {
          setChaos(snapshot.enabled)
          setLoss(snapshot.packet_loss_pct)
        })
        .catch(() => undefined)
    }, 2500)

    return () => window.clearInterval(timer)
  }, [checkHealthAndConnect])

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
    setToast('Demo scenario started: dispatching multi-robot coordination sequence...')
    try {
      if (!status.running) {
        await api.simulation('start')
        setStatus((prev) => ({ ...prev, running: true }))
      }
      const demoActions = [
        // 1. Goods-to-Person pod retrieval for SKU-A10 -> Pick Station -> OUT-1
        () => api.createOrder({ sku: 'SKU-A10', quantity: 1, destination_gate: 'OUT-1', urgency: 5 }),
        // 2. Sorting AMR batch induction
        () => api.submitJob({ job_type: 'sort_batch', zone: 'SORTING_ZONE', urgency: 4 }),
        // 3. Scanning AMR audit patrol
        () => api.submitJob({ job_type: 'audit_checkpoint', urgency: 3 }),
        // 4. Secondary G2P order for SKU-B10 -> OUT-2
        () => api.createOrder({ sku: 'SKU-B10', quantity: 1, destination_gate: 'OUT-2', urgency: 4 }),
        // 5. Secondary Sorting AMR transfer to chute
        () => api.submitJob({ job_type: 'sort_batch', destination_chute: 'CHUTE-02', urgency: 5 }),
        // 6. Secondary audit mission
        () => api.submitJob({ job_type: 'audit_checkpoint', urgency: 2 }),
      ]
      for (const [index, action] of demoActions.entries()) {
        if (index > 0) await pause(350)
        try {
          await action()
        } catch (error) {
          console.warn(`Demo action ${index + 1} skipped:`, error)
        }
      }
      setToast('Demo active: 6 autonomous AMRs moving across G2P, Sorting, and Audit!')
      void Promise.all([api.robots().then(setRobots), api.tasks().then(setTasks), api.getOrders().then(setOrders).catch(() => undefined)])
    } catch (error) {
      setToast(error instanceof Error ? `Demo stopped: ${error.message}` : 'Demo scenario stopped')
    } finally {
      setBusy(false)
    }
  }

  const connectionBanner = (!backendConnected || socket === 'disconnected') ? (
    <div className="backend-offline-banner" role="alert">
      <div className="banner-content">
        <AlertTriangle size={18} className="banner-icon" />
        <div className="banner-text">
          <span className="banner-title">Backend Unreachable:</span> Cannot reach <code>{API_BASE}</code>.
          <span className="banner-command-hint">
            Start backend: <code>python -m uvicorn app.main:app --app-dir backend/backend --port 8000</code>
          </span>
        </div>
        <button
          type="button"
          className="banner-retry-btn"
          disabled={isRetrying}
          onClick={async () => {
            setIsRetrying(true)
            await checkHealthAndConnect()
            setIsRetrying(false)
          }}
        >
          <RefreshCw size={13} className={isRetrying ? 'animate-spin' : ''} />
          <span>{isRetrying ? 'Checking...' : 'Retry Connection'}</span>
        </button>
      </div>
    </div>
  ) : null

  if (loading) {
    return <LoadingScreen theme={theme} durationMs={5000} onComplete={handleLoadingComplete} />
  }

  if (showMapEditor) {
    return (
      <div className="app-shell editor-active" data-theme={theme} style={{ height: '100vh', width: '100vw', overflow: 'hidden' }}>
        <WarehouseMapEditor
          initialMap={activeMap}
          onLaunch={handleLaunchFromEditor}
          onCancel={() => {
            setShowMapEditor(false)
          }}
          theme={theme}
        />
      </div>
    )
  }

  if (!launchChoiceMade) {
    return (
      <div className="app-shell" data-theme={theme}>
        {connectionBanner}
        <LaunchScreen
          onUseBuiltIn={handleUseBuiltIn}
          onOpenEditor={() => setShowMapEditor(true)}
          onSelectPreset={handleSelectPreset}
          theme={theme}
        />
      </div>
    )
  }

  return (
    <div className="app-shell" data-theme={theme}>
      {connectionBanner}
      <ControlBar
        running={status.running}
        armedState={armedState}
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
        onOpenMapEditor={() => setShowMapEditor(true)}
        activeMapName={activeMapName}
        gridDimensions={gridDimensions}
        robotCounts={robotCounts}
        speed={simSpeed}
        onSetSpeed={handleSetSpeed}
        onChangeMap={handleChangeMap}
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
                  api.getOrders().then(setOrders).catch(() => []),
                  api.inventory().then((inv) => setInventory(inv.shelves)).catch(() => null),
                  api.mapCurrent().then((m) => { if (m) setActiveMap(m) }).catch(() => null),
                ])
              } else if (action === 'start') {
                void api.tasks().then((tList) => {
                  const hasActive = (tList || []).some(
                    (t) => t.status !== 'COMPLETED' && t.status !== 'FAILED'
                  )
                  if (!hasActive) {
                    api.createOrder({ sku: 'SKU-A10', quantity: 1, destination_gate: 'OUT-1', urgency: 5 })
                      .then(() => {
                        void Promise.all([api.robots().then(setRobots), api.tasks().then(setTasks), api.getOrders().then(setOrders).catch(() => undefined)])
                      })
                      .catch(() => undefined)
                  }
                }).catch(() => undefined)
                void api.status().then(setStatus)
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

      {unfinishedRecoveryCount > 0 && showRecoveryBanner && (
        <div className="recovery-jobs-banner" role="alert">
          <AlertCircle size={16} />
          <span>{unfinishedRecoveryCount} unfinished jobs from a previous session</span>
          <div className="recovery-actions">
            <button className="btn-primary" onClick={handleResumeRecovery}>Resume</button>
            <button className="btn-secondary" onClick={handleDiscardRecovery}>Discard</button>
            <button className="btn-icon" onClick={() => setShowRecoveryBanner(false)} title="Dismiss">✕</button>
          </div>
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
            storeRef={storeRef}
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
              className={`side-tab-btn ${activeTab === 'orders' ? 'active' : ''}`}
              onClick={() => setActiveTab('orders')}
              title="Order Fulfillment Pipeline"
            >
              <ShoppingCart size={13} />
              <span>Orders ({orders.length})</span>
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
                orders={orders}
                busy={busy}
                operatorRole={operatorRole ?? 'AUTHORITY'}
                simulationRunning={status.running}
                world={world}
                onJob={(body) =>
                  run(() => api.submitJob(body), 'Mission queued').then((res) => {
                    void api.tasks().then(setTasks)
                    return res
                  })
                }
              />
            </>
          )}

          {activeTab === 'orders' && (
            <OrdersPanel orders={orders} currentTick={status.tick} />
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