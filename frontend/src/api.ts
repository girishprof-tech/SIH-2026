import type { Metrics, SimulationStatus, Task, TempObstacle, World } from './types'

export const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { headers: { 'Content-Type': 'application/json', ...init?.headers }, ...init })
  if (!response.ok) {
    let detail = response.statusText
    try { detail = (await response.json()).detail ?? detail } catch { /* plain error response */ }
    throw new Error(`${response.status}: ${detail}`)
  }
  return response.json() as Promise<T>
}

export const api = {
  world: () => request<World>('/api/world'),
  tasks: () => request<Task[]>('/api/task/all'),
  obstacles: () => request<TempObstacle[]>('/api/obstacles'),
  metrics: () => request<Metrics>('/api/metrics'),
  status: () => request<SimulationStatus>('/api/simulation/status'),
  simulation: (action: 'start' | 'pause' | 'reset') => request(`/api/simulation/${action}`, { method: 'POST' }),
  chaos: (packet_loss_pct: number) => request('/api/chaos/toggle', { method: 'POST', body: JSON.stringify({ packet_loss_pct }) }),
  injectTask: (body: { pickup: { x: number; y: number }; dropoff: { x: number; y: number }; urgency: number }) => request<Task>('/api/task/inject', { method: 'POST', body: JSON.stringify(body) }),
  addObstacle: (body: { obstacle_id: string; x: number; y: number; duration_ticks: number }) => request<TempObstacle>('/api/obstacles', { method: 'POST', body: JSON.stringify(body) }),
  removeObstacle: (id: string) => request(`/api/obstacles/${encodeURIComponent(id)}`, { method: 'DELETE' }),
}

export const wsUrl = () => (import.meta.env.VITE_WS_URL ?? API_BASE.replace(/^http/, 'ws') + '/ws/fleet')