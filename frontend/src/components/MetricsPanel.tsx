import { Activity, Gauge, Timer, Zap } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api'
import { ROBOT_TYPE_COLORS, ROBOT_TYPE_LABELS } from '../state-meta'
import type { Metrics, Robot, RobotState, RobotType } from '../types'

const robotTypes: RobotType[] = ['GOODS_TO_PERSON', 'SORTING', 'SCANNING_AUDIT']
const states: RobotState[] = ['IDLE', 'EN_ROUTE_PICKUP', 'EN_ROUTE_DROPOFF', 'AUDITING', 'CHARGING', 'CONFLICT_NEGOTIATING']

export function MetricsPanel({
  metrics,
  history,
  robots,
  theme = 'light',
}: {
  metrics: Metrics | null
  history: Array<{ tick: number; process: number; planner: number; conflicts: number; replans: number }>
  robots: Robot[]
  theme?: 'light' | 'dark'
}) {
  const [liveMetrics, setLiveMetrics] = useState<Metrics | null>(metrics)
  const [liveHistory, setLiveHistory] = useState(history)

  useEffect(() => {
    setLiveMetrics(metrics)
  }, [metrics])

  useEffect(() => {
    const refresh = () => {
      void api
        .metrics()
        .then((next) => {
          setLiveMetrics(next)
          setLiveHistory((previous) =>
            [
              ...previous,
              {
                tick: next.total_ticks,
                process: next.last_tick_processing_ms,
                planner: next.planner_latency_ms,
                conflicts: next.active_conflicts,
                replans: next.replans,
              },
            ].slice(-50)
          )
        })
        .catch(() => undefined)
    }
    refresh()
    const timer = window.setInterval(refresh, 1500)
    return () => window.clearInterval(timer)
  }, [])

  const stateCount = (type: RobotType, state: RobotState) =>
    robots.filter((robot) => robot.robot_type === type && robot.state === state).length

  const chartColor = '#FF6B35'
  const chartFill = theme === 'light' ? 'rgba(255, 107, 53, 0.14)' : 'rgba(255, 107, 53, 0.22)'

  return (
    <section className="metrics panel">
      <div className="panel-heading">
        <h2>Performance telemetry</h2>
        <Activity size={16} />
      </div>

      <div className="metric-cards">
        <div>
          <Timer size={15} />
          <span>Tick processing</span>
          <strong>
            {liveMetrics?.last_tick_processing_ms?.toFixed(1) ?? '--'}
            <small> ms</small>
          </strong>
        </div>
        <div>
          <Zap size={15} />
          <span>Planner latency</span>
          <strong>
            {liveMetrics?.planner_latency_ms?.toFixed(1) ?? '--'}
            <small> ms</small>
          </strong>
        </div>
        <div>
          <Gauge size={15} />
          <span>Active conflicts</span>
          <strong>{liveMetrics?.active_conflicts ?? '--'}</strong>
        </div>
        <div>
          <Activity size={15} />
          <span>Replans</span>
          <strong>{liveMetrics?.replans ?? '--'}</strong>
        </div>
      </div>

      <div className="composition-card">
        <div className="composition-heading">
          <h3>Fleet composition</h3>
          <strong>{robots.length} units</strong>
        </div>
        {robotTypes.map((type) => (
          <div className="composition-row" key={type}>
            <span className="composition-type">
              <i style={{ backgroundColor: ROBOT_TYPE_COLORS[type] }} />
              {ROBOT_TYPE_LABELS[type]}
            </span>
            <strong>{robots.filter((robot) => robot.robot_type === type).length}</strong>
            <small>
              {states
                .map((state) => {
                  const count = stateCount(type, state)
                  if (!count) return ''
                  const label = state
                    .replace('CONFLICT_NEGOTIATING', 'Conflict')
                    .replace('EN_ROUTE_PICKUP', 'Pickup')
                    .replace('EN_ROUTE_DROPOFF', 'Dropoff')
                    .replace('IDLE', 'Idle')
                    .replace('AUDITING', 'Audit')
                    .replace('CHARGING', 'Charging')
                  return `${label}: ${count}`
                })
                .filter(Boolean)
                .join(', ') || 'Idle: 0'}
            </small>
          </div>
        ))}
      </div>

      <div className="chart">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={liveHistory.length ? liveHistory : history}>
            <XAxis dataKey="tick" hide />
            <YAxis hide domain={[0, 'auto']} />
            <Tooltip
              contentStyle={{
                backgroundColor: theme === 'light' ? '#FFFFFF' : '#171717',
                borderColor: theme === 'light' ? '#D4D4D4' : '#333333',
                borderRadius: '6px',
                color: theme === 'light' ? '#141414' : '#F5F5F5',
                fontSize: 12,
                boxShadow: '0 4px 12px rgba(0, 0, 0, 0.25)',
              }}
              labelFormatter={(value) => `Tick ${value}`}
            />
            <Area
              type="monotone"
              dataKey="process"
              stroke={chartColor}
              fill={chartFill}
              strokeWidth={2}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}