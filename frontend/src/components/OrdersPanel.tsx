import React, { useState } from 'react'
import {
  PackageCheck,
  CheckCircle2,
  Clock,
  AlertTriangle,
  Boxes,
  Bot,
  Warehouse,
  ChevronRight,
  Layers,
} from 'lucide-react'
import type { OrderInfo, OrderStage } from '../types'

const ORDER_STAGES: OrderStage[] = [
  'Announced',
  'G2P assigned',
  'Pod lifted',
  'At pick station',
  'Sorting assigned',
  'In chute',
  'Shipped',
]

interface OrdersPanelProps {
  orders: OrderInfo[]
  currentTick: number
}

export function OrdersPanel({ orders, currentTick }: OrdersPanelProps) {
  const [filter, setFilter] = useState<'ALL' | 'ACTIVE' | 'SHIPPED'>('ALL')

  const filteredOrders = orders.filter((order) => {
    if (filter === 'ACTIVE') return order.stage !== 'Shipped'
    if (filter === 'SHIPPED') return order.stage === 'Shipped'
    return true
  })

  const activeCount = orders.filter((o) => o.stage !== 'Shipped').length
  const shippedCount = orders.filter((o) => o.stage === 'Shipped').length

  const getStageStatus = (stage: string, currentOrderStage: string) => {
    const stageIdx = ORDER_STAGES.indexOf(stage as OrderStage)
    const currentIdx = ORDER_STAGES.indexOf(currentOrderStage as OrderStage)
    if (stageIdx < currentIdx) return 'completed'
    if (stageIdx === currentIdx) return 'current'
    return 'upcoming'
  }

  return (
    <section className="panel orders-panel">
      <div className="panel-heading">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <PackageCheck size={18} style={{ color: 'var(--accent-primary)' }} />
          <h2>
            Order Fulfillment Pipeline <em>{orders.length}</em>
          </h2>
        </div>
        <div className="orders-filter-group">
          <button
            type="button"
            className={`orders-filter-btn ${filter === 'ALL' ? 'active' : ''}`}
            onClick={() => setFilter('ALL')}
          >
            All ({orders.length})
          </button>
          <button
            type="button"
            className={`orders-filter-btn ${filter === 'ACTIVE' ? 'active' : ''}`}
            onClick={() => setFilter('ACTIVE')}
          >
            Active ({activeCount})
          </button>
          <button
            type="button"
            className={`orders-filter-btn ${filter === 'SHIPPED' ? 'active' : ''}`}
            onClick={() => setFilter('SHIPPED')}
          >
            Shipped ({shippedCount})
          </button>
        </div>
      </div>

      <div className="orders-scroll-list">
        {filteredOrders.map((order) => {
          const isShipped = order.stage === 'Shipped'

          return (
            <div
              key={order.order_id}
              className={`order-card ${isShipped ? 'order-card-shipped' : ''}`}
            >
              {/* Order Header */}
              <div className="order-header-row">
                <div className="order-id-group">
                  <span className="order-id-text">
                    {order.order_id}
                  </span>
                  <span className="order-gate-badge">
                    Gate {order.destination_gate}
                  </span>
                  <span
                    className={`order-urgency-badge ${
                      order.urgency >= 4 ? 'urgency-high' : 'urgency-standard'
                    }`}
                  >
                    Urgency P{order.urgency}
                  </span>
                </div>

                <div className="order-tick-time">
                  <Clock size={12} />
                  <span>Tick {order.created_tick}</span>
                </div>
              </div>

              {/* Product SKU and Units */}
              <div className="order-sku-row">
                <Boxes size={14} style={{ color: 'var(--text-muted)' }} />
                <span>
                  <strong>{order.quantity}x</strong> <span className="order-sku-code">{order.sku}</span>
                </span>
                {order.shelf_id && (
                  <span className="order-pod-location">
                    <Warehouse size={12} /> Pod: {order.shelf_id}
                  </span>
                )}
              </div>

              {/* Multi-Agent Assignments */}
              <div className="order-assignments-grid">
                <div className="order-assignment-cell">
                  <Bot size={13} style={{ color: '#D97706' }} />
                  <span className="order-assignment-label">G2P AMR:</span>
                  <span
                    className={`order-assignment-value ${
                      order.g2p_robot_id ? 'g2p' : 'pending'
                    }`}
                  >
                    {order.g2p_robot_id || 'Bidding...'}
                  </span>
                </div>

                <div className="order-assignment-cell">
                  <Bot size={13} style={{ color: '#0284C7' }} />
                  <span className="order-assignment-label">Sort AMR:</span>
                  <span
                    className={`order-assignment-value ${
                      order.sorting_robot_id ? 'sort' : 'pending'
                    }`}
                  >
                    {order.sorting_robot_id || (order.stage === 'Shipped' ? 'Dispatched' : 'Awaiting drop')}
                  </span>
                </div>

                {order.chute_id && (
                  <div className="order-assignment-cell">
                    <Layers size={13} style={{ color: '#7C3AED' }} />
                    <span className="order-assignment-label">Chute:</span>
                    <span className="order-assignment-value chute">{order.chute_id}</span>
                  </div>
                )}
              </div>

              {/* Stage Timeline */}
              <div style={{ marginTop: '2px' }}>
                <div className="order-stepper-track">
                  {ORDER_STAGES.map((st, idx) => {
                    const status = getStageStatus(st, order.stage)
                    const isLast = idx === ORDER_STAGES.length - 1

                    return (
                      <React.Fragment key={st}>
                        <div className={`order-step-pill ${status}`}>
                          {status === 'completed' && <CheckCircle2 size={11} />}
                          {status === 'current' && <span className="order-pulse-dot" />}
                          <span>{st}</span>
                        </div>
                        {!isLast && <ChevronRight size={11} className="order-chevron" />}
                      </React.Fragment>
                    )
                  })}
                </div>
              </div>

              {/* Plain-Language Stuck Reason Warning Card */}
              {order.stuck_reason && (
                <div className="order-status-notice">
                  <AlertTriangle size={15} className="order-notice-icon" />
                  <span>
                    <strong>Status Notice:</strong> {order.stuck_reason}
                  </span>
                </div>
              )}
            </div>
          )
        })}

        {filteredOrders.length === 0 && (
          <div
            style={{
              padding: '28px 16px',
              textAlign: 'center',
              color: 'var(--text-muted)',
              fontSize: '12px',
              backgroundColor: 'var(--bg-surface-subtle)',
              borderRadius: '8px',
              border: '1px dashed var(--border-subtle)',
            }}
          >
            <Boxes size={26} style={{ margin: '0 auto 8px auto', opacity: 0.6 }} />
            <div style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>No orders match the current filter.</div>
            <div style={{ fontSize: '11px', marginTop: '4px', opacity: 0.8 }}>
              Create an order from the Task Panel to see it progress through the multi-agent pipeline!
            </div>
          </div>
        )}
      </div>
    </section>
  )
}

