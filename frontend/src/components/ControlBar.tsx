import {
  Pause,
  Play,
  RotateCcw,
  Radio,
  AlertTriangle,
  Sparkles,
  Layers,
  Maximize,
  Minimize,
  Edit3,
  FolderOpen,
  Gauge,
} from 'lucide-react'
import type { SocketStatus } from '../hooks/useFleetSocket'

type Props = {
  running: boolean
  armedState?: string
  tick: number
  lastSyncedTick: number
  fleetMode?: string
  timestamp: number
  socket: SocketStatus
  skipped: number
  chaos: boolean
  loss: number
  busy: boolean
  showMeshLinks?: boolean
  onToggleMeshLinks?: () => void
  theme?: 'light' | 'dark'
  onToggleTheme?: () => void
  isFullscreen?: boolean
  onToggleFullscreen?: () => void
  operatorRole?: 'IMPORT' | 'EXPORT' | 'AUTHORITY'
  onOpenRoleModal?: () => void
  onOpenMapEditor?: () => void
  onAction: (action: 'start' | 'pause' | 'reset') => void
  onChaos: (enabled: boolean, loss: number) => void
  onDemo: () => void
  activeMapName?: string
  gridDimensions?: { width: number; height: number }
  robotCounts?: { g2p: number; sorting: number; audit: number; total: number }
  tickRateHz?: number
  speed?: number
  onSetSpeed?: (speed: number) => void
  onChangeMap?: () => void
}

