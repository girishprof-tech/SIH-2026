import { Pause, Play, RotateCcw, Wifi, WifiOff, Radio, AlertTriangle } from 'lucide-react'
import type { SocketStatus } from '../hooks/useFleetSocket'

type Props = { running: boolean; tick: number; timestamp: number; socket: SocketStatus; skipped: number; chaos: boolean; loss: number; busy: boolean; onAction: (action: 'start' | 'pause' | 'reset') => void; onChaos: (enabled: boolean, loss: number) => void }
export function ControlBar({ running, tick, timestamp, socket, skipped, chaos, loss, busy, onAction, onChaos }: Props) {
  const connectionLabel = socket === 'connected' ? 'LIVE LINK' : socket === 'reconnecting' ? 'RECONNECTING' : 'OFFLINE'
  return <header className="topbar">
    <div className="brand"><span className="brand-mark">//</span><div><strong>FLEET<span>CONTROL</span></strong><small>EDGE-AI COORDINATION NETWORK</small></div></div>
    <div className="top-controls">
      <div className="button-group"><button className="control-button primary" disabled={busy || running} onClick={() => onAction('start')}><Play size={14} fill="currentColor" /> START</button><button className="control-button" disabled={busy || !running} onClick={() => onAction('pause')}><Pause size={14} fill="currentColor" /> PAUSE</button><button className="icon-button" disabled={busy} aria-label="Reset simulation" onClick={() => onAction('reset')}><RotateCcw size={15} /></button></div>
      <div className="readout"><span className="label">TICK</span><strong>{String(tick).padStart(5, '0')}</strong></div><div className="readout clock"><span className="label">SIM CLOCK</span><strong>{timestamp ? new Date(timestamp).toLocaleTimeString([], { hour12: false }) : '--:--:--'}</strong></div>
      <div className={`link-status ${socket}`}><span className="status-dot" />{socket === 'connected' ? <Wifi size={14} /> : <WifiOff size={14} />} {connectionLabel}</div>
      {skipped > 0 && <div className="loss-alert"><AlertTriangle size={14} /> {skipped} TICKS LOST</div>}
      <div className="chaos-control"><Radio size={14} /><span>CHAOS</span><button className={`toggle ${chaos ? 'on' : ''}`} onClick={() => onChaos(!chaos, loss)} aria-label="Toggle chaos mode"><span /></button>{chaos && <input aria-label="Packet loss percentage" type="range" min="0" max="50" value={loss} onChange={e => onChaos(true, Number(e.target.value))} />}<b>{loss}%</b></div>
    </div>
  </header>
}