import type { RobotState, RobotType } from './types'

export const ROBOT_TYPE_COLORS: Record<RobotType, string> = {
  GOODS_TO_PERSON: '#ea580c',
  SORTING: '#0284c7',
  SCANNING_AUDIT: '#6366f1',
}

export const ROBOT_TYPE_LABELS: Record<RobotType, string> = {
  GOODS_TO_PERSON: 'Goods to person',
  SORTING: 'Sorting',
  SCANNING_AUDIT: 'Scanning & audit',
}

export const STATE_COLORS: Record<RobotState, string> = {
  IDLE: '#10b981',
  ASSIGNED: '#3b82f6',
  EN_ROUTE_PICKUP: '#0284c7',
  PICKING: '#0d9488',
  EN_ROUTE_DROPOFF: '#2563eb',
  DROPPING: '#d97706',
  CONFLICT_NEGOTIATING: '#ea580c',
  AUDITING: '#6366f1',
  CHARGING: '#8b5cf6',
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
