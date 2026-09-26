import React, { useMemo } from 'react'
import { Bot, Battery, Compass, Gauge, Target, Package, ShieldAlert, Crosshair, X, Zap, RotateCcw } from 'lucide-react'
import type { Robot } from '../types'
import { ROBOT_TYPE_COLORS, STATE_COLORS, STATE_LABELS } from '../state-meta'

type Props = {
  robot: Robot | null
  tick: number
  cameraFollow: boolean
  onToggleCameraFollow: () => void
  onClose: () => void
  onEStop?: (robotId: string) => void
  onReset?: (robotId: string) => void
}

export function RobotInspectorPanel({
  robot,
  cameraFollow,
  onToggleCameraFollow,
  onClose,
  onEStop,
  onReset,
}: Props) {
  if (!robot) return null

  // Calculate distance to goal if path exists
  const goalPoint = robot.path && robot.path.length > 0 ? robot.path[robot.path.length - 1] : null
  const distanceToGoal = useMemo(() => {
    if (!goalPoint) return 0
    return Math.abs(goalPoint.x - robot.position.x) + Math.abs(goalPoint.y - robot.position.y)
  }, [goalPoint, robot.position])

  const estTicksToGoal = robot.path ? robot.path.length : 0

  const typeColor = ROBOT_TYPE_COLORS[robot.robot_type] || '#3b82f6'
  const stateColor = STATE_COLORS[robot.state] || '#94a3b8'

  return (
    <aside className="robot-detail panel" style={{ width: '320px', zIndex: 40 }}>
      <button className="close-detail" onClick={onClose} aria-label="Close inspector">
        <X size={16} />
      </button>

      {/* Header with Type & ID */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
        <div
          style={{
            width: '12px',
            height: '12px',
            borderRadius: '50%',
            backgroundColor: typeColor,
            boxShadow: `0 0 8px ${typeColor}`,
          }}
        />
        <h2 style={{ margin: 0, fontSize: '1.1rem', display: 'flex', alignItems: 'center', gap: '6px' }}>
          <Bot size={18} color={typeColor} /> {robot.robot_id}
        </h2>
      </div>

      <div style={{ display: 'flex', gap: '6px', alignItems: 'center', marginBottom: '12px' }}>
        <span
          className="state-badge"
          style={{
            backgroundColor: `${stateColor}22`,
            color: stateColor,
            border: `1px solid ${stateColor}44`,
            fontSize: '0.75rem',
            padding: '2px 8px',
            borderRadius: '4px',
            fontWeight: 600,
          }}
        >
          {STATE_LABELS[robot.state] ?? robot.state.replace(/_/g, ' ')}
        </span>
        <span
          style={{
            fontSize: '0.7rem',
            background: 'rgba(255,255,255,0.06)',
            padding: '2px 6px',
            borderRadius: '3px',
            color: '#94a3b8',
          }}
        >
          {robot.robot_type}
        </span>
      </div>

      {/* Camera Follow Mode Button */}
      <button
        onClick={onToggleCameraFollow}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '8px',
          padding: '8px 12px',
          marginBottom: '12px',
          borderRadius: '6px',
          border: cameraFollow ? '1px solid #38bdf8' : '1px solid rgba(255,255,255,0.15)',
          background: cameraFollow ? 'rgba(56, 189, 248, 0.15)' : 'rgba(255,255,255,0.05)',
          color: cameraFollow ? '#38bdf8' : '#e2e8f0',
          cursor: 'pointer',
          fontWeight: 600,
          fontSize: '0.85rem',
          transition: 'all 0.2s ease',
        }}
      >
        <Crosshair size={15} />
        {cameraFollow ? 'Camera Following AMR (Active)' : 'Follow AMR in 3D View'}
      </button>

      {/* Battery Gauge */}
      <div className="detail-battery" style={{ marginBottom: '12px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', marginBottom: '4px' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: '4px', color: '#94a3b8' }}>
            <Battery size={14} /> Battery
          </span>
          <strong style={{ color: robot.battery_pct < 25 ? '#ef4444' : '#22c55e' }}>
            {robot.battery_pct.toFixed(1)}%
          </strong>
        </div>
        <div style={{ height: '6px', background: 'rgba(255,255,255,0.1)', borderRadius: '3px', overflow: 'hidden' }}>
          <div
            style={{
              width: `${robot.battery_pct}%`,
              height: '100%',
              backgroundColor: robot.battery_pct < 25 ? '#ef4444' : '#22c55e',
              transition: 'width 0.3s ease',
            }}
          />
        </div>
      </div>

      {/* Telemetry Metrics Grid */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: '8px',
          marginBottom: '14px',
          fontSize: '0.8rem',
        }}
      >
        <div style={{ background: 'rgba(255,255,255,0.04)', padding: '6px 8px', borderRadius: '4px' }}>
          <div style={{ color: '#94a3b8', display: 'flex', alignItems: 'center', gap: '4px' }}>
            <Compass size={13} /> Coordinates
          </div>
          <strong style={{ color: '#f1f5f9' }}>
            ({robot.position.x}, {robot.position.y})
          </strong>
        </div>

        <div style={{ background: 'rgba(255,255,255,0.04)', padding: '6px 8px', borderRadius: '4px' }}>
          <div style={{ color: '#94a3b8', display: 'flex', alignItems: 'center', gap: '4px' }}>
            <Gauge size={13} /> Heading
          </div>
          <strong style={{ color: '#f1f5f9' }}>{robot.heading}</strong>
        </div>

        <div style={{ background: 'rgba(255,255,255,0.04)', padding: '6px 8px', borderRadius: '4px' }}>
          <div style={{ color: '#94a3b8', display: 'flex', alignItems: 'center', gap: '4px' }}>
            <Target size={13} /> Distance to Goal
          </div>
          <strong style={{ color: '#f1f5f9' }}>
            {distanceToGoal} cells ({estTicksToGoal} ticks)
          </strong>
        </div>

        <div style={{ background: 'rgba(255,255,255,0.04)', padding: '6px 8px', borderRadius: '4px' }}>
          <div style={{ color: '#94a3b8', display: 'flex', alignItems: 'center', gap: '4px' }}>
            <Zap size={13} /> Priority Score
          </div>
          <strong style={{ color: '#f1f5f9' }}>{robot.priority_score.toFixed(1)}</strong>
        </div>
      </div>

      {/* Payload Indicator */}
      <div
        style={{
          background: 'rgba(255,255,255,0.04)',
          padding: '8px 10px',
          borderRadius: '6px',
          marginBottom: '12px',
          fontSize: '0.8rem',
        }}
      >
        <div style={{ color: '#94a3b8', display: 'flex', alignItems: 'center', gap: '5px', marginBottom: '4px' }}>
          <Package size={14} /> Active Payload
        </div>
        {robot.carrying_pod_id ? (
          <div style={{ color: '#38bdf8', fontWeight: 600 }}>
            Lifting Pod: {robot.carrying_pod_id}
          </div>
        ) : robot.robot_type === 'SORTING' && robot.state === 'EN_ROUTE_DROPOFF' ? (
          <div style={{ color: '#f59e0b', fontWeight: 600 }}>
            Carrying Sortation Carton
          </div>
        ) : (
          <div style={{ color: '#64748b' }}>None (Unladen)</div>
        )}
      </div>

      {/* Mission Task */}
      <div style={{ fontSize: '0.8rem', marginBottom: '14px' }}>
        <span style={{ color: '#94a3b8' }}>Mission: </span>
        <strong style={{ color: robot.current_task_id ? '#38bdf8' : '#64748b' }}>
          {robot.current_task_id ?? 'None (Patrolling / Ready)'}
        </strong>
      </div>

      {/* Individual Robot Emergency Controls */}
      <div style={{ display: 'flex', gap: '8px', marginTop: 'auto' }}>
        <button
          onClick={() => onEStop && onEStop(robot.robot_id)}
          style={{
            flex: 1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '6px',
            padding: '6px 10px',
            borderRadius: '4px',
            background: 'rgba(239, 68, 68, 0.15)',
            border: '1px solid rgba(239, 68, 68, 0.4)',
            color: '#ef4444',
            fontSize: '0.75rem',
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          <ShieldAlert size={14} /> E-Stop AMR
        </button>

        <button
          onClick={() => onReset && onReset(robot.robot_id)}
          style={{
            flex: 1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '6px',
            padding: '6px 10px',
            borderRadius: '4px',
            background: 'rgba(255, 255, 255, 0.05)',
            border: '1px solid rgba(255, 255, 255, 0.15)',
            color: '#e2e8f0',
            fontSize: '0.75rem',
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          <RotateCcw size={14} /> Reset AMR
        </button>
      </div>
    </aside>
  )
}
