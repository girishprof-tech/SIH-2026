import { Battery, ChevronRight, Filter } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Robot, RobotState, RobotType } from '../types'
import { ALL_STATES, ROBOT_TYPE_LABELS, STATE_LABELS } from '../state-meta'

const states: Array<RobotState | 'ALL'> = ['ALL', ...ALL_STATES]
const robotTypes: Array<RobotType | 'ALL'> = ['ALL', 'GOODS_TO_PERSON', 'SORTING', 'SCANNING_AUDIT']

export function FleetSidebar({
  robots,
  selected,
  filter,
  onFilter,
  onSelect,
}: {
  robots: Robot[]
  selected: string | null
  filter: string
  onFilter: (value: string) => void
  onSelect: (robot: Robot) => void
}) {
  const [initialRobots, setInitialRobots] = useState<Robot[]>([])

  useEffect(() => {
    void api.robots().then(setInitialRobots).catch(() => undefined)
  }, [])

  const displayedRobots = robots.length ? robots : initialRobots
  const typeFilter = filter.startsWith('TYPE:') ? (filter.slice(5) as RobotType) : 'ALL'
  const stateFilter = filter.startsWith('STATE:')
    ? (filter.slice(6) as RobotState)
    : filter.startsWith('TYPE:')
    ? 'ALL'
    : filter
  const filtered = displayedRobots.filter(
    (robot) =>
      (typeFilter === 'ALL' || robot.robot_type === typeFilter) &&
      (stateFilter === 'ALL' || robot.state === stateFilter)
  )
  const groups = robotTypes.filter((type) => type === 'ALL' || filtered.some((robot) => robot.robot_type === type))

  return (
    <aside className="panel fleet-panel">
      <div className="panel-heading">
        <h2>
          Fleet status <em>{filtered.length}/{displayedRobots.length}</em>
        </h2>
        <Filter size={16} />
      </div>

      <div className="filter-section">
        <div className="filter-label">Robot type</div>
        <div className="filter-row type-filters">
          {robotTypes.map((type) => (
            <button
              key={type}
              className={typeFilter === type ? 'active' : ''}
              onClick={() => onFilter(type === 'ALL' ? stateFilter : `TYPE:${type}`)}
            >
              {type === 'ALL' ? 'All' : ROBOT_TYPE_LABELS[type]}
            </button>
          ))}
        </div>

        <div className="filter-label" style={{ marginTop: 10 }}>
          State
        </div>
        <div className="filter-row">
          {states.map((state) => (
            <button
              key={state}
              className={stateFilter === state ? 'active' : ''}
              onClick={() =>
                onFilter(state === 'ALL' ? (typeFilter === 'ALL' ? 'ALL' : `TYPE:${typeFilter}`) : `STATE:${state}`)
              }
            >
              {state === 'ALL' ? 'All' : STATE_LABELS[state]}
            </button>
          ))}
        </div>
      </div>

      <div className="fleet-list">
        {groups.map((group) => (
          <div key={group}>
            {group !== 'ALL' && <div className="fleet-group-title">{ROBOT_TYPE_LABELS[group]}</div>}
            {filtered
              .filter((robot) => group === 'ALL' || robot.robot_type === group)
              .map((robot) => (
                <button
                  className={`robot-row ${selected === robot.robot_id ? 'selected' : ''}`}
                  key={robot.robot_id}
                  onClick={() => onSelect(robot)}
                >
                  <div className="robot-id">
                    <span className={`state-light ${robot.state.toLowerCase()}`} />
                    <span>{robot.robot_id}</span>
                    <ChevronRight size={14} />
                  </div>
                  <span className="robot-type-label">{ROBOT_TYPE_LABELS[robot.robot_type]}</span>
                  <div>
                    <span className={`state-badge ${robot.state.toLowerCase()}`}>
                      {STATE_LABELS[robot.state] ?? robot.state.replace(/_/g, ' ')}
                    </span>
                  </div>
                  <div className="robot-meta">
                    <span className={robot.battery_pct < 20 ? 'battery-low' : ''}>
                      <Battery size={13} /> {robot.battery_pct.toFixed(0)}%
                    </span>
                    <span>{robot.current_task_id ?? 'No task'}</span>
                    <strong>{robot.priority_score.toFixed(1)} Pri</strong>
                  </div>
                </button>
              ))}
          </div>
        ))}
        {filtered.length === 0 && <div className="empty-state">No units match filters</div>}
      </div>
    </aside>
  )
}
