import React from 'react'
import { Wifi, Radio, Zap, AlertCircle } from 'lucide-react'
import type { HaLowStatus } from '../types'

interface Props {
  status?: HaLowStatus
  currentTick: number
}

export function HaLowStatusWidget({ status, currentTick }: Props) {
  const isConnected = status?.connected ?? true
  const lastAgeSec = status?.last_msg_timestamp_ms
    ? Math.max(0, (Date.now() - status.last_msg_timestamp_ms) / 1000)
    : 0
  const isStale = lastAgeSec > 5.0 && currentTick > 10
  const utilPct = Math.round((status?.throttle_utilization ?? 0.15) * 100)
  const bitrate = status?.bitrate_kbps ?? 150

  return (
    <div className={`halow-status-card ${isStale ? 'halow-stale' : 'halow-active'}`}>
      <div className="halow-header">
        <div className="halow-title">
          <Wifi size={14} className="halow-icon" />
          <span>WiFi HaLow (802.11ah)</span>
        </div>
        <span className={`halow-pill ${isStale ? 'pill-stale' : 'pill-active'}`}>
          {isStale ? 'THROTTLED' : 'SYNCED'}
        </span>
      </div>

      <div className="halow-metrics-grid">
        <div className="halow-metric-item">
          <span className="halow-metric-label">Bandwidth</span>
          <span className="halow-metric-val">{bitrate} kbps</span>
        </div>
        <div className="halow-metric-item">
          <span className="halow-metric-label">Token Bucket</span>
          <span className="halow-metric-val">{utilPct}% util</span>
        </div>
        <div className="halow-metric-item">
          <span className="halow-metric-label">Last Uplink</span>
          <span className="halow-metric-val">
            {lastAgeSec < 1.0 ? '< 1s' : `${lastAgeSec.toFixed(1)}s`}
          </span>
        </div>
      </div>

      {isStale && (
        <div className="halow-stale-warning">
          <AlertCircle size={13} />
          <span>High channel load: snapshots coalescing</span>
        </div>
      )}
    </div>
  )
}
