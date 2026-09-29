import {
  Pause,
  Play,
  RotateCcw,
  Radio,
  Share2,
  AlertTriangle,
  Sparkles,
  Sun,
  Moon,
  Layers,
  Network,
  Maximize,
  Minimize,
  Edit3,
  FolderOpen,
  Gauge,
  Bot,
  MapPin,
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
  showMeshLinks: boolean
  onToggleMeshLinks: () => void
  theme: 'light' | 'dark'
  onToggleTheme: () => void
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

        {/* Active Map & Grid Chip */}
        <div className="map-info-chip" title={`Active Map: ${activeMapName} (${gridDimensions.width}x${gridDimensions.height})`}>
          <MapPin size={12} className="map-chip-icon" />
          <span className="map-chip-name">{activeMapName}</span>
          <span className="map-chip-dims">{gridDimensions.width}×{gridDimensions.height}</span>
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

        {/* Dashboard state: ARMED vs RUNNING vs PAUSED */}
        {running ? (
          <div
            className={`fleet-armed-badge ${armedState === 'RUNNING' ? 'running' : 'armed'}`}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '4px 10px',
              borderRadius: '9999px',
              fontSize: '10.5px',
              fontWeight: 700,
              letterSpacing: '0.04em',
              textTransform: 'uppercase',
              background: armedState === 'RUNNING' ? 'rgba(34, 197, 94, 0.15)' : 'rgba(56, 189, 248, 0.15)',
              color: armedState === 'RUNNING' ? '#22c55e' : '#38bdf8',
              border: `1px solid ${armedState === 'RUNNING' ? 'rgba(34, 197, 94, 0.35)' : 'rgba(56, 189, 248, 0.35)'}`,
            }}
          >
            <span
              style={{
                width: '7px',
                height: '7px',
                borderRadius: '50%',
                background: armedState === 'RUNNING' ? '#22c55e' : '#38bdf8',
                boxShadow: armedState === 'RUNNING' ? '0 0 8px #22c55e' : '0 0 8px #38bdf8',
              }}
            />
            {armedState}
          </div>
        ) : (
          <div
            className="fleet-armed-badge paused"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '4px 10px',
              borderRadius: '9999px',
              fontSize: '10.5px',
              fontWeight: 600,
              background: 'rgba(148, 163, 184, 0.12)',
              color: '#94a3b8',
              border: '1px solid rgba(148, 163, 184, 0.25)',
            }}
          >
            <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: '#94a3b8' }} />
            PAUSED
          </div>
        )}
      </div>

      {/* ── Right: Robot Counts, Readouts & View Controls ── */}
      <div className="topbar-section right-section">
        {/* Robot Counts breakdown */}
        <div className="robot-breakdown-chip" title="Robot Fleet breakdown">
          <Bot size={13} className="fleet-icon" />
          <span className="count-total"><b>{robotCounts.total}</b> AMRs</span>
          <span className="count-divider">•</span>
          <span className="count-tag g2p" title="Goods-to-Person AMRs">{robotCounts.g2p} G2P</span>
          <span className="count-tag sort" title="Sorting AMRs">{robotCounts.sorting} Sort</span>
          <span className="count-tag audit" title="Scanning & Audit AMRs">{robotCounts.audit} Audit</span>
        </div>

        <div className="readout tick-rate-readout" title="Simulation Tick Rate">
          <span className="label">Rate</span>
          <strong>{effectiveHz.toFixed(1)} Hz</strong>
        </div>

        <div className="readout">
          <span className="label">Tick</span>
          <strong>{String(tick).padStart(5, '0')}</strong>
        </div>

        <div className={`link-status ${socket}`} title="Decentralized UDP Peer Telemetry Stream">
          <span className="status-dot" />
          <Network size={13} />
          <span>{connectionLabel}</span>
        </div>

        {skipped > 0 && (
          <div className="loss-alert">
            <AlertTriangle size={13} />
            <span>{skipped} lost</span>
          </div>
        )}

        <button
          className={`control-button mesh-toggle ${showMeshLinks ? 'active' : ''}`}
          onClick={onToggleMeshLinks}
          title="Toggle real-time autonomous peer-to-peer RF mesh topology and data packets"
          aria-label="Toggle P2P Mesh Links"
        >
          <Share2 size={13} />
          <span>Mesh</span>
        </button>

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

        <button
          className="icon-button theme-toggle-btn"
          onClick={onToggleTheme}
          aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
          title={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
        >
          {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
        </button>
      </div>
    </header>
  )
}