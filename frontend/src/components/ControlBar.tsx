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
}: Props) {
  const connectionLabel =
    socket === 'connected' ? 'P2P Mesh Active' : socket === 'reconnecting' ? 'Reconnecting' : 'Offline'

  const formattedMode =
    fleetMode === 'spawned_new_fleet'
      ? 'Autonomous (10 AMRs)'
      : fleetMode === 'attached_to_existing_fleet'
      ? 'Attached Fleet'
      : fleetMode.replace(/_/g, ' ')

  return (
    <header className="topbar">
      <div className="brand">
        <div className="brand-icon">
          <Layers size={18} strokeWidth={2.2} />
        </div>
        <span className="brand-name">Kinetix</span>
      </div>

      <div className="top-controls">
        <div className="button-group">
          <button
            className="control-button primary"
            disabled={busy || running}
            onClick={() => onAction('start')}
            aria-label="Start simulation"
          >
            <Play size={14} fill="currentColor" /> Start
          </button>
          <button
            className="control-button"
            disabled={busy || !running}
            onClick={() => onAction('pause')}
            aria-label="Pause simulation"
          >
            <Pause size={14} fill="currentColor" /> Pause
          </button>
          <button
            className="icon-button"
            disabled={busy}
            aria-label="Reset simulation"
            onClick={() => onAction('reset')}
          >
            <RotateCcw size={15} />
          </button>
          <button
            className="control-button demo-button"
            disabled={busy}
            onClick={onDemo}
            aria-label="Run demo scenario"
          >
            <Sparkles size={14} /> Demo scenario
          </button>
        </div>

        {/* Dashboard state: ARMED — waiting for tasks vs RUNNING */}
        {running ? (
          <div
            className={`fleet-armed-badge ${armedState === 'RUNNING' ? 'running' : 'armed'}`}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '5px 12px',
              borderRadius: '9999px',
              fontSize: '11px',
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
              padding: '5px 12px',
              borderRadius: '9999px',
              fontSize: '11px',
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

        <button
          className={`control-button mesh-toggle ${showMeshLinks ? 'active' : ''}`}
          onClick={onToggleMeshLinks}
          title="Toggle real-time autonomous peer-to-peer RF mesh topology and data packets"
          aria-label="Toggle P2P Mesh Links"
        >
          <Share2 size={13} />
          <span>P2P Mesh</span>
        </button>

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

        {onToggleFullscreen && (
          <button
            className={`control-button fullscreen-toggle ${isFullscreen ? 'active' : ''}`}
            onClick={onToggleFullscreen}
            title={isFullscreen ? 'Exit Full Screen' : 'View Simulation in Full Screen'}
            aria-label={isFullscreen ? 'Exit Full Screen' : 'View Simulation in Full Screen'}
          >
            {isFullscreen ? <Minimize size={13} /> : <Maximize size={13} />}
            <span>{isFullscreen ? 'Exit Full' : 'Full Screen'}</span>
          </button>
        )}

        <div className="readout">
          <span className="label">Tick</span>
          <strong>{String(tick).padStart(5, '0')}</strong>
        </div>

        <div className="readout clock">
          <span className="label">Sim clock</span>
          <strong>
            {timestamp
              ? new Date(timestamp).toLocaleTimeString([], { hour12: false })
              : '--:--:--'}
          </strong>
        </div>

        <div className="readout sync-indicator" title="Authoritative tick received via WebSocket">
          <span className="label">Sync</span>
          <strong>#{String(lastSyncedTick).padStart(5, '0')}</strong>
        </div>

        <div className={`link-status ${socket}`} title="Decentralized UDP Peer Telemetry Stream">
          <span className="status-dot" />
          <Network size={14} />
          <span>{connectionLabel}</span>
        </div>

        {skipped > 0 && (
          <div className="loss-alert">
            <AlertTriangle size={14} />
            <span>{skipped} ticks lost</span>
          </div>
        )}

        <div className="chaos-control">
          <Radio size={14} />
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
            <span style={{ fontSize: '11px', color: '#94a3b8' }}>Switch</span>
          </button>
        )}

        <button
          className="icon-button"
          onClick={onToggleTheme}
          aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
          title={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
        >
          {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
        </button>
      </div>
    </header>
  )
}