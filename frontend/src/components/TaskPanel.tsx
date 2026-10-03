import React, { useState, useEffect, useMemo } from 'react'
import {
  ShoppingCart,
  Send,
  AlertTriangle,
  Boxes,
  ShieldCheck,
  CheckCircle2,
  Clock,
  ChevronRight,
  RotateCcw,
  Sparkles,
} from 'lucide-react'
import { api, ApiError } from '../api'
import type { JobRequest, JobResponse, World, CatalogProduct, OrderInfo, OrderStage } from '../types'

interface TaskPanelProps {
  tasks: any[]
  orders?: OrderInfo[]
  onOrderPlaced?: () => void
  onJob: (body: JobRequest) => Promise<JobResponse>
  busy: boolean
  operatorRole?: 'IMPORT' | 'EXPORT' | 'AUTHORITY'
  simulationRunning?: boolean
  world?: World | null
}

const ORDER_STAGES: OrderStage[] = [
  'Announced',
  'G2P assigned',
  'Pod lifted',
  'At pick station',
  'Sorting assigned',
  'In chute',
  'Shipped',
]

export function TaskPanel({
  tasks,
  orders = [],
  onOrderPlaced,
  onJob,
  busy,
  operatorRole = 'AUTHORITY',
  simulationRunning = true,
  world,
}: TaskPanelProps) {
  const [activeTab, setActiveTab] = useState<'order' | 'audit'>('order')
  const [catalog, setCatalog] = useState<CatalogProduct[]>([])
  const [loadingCatalog, setLoadingCatalog] = useState<boolean>(true)

  // Order Form State
  const [selectedSku, setSelectedSku] = useState<string>('')
  const [quantity, setQuantity] = useState<number>(1)
  const [destinationGate, setDestinationGate] = useState<string>('OUT-1')
  const [urgency, setUrgency] = useState<number>(3)
  const [error, setError] = useState<string | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState<boolean>(false)

  // Audit Form State
  const [selectedShelf, setSelectedShelf] = useState<string>('')
  const [auditUrgency, setAuditUrgency] = useState<number>(2)

  // Load Catalog without aggressive flickering
  useEffect(() => {
    let mounted = true
    if (catalog.length === 0) {
      setLoadingCatalog(true)
    }
    api
      .catalog()
      .then((data) => {
        if (!mounted) return
        setCatalog(data)
        if (data.length > 0 && !selectedSku) {
          const firstInStock = data.find((p) => p.total_stock > 0) || data[0]
          setSelectedSku(firstInStock.sku)
        }
      })
      .catch((e) => {
        console.error('Failed to load catalog:', e)
      })
      .finally(() => {
        if (mounted) setLoadingCatalog(false)
      })
    return () => {
      mounted = false
    }
  }, [world?.width, world?.height])

  // Destination gates from world
  const exitGates = useMemo(() => {
    if (!world?.exit_gates || world.exit_gates.length === 0) {
      return [{ id: 'OUT-1' }, { id: 'OUT-2' }, { id: 'OUT-3' }]
    }
    return world.exit_gates
  }, [world])

  useEffect(() => {
    if (exitGates.length > 0 && !exitGates.some((g) => g.id === destinationGate)) {
      setDestinationGate(exitGates[0].id)
    }
  }, [exitGates, destinationGate])

  // Shelves for Audit
  const shelfList = useMemo(() => {
    if (!world?.pod_slots) return []
    return world.pod_slots
  }, [world])

  useEffect(() => {
    if (shelfList.length > 0 && !selectedShelf) {
      setSelectedShelf(shelfList[0].shelf_id)
    }
  }, [shelfList, selectedShelf])

  const currentProduct = useMemo(() => {
    return catalog.find((p) => p.sku === selectedSku)
  }, [catalog, selectedSku])

  const maxAvailable = currentProduct ? currentProduct.total_stock : 1

  const handleOrderSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedSku) {
      setError('Please select a product from the catalog.')
      return
    }
    if (quantity < 1) {
      setError('Quantity must be at least 1.')
      return
    }
    if (currentProduct && quantity > currentProduct.total_stock) {
      setError(`Cannot order ${quantity} units: only ${currentProduct.total_stock} available in warehouse.`)
      return
    }

    setSubmitting(true)
    setError(null)
    setSuccessMsg(null)

    try {
      if (!simulationRunning) {
        api.simulation('start').catch(() => {})
      }
      const res = await api.createOrder({
        sku: selectedSku,
        quantity,
        destination_gate: destinationGate,
        urgency,
      })
      setSuccessMsg(`Order ${res.order_id || res.task_id} placed! Autonomous G2P & Sorting AMRs dispatched.`)
      // Refresh catalog stock and immediate orders
      api.catalog().then(setCatalog).catch(() => {})
      if (onOrderPlaced) {
        onOrderPlaced()
      }
    } catch (err: any) {
      setError(err instanceof ApiError ? err.message : err?.message || 'Failed to create order.')
    } finally {
      setSubmitting(false)
    }
  }

  const handleAuditSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedShelf) {
      setError('Please select a shelf to audit.')
      return
    }

    setSubmitting(true)
    setError(null)
    setSuccessMsg(null)

    try {
      if (!simulationRunning) {
        api.simulation('start').catch(() => {})
      }
      const res = await onJob({
        job_type: 'audit_checkpoint',
        shelf_id: selectedShelf,
        urgency: auditUrgency,
      })
      setSuccessMsg(`Audit mission dispatched for shelf ${selectedShelf}.`)
    } catch (err: any) {
      setError(err instanceof ApiError ? err.message : err?.message || 'Failed to dispatch audit.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="panel tasks-panel" style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      <div className="panel-heading" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <ShoppingCart size={16} style={{ color: 'var(--accent-primary)' }} />
          <h2 style={{ margin: 0, fontSize: '13px', fontWeight: 700 }}>Dispatch Orders</h2>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span
            style={{
              fontSize: '10px',
              fontWeight: 700,
              padding: '2px 6px',
              borderRadius: '4px',
              background: 'var(--accent-subtle)',
              color: 'var(--accent-text)',
              border: '1px solid var(--accent-border)',
            }}
          >
            ORDER-DRIVEN
          </span>
        </div>
      </div>

      {/* Mode Selector Tabs */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px', marginBottom: '4px' }}>
        <button
          type="button"
          className={`side-tab-btn ${activeTab === 'order' ? 'active' : ''}`}
          onClick={() => {
            setActiveTab('order')
            setError(null)
            setSuccessMsg(null)
          }}
          style={{
            padding: '6px 8px',
            borderRadius: '6px',
            border: activeTab === 'order' ? '1px solid var(--accent-primary)' : '1px solid var(--border-subtle)',
            background: activeTab === 'order' ? 'var(--accent-subtle)' : 'var(--bg-surface-subtle)',
            color: activeTab === 'order' ? 'var(--accent-primary)' : 'var(--text-secondary)',
            fontWeight: 600,
            fontSize: '11px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '6px',
            cursor: 'pointer',
          }}
        >
          <Boxes size={13} />
          <span>Customer Order</span>
        </button>

        <button
          type="button"
          className={`side-tab-btn ${activeTab === 'audit' ? 'active' : ''}`}
          onClick={() => {
            setActiveTab('audit')
            setError(null)
            setSuccessMsg(null)
          }}
          style={{
            padding: '6px 8px',
            borderRadius: '6px',
            border: activeTab === 'audit' ? '1px solid #D97706' : '1px solid var(--border-subtle)',
            background: activeTab === 'audit' ? 'rgba(217, 119, 6, 0.12)' : 'var(--bg-surface-subtle)',
            color: activeTab === 'audit' ? '#B45309' : 'var(--text-secondary)',
            fontWeight: 600,
            fontSize: '11px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '6px',
            cursor: 'pointer',
          }}
        >
          <ShieldCheck size={13} />
          <span>Scan & Audit</span>
        </button>
      </div>

      {!simulationRunning && (
        <div
          style={{
            padding: '6px 10px',
            borderRadius: '6px',
            backgroundColor: 'rgba(234, 179, 8, 0.1)',
            border: '1px solid rgba(234, 179, 8, 0.25)',
            color: '#eab308',
            fontSize: '11px',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
          }}
        >
          <AlertTriangle size={13} />
          <span>Fleet ARMED — zero motion until started. Click <strong>Start</strong> in top bar.</span>
        </div>
      )}

      {/* 1. Customer Order Form */}
      {activeTab === 'order' && (
        <form
          onSubmit={handleOrderSubmit}
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
            opacity: 1,
            pointerEvents: 'auto',
          }}
        >
          {/* Product Dropdown */}
          <label style={{ fontSize: '11px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>Product SKU</span>
            <select
              value={selectedSku}
              onChange={(e) => {
                setSelectedSku(e.target.value)
                setQuantity(1)
              }}
              disabled={submitting || busy}
              style={{
                padding: '7px 9px',
                borderRadius: '6px',
                border: '1px solid var(--border-subtle)',
                background: 'var(--bg-surface)',
                color: 'var(--text-primary)',
                fontSize: '12px',
                outline: 'none',
                boxShadow: '0 1px 2px rgba(0,0,0,0.03)',
              }}
            >
              {catalog.map((p) => (
                <option key={p.sku} value={p.sku} disabled={p.total_stock <= 0}>
                  {p.name} ({p.sku}) — {p.total_stock > 0 ? `${p.total_stock} in stock` : 'OUT OF STOCK'}
                </option>
              ))}
            </select>
          </label>

          {/* Quantity & Destination Gate */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.5fr', gap: '8px' }}>
            <label style={{ fontSize: '11px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>
                Qty <small style={{ opacity: 0.7 }}>(Max {maxAvailable})</small>
              </span>
              <input
                type="number"
                min="1"
                max={Math.max(1, maxAvailable)}
                value={quantity}
                onChange={(e) => setQuantity(Math.max(1, parseInt(e.target.value) || 1))}
                disabled={busy || submitting}
                style={{
                  padding: '7px 9px',
                  borderRadius: '6px',
                  border: '1px solid var(--border-subtle)',
                  background: 'var(--bg-surface)',
                  color: 'var(--text-primary)',
                  fontSize: '12px',
                  outline: 'none',
                  boxShadow: '0 1px 2px rgba(0,0,0,0.03)',
                }}
              />
            </label>

            <label style={{ fontSize: '11px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>Destination Exit Gate</span>
              <select
                value={destinationGate}
                onChange={(e) => setDestinationGate(e.target.value)}
                disabled={busy || submitting}
                style={{
                  padding: '7px 9px',
                  borderRadius: '6px',
                  border: '1px solid var(--border-subtle)',
                  background: 'var(--bg-surface)',
                  color: 'var(--text-primary)',
                  fontSize: '12px',
                  outline: 'none',
                  boxShadow: '0 1px 2px rgba(0,0,0,0.03)',
                }}
              >
                {exitGates.map((g) => (
                  <option key={g.id} value={g.id}>
                    Gate {g.id}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {/* Urgency Selector */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: '2px' }}>
            <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)' }}>Urgency</span>
            <div style={{ display: 'flex', gap: '4px' }}>
              {[1, 2, 3, 4, 5].map((lvl) => {
                const isSelected = urgency === lvl
                return (
                  <button
                    type="button"
                    key={lvl}
                    onClick={() => setUrgency(lvl)}
                    style={{
                      width: '28px',
                      height: '26px',
                      padding: 0,
                      borderRadius: '5px',
                      fontSize: '11px',
                      fontWeight: isSelected ? 700 : 500,
                      border: isSelected ? '1px solid #18181B' : '1px solid var(--border-subtle)',
                      background: isSelected ? '#18181B' : 'var(--bg-surface-subtle)',
                      color: isSelected ? '#FFFFFF' : 'var(--text-secondary)',
                      cursor: 'pointer',
                      transition: 'all 0.15s ease',
                    }}
                  >
                    {lvl}
                  </button>
                )
              })}
            </div>
          </div>

          {/* Submit Button */}
          <button
            type="submit"
            className="submit-button"
            disabled={busy || submitting || !selectedSku || (currentProduct && currentProduct.total_stock <= 0)}
            style={{
              marginTop: '4px',
              padding: '9px',
              background: 'var(--accent-primary, #FF6B35)',
              color: '#ffffff',
              border: 'none',
              borderRadius: '6px',
              fontWeight: 600,
              fontSize: '12px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '6px',
              cursor: 'pointer',
              transition: 'opacity 0.15s ease',
            }}
          >
            <Send size={13} />
            <span>{submitting ? 'Placing Order...' : 'Place Customer Order'}</span>
          </button>
        </form>
      )}

      {/* 2. Scan & Audit Form */}
      {activeTab === 'audit' && (
        <form
          onSubmit={handleAuditSubmit}
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
            opacity: 1,
            pointerEvents: 'auto',
          }}
        >
          <label style={{ fontSize: '11px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>Shelf Pod to Scan</span>
            <select
              value={selectedShelf}
              onChange={(e) => setSelectedShelf(e.target.value)}
              disabled={busy || submitting}
              style={{
                padding: '7px 9px',
                borderRadius: '6px',
                border: '1px solid var(--border-subtle)',
                background: 'var(--bg-surface)',
                color: 'var(--text-primary)',
                fontSize: '12px',
                outline: 'none',
                boxShadow: '0 1px 2px rgba(0,0,0,0.03)',
              }}
            >
              {shelfList.map((s) => (
                <option key={s.shelf_id} value={s.shelf_id}>
                  Shelf {s.shelf_id} ({s.x}, {s.y})
                </option>
              ))}
            </select>
          </label>

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: '2px' }}>
            <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)' }}>Scan Priority</span>
            <div style={{ display: 'flex', gap: '4px' }}>
              {[1, 2, 3, 4, 5].map((lvl) => (
                <button
                  type="button"
                  key={lvl}
                  onClick={() => setAuditUrgency(lvl)}
                  style={{
                    width: '28px',
                    height: '26px',
                    padding: 0,
                    borderRadius: '5px',
                    fontSize: '11px',
                    fontWeight: auditUrgency === lvl ? 700 : 500,
                    border: auditUrgency === lvl ? '1px solid #18181B' : '1px solid var(--border-subtle)',
                    background: auditUrgency === lvl ? '#18181B' : 'var(--bg-surface-subtle)',
                    color: auditUrgency === lvl ? '#FFFFFF' : 'var(--text-secondary)',
                    cursor: 'pointer',
                    transition: 'all 0.15s ease',
                  }}
                >
                  {lvl}
                </button>
              ))}
            </div>
          </div>

          <button
            type="submit"
            className="submit-button"
            disabled={busy || submitting || !selectedShelf}
            style={{
              marginTop: '4px',
              padding: '9px',
              background: '#18181B',
              color: '#ffffff',
              border: 'none',
              borderRadius: '6px',
              fontWeight: 600,
              fontSize: '12px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '6px',
              cursor: 'pointer',
              transition: 'opacity 0.15s ease',
            }}
          >
            <ShieldCheck size={13} />
            <span>{submitting ? 'Dispatching...' : 'Dispatch Audit Scan'}</span>
          </button>
        </form>
      )}

      {/* Error & Success Feedback */}
      {error && (
        <div
          style={{
            backgroundColor: '#FAFAFA',
            border: '1px solid #E4E4E7',
            borderLeft: '3px solid #DC2626',
            borderRadius: '6px',
            padding: '7px 10px',
            color: '#18181B',
            fontSize: '11px',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
          }}
        >
          <AlertTriangle size={14} style={{ color: '#DC2626', flexShrink: 0 }} />
          <span>{error}</span>
        </div>
      )}

      {successMsg && (
        <div
          style={{
            backgroundColor: '#FAFAFA',
            border: '1px solid #E4E4E7',
            borderLeft: '3px solid #16A34A',
            borderRadius: '6px',
            padding: '7px 10px',
            color: '#18181B',
            fontSize: '11px',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
          }}
        >
          <CheckCircle2 size={14} style={{ color: '#16A34A', flexShrink: 0 }} />
          <span>{successMsg}</span>
        </div>
      )}

      {/* Recent Orders Progress Summary */}
      <div style={{ marginTop: '8px', borderTop: '1px solid var(--border-subtle)', paddingTop: '8px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
          <strong style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-secondary)' }}>
            Live Order Pipeline ({orders.length})
          </strong>
          <span style={{ fontSize: '10px', color: 'var(--text-muted)' }}>Decentralized Dispatch</span>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '200px', overflowY: 'auto' }}>
          {orders.slice(0, 5).map((order) => {
            const isShipped = order.stage === 'Shipped'
            return (
              <div
                key={order.order_id}
                style={{
                  backgroundColor: 'var(--bg-surface-elevated)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: '6px',
                  padding: '8px 10px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '4px',
                  boxShadow: 'var(--shadow-card)',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '11px' }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--text-primary)' }}>
                    {order.order_id}
                  </span>
                  <span
                    style={{
                      fontSize: '9px',
                      padding: '2px 7px',
                      borderRadius: '4px',
                      backgroundColor: isShipped ? '#18181B' : '#F4F4F5',
                      color: isShipped ? '#FFFFFF' : '#18181B',
                      border: isShipped ? '1px solid #18181B' : '1px solid #E4E4E7',
                      fontWeight: 600,
                    }}
                  >
                    {order.stage}
                  </span>
                </div>

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '10px', color: 'var(--text-secondary)' }}>
                  <span>
                    <strong>{order.quantity}x</strong> {order.sku} → Gate {order.destination_gate}
                  </span>
                  <span style={{ fontFamily: 'var(--font-mono)', color: order.g2p_robot_id ? '#B45309' : 'var(--text-muted)' }}>
                    {order.g2p_robot_id ? `AMR: ${order.g2p_robot_id}` : 'Bidding...'}
                  </span>
                </div>

                {order.stuck_reason && (
                  <div style={{ fontSize: '10px', color: '#18181B', backgroundColor: '#FAFAFA', border: '1px solid #E4E4E7', borderLeft: '3px solid #D97706', borderRadius: '4px', padding: '4px 6px', marginTop: '2px' }}>
                    ⚠ {order.stuck_reason}
                  </div>
                )}
              </div>
            )
          })}

          {orders.length === 0 && (
            <div style={{ fontSize: '10px', opacity: 0.5, textAlign: 'center', padding: '12px' }}>
              No active customer orders placed yet.
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
