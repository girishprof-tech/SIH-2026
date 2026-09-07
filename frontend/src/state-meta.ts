import type { RobotState, RobotType } from './types'

export const ROBOT_TYPE_COLORS: Record<RobotType, string> = {
  GOODS_TO_PERSON: '#ea580c',
  SORTING: '#d97706',
  SCANNING_AUDIT: '#71717a',
}

export const ROBOT_TYPE_LABELS: Record<RobotType, string> = {
  GOODS_TO_PERSON: 'Goods to person',
  SORTING: 'Sorting',
  SCANNING_AUDIT: 'Scanning & audit',
}

export const STATE_COLORS: Record<RobotState, string> = {
  IDLE: '#10b981',
  ASSIGNED: '#eab308',
  EN_ROUTE_PICKUP: '#f97316',
  PICKING: '#ea580c',
  EN_ROUTE_DROPOFF: '#c2410c',
  DROPPING: '#d97706',
  CONFLICT_NEGOTIATING: '#f59e0b',
  AUDITING: '#78716c',
  CHARGING: '#14b8a6',
  FAILSAFE_HOLD: '#b45309',
  EMERGENCY_STOP: '#dc2626',
}

export const STATE_LABELS: Record<RobotState, string> = {
  IDLE: 'Idle',
  ASSIGNED: 'Assigned',
  EN_ROUTE_PICKUP: 'En route',
  PICKING: 'Picking',
  EN_ROUTE_DROPOFF: 'Dropoff route',
  DROPPING: 'Dropping',
  CONFLICT_NEGOTIATING: 'Conflict',
  AUDITING: 'Audit',
  CHARGING: 'Charging',
  FAILSAFE_HOLD: 'Failsafe',
  EMERGENCY_STOP: 'Emergency stop',
}

export const ALL_STATES: RobotState[] = [
  'IDLE',
  'ASSIGNED',
  'EN_ROUTE_PICKUP',
  'PICKING',
  'EN_ROUTE_DROPOFF',
  'DROPPING',
  'CONFLICT_NEGOTIATING',
  'AUDITING',
  'CHARGING',
  'FAILSAFE_HOLD',
  'EMERGENCY_STOP',
]
