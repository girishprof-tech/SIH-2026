import {
  Pause,
  Play,
  RotateCcw,
  Wifi,
  WifiOff,
  Radio,
  AlertTriangle,
  Sparkles,
  Sun,
  Moon,
  Layers,
} from 'lucide-react'
import type { SocketStatus } from '../hooks/useFleetSocket'

type Props = {
  running: boolean
  tick: number
  timestamp: number
  socket: SocketStatus
  skipped: number
  chaos: boolean
  loss: number
  busy: boolean
  theme: 'light' | 'dark'
  onToggleTheme: () => void
  onAction: (action: 'start' | 'pause' | 'reset') => void
  onChaos: (enabled: boolean, loss: number) => void
  onDemo: () => void
}

export function ControlBar({
  running,
  tick,
  timestamp,
  socket,
  skipped,
  chaos,
  loss,
  busy,
  theme,
  onToggleTheme,
  onAction,
  onChaos,
  onDemo,
}: Props) {
  const connectionLabel =
    socket === 'connected' ? 'Connected' : socket === 'reconnecting' ? 'Reconnecting' : 'Offline'

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

        <div className={`link-status ${socket}`}>
          <span className="status-dot" />
          {socket === 'connected' ? <Wifi size={14} /> : <WifiOff size={14} />}
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