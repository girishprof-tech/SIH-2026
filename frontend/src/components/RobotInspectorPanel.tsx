import React, { useMemo, useState, useEffect, useRef } from 'react'
import {
  Bot,
  Battery,
  Compass,
  Gauge,
  Target,
  Package,
  ShieldAlert,
  Crosshair,
  X,
  Zap,
  RotateCcw,
  Activity,
  CheckCircle2,
  Clock,
  Layers,
  Scan,
  Cpu,
  AlertTriangle,
  ArrowRight,
  ShieldCheck,
} from 'lucide-react'
import type { Robot, RobotState } from '../types'
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

interface TransitionRecord {
  from: string
  to: string
  tick: number
}

export function RobotInspectorPanel({
  robot,
  tick,
  cameraFollow,
  onToggleCameraFollow,
  onClose,
  onEStop,
  onReset,
}: Props) {
  if (!robot) return null

  // Rolling state transition history
  const [transitionHistory, setTransitionHistory] = useState<TransitionRecord[]>([])
  const prevStateRef = useRef<RobotState>(robot.state)
  const prevRobotIdRef = useRef<string>(robot.robot_id)

  useEffect(() => {
    // If inspecting a different robot, reset history
    if (prevRobotIdRef.current !== robot.robot_id) {
      prevRobotIdRef.current = robot.robot_id
      prevStateRef.current = robot.state
      setTransitionHistory([])
      return
    }

    if (prevStateRef.current !== robot.state) {
      const newRecord: TransitionRecord = {
        from: prevStateRef.current,
        to: robot.state,
        tick,
      }
      setTransitionHistory((prev) => [newRecord, ...prev].slice(0, 4))
      prevStateRef.current = robot.state
    }
  }, [robot.state, robot.robot_id, tick])

  // Distance to Goal & ETA
  const goalPoint = robot.path && robot.path.length > 0 ? robot.path[robot.path.length - 1] : null
  const distanceToGoal = useMemo(() => {
    if (!goalPoint) return 0
    return Math.abs(goalPoint.x - robot.position.x) + Math.abs(goalPoint.y - robot.position.y)
  }, [goalPoint, robot.position])

  const estTicksToGoal = robot.path ? robot.path.length : 0

  // Task Progress Calculation (0 - 100%)
  const taskProgress = useMemo(() => {
    switch (robot.state) {
      case 'IDLE':
        return robot.current_task_id ? 100 : 0
      case 'ASSIGNED':
        return 10
      case 'EN_ROUTE_PICKUP': {
        const remaining = robot.path ? robot.path.length : 0
        const progress = Math.max(15, Math.min(45, 45 - remaining * 2))
        return progress
      }
      case 'PICKING':
        return 48
      case 'EN_ROUTE_DROPOFF': {
        const remaining = robot.path ? robot.path.length : 0
        const progress = Math.max(55, Math.min(90, 90 - remaining * 2))
        return progress
      }
      case 'DROPPING':
        return 95
      case 'CHARGING':
        return Math.round(robot.battery_pct)
      case 'AUDITING':
        return 65
      case 'CONFLICT_NEGOTIATING':
        return 50
      case 'FAILSAFE_HOLD':
      case 'EMERGENCY_STOP':
        return 0
      default:
        return 30
    }
  }, [robot.state, robot.current_task_id, robot.path, robot.battery_pct])

  const typeColor = ROBOT_TYPE_COLORS[robot.robot_type] || '#3b82f6'
  const stateColor = STATE_COLORS[robot.state] || '#94a3b8'

  // Type Icon
  const TypeIcon =
    robot.robot_type === 'GOODS_TO_PERSON'
      ? Layers
      : robot.robot_type === 'SORTING'
      ? Cpu
      : Scan

  const isFailsafe = robot.state === 'FAILSAFE_HOLD' || robot.state === 'EMERGENCY_STOP'

  return (
    <aside
      className="robot-detail panel"
      style={{
        width: '340px',
        maxHeight: '92vh',
        overflowY: 'auto',
        zIndex: 40,
        backgroundColor: 'var(--bg-surface, #FFFFFF)',
        border: '1px solid var(--border-subtle, #E4E4E7)',
        borderRadius: '12px',
        padding: '16px',
        boxShadow: '0 10px 30px -5px rgba(0, 0, 0, 0.12), 0 4px 6px -2px rgba(0, 0, 0, 0.05)',
        color: 'var(--text-primary, #18181B)',
      }}
    >
      {/* Close button */}
      <button
        className="close-detail"
        onClick={onClose}
        aria-label="Close inspector"
        style={{
          position: 'absolute',
          top: '12px',
          right: '12px',
          background: 'var(--bg-surface-subtle, #F4F4F5)',
          border: '1px solid var(--border-subtle, #E4E4E7)',
          color: 'var(--text-secondary, #71717A)',
          borderRadius: '6px',
          padding: '4px',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <X size={15} />
      </button>

      {/* Header with Type & ID */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
        <div
          style={{
            width: '34px',
            height: '34px',
            borderRadius: '8px',
            backgroundColor: 'var(--bg-surface-subtle, #F4F4F5)',
            border: '1px solid var(--border-subtle, #E4E4E7)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <TypeIcon size={18} color={typeColor} />
        </div>
        <div>
          <h2 style={{ margin: 0, fontSize: '1.15rem', fontWeight: 700, letterSpacing: '-0.02em', color: 'var(--text-primary, #18181B)' }}>
            {robot.robot_id}
          </h2>
          <div style={{ fontSize: '0.72rem', color: 'var(--text-muted, #71717A)', textTransform: 'uppercase', letterSpacing: '0.04em', fontWeight: 600 }}>
            {robot.robot_type.replace(/_/g, ' ')}
          </div>
        </div>
      </div>

      {/* State & Activity Badge */}
      <div style={{ display: 'flex', gap: '6px', alignItems: 'center', marginBottom: '14px' }}>
        <span
          className="state-badge"
          style={{
            backgroundColor: `${stateColor}18`,
            color: stateColor,
            border: `1px solid ${stateColor}44`,
            fontSize: '0.75rem',
            padding: '3px 10px',
            borderRadius: '6px',
            fontWeight: 700,
            display: 'inline-flex',
            alignItems: 'center',
            gap: '6px',
          }}
        >
          <span
            style={{
              width: '6px',
              height: '6px',
              borderRadius: '50%',
              backgroundColor: stateColor,
            }}
          />
          {STATE_LABELS[robot.state] ?? robot.state.replace(/_/g, ' ')}
        </span>

        {robot.action && (
          <span
            style={{
              fontSize: '0.7rem',
              background: 'var(--bg-surface-subtle, #F4F4F5)',
              border: '1px solid var(--border-subtle, #E4E4E7)',
              padding: '3px 8px',
              borderRadius: '6px',
              color: 'var(--text-secondary, #71717A)',
              fontWeight: 600,
            }}
          >
            {robot.action}
          </span>
        )}
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
          marginBottom: '14px',
          borderRadius: '8px',
          border: cameraFollow ? '1px solid #3B82F6' : '1px solid var(--border-subtle, #E4E4E7)',
          background: cameraFollow
            ? '#EFF6FF'
            : 'var(--bg-surface-subtle, #F4F4F5)',
          color: cameraFollow ? '#1D4ED8' : 'var(--text-primary, #18181B)',
          cursor: 'pointer',
          fontWeight: 600,
          fontSize: '0.82rem',
          transition: 'all 0.15s ease',
        }}
      >
        <Crosshair size={14} />
        {cameraFollow ? 'Camera Following AMR (Active)' : 'Follow AMR in 3D View'}
      </button>

      {/* Dynamic Task Progress Bar */}
      <div
        style={{
          background: 'var(--bg-surface-subtle, #FAFAFA)',
          border: '1px solid var(--border-subtle, #E4E4E7)',
          borderRadius: '8px',
          padding: '10px 12px',
          marginBottom: '14px',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
          <span style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-secondary, #71717A)', display: 'flex', alignItems: 'center', gap: '5px' }}>
            <Activity size={13} color="var(--accent-primary, #FF6B35)" /> Mission Lifecycle
          </span>
          <span style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--text-primary, #18181B)' }}>
            {taskProgress}%
          </span>
        </div>

        {/* Progress track */}
        <div style={{ height: '6px', background: '#E4E4E7', borderRadius: '3px', overflow: 'hidden', marginBottom: '8px' }}>
          <div
            style={{
              width: `${taskProgress}%`,
              height: '100%',
              background:
                isFailsafe
                  ? '#EF4444'
                  : taskProgress === 100
                  ? '#10B981'
                  : 'var(--accent-primary, #FF6B35)',
              transition: 'width 0.4s ease',
            }}
          />
        </div>

        {/* Milestone Steps */}
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.68rem', color: 'var(--text-muted, #71717A)' }}>
          <span style={{ color: taskProgress >= 20 ? 'var(--text-primary, #18181B)' : 'var(--text-muted, #71717A)', fontWeight: taskProgress >= 20 ? 600 : 400 }}>1. Pickup</span>
          <span style={{ color: taskProgress >= 50 ? 'var(--text-primary, #18181B)' : 'var(--text-muted, #71717A)', fontWeight: taskProgress >= 50 ? 600 : 400 }}>2. Transit</span>
          <span style={{ color: taskProgress >= 90 ? 'var(--text-primary, #18181B)' : 'var(--text-muted, #71717A)', fontWeight: taskProgress >= 90 ? 600 : 400 }}>3. Dropoff</span>
        </div>
      </div>

      {/* Telemetry Metrics 2x2 Grid */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: '8px',
          marginBottom: '14px',
          fontSize: '0.8rem',
        }}
      >
        <div style={{ background: 'var(--bg-surface-subtle, #FAFAFA)', border: '1px solid var(--border-subtle, #E4E4E7)', padding: '8px 10px', borderRadius: '8px' }}>
          <div style={{ color: 'var(--text-muted, #71717A)', display: 'flex', alignItems: 'center', gap: '5px', fontSize: '0.72rem', fontWeight: 600 }}>
            <Compass size={13} color="var(--text-secondary, #71717A)" /> Position
          </div>
          <strong style={{ color: 'var(--text-primary, #18181B)', fontSize: '0.9rem', fontFamily: 'var(--font-mono)' }}>
            ({robot.position.x}, {robot.position.y})
          </strong>
        </div>

        <div style={{ background: 'var(--bg-surface-subtle, #FAFAFA)', border: '1px solid var(--border-subtle, #E4E4E7)', padding: '8px 10px', borderRadius: '8px' }}>
          <div style={{ color: 'var(--text-muted, #71717A)', display: 'flex', alignItems: 'center', gap: '5px', fontSize: '0.72rem', fontWeight: 600 }}>
            <Gauge size={13} color="var(--text-secondary, #71717A)" /> Heading
          </div>
          <strong style={{ color: 'var(--text-primary, #18181B)', fontSize: '0.9rem', fontFamily: 'var(--font-mono)' }}>{robot.heading}</strong>
        </div>

        <div style={{ background: 'var(--bg-surface-subtle, #FAFAFA)', border: '1px solid var(--border-subtle, #E4E4E7)', padding: '8px 10px', borderRadius: '8px' }}>
          <div style={{ color: 'var(--text-muted, #71717A)', display: 'flex', alignItems: 'center', gap: '5px', fontSize: '0.72rem', fontWeight: 600 }}>
            <Target size={13} color="var(--text-secondary, #71717A)" /> Goal Dist / ETA
          </div>
          <strong style={{ color: 'var(--text-primary, #18181B)', fontSize: '0.85rem', fontFamily: 'var(--font-mono)' }}>
            {distanceToGoal} cells <span style={{ color: 'var(--text-muted, #71717A)', fontSize: '0.75rem' }}>({estTicksToGoal}t)</span>
          </strong>
        </div>

        <div style={{ background: 'var(--bg-surface-subtle, #FAFAFA)', border: '1px solid var(--border-subtle, #E4E4E7)', padding: '8px 10px', borderRadius: '8px' }}>
          <div style={{ color: 'var(--text-muted, #71717A)', display: 'flex', alignItems: 'center', gap: '5px', fontSize: '0.72rem', fontWeight: 600 }}>
            <Zap size={13} color="var(--text-secondary, #71717A)" /> Priority Score
          </div>
          <strong style={{ color: 'var(--text-primary, #18181B)', fontSize: '0.9rem', fontFamily: 'var(--font-mono)' }}>
            {robot.priority_score.toFixed(1)}{' '}
            <span style={{ fontSize: '0.7rem', color: robot.priority_score > 3.0 ? '#DC2626' : '#15803D', fontWeight: 700 }}>
              ({robot.priority_score > 3.0 ? 'HIGH' : robot.priority_score > 1.5 ? 'MED' : 'NORM'})
            </span>
          </strong>
        </div>
      </div>

      {/* Battery Gauge */}
      <div
        style={{
          background: 'var(--bg-surface-subtle, #FAFAFA)',
          border: '1px solid var(--border-subtle, #E4E4E7)',
          padding: '10px 12px',
          borderRadius: '8px',
          marginBottom: '14px',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', marginBottom: '6px' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: '5px', color: 'var(--text-secondary, #71717A)', fontWeight: 600 }}>
            <Battery size={14} color={robot.battery_pct < 25 ? '#DC2626' : '#15803D'} /> Battery Charge
          </span>
          <strong style={{ color: 'var(--text-primary, #18181B)', fontSize: '0.85rem', fontFamily: 'var(--font-mono)' }}>
            {robot.battery_pct.toFixed(1)}%
          </strong>
        </div>
        <div style={{ height: '6px', background: '#E4E4E7', borderRadius: '3px', overflow: 'hidden' }}>
          <div
            style={{
              width: `${robot.battery_pct}%`,
              height: '100%',
              backgroundColor: robot.battery_pct < 25 ? '#DC2626' : robot.battery_pct < 50 ? '#D97706' : '#10B981',
              transition: 'width 0.3s ease',
            }}
          />
        </div>
      </div>

      {/* Active Payload / Carrier Card */}
      <div
        style={{
          background: 'var(--bg-surface-subtle, #FAFAFA)',
          border: '1px solid var(--border-subtle, #E4E4E7)',
          padding: '10px 12px',
          borderRadius: '8px',
          marginBottom: '14px',
          fontSize: '0.8rem',
        }}
      >
        <div style={{ color: 'var(--text-secondary, #71717A)', display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px', fontSize: '0.75rem', fontWeight: 600 }}>
          <Package size={14} color="var(--text-secondary, #71717A)" /> Physical Load / Cargo
        </div>
        {robot.carrying_pod_id ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ color: '#1D4ED8', fontWeight: 700, fontSize: '0.88rem' }}>
              Pod: {robot.carrying_pod_id}
            </span>
            <span style={{ fontSize: '0.7rem', background: '#EFF6FF', color: '#1D4ED8', padding: '2px 6px', borderRadius: '4px', border: '1px solid #BFDBFE', fontWeight: 700 }}>
              LOADED
            </span>
          </div>
        ) : robot.robot_type === 'SORTING' && robot.state === 'EN_ROUTE_DROPOFF' ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ color: '#B45309', fontWeight: 700, fontSize: '0.88rem' }}>
              Sortation Carton
            </span>
            <span style={{ fontSize: '0.7rem', background: '#FEF3C7', color: '#B45309', padding: '2px 6px', borderRadius: '4px', border: '1px solid #FDE68A', fontWeight: 700 }}>
              IN TRANSIT
            </span>
          </div>
        ) : (
          <div style={{ color: 'var(--text-muted, #71717A)', fontStyle: 'italic', fontSize: '0.78rem' }}>
            Unladen (No active payload attached)
          </div>
        )}
      </div>

      {/* Real-time Conflict Arbitration Status Badge */}
      <div
        style={{
          background: robot.conflict ? '#FEF2F2' : '#F0FDF4',
          border: robot.conflict ? '1px solid #FECACA' : '1px solid #BBF7D0',
          padding: '10px 12px',
          borderRadius: '8px',
          marginBottom: '14px',
          fontSize: '0.78rem',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px' }}>
          {robot.conflict ? (
            <>
              <AlertTriangle size={14} color="#DC2626" />
              <strong style={{ color: '#DC2626' }}>Contention / Arbitration Active</strong>
            </>
          ) : (
            <>
              <ShieldCheck size={14} color="#15803D" />
              <strong style={{ color: '#15803D' }}>Traffic Nominal (No Conflicts)</strong>
            </>
          )}
        </div>
        {robot.conflict ? (
          <div style={{ color: '#991B1B', fontSize: '0.72rem' }}>
            Contending at cell ({robot.conflict.cell.x}, {robot.conflict.cell.y}) | Action:{' '}
            <span style={{ color: '#7F1D1D', fontWeight: 700 }}>{robot.conflict.action ?? 'RESOLVING'}</span>
          </div>
        ) : (
          <div style={{ color: '#166534', fontSize: '0.72rem' }}>
            Clear path reservation registered on peer UDP mesh
          </div>
        )}
      </div>

      {/* Recent FSM Transitions Log */}
      <div
        style={{
          background: 'var(--bg-surface-subtle, #FAFAFA)',
          border: '1px solid var(--border-subtle, #E4E4E7)',
          padding: '10px 12px',
          borderRadius: '8px',
          marginBottom: '16px',
        }}
      >
        <div style={{ color: 'var(--text-secondary, #71717A)', display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '8px', fontSize: '0.75rem', fontWeight: 600 }}>
          <Clock size={13} color="var(--text-secondary, #71717A)" /> Recent FSM Transitions
        </div>
        {transitionHistory.length === 0 ? (
          <div style={{ color: 'var(--text-muted, #71717A)', fontStyle: 'italic', fontSize: '0.72rem' }}>
            Steady state: {robot.state} (Tick {tick})
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {transitionHistory.map((rec, idx) => (
              <div
                key={idx}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  fontSize: '0.7rem',
                  color: 'var(--text-primary, #18181B)',
                  background: 'var(--bg-surface, #FFFFFF)',
                  border: '1px solid var(--border-subtle, #E4E4E7)',
                  padding: '4px 6px',
                  borderRadius: '4px',
                }}
              >
                <span style={{ color: 'var(--text-muted, #71717A)', fontFamily: 'var(--font-mono)' }}>T{rec.tick}:</span>
                <span style={{ color: 'var(--text-secondary, #71717A)' }}>{rec.from}</span>
                <ArrowRight size={11} color="var(--text-muted, #71717A)" />
                <span style={{ color: 'var(--accent-primary, #FF6B35)', fontWeight: 600 }}>{rec.to}</span>
              </div>
            ))}
          </div>
        )}
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
            padding: '8px 12px',
            borderRadius: '6px',
            background: '#FEF2F2',
            border: '1px solid #FCA5A5',
            color: '#DC2626',
            fontSize: '0.78rem',
            fontWeight: 700,
            cursor: 'pointer',
            transition: 'background 0.15s ease',
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
            padding: '8px 12px',
            borderRadius: '6px',
            background: 'var(--bg-surface-subtle, #F4F4F5)',
            border: '1px solid var(--border-subtle, #E4E4E7)',
            color: 'var(--text-primary, #18181B)',
            fontSize: '0.78rem',
            fontWeight: 700,
            cursor: 'pointer',
            transition: 'background 0.15s ease',
          }}
        >
          <RotateCcw size={14} /> Reset AMR
        </button>
      </div>
    </aside>
  )
}
