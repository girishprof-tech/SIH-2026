import type { RobotState, RobotType } from './types'

export const ROBOT_TYPE_COLORS: Record<RobotType, string> = {
  GOODS_TO_PERSON: '#FF6B35',
  SORTING: '#FFC300',
  SCANNING_AUDIT: '#737373',
}

export const ROBOT_TYPE_LABELS: Record<RobotType, string> = {
  GOODS_TO_PERSON: 'Goods to person',
  SORTING: 'Sorting',
  SCANNING_AUDIT: 'Scanning & audit',
}

export const STATE_COLORS: Record<RobotState, string> = {
  IDLE: '#16A34A',
  ASSIGNED: '#FFC300',
  EN_ROUTE_PICKUP: '#FF6B35',
  PICKING: '#F97316',
  EN_ROUTE_DROPOFF: '#EA580C',
  DROPPING: '#D97706',
  CONFLICT_NEGOTIATING: '#DC2626',
  AUDITING: '#737373',
  CHARGING: '#16A34A',
  FAILSAFE_HOLD: '#D97706',
  EMERGENCY_STOP: '#B91C1C',
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
