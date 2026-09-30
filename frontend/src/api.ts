import type { HealthStatus, JobRequest, JobResponse, Metrics, Robot, SimulationStatus, Task, TempObstacle, World } from './types'

export const API_BASE = import.meta.env.VITE_API_BASE ?? (
  typeof window !== 'undefined' && (window.location.port === '5173' || window.location.port === '5174')
    ? 'http://localhost:8000'
    : (typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8000')
)

let currentOperatorRole = 'AUTHORITY'

export function setApiOperatorRole(role: string) {
  currentOperatorRole = role
}

export function getApiOperatorRole(): string {
  return currentOperatorRole
}

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) { super(message); this.name = 'ApiError' }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      'X-Operator-Role': currentOperatorRole,
      ...init?.headers,
    },
    ...init,
  })
  if (!response.ok) {
    let detail = response.statusText
    try { detail = (await response.json()).detail ?? detail } catch { /* plain error response */ }
    throw new ApiError(response.status, `${response.status}: ${detail}`)
  }
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<HealthStatus>('/health'),
  world: () => request<World>('/api/world'),
  robots: () => request<Robot[]>('/api/robots/'),
  tasks: () => request<Task[]>('/api/task/all'),
  obstacles: () => request<TempObstacle[]>('/api/obstacles'),
  metrics: () => request<Metrics>('/api/metrics'),
  status: () => request<SimulationStatus>('/api/simulation/status'),
  simulation: (action: 'start' | 'pause' | 'reset') => request(`/api/simulation/${action}`, { method: 'POST' }),
  setSimulationSpeed: (speed: number) => request<{ status: string; speed: number }>('/api/simulation/speed', { method: 'POST', body: JSON.stringify({ speed }) }),
  chaos: (packet_loss_pct: number) => request('/api/chaos/toggle', { method: 'POST', body: JSON.stringify({ packet_loss_pct }) }),
  chaosStatus: () => request<{ enabled: boolean; packet_loss_pct: number }>('/api/chaos/status'),
  submitJob: (body: JobRequest) => request<JobResponse>('/api/job', { method: 'POST', body: JSON.stringify(body) }),
  injectTask: (body: { pickup: { x: number; y: number }; dropoff: { x: number; y: number }; urgency: number }) => request<Task>('/api/task/inject', { method: 'POST', body: JSON.stringify(body) }),
  addObstacle: (body: { obstacle_id: string; x: number; y: number; duration_ticks: number }) => request<TempObstacle>('/api/obstacles', { method: 'POST', body: JSON.stringify(body) }),
  removeObstacle: (id: string) => request(`/api/obstacles/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  inventory: () => request<{ total_shelves: number; total_boxes: number; shelves: import('./types').ShelfRecord[] }>('/api/inventory'),
  eStop: (robotId: string) => request(`/api/robots/${encodeURIComponent(robotId)}/emergency_stop`, { method: 'POST' }),
  resetRobot: (robotId: string) => request(`/api/robots/${encodeURIComponent(robotId)}/reset`, { method: 'POST' }),
  mapValidate: (mapData: import('./types').WarehouseMap) => request<import('./types').MapValidationResult>('/api/map/validate', { method: 'POST', body: JSON.stringify(mapData) }),
  mapLaunch: (mapData: import('./types').WarehouseMap | string) => request<any>('/api/map/launch', { method: 'POST', body: JSON.stringify(typeof mapData === 'string' ? { filename: mapData } : mapData) }),
  mapCurrent: () => request<import('./types').WarehouseMap>('/api/map/current'),
  mapPresets: () => request<import('./types').MapPreset[]>('/api/map/presets'),
  mapSave: (name: string, mapData: import('./types').WarehouseMap) => request<{ status: string; filename: string; path: string }>('/api/map/save', { method: 'POST', body: JSON.stringify({ name, map_data: mapData }) }),
  mapTemplate: () => request<import('./types').WarehouseMap>('/api/map/template'),
  catalog: () => request<Array<{ sku: string; name: string; weight_kg: number; total_stock: number }>>('/api/catalog'),
  createOrder: (body: { sku: string; quantity: number; destination_gate?: string; urgency?: number }) =>
    request<JobResponse>('/api/order', { method: 'POST', body: JSON.stringify(body) }),
  getOrders: () => request<import('./types').OrderInfo[]>('/api/orders'),
  recoveryPending: () => request<{ count: number; jobs: any[] }>('/api/tasks/recovery/pending'),
  recoveryResume: () => request<{ resumed_count: number; discarded_count: number; resumed_task_ids: string[] }>('/api/tasks/recovery/resume', { method: 'POST' }),
  recoveryDiscard: () => request<{ discarded_count: number; message: string }>('/api/tasks/recovery/discard', { method: 'POST' }),
}

export const wsUrl = () => {
  if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL
  if (typeof window !== 'undefined') {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    if (window.location.port === '5173' || window.location.port === '5174') {
      return `${proto}//${window.location.hostname}:8000/ws/fleet`
    }
    return `${proto}//${window.location.host}/ws/fleet`
  }
  return API_BASE.replace(/^http/, 'ws') + '/ws/fleet'
}