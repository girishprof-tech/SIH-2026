import { Battery, ChevronRight, Filter } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Robot, RobotState, RobotType } from '../types'
import { ALL_STATES, ROBOT_TYPE_LABELS, STATE_LABELS } from '../state-meta'

const states: Array<RobotState | 'ALL'> = ['ALL', ...ALL_STATES]
const robotTypes: Array<RobotType | 'ALL'> = ['ALL', 'GOODS_TO_PERSON', 'SORTING', 'SCANNING_AUDIT']

export function FleetSidebar({ robots, selected, filter, onFilter, onSelect }: { robots: Robot[]; selected: string | null; filter: string; onFilter: (value: string) => void; onSelect: (robot: Robot) => void }) {
  const [initialRobots, setInitialRobots] = useState<Robot[]>([])
  useEffect(() => { void api.robots().then(setInitialRobots).catch(() => undefined) }, [])
  const displayedRobots = robots.length ? robots : initialRobots
  const typeFilter = filter.startsWith('TYPE:') ? filter.slice(5) as RobotType : 'ALL'
  const stateFilter = filter.startsWith('STATE:') ? filter.slice(6) as RobotState : filter.startsWith('TYPE:') ? 'ALL' : filter
  const filtered = displayedRobots.filter(robot => (typeFilter === 'ALL' || robot.robot_type === typeFilter) && (stateFilter === 'ALL' || robot.state === stateFilter))
  const groups = robotTypes.filter(type => type === 'ALL' || filtered.some(robot => robot.robot_type === type))
  return <aside className="panel fleet-panel"><div className="panel-heading"><div><span className="eyebrow">UNIT REGISTRY</span><h2>FLEET STATUS <em>{filtered.length}/{displayedRobots.length}</em></h2></div><Filter size={15} /></div><div className="filter-label">ROBOT TYPE</div><div className="filter-row type-filters">{robotTypes.map(type => <button key={type} className={typeFilter === type ? 'active' : ''} onClick={() => onFilter(type === 'ALL' ? stateFilter : `TYPE:${type}`)}>{type === 'ALL' ? 'ALL' : ROBOT_TYPE_LABELS[type]}</button>)}</div><div className="filter-label">STATE</div><div className="filter-row">{states.map(state => <button key={state} className={stateFilter === state ? 'active' : ''} onClick={() => onFilter(state === 'ALL' ? (typeFilter === 'ALL' ? 'ALL' : `TYPE:${typeFilter}`) : `STATE:${state}`)}>{state === 'ALL' ? 'ALL' : STATE_LABELS[state]}</button>)}</div><div className="fleet-list">{groups.map(group => <div key={group}>{group !== 'ALL' && <div className="fleet-group-title">{ROBOT_TYPE_LABELS[group]}</div>}{filtered.filter(robot => group === 'ALL' || robot.robot_type === group).map(robot => <button className={`robot-row ${selected === robot.robot_id ? 'selected' : ''}`} key={robot.robot_id} onClick={() => onSelect(robot)}><div className="robot-id"><span className={`state-light ${robot.state.toLowerCase()}`} />{robot.robot_id}<ChevronRight size={13} /></div><span className="robot-type-label">{ROBOT_TYPE_LABELS[robot.robot_type]}</span><span className={`state-badge ${robot.state.toLowerCase()}`}>{robot.state.replace(/_/g, ' ')}</span><div className="robot-meta"><span className={robot.battery_pct < 20 ? 'battery-low' : ''}><Battery size={12} /> {robot.battery_pct.toFixed(0)}%</span><span>{robot.current_task_id ?? 'NO TASK'}</span><strong>{robot.priority_score.toFixed(1)} PRI</strong></div></button>)}</div>)}{filtered.length === 0 && <div className="empty-state">No units match filters</div>}</div></aside>
}
