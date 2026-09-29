import { PackageCheck, Send, RotateCcw, AlertTriangle, ShieldCheck } from 'lucide-react'
import { useState, useMemo } from 'react'
import { ApiError } from '../api'
import type { JobRequest, JobResponse, JobType, Task, World, Point } from '../types'

interface JobOption {
  value: JobType
  label: string
  type: string
  robotClass: 'G2P' | 'SORTING' | 'AUDIT' | 'RELOCATE'
  description: string
}

const jobOptions: JobOption[] = [
  {
    value: 'fetch_item',
    label: 'G2P Shelf Fetch',
    type: 'Goods-to-Person AMR',
    robotClass: 'G2P',
    description: 'Transports entire shelf pod to station and optionally returns home',
  },
  {
    value: 'sort_batch',
    label: 'Sort Batch',
    type: 'Sorting AMR',
    robotClass: 'SORTING',
    description: 'Inducts parcels from entry gate and decants to destination chute',
  },
  {
    value: 'audit_checkpoint',
    label: 'Scan & Audit',
    type: 'Scanning & Audit AMR',
    robotClass: 'AUDIT',
    description: 'Perception scan of aisle shelves or checkpoint positions',
  },
  {
    value: 'relocate',
    label: 'Relocate / Charge',
    type: 'Fleet Relocation',
    robotClass: 'RELOCATE',
    description: 'Directs AMR to charging pad or designated maintenance slot',
  },
]

