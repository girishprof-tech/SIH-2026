import { PackageCheck, Send } from 'lucide-react'
import { useState } from 'react'
import { ApiError } from '../api'
import type { JobRequest, JobResponse, JobType, Task } from '../types'

const jobOptions: Array<{ value: JobType; label: string; type: string }> = [
  { value: 'fetch_item', label: 'Fetch item', type: 'Goods to person' },
  { value: 'sort_batch', label: 'Sort batch', type: 'Sorting' },
  { value: 'audit_checkpoint', label: 'Audit checkpoint', type: 'Scanning & audit' },
]

export function TaskPanel({
  tasks,
  onJob,
  busy,
  operatorRole = 'AUTHORITY',
}: {
  tasks: Task[]
  onJob: (body: JobRequest) => Promise<JobResponse>
  busy: boolean
  operatorRole?: 'IMPORT' | 'EXPORT' | 'AUTHORITY'
}) {
  const initialJob: JobType = operatorRole === 'EXPORT' ? 'sort_batch' : 'fetch_item'
  const [jobType, setJobType] = useState<JobType>(initialJob)
  const [itemId, setItemId] = useState('')
  const [sku, setSku] = useState('')
  const [quantity, setQuantity] = useState(1)
  const [zone, setZone] = useState('')
  const [urgency, setUrgency] = useState(3)
  const [jobs, setJobs] = useState<JobResponse[]>([])
  const [error, setError] = useState<string | null>(null)

  const isOptionDisabled = (value: JobType) => {
    if (operatorRole === 'IMPORT' && value === 'sort_batch') return true
    if (operatorRole === 'EXPORT' && value === 'fetch_item') return true
    return false
  }

  const submit = async () => {
    if (isOptionDisabled(jobType)) {
      setError(`STATION_AUTHORITY_VIOLATION: ${jobType} is outside the command scope of ${operatorRole} role.`)
      return
    }
    const body: JobRequest = { job_type: jobType, urgency }
    if (jobType === 'fetch_item') {
      const targetSku = sku.trim() || itemId.trim()
      if (targetSku) {
        body.sku = targetSku
        body.item_id = targetSku
      }
      body.quantity = Math.max(1, quantity)
    }
    if (jobType !== 'audit_checkpoint' && zone.trim()) body.zone = zone.trim()
    try {
      setError(null)
      const result = await onJob(body)
      setJobs((previous) => [result, ...previous].slice(0, 8))
    } catch (reason) {
      setError(
        reason instanceof ApiError && reason.status === 404
          ? `SKU not found: ${reason.message}`
          : reason instanceof ApiError && reason.status === 409
          ? 'No robot available for this job type'
          : reason instanceof Error
          ? reason.message
          : 'Job submission failed'
      )
    }
  }

  const jobRows = jobs.map((job) => ({
    id: job.audit_id ?? job.task_id ?? `${job.job_type}-${job.robot_id}`,
    label:
      job.job_type === 'audit_checkpoint'
        ? 'Audit mission'
        : job.job_type === 'fetch_item'
        ? 'Fetch item'
        : 'Sort batch',
    status: job.status,
    robot: job.robot_id ?? 'Pending',
    type: job.robot_type,
    audit: Boolean(job.audit_id),
  }))

  const taskRows = tasks
    .slice(-6)
    .reverse()
    .map((task) => ({
      id: task.task_id,
      label: 'Task',
      status: task.status,
      robot: task.assigned_robot_id ?? 'Pending',
      type: task.robot_type ?? 'Dispatched',
      audit: false,
    }))

  return (
    <section className="panel tasks-panel">
      <div className="panel-heading">
        <h2>
          Submit job <em>{jobs.length + tasks.length}</em>
        </h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span style={{ fontSize: '10px', fontWeight: 700, padding: '2px 6px', borderRadius: '4px', background: operatorRole === 'IMPORT' ? 'rgba(16,185,129,0.2)' : operatorRole === 'EXPORT' ? 'rgba(56,189,248,0.2)' : 'rgba(168,85,247,0.2)', color: operatorRole === 'IMPORT' ? '#34d399' : operatorRole === 'EXPORT' ? '#38bdf8' : '#c084fc' }}>
            {operatorRole}
          </span>
          <PackageCheck size={16} />
        </div>
      </div>

      <div className="job-type-grid">
        {jobOptions.map((option) => {
          const disabled = isOptionDisabled(option.value)
          return (
            <button
              type="button"
              key={option.value}
              className={`${jobType === option.value ? 'selected' : ''} ${disabled ? 'opacity-40 cursor-not-allowed' : ''}`}
              onClick={() => !disabled && setJobType(option.value)}
              disabled={disabled}
              title={disabled ? `Restricted to ${option.value === 'sort_batch' ? 'Export' : 'Import'} Station` : undefined}
            >
              <strong>{option.label}</strong>
              <small>{disabled ? '(Scope Restricted)' : option.type}</small>
            </button>
          )
        })}
      </div>

      <form
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <label>
          Job type
          <select
            value={jobType}
            onChange={(event) => {
              const val = event.target.value as JobType
              if (!isOptionDisabled(val)) setJobType(val)
            }}
          >
            {jobOptions.map((option) => {
              const disabled = isOptionDisabled(option.value)
              return (
                <option key={option.value} value={option.value} disabled={disabled}>
                  {option.label} ({option.type}){disabled ? ' — RESTRICTED' : ''}
                </option>
              )
            })}
          </select>
        </label>

        {jobType === 'fetch_item' && (
          <div className="fetch-item-inputs" style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '0.5rem' }}>
            <label>
              SKU to Retrieve
              <input
                value={sku}
                placeholder="e.g. SKU-A, SKU-B"
                onChange={(event) => setSku(event.target.value)}
              />
            </label>
            <label>
              Quantity
              <input
                type="number"
                min="1"
                max="50"
                value={quantity}
                onChange={(event) => setQuantity(Math.max(1, parseInt(event.target.value) || 1))}
              />
            </label>
          </div>
        )}

        {jobType !== 'audit_checkpoint' && (
          <label>
            Zone
            <input
              value={zone}
              placeholder="Optional warehouse zone"
              onChange={(event) => setZone(event.target.value)}
            />
          </label>
        )}

        <div className="urgency-line">
          <span>Urgency</span>
          {[1, 2, 3, 4, 5].map((value) => (
            <button
              type="button"
              key={value}
              className={urgency === value ? 'selected' : ''}
              onClick={() => setUrgency(value)}
            >
              {value}
            </button>
          ))}
          <button className="submit-button" disabled={busy} type="submit">
            <Send size={13} /> Submit job
          </button>
        </div>

        {error && <div className="job-error">{error}</div>}
      </form>

      <div className="task-list">
        {[...jobRows, ...taskRows].slice(0, 8).map((row) => (
          <div className={`task-row ${row.audit ? 'audit-row' : ''}`} key={row.id}>
            <span className="job-kind">{row.audit ? 'Audit' : 'Job'}</span>
            <div>
              <strong>{row.label}</strong>
              <small>
                {row.id}, {row.type}
              </small>
            </div>
            <span className={`state-badge ${row.status.toLowerCase()}`}>{row.status}</span>
            <small className="assigned-unit">{row.robot}</small>
          </div>
        ))}
        {jobs.length === 0 && tasks.length === 0 && <div className="empty-state">No active jobs</div>}
      </div>
    </section>
  )
}
