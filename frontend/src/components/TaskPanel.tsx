import { PackageCheck, Send } from 'lucide-react'
import { useState } from 'react'
import { api, ApiError } from '../api'
import type { JobRequest, JobResponse, JobType, Point, Task } from '../types'

const jobOptions: Array<{ value: JobType; label: string; type: string }> = [
  { value: 'fetch_item', label: 'FETCH ITEM', type: 'GOODS-TO-PERSON' },
  { value: 'sort_batch', label: 'SORT BATCH', type: 'SORTING' },
  { value: 'audit_checkpoint', label: 'AUDIT CHECKPOINT', type: 'SCANNING & AUDIT' },
]

export function TaskPanel({ tasks, onJob, busy }: { tasks: Task[]; onJob?: (body: JobRequest) => Promise<JobResponse>; busy: boolean; onInject?: (body: { pickup: Point; dropoff: Point; urgency: number }) => void }) {
  const [jobType, setJobType] = useState<JobType>('fetch_item')
  const [itemId, setItemId] = useState('')
  const [zone, setZone] = useState('')
  const [urgency, setUrgency] = useState(3)
  const [jobs, setJobs] = useState<JobResponse[]>([])
  const [error, setError] = useState<string | null>(null)
  const selected = jobOptions.find(option => option.value === jobType) ?? jobOptions[0]

  const submit = async () => {
    const body: JobRequest = { job_type: jobType, urgency }
    if (jobType === 'fetch_item' && itemId.trim()) body.item_id = itemId.trim()
    if (jobType !== 'audit_checkpoint' && zone.trim()) body.zone = zone.trim()
    try {
      setError(null)
      const result = await (onJob ?? api.submitJob)(body)
      setJobs(previous => [result, ...previous].slice(0, 8))
    } catch (reason) {
      setError(reason instanceof ApiError && reason.status === 409 ? 'NO ROBOT AVAILABLE FOR THIS JOB TYPE' : reason instanceof Error ? reason.message : 'JOB SUBMISSION FAILED')
    }
  }

  const jobRows = jobs.map(job => ({ id: job.audit_id ?? job.task_id ?? `${job.job_type}-${job.robot_id}`, label: job.job_type === 'audit_checkpoint' ? 'AUDIT MISSION' : job.job_type.replace('_', ' ').toUpperCase(), status: job.status, robot: job.robot_id ?? 'PENDING', type: job.robot_type, audit: Boolean(job.audit_id) }))
  const taskRows = tasks.slice(-6).reverse().map(task => ({ id: task.task_id, label: 'TASK', status: task.status, robot: task.assigned_robot_id ?? 'PENDING', type: task.robot_type ?? 'DISPATCHED', audit: false }))

  return <section className="panel tasks-panel"><div className="panel-heading"><div><span className="eyebrow">MISSION QUEUE</span><h2>SUBMIT A JOB <em>{jobs.length + tasks.length}</em></h2></div><PackageCheck size={15} /></div><div className="job-type-grid">{jobOptions.map(option => <button type="button" key={option.value} className={jobType === option.value ? 'selected' : ''} onClick={() => setJobType(option.value)}><strong>{option.label}</strong><small>{option.type}</small></button>)}</div><form onSubmit={event => { event.preventDefault(); void submit() }}><label>JOB TYPE <select value={jobType} onChange={event => setJobType(event.target.value as JobType)}>{jobOptions.map(option => <option key={option.value} value={option.value}>{option.label} / {option.type}</option>)}</select></label>{jobType === 'fetch_item' && <label>ITEM ID <input value={itemId} placeholder="Optional SKU or item reference" onChange={event => setItemId(event.target.value)} /></label>}{jobType !== 'audit_checkpoint' && <label>ZONE <input value={zone} placeholder="Optional warehouse zone" onChange={event => setZone(event.target.value)} /></label>}<div className="urgency-line"><span>URGENCY</span>{[1, 2, 3, 4, 5].map(value => <button type="button" key={value} className={urgency === value ? 'selected' : ''} onClick={() => setUrgency(value)}>{value}</button>)}<button className="submit-button" disabled={busy} type="submit"><Send size={13} /> SUBMIT JOB</button></div>{error && <div className="job-error">{error}</div>}</form><div className="task-list">{[...jobRows, ...taskRows].slice(0, 8).map(row => <div className={`task-row ${row.audit ? 'audit-row' : ''}`} key={row.id}><span className="job-kind">{row.audit ? 'AUDIT' : 'JOB'}</span><div><strong>{row.label}</strong><small>{row.id} · {row.type}</small></div><span className={`task-status ${row.status.toLowerCase()}`}>{row.status}</span><small className="assigned-unit">{row.robot}</small></div>)}{jobs.length === 0 && tasks.length === 0 && <div className="empty-state">No active jobs</div>}</div></section>
}
