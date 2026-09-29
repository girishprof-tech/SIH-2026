export type Point = { x: number; y: number }
export type RobotState = 'IDLE' | 'ASSIGNED' | 'EN_ROUTE_PICKUP' | 'PICKING' | 'EN_ROUTE_DROPOFF' | 'DROPPING' | 'CONFLICT_NEGOTIATING' | 'AUDITING' | 'CHARGING' | 'FAILSAFE_HOLD' | 'EMERGENCY_STOP'
export type Heading = 'NORTH' | 'SOUTH' | 'EAST' | 'WEST'
export type RobotType = 'GOODS_TO_PERSON' | 'SORTING' | 'SCANNING_AUDIT'
export type Robot = {
  robot_id: string
  position: Point
  heading: Heading
  robot_type: RobotType
  state: RobotState
  battery_pct: number
  current_task_id: string | null
  priority_score: number
  last_updated_tick: number
  path: Array<Point & { t: number }>
  carrying_pod_id?: string | null
  wait_ticks_so_far?: number
  action?: string
  conflict?: Conflict | null
  planner_latency_ms?: number
  goal?: Point | number[]
}
export type JobType = 'fetch_item' | 'sort_batch' | 'audit_checkpoint' | 'relocate'
export type JobRequest = {
  job_type: JobType | string
  item_id?: string
  sku?: string
  quantity?: number
  zone?: string
  urgency: number
  shelf_id?: string
  pickup?: Point
  dropoff?: Point
  return_to_home?: boolean
  source_gate?: string
  destination_chute?: string
  route_code?: string
  checkpoint?: Point
  target_robot_id?: string
}
export type JobResponse = {
  job_type: JobType | string
  robot_type: RobotType | string
  task_id?: string | null
  audit_id?: string | null
  robot_id?: string | null
  target_shelf_id?: string | null
  sku?: string | null
  quantity?: number | null
  status: string
  message: string
  lease_expires_tick?: number | null
  unclaimed_reason?: string | null
}
export type Conflict = { robot_ids?: string[]; cell: Point; resolved_by?: string; winner_id?: string; loser_id?: string; action?: string }
export type TempObstacle = { obstacle_id: string; position: Point; created_tick: number; expires_at_tick: number }
export type ShelfRecord = {
  shelf_id: string
  x: number
  y: number
  capacity_boxes: number
  current_box_count: number
  sku_manifest: Record<string, number>
  last_audited_tick: number
  last_audited_by?: string | null
  confidence: number
}

export type SortationChute = {
  chute_id: string
  destination_zone: string
  x: number
  y: number
  current_count?: number
  capacity?: number
}

export type InventoryUpdateEvent = {
  id: string
  shelf_id: string
  source_robot_id: string
  source: string
  channel: 'HALOW' | 'PEER_MESH'
  tick: number
  box_count: number
  confidence: number
  sku_manifest: Record<string, number>
  timestamp_ms: number
}

export type HaLowStatus = {
  connected: boolean
  last_msg_timestamp_ms: number
  throttle_utilization: number
  bitrate_kbps: number
  packets_received: number
}

export type TickUpdate = {
  type?: string
  tick: number
  timestamp_ms: number
  robots: Robot[]
  active_conflicts: Conflict[]
  temporary_obstacles: TempObstacle[]
  tasks?: Task[]
  metrics?: Metrics
  fleet_status?: { running: boolean; mode: string; tick: number; armed_state?: string }
  inventory?: ShelfRecord[]
  sortation_chutes?: Record<string, { destination_zone: string; x: number; y: number }>
  halow_status?: HaLowStatus
}
export type Task = {
  task_id: string
  pickup: Point
  dropoff: Point
  urgency: number
  status: string
  assigned_robot_id?: string | null
  robot_type?: RobotType | string
  created_tick: number
  task_type?: string
  target_shelf_id?: string | null
  return_to_home?: boolean
  lease_expires_tick?: number | null
  unclaimed_reason?: string | null
}

export type World = {
  width: number
  height: number
  static_obstacles: Point[]
  charging_stations: Point[]
  pickup_stations: Point[]
  dropoff_stations: Point[]
  pod_slots?: Array<{ shelf_id: string; x: number; y: number }>
  sortation_chutes?: Record<string, { destination_zone: string; x: number; y: number }>
  pick_stations?: Array<{ id: string; x: number; y: number; bufferCount?: number }>
  entry_gates?: Array<{ id: string; cells: Point[] }>
  exit_gates?: Array<{ id: string; cells: Point[] }>
}
export type Metrics = { tick_ms_configured: number; last_tick_processing_ms: number; planner_latency_ms: number; broadcast_latency_ms: number; connected_clients: number; active_robots: number; active_conflicts: number; replans: number; total_ticks: number; task_injection_latency_ms: number; conflict_resolution_latency_ms: number }
export type SimulationStatus = { running: boolean; tick: number; timestamp_ms: number; fleet_size: number; tick_ms: number }
export type HealthStatus = { status: string; tick: number; running: boolean; is_paused?: boolean; robots: number; fleet_mode: string; fleet_mode_description: string }

export type WarehouseMap = {
  schema_version: string
  name: string
  description?: string
  grid: { width: number; height: number; cell_size_m?: number }
  blocked_cells: Point[]
  shelves: Array<{ id: string; x: number; y: number; capacity?: number; bank?: string }>
  entry_gates: Array<{ id: string; name?: string; cells: Point[] }>
  exit_gates: Array<{ id: string; name?: string; cells: Point[] }>
  sorting_stations: Array<{ id: string; name?: string; x: number; y: number; destination_zone?: string; capacity?: number; gate_id?: string }>
  pick_stations: Array<{ id: string; name?: string; x: number; y: number; capacity?: number }>
  chargers: Array<{ id: string; x: number; y: number; assigned_robot_id?: string | null }>
  robot_starts: Array<{ id: string; type: RobotType; x: number; y: number; battery_pct?: number; urgency?: number }>
  fixed_stations?: Array<{ id: string; name?: string; role?: string; x: number; y: number; port?: number }>
}

export type MapValidationResult = {
  valid: boolean
  errors: string[]
  warnings: string[]
  stats?: Record<string, any>
}

export type MapPreset = {
  filename: string
  id: string
  name: string
  description?: string
  width: number
  height: number
  shelves_count: number
  robots_count: number
  chutes_count: number
}