export function ControlBar({
  running,
  armedState = 'ARMED — waiting for tasks',
  tick,
  lastSyncedTick,
  fleetMode = 'Autonomous (10 AMRs)',
  timestamp,
  socket,
  skipped,
  chaos,
  loss,
  busy,
  showMeshLinks,
  onToggleMeshLinks,
  theme,
  onToggleTheme,
  isFullscreen = false,
  onToggleFullscreen,
  operatorRole = 'AUTHORITY',
  onOpenRoleModal,
  onOpenMapEditor,
  onAction,
  onChaos,
  onDemo,
  activeMapName = 'Standard 30x30 Test Warehouse',
  gridDimensions = { width: 30, height: 30 },
  robotCounts = { g2p: 4, sorting: 3, audit: 3, total: 10 },
  tickRateHz,
  speed = 1.0,
  onSetSpeed,
  onChangeMap,
}: Props) {
  const connectionLabel =
    socket === 'connected' ? 'P2P Mesh Active' : socket === 'reconnecting' ? 'Reconnecting' : 'Offline'

  const effectiveHz = tickRateHz !== undefined ? tickRateHz : (6.7 * speed)

  return (
    <header className="topbar">
      {/* ── Left: Brand & Active Map Info ── */}
      <div className="topbar-section left-section">
        <div className="brand">
          <div className="brand-icon">
            <Layers size={18} strokeWidth={2.2} />
          </div>
          <span className="brand-name">Kinetix</span>
        </div>

        {onChangeMap && (
          <button
            className="control-button change-map-btn"
            onClick={onChangeMap}
            title="Cleanly switch to another warehouse map preset or editor without full page reload"
            aria-label="Change map"
          >
            <FolderOpen size={13} />
            <span>Change map</span>
          </button>
        )}

        {onOpenMapEditor && (
          <button
            className="control-button map-editor-btn"
            onClick={onOpenMapEditor}
            title="Open interactive warehouse map editor"
            aria-label="Open Map Editor"
          >
            <Edit3 size={13} />
            <span>Map Editor</span>
          </button>
        )}
      </div>

      {/* ── Center: Simulation Engine Controls (Start/Pause, Speed, Reset) ── */}
      <div className="topbar-section center-section">
        <div className="button-group sim-actions">
          {running ? (
            <button
              className="control-button sim-btn pause-state active"
              disabled={busy}
              onClick={() => onAction('pause')}
              aria-label="Pause simulation"
              title="Pause autonomous simulation and robot process ticks"
            >
              <Pause size={14} fill="currentColor" />
              <span>PAUSE</span>
            </button>
          ) : (
            <button
              className="control-button sim-btn start-state active"
              disabled={busy}
              onClick={() => onAction('start')}
              aria-label="Start simulation"
              title="Start / Resume autonomous simulation"
            >
              <Play size={14} fill="currentColor" />
              <span>START</span>
            </button>
          )}

          {/* Speed Selector (0.5x, 1x, 2x, 4x) */}
          <div className="speed-selector-group" role="group" aria-label="Simulation speed selector">
            <Gauge size={12} className="speed-icon" />
            {[0.5, 1, 2, 4].map((s) => (
              <button
                key={s}
                className={`speed-pill ${Math.abs(speed - s) < 0.05 ? 'selected' : ''}`}
                onClick={() => onSetSpeed && onSetSpeed(s)}
                disabled={busy}
                title={`Scale simulation tick speed to ${s}x`}
              >
                {s}x
              </button>
            ))}
          </div>

          {/* Reset Simulation Button */}
          <button
            className="control-button reset-btn"
            disabled={busy}
            aria-label="Reset simulation"
            onClick={() => onAction('reset')}
            title="Reset simulation state, robots, tasks, orders, and reseed inventory"
          >
            <RotateCcw size={13} />
            <span>Reset</span>
          </button>

          <button
            className="control-button demo-button"
            disabled={busy}
            onClick={onDemo}
            aria-label="Run demo scenario"
            title="Run simulated multi-robot cross-traffic demo"
          >
            <Sparkles size={13} />
            <span>Demo</span>
          </button>
        </div>
      </div>


      {/* ── Right: Robot Counts, Readouts & View Controls ── */}
      <div className="topbar-section right-section">
        <div className="readout">
          <span className="label">Tick</span>
          <strong>{String(tick).padStart(5, '0')}</strong>
        </div>

        {onToggleFullscreen && (
          <button
            className={`control-button fullscreen-toggle ${isFullscreen ? 'active' : ''}`}
            onClick={onToggleFullscreen}
            title={isFullscreen ? 'Exit Full Screen' : 'View Simulation in Full Screen'}
            aria-label={isFullscreen ? 'Exit Full Screen' : 'View Simulation in Full Screen'}
          >
            {isFullscreen ? <Minimize size={13} /> : <Maximize size={13} />}
          </button>
        )}

        <div className="chaos-control">
          <Radio size={13} />
          <span>Chaos</span>
          <button
            className={`toggle ${chaos ? 'on' : ''}`}
            onClick={() => onChaos(!chaos, loss)}
            aria-label="Toggle chaos mode"
          >
            <span />
          </button>
          {chaos && (
            <input
              aria-label="Packet loss percentage"
              type="range"
              min="0"
              max="50"
              value={loss}
              onChange={(e) => onChaos(true, Number(e.target.value))}
            />
          )}
          <b>{loss}%</b>
        </div>

        {operatorRole && (
          <button
            type="button"
            className="station-role-indicator"
            onClick={onOpenRoleModal}
            title="Click to switch Station Console"
          >
            <span
              className="station-role-tag"
              style={{
                background:
                  operatorRole === 'IMPORT'
                    ? 'rgba(16,185,129,0.2)'
                    : operatorRole === 'EXPORT'
                    ? 'rgba(56,189,248,0.2)'
                    : 'rgba(168,85,247,0.2)',
                color:
                  operatorRole === 'IMPORT'
                    ? '#34d399'
                    : operatorRole === 'EXPORT'
                    ? '#38bdf8'
                    : '#c084fc',
              }}
            >
              {operatorRole === 'IMPORT'
                ? 'IMPORT 9601'
                : operatorRole === 'EXPORT'
                ? 'EXPORT 9602'
                : 'AUTHORITY 9603'}
            </span>
          </button>
        )}
      </div>
    </header>
  )
}