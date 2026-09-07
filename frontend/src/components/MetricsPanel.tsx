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
  theme = 'dark',
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

  const chartColor = theme === 'light' ? '#c2410c' : '#f97316'
  const chartFill = theme === 'light' ? '#fff7ed' : '#43140733'

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
                backgroundColor: theme === 'light' ? '#ffffff' : '#1e283b',
                borderColor: theme === 'light' ? '#cbd5e1' : '#334155',
                borderRadius: '6px',
                color: theme === 'light' ? '#0f172a' : '#f8fafc',
                fontSize: 12,
                boxShadow: '0 4px 12px rgba(0, 0, 0, 0.1)',
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