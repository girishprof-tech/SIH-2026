import React from 'react'
import { Activity, Radio, Wifi, ArrowRight, CheckCircle, ShieldAlert } from 'lucide-react'
import type { InventoryUpdateEvent } from '../types'

interface Props {
  logs: InventoryUpdateEvent[]
  onClearLogs?: () => void
}

export function TransferLogPanel({ logs, onClearLogs }: Props) {
  return (
    <div className="panel transfer-log-panel">
      <div className="panel-header">
        <div className="panel-title-group">
          <Activity className="panel-icon" size={16} />
          <span className="panel-title">Sync & Transfer Log</span>
          <span className="badge badge-subtle">{logs.length} events</span>
        </div>
        {logs.length > 0 && onClearLogs && (
          <button className="text-btn-subtle" onClick={onClearLogs}>
            Clear
          </button>
        )}
      </div>

      <div className="transfer-log-list custom-scrollbar">
        {logs.length === 0 ? (
          <div className="empty-state-text">
            Waiting for peer inventory broadcasts (audit scans, pod picks, chute decants)...
          </div>
        ) : (
          logs.map((event) => {
            const isHalow = event.channel === 'HALOW'
            const sourceLabel =
              event.source === 'audit_scan'
                ? 'AUDIT SCAN'
                : event.source === 'pod_pick'
                ? 'G2P PICK'
                : event.source === 'batch_decant'
                ? 'DECANT'
                : 'P2P GOSSIP'

            return (
              <div key={event.id} className="transfer-log-item">
                <div className="transfer-log-header">
                  <div className="transfer-channel-badge">
                    {isHalow ? (
                      <span className="channel-tag tag-halow">
                        <Wifi size={11} />
                        WiFi HaLow
                      </span>
                    ) : (
                      <span className="channel-tag tag-mesh">
                        <Radio size={11} />
                        Peer Mesh
                      </span>
                    )}
                    <span className="event-source-tag">{sourceLabel}</span>
                  </div>
                  <span className="event-tick">Tick {event.tick}</span>
                </div>

                <div className="transfer-log-body">
                  <div className="transfer-detail-row">
                    <span className="transfer-shelf">{event.shelf_id}</span>
                    <ArrowRight size={12} className="transfer-arrow" />
                    <span className="transfer-robot">{event.source_robot_id}</span>
                    <span className="transfer-count">({event.box_count} boxes)</span>
                  </div>
                  {event.confidence !== undefined && (
                    <div className="transfer-conf-tag">
                      Conf: {Math.round(event.confidence * 100)}%
                    </div>
                  )}
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