export function TaskPanel({
  tasks,
  onJob,
  busy,
  operatorRole = 'AUTHORITY',
  simulationRunning = true,
  world,
}: {
  tasks: Task[]
  onJob: (body: JobRequest) => Promise<JobResponse>
  busy: boolean
  operatorRole?: 'IMPORT' | 'EXPORT' | 'AUTHORITY'
  simulationRunning?: boolean
  world?: World | null
}) {
  const initialJob: JobType = operatorRole === 'EXPORT' ? 'sort_batch' : 'fetch_item'
  const [jobType, setJobType] = useState<JobType>(initialJob)
  const [urgency, setUrgency] = useState(3)
  const [error, setError] = useState<string | null>(null)
  const [jobs, setJobs] = useState<JobResponse[]>([])

  // G2P Fetch Fields
  const [selectedShelfId, setSelectedShelfId] = useState<string>('')
  const [pickCoord, setPickCoord] = useState<string>('')
  const [dropStation, setDropStation] = useState<string>('')
  const [returnToHome, setReturnToHome] = useState<boolean>(true)
  const [sku, setSku] = useState<string>('')
  const [quantity, setQuantity] = useState<number>(1)
  const [deadlineTicks, setDeadlineTicks] = useState<string>('')

  // Sorting AMR Fields
  const [sourceGate, setSourceGate] = useState<string>('')
  const [destinationChute, setDestinationChute] = useState<string>('')
  const [routeCode, setRouteCode] = useState<string>('ROUTE-WEST-01')

  // Audit Fields
  const [auditTarget, setAuditTarget] = useState<string>('')

  // Relocate Fields
  const [targetCharger, setTargetCharger] = useState<string>('')

  // Available shelves from loaded world
  const shelfList = useMemo(() => {
    if (!world?.pod_slots) return []
    return world.pod_slots
  }, [world])

  // Dropoff and pick stations
  const dropStationsList = useMemo(() => {
    const list: Array<{ id: string; label: string; point: Point }> = []
    if (world?.pick_stations) {
      world.pick_stations.forEach((ps) => {
        list.push({ id: ps.id, label: `Pick Station ${ps.id} (${ps.x}, ${ps.y})`, point: { x: ps.x, y: ps.y } })
      })
    }
    if (world?.dropoff_stations) {
      world.dropoff_stations.forEach((ds, idx) => {
        list.push({ id: `DROPOFF-${idx + 1}`, label: `Drop Station ${idx + 1} (${ds.x}, ${ds.y})`, point: ds })
      })
    }
    return list
  }, [world])

  // Gates and chutes
  const entryGatesList = useMemo(() => {
    if (!world?.entry_gates) return []
    return world.entry_gates
  }, [world])

  const chutesList = useMemo(() => {
    if (!world?.sortation_chutes) return []
    return Object.entries(world.sortation_chutes).map(([id, c]) => ({
      id,
      zone: c.destination_zone,
      x: c.x,
      y: c.y,
    }))
  }, [world])

  const chargersList = useMemo(() => {
    if (!world?.charging_stations) return []
    return world.charging_stations
  }, [world])

  // Handle shelf selection
  const handleShelfChange = (shelfId: string) => {
    setSelectedShelfId(shelfId)
    const slot = shelfList.find((s) => s.shelf_id === shelfId)
    if (slot) {
      setPickCoord(`${slot.x},${slot.y}`)
    }
  }

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
      if (selectedShelfId) {
        body.shelf_id = selectedShelfId
      }
      if (pickCoord) {
        const parts = pickCoord.split(',').map((p) => parseInt(p.trim()))
        if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
          body.pickup = { x: parts[0], y: parts[1] }
        }
      }
      if (dropStation) {
        const found = dropStationsList.find((ds) => ds.id === dropStation)
        if (found) {
          body.dropoff = found.point
        } else {
          const parts = dropStation.split(',').map((p) => parseInt(p.trim()))
          if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
            body.dropoff = { x: parts[0], y: parts[1] }
          }
        }
      }
      body.return_to_home = returnToHome
      const targetSku = sku.trim()
      if (targetSku) {
        body.sku = targetSku
        body.item_id = targetSku
      }
      body.quantity = Math.max(1, quantity)
    } else if (jobType === 'sort_batch') {
      if (sourceGate) body.source_gate = sourceGate
      if (destinationChute) body.destination_chute = destinationChute
      if (routeCode) body.route_code = routeCode
    } else if (jobType === 'audit_checkpoint') {
      if (auditTarget) {
        if (auditTarget.startsWith('POD-')) {
          body.shelf_id = auditTarget
        } else {
          const parts = auditTarget.split(',').map((p) => parseInt(p.trim()))
          if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
            body.checkpoint = { x: parts[0], y: parts[1] }
          }
        }
      }
    } else if (jobType === 'relocate') {
      if (targetCharger) {
        const parts = targetCharger.split(',').map((p) => parseInt(p.trim()))
        if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
          body.dropoff = { x: parts[0], y: parts[1] }
        }
      }
    }

    try {
      setError(null)
      const result = await onJob(body)
      setJobs((prev) => [result, ...prev].slice(0, 10))
    } catch (reason) {
      setError(
        reason instanceof ApiError && reason.status === 404
          ? `Resource not found on map: ${reason.message}`
          : reason instanceof ApiError && reason.status === 409
          ? 'No eligible robot available for this task type'
          : reason instanceof Error
          ? reason.message
          : 'Task announcement failed'
      )
    }
  }

  // Combined live rows from server tasks and recently injected jobs
  const combinedTasks = useMemo(() => {
    const taskMap = new Map<string, any>()
    tasks.forEach((t) => {
      taskMap.set(t.task_id, {
        id: t.task_id,
        type: t.task_type || 'STANDARD',
        route: `(${t.pickup.x},${t.pickup.y}) → (${t.dropoff.x},${t.dropoff.y})`,
        target: t.target_shelf_id ? `Shelf ${t.target_shelf_id}` : '',
        robot: t.assigned_robot_id ?? (t.status === 'UNCLAIMED' ? 'No Bids' : 'Bidding...'),
        status: t.status,
        returnHome: t.return_to_home ?? false,
        lease: t.lease_expires_tick,
        unclaimedReason: t.unclaimed_reason,
      })
    })

    jobs.forEach((j) => {
      const tid = j.task_id || j.audit_id
      if (tid && !taskMap.has(tid)) {
        taskMap.set(tid, {
          id: tid,
          type: j.job_type,
          route: j.target_shelf_id ? `Shelf ${j.target_shelf_id}` : 'Injected',
          target: j.target_shelf_id ? `Shelf ${j.target_shelf_id}` : '',
          robot: j.robot_id ?? 'Bidding...',
          status: j.status || 'ANNOUNCED',
          returnHome: false,
        })
      }
    })

    return Array.from(taskMap.values()).slice(-10).reverse()
  }, [tasks, jobs])

  return (
    <section className="panel tasks-panel">
      <div className="panel-heading">
        <h2>
          Task Injection <em>{tasks.length}</em>
        </h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span
            style={{
              fontSize: '10px',
              fontWeight: 700,
              padding: '2px 6px',
              borderRadius: '4px',
              background:
                operatorRole === 'IMPORT'
                  ? 'rgba(16,185,129,0.2)'
                  : operatorRole === 'EXPORT'
                  ? 'rgba(56,189,248,0.2)'
                  : 'rgba(168,85,247,0.2)',
              color:
                operatorRole === 'IMPORT'
                  ? '#34d399'
                  : operatorRole === 'EXPORT'
                  ? '#38bdf8'
                  : '#c084fc',
            }}
          >
            {operatorRole} ROLE
          </span>
          <PackageCheck size={16} />
        </div>
      </div>

      {/* Class-adaptive task type selector */}
      <div className="job-type-grid" style={{ gridTemplateColumns: 'repeat(2, 1fr)', gap: '6px', marginBottom: '10px' }}>
        {jobOptions.map((option) => {
          const disabled = isOptionDisabled(option.value)
          return (
            <button
              type="button"
              key={option.value}
              className={`${jobType === option.value ? 'selected' : ''} ${disabled ? 'opacity-40 cursor-not-allowed' : ''}`}
              onClick={() => !disabled && setJobType(option.value)}
              disabled={disabled}
              title={disabled ? `Restricted to ${option.value === 'sort_batch' ? 'Export' : 'Import'} Station` : option.description}
              style={{ textAlign: 'left', padding: '6px 8px' }}
            >
              <strong style={{ display: 'block', fontSize: '11px' }}>{option.label}</strong>
              <small style={{ fontSize: '9px', opacity: 0.75 }}>{disabled ? '(Scope Restricted)' : option.type}</small>
            </button>
          )
        })}
      </div>

      {!simulationRunning && (
        <div
          style={{
            padding: '8px 12px',
            margin: '0 0 10px 0',
            borderRadius: 6,
            backgroundColor: 'rgba(234, 179, 8, 0.1)',
            border: '1px solid rgba(234, 179, 8, 0.25)',
            color: '#eab308',
            fontSize: '11px',
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}
        >
          <AlertTriangle size={14} />
          <span>Fleet ARMED — waiting for start. Click <strong>Start</strong> in top bar to dispatch tasks.</span>
        </div>
      )}

      {/* Adaptive Task Creation Form */}
      <form
        style={{
          opacity: simulationRunning ? 1 : 0.45,
          pointerEvents: simulationRunning ? 'auto' : 'none',
          display: 'flex',
          flexDirection: 'column',
          gap: '8px',
        }}
        onSubmit={(event) => {
          event.preventDefault()
          if (simulationRunning) void submit()
        }}
      >
        {/* 1. G2P Fetch Form */}
        {jobType === 'fetch_item' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
              <label style={{ fontSize: '11px' }}>
                Target Shelf Pod
                <select
                  value={selectedShelfId}
                  onChange={(e) => handleShelfChange(e.target.value)}
                  style={{ width: '100%', marginTop: '3px', fontSize: '11px' }}
                >
                  <option value="">-- Choose Shelf --</option>
                  {shelfList.slice(0, 50).map((s) => (
                    <option key={s.shelf_id} value={s.shelf_id}>
                      {s.shelf_id} ({s.x}, {s.y})
                    </option>
                  ))}
                </select>
              </label>

              <label style={{ fontSize: '11px' }}>
                Pick Location (x, y)
                <input
                  value={pickCoord}
                  placeholder="e.g. 5, 10"
                  onChange={(e) => setPickCoord(e.target.value)}
                  style={{ width: '100%', marginTop: '3px', fontSize: '11px' }}
                />
              </label>
            </div>

            <label style={{ fontSize: '11px' }}>
              Drop Station (Pick Station / Buffer)
              <select
                value={dropStation}
                onChange={(e) => setDropStation(e.target.value)}
                style={{ width: '100%', marginTop: '3px', fontSize: '11px' }}
              >
                <option value="">-- Default Drop Station --</option>
                {dropStationsList.map((ds) => (
                  <option key={ds.id} value={ds.id}>
                    {ds.label}
                  </option>
                ))}
              </select>
            </label>

            <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '6px' }}>
              <label style={{ fontSize: '11px' }}>
                SKU (optional)
                <input
                  value={sku}
                  placeholder="e.g. SKU-A"
                  onChange={(e) => setSku(e.target.value)}
                  style={{ width: '100%', marginTop: '3px', fontSize: '11px' }}
                />
              </label>
              <label style={{ fontSize: '11px' }}>
                Qty
                <input
                  type="number"
                  min="1"
                  max="100"
                  value={quantity}
                  onChange={(e) => setQuantity(Math.max(1, parseInt(e.target.value) || 1))}
                  style={{ width: '100%', marginTop: '3px', fontSize: '11px' }}
                />
              </label>
            </div>

            <label
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                fontSize: '11px',
                cursor: 'pointer',
                backgroundColor: 'rgba(56, 189, 248, 0.08)',
                padding: '6px 8px',
                borderRadius: '4px',
                border: '1px solid rgba(56, 189, 248, 0.2)',
              }}
            >
              <input
                type="checkbox"
                checked={returnToHome}
                onChange={(e) => setReturnToHome(e.target.checked)}
              />
              <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                <RotateCcw size={12} color="#38bdf8" />
                <strong>Return shelf to home slot</strong>
              </span>
            </label>
          </div>
        )}

        {/* 2. Sorting AMR Form */}
        {jobType === 'sort_batch' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
              <label style={{ fontSize: '11px' }}>
                Source Gate
                <select
                  value={sourceGate}
                  onChange={(e) => setSourceGate(e.target.value)}
                  style={{ width: '100%', marginTop: '3px', fontSize: '11px' }}
                >
                  <option value="">-- Choose Gate --</option>
                  {entryGatesList.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.id} ({g.cells[0]?.x}, {g.cells[0]?.y})
                    </option>
                  ))}
                </select>
              </label>

              <label style={{ fontSize: '11px' }}>
                Parcel / Route Code
                <input
                  value={routeCode}
                  placeholder="e.g. ROUTE-EAST-01"
                  onChange={(e) => setRouteCode(e.target.value)}
                  style={{ width: '100%', marginTop: '3px', fontSize: '11px' }}
                />
              </label>
            </div>

            <label style={{ fontSize: '11px' }}>
              Destination Chute
              <select
                value={destinationChute}
                onChange={(e) => setDestinationChute(e.target.value)}
                style={{ width: '100%', marginTop: '3px', fontSize: '11px' }}
              >
                <option value="">-- Choose Chute --</option>
                {chutesList.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.id} ({c.zone} at {c.x}, {c.y})
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}

        {/* 3. Audit AMR Form */}
        {jobType === 'audit_checkpoint' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <label style={{ fontSize: '11px' }}>
              Shelf / Checkpoint to Scan
              <select
                value={auditTarget}
                onChange={(e) => setAuditTarget(e.target.value)}
                style={{ width: '100%', marginTop: '3px', fontSize: '11px' }}
              >
                <option value="">-- Choose Audit Target --</option>
                {shelfList.slice(0, 40).map((s) => (
                  <option key={s.shelf_id} value={s.shelf_id}>
                    Shelf {s.shelf_id} ({s.x}, {s.y})
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}

        {/* 4. Relocate / Charge Form */}
        {jobType === 'relocate' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <label style={{ fontSize: '11px' }}>
              Target Charging Station
              <select
                value={targetCharger}
                onChange={(e) => setTargetCharger(e.target.value)}
                style={{ width: '100%', marginTop: '3px', fontSize: '11px' }}
              >
                <option value="">-- Choose Charger --</option>
                {chargersList.map((c, idx) => (
                  <option key={idx} value={`${c.x},${c.y}`}>
                    Charger {idx + 1} ({c.x}, {c.y})
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}

        {/* Urgency and Submit Button */}
        <div className="urgency-line" style={{ marginTop: '4px' }}>
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
            <Send size={13} /> Inject Task
          </button>
        </div>

        {error && <div className="job-error">{error}</div>}
      </form>

      {/* Live Decentralized Task Feed */}
      <div className="task-list" style={{ marginTop: '10px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
          <strong style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.04em', opacity: 0.8 }}>
            Decentralized Mission Queue
          </strong>
          <span style={{ fontSize: '10px', opacity: 0.6 }}>Contract-Net Bidding</span>
        </div>

        {combinedTasks.map((row) => (
          <div className="task-row" key={row.id} style={{ display: 'flex', flexDirection: 'column', gap: '4px', padding: '6px 8px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span className="job-kind" style={{ fontSize: '10px' }}>
                {row.type}
              </span>
              <span className={`state-badge ${row.status.toLowerCase()}`} style={{ fontSize: '10px' }}>
                {row.status}
              </span>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ fontSize: '11px' }}>
                <strong>{row.id}</strong> {row.target && <small>({row.target})</small>}
              </div>
              <small className="assigned-unit" style={{ fontSize: '10px', fontWeight: 600 }}>
                {row.robot}
              </small>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '10px', opacity: 0.75 }}>
              <span>{row.route}</span>
              {row.returnHome && (
                <span style={{ color: '#38bdf8', display: 'flex', alignItems: 'center', gap: '2px' }}>
                  <RotateCcw size={10} /> Home Return
                </span>
              )}
            </div>

            {row.status === 'UNCLAIMED' && row.unclaimedReason && (
              <div style={{ fontSize: '9px', color: '#f59e0b', marginTop: '2px' }}>
                ⚠ {row.unclaimedReason} (Re-announcing...)
              </div>
            )}
          </div>
        ))}

        {combinedTasks.length === 0 && <div className="empty-state">No active decentralized jobs</div>}
      </div>
    </section>
  )
}
