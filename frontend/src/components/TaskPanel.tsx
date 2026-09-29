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

  // Load Catalog
  useEffect(() => {
    let mounted = true
    setLoadingCatalog(true)
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
  }, [world])

  // Destination gates from world
  const exitGates = useMemo(() => {
    if (!world?.exit_gates || world.exit_gates.length === 0) {
      return [{ id: 'OUT-1' }]
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
      const res = await api.createOrder({
        sku: selectedSku,
        quantity,
        destination_gate: destinationGate,
        urgency,
      })
      setSuccessMsg(`Order ${res.order_id || res.task_id} placed! Autonomous G2P & Sorting AMRs dispatched.`)
      // Refresh catalog stock
      api.catalog().then(setCatalog).catch(() => {})
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
          <ShoppingCart size={16} style={{ color: '#38bdf8' }} />
          <h2 style={{ margin: 0, fontSize: '13px', fontWeight: 700 }}>Dispatch Orders</h2>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span
            style={{
              fontSize: '10px',
              fontWeight: 700,
              padding: '2px 6px',
              borderRadius: '4px',
              background: 'rgba(56,189,248,0.15)',
              color: '#38bdf8',
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
            border: activeTab === 'order' ? '1px solid #38bdf8' : '1px solid rgba(255,255,255,0.1)',
            background: activeTab === 'order' ? 'rgba(56,189,248,0.15)' : 'rgba(255,255,255,0.03)',
            color: activeTab === 'order' ? '#38bdf8' : 'inherit',
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
            border: activeTab === 'audit' ? '1px solid #a855f7' : '1px solid rgba(255,255,255,0.1)',
            background: activeTab === 'audit' ? 'rgba(168,85,247,0.15)' : 'rgba(255,255,255,0.03)',
            color: activeTab === 'audit' ? '#c084fc' : 'inherit',
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
            opacity: simulationRunning ? 1 : 0.6,
            pointerEvents: simulationRunning ? 'auto' : 'none',
          }}
        >
          {/* Product Dropdown */}
          <label style={{ fontSize: '11px', display: 'flex', flexDirection: 'column', gap: '3px' }}>
            <span style={{ fontWeight: 600, color: '#cbd5e1' }}>Product SKU</span>
            <select
              value={selectedSku}
              onChange={(e) => {
                setSelectedSku(e.target.value)
                setQuantity(1)
              }}
              disabled={loadingCatalog || busy || submitting}
              style={{
                padding: '6px 8px',
                borderRadius: '6px',
                border: '1px solid rgba(255,255,255,0.15)',
                background: 'rgba(15, 23, 42, 0.8)',
                color: '#f8fafc',
                fontSize: '11px',
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
            <label style={{ fontSize: '11px', display: 'flex', flexDirection: 'column', gap: '3px' }}>
              <span style={{ fontWeight: 600, color: '#cbd5e1' }}>
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
                  padding: '6px 8px',
                  borderRadius: '6px',
                  border: '1px solid rgba(255,255,255,0.15)',
                  background: 'rgba(15, 23, 42, 0.8)',
                  color: '#f8fafc',
                  fontSize: '11px',
                }}
              />
            </label>

            <label style={{ fontSize: '11px', display: 'flex', flexDirection: 'column', gap: '3px' }}>
              <span style={{ fontWeight: 600, color: '#cbd5e1' }}>Destination Exit Gate</span>
              <select
                value={destinationGate}
                onChange={(e) => setDestinationGate(e.target.value)}
                disabled={busy || submitting}
                style={{
                  padding: '6px 8px',
                  borderRadius: '6px',
                  border: '1px solid rgba(255,255,255,0.15)',
                  background: 'rgba(15, 23, 42, 0.8)',
                  color: '#f8fafc',
                  fontSize: '11px',
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
            <span style={{ fontSize: '11px', fontWeight: 600, color: '#cbd5e1' }}>Urgency</span>
            <div style={{ display: 'flex', gap: '4px' }}>
              {[1, 2, 3, 4, 5].map((lvl) => (
                <button
                  type="button"
                  key={lvl}
                  onClick={() => setUrgency(lvl)}
                  style={{
                    width: '26px',
                    height: '24px',
                    padding: 0,
                    borderRadius: '4px',
                    fontSize: '11px',
                    fontWeight: urgency === lvl ? 700 : 500,
                    border: urgency === lvl ? '1px solid #38bdf8' : '1px solid rgba(255,255,255,0.1)',
                    background: urgency === lvl ? 'rgba(56,189,248,0.25)' : 'rgba(255,255,255,0.03)',
                    color: urgency === lvl ? '#38bdf8' : 'inherit',
                    cursor: 'pointer',
                  }}
                >
                  {lvl}
                </button>
              ))}
            </div>
          </div>

          {/* Submit Button */}
          <button
            type="submit"
            className="submit-button"
            disabled={busy || submitting || !selectedSku || (currentProduct && currentProduct.total_stock <= 0)}
            style={{
              marginTop: '4px',
              padding: '8px',
              background: 'linear-gradient(135deg, #0284c7, #0369a1)',
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
            opacity: simulationRunning ? 1 : 0.6,
            pointerEvents: simulationRunning ? 'auto' : 'none',
          }}
        >
          <label style={{ fontSize: '11px', display: 'flex', flexDirection: 'column', gap: '3px' }}>
            <span style={{ fontWeight: 600, color: '#cbd5e1' }}>Shelf Pod to Scan</span>
            <select
              value={selectedShelf}
              onChange={(e) => setSelectedShelf(e.target.value)}
              disabled={busy || submitting}
              style={{
                padding: '6px 8px',
                borderRadius: '6px',
                border: '1px solid rgba(255,255,255,0.15)',
                background: 'rgba(15, 23, 42, 0.8)',
                color: '#f8fafc',
                fontSize: '11px',
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
            <span style={{ fontSize: '11px', fontWeight: 600, color: '#cbd5e1' }}>Scan Priority</span>
            <div style={{ display: 'flex', gap: '4px' }}>
              {[1, 2, 3, 4, 5].map((lvl) => (
                <button
                  type="button"
                  key={lvl}
                  onClick={() => setAuditUrgency(lvl)}
                  style={{
                    width: '26px',
                    height: '24px',
                    padding: 0,
                    borderRadius: '4px',
                    fontSize: '11px',
                    fontWeight: auditUrgency === lvl ? 700 : 500,
                    border: auditUrgency === lvl ? '1px solid #c084fc' : '1px solid rgba(255,255,255,0.1)',
                    background: auditUrgency === lvl ? 'rgba(168,85,247,0.25)' : 'rgba(255,255,255,0.03)',
                    color: auditUrgency === lvl ? '#c084fc' : 'inherit',
                    cursor: 'pointer',
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
              padding: '8px',
              background: 'linear-gradient(135deg, #7e22ce, #6b21a8)',
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
            backgroundColor: 'rgba(239, 68, 68, 0.12)',
            border: '1px solid rgba(239, 68, 68, 0.3)',
            borderRadius: '6px',
            padding: '6px 10px',
            color: '#f87171',
            fontSize: '11px',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
          }}
        >
          <AlertTriangle size={13} style={{ flexShrink: 0 }} />
          <span>{error}</span>
        </div>
      )}

      {successMsg && (
        <div
          style={{
            backgroundColor: 'rgba(16, 185, 129, 0.12)',
            border: '1px solid rgba(16, 185, 129, 0.3)',
            borderRadius: '6px',
            padding: '6px 10px',
            color: '#34d399',
            fontSize: '11px',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
          }}
        >
          <CheckCircle2 size={13} style={{ flexShrink: 0 }} />
          <span>{successMsg}</span>
        </div>
      )}

      {/* Recent Orders Progress Summary */}
      <div style={{ marginTop: '8px', borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: '8px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
          <strong style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.04em', opacity: 0.8 }}>
            Live Order Pipeline ({orders.length})
          </strong>
          <span style={{ fontSize: '10px', opacity: 0.6 }}>Decentralized Flow</span>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '200px', overflowY: 'auto' }}>
          {orders.slice(0, 5).map((order) => {
            const isShipped = order.stage === 'Shipped'
            return (
              <div
                key={order.order_id}
                style={{
                  background: 'rgba(0,0,0,0.2)',
                  border: isShipped ? '1px solid rgba(16,185,129,0.25)' : '1px solid rgba(56,189,248,0.2)',
                  borderRadius: '6px',
                  padding: '6px 8px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '4px',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '11px' }}>
                  <span style={{ fontFamily: 'monospace', fontWeight: 700, color: isShipped ? '#34d399' : '#38bdf8' }}>
                    {order.order_id}
                  </span>
                  <span
                    style={{
                      fontSize: '9px',
                      padding: '1px 6px',
                      borderRadius: '4px',
                      background: isShipped ? 'rgba(16,185,129,0.2)' : 'rgba(56,189,248,0.2)',
                      color: isShipped ? '#34d399' : '#38bdf8',
                      fontWeight: 600,
                    }}
                  >
                    {order.stage}
                  </span>
                </div>

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '10px', opacity: 0.8 }}>
                  <span>
                    {order.quantity}x {order.sku} → Gate {order.destination_gate}
                  </span>
                  <span>{order.g2p_robot_id ? `AMR: ${order.g2p_robot_id}` : 'Bidding...'}</span>
                </div>

                {order.stuck_reason && (
                  <div style={{ fontSize: '9px', color: '#facc15', marginTop: '2px' }}>
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
