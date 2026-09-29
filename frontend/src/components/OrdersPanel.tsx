import React, { useState } from 'react'
import {
  PackageCheck,
  Truck,
  CheckCircle2,
  Clock,
  AlertTriangle,
  ArrowRight,
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
    <section className="panel orders-panel" style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
      <div className="panel-heading" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <PackageCheck size={18} style={{ color: '#38bdf8' }} />
          <h2 style={{ margin: 0, fontSize: '14px', fontWeight: 700 }}>
            Order Fulfillment Pipeline <em>{orders.length}</em>
          </h2>
        </div>
        <div style={{ display: 'flex', gap: '4px' }}>
          <button
            type="button"
            className={`filter-chip ${filter === 'ALL' ? 'active' : ''}`}
            onClick={() => setFilter('ALL')}
            style={{
              padding: '2px 8px',
              fontSize: '10px',
              borderRadius: '4px',
              border: '1px solid rgba(255,255,255,0.1)',
              background: filter === 'ALL' ? 'rgba(56,189,248,0.2)' : 'transparent',
              color: filter === 'ALL' ? '#38bdf8' : 'inherit',
              cursor: 'pointer',
            }}
          >
            All ({orders.length})
          </button>
          <button
            type="button"
            className={`filter-chip ${filter === 'ACTIVE' ? 'active' : ''}`}
            onClick={() => setFilter('ACTIVE')}
            style={{
              padding: '2px 8px',
              fontSize: '10px',
              borderRadius: '4px',
              border: '1px solid rgba(255,255,255,0.1)',
              background: filter === 'ACTIVE' ? 'rgba(234,179,8,0.2)' : 'transparent',
              color: filter === 'ACTIVE' ? '#facc15' : 'inherit',
              cursor: 'pointer',
            }}
          >
            Active ({activeCount})
          </button>
          <button
            type="button"
            className={`filter-chip ${filter === 'SHIPPED' ? 'active' : ''}`}
            onClick={() => setFilter('SHIPPED')}
            style={{
              padding: '2px 8px',
              fontSize: '10px',
              borderRadius: '4px',
              border: '1px solid rgba(255,255,255,0.1)',
              background: filter === 'SHIPPED' ? 'rgba(16,185,129,0.2)' : 'transparent',
              color: filter === 'SHIPPED' ? '#34d399' : 'inherit',
              cursor: 'pointer',
            }}
          >
            Shipped ({shippedCount})
          </button>
        </div>
      </div>

      <div className="orders-scroll-list" style={{ display: 'flex', flexDirection: 'column', gap: '10px', maxHeight: '550px', overflowY: 'auto' }}>
        {filteredOrders.map((order) => {
          const isShipped = order.stage === 'Shipped'

          return (
            <div
              key={order.order_id}
              className="order-card"
              style={{
                borderRadius: '8px',
                border: isShipped ? '1px solid rgba(16, 185, 129, 0.3)' : '1px solid rgba(56, 189, 248, 0.25)',
                background: isShipped
                  ? 'linear-gradient(135deg, rgba(16,185,129,0.06), rgba(15,23,42,0.4))'
                  : 'linear-gradient(135deg, rgba(56,189,248,0.06), rgba(15,23,42,0.4))',
                padding: '10px 12px',
                display: 'flex',
                flexDirection: 'column',
                gap: '8px',
                boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
              }}
            >
              {/* Order Header */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <span
                    style={{
                      fontFamily: 'monospace',
                      fontWeight: 700,
                      fontSize: '12px',
                      color: isShipped ? '#34d399' : '#38bdf8',
                    }}
                  >
                    {order.order_id}
                  </span>
                  <span
                    style={{
                      fontSize: '10px',
                      padding: '2px 6px',
                      borderRadius: '4px',
                      background: 'rgba(255,255,255,0.08)',
                      fontWeight: 600,
                    }}
                  >
                    Gate {order.destination_gate}
                  </span>
                  <span
                    style={{
                      fontSize: '10px',
                      padding: '2px 6px',
                      borderRadius: '4px',
                      background: order.urgency >= 4 ? 'rgba(239, 68, 68, 0.2)' : 'rgba(234, 179, 8, 0.2)',
                      color: order.urgency >= 4 ? '#f87171' : '#facc15',
                      fontWeight: 600,
                    }}
                  >
                    Urgency P{order.urgency}
                  </span>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '10px', opacity: 0.7 }}>
                  <Clock size={11} />
                  <span>Tick {order.created_tick}</span>
                </div>
              </div>

              {/* Product SKU and Units */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '12px' }}>
                <Boxes size={14} style={{ color: '#94a3b8' }} />
                <span>
                  <strong>{order.quantity}x</strong> <span style={{ fontFamily: 'monospace' }}>{order.sku}</span>
                </span>
                {order.shelf_id && (
                  <span style={{ fontSize: '11px', opacity: 0.75, display: 'flex', alignItems: 'center', gap: '4px' }}>
                    <Warehouse size={11} /> Pod: {order.shelf_id}
                  </span>
                )}
              </div>

              {/* Multi-Agent Assignments */}
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
                  gap: '6px',
                  background: 'rgba(0,0,0,0.2)',
                  padding: '6px 8px',
                  borderRadius: '6px',
                  fontSize: '10px',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                  <Bot size={11} style={{ color: '#f59e0b' }} />
                  <span>G2P AMR:</span>
                  <strong style={{ color: order.g2p_robot_id ? '#f59e0b' : '#64748b' }}>
                    {order.g2p_robot_id || 'Bidding...'}
                  </strong>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                  <Bot size={11} style={{ color: '#06b6d4' }} />
                  <span>Sort AMR:</span>
                  <strong style={{ color: order.sorting_robot_id ? '#06b6d4' : '#64748b' }}>
                    {order.sorting_robot_id || (order.stage === 'Shipped' ? 'Dispatched' : 'Awaiting drop')}
                  </strong>
                </div>

                {order.chute_id && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                    <Layers size={11} style={{ color: '#a855f7' }} />
                    <span>Chute:</span>
                    <strong style={{ color: '#c084fc' }}>{order.chute_id}</strong>
                  </div>
                )}
              </div>

              {/* Stage Timeline */}
              <div style={{ marginTop: '2px' }}>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px',
                    overflowX: 'auto',
                    paddingBottom: '4px',
                  }}
                >
                  {ORDER_STAGES.map((st, idx) => {
                    const status = getStageStatus(st, order.stage)
                    const isLast = idx === ORDER_STAGES.length - 1

                    let badgeBg = 'rgba(255,255,255,0.05)'
                    let badgeColor = '#64748b'
                    let badgeBorder = '1px solid rgba(255,255,255,0.08)'

                    if (status === 'completed') {
                      badgeBg = 'rgba(16, 185, 129, 0.15)'
                      badgeColor = '#34d399'
                      badgeBorder = '1px solid rgba(16, 185, 129, 0.35)'
                    } else if (status === 'current') {
                      badgeBg = 'rgba(56, 189, 248, 0.2)'
                      badgeColor = '#38bdf8'
                      badgeBorder = '1px solid rgba(56, 189, 248, 0.5)'
                    }

                    return (
                      <React.Fragment key={st}>
                        <div
                          style={{
                            padding: '3px 7px',
                            borderRadius: '4px',
                            background: badgeBg,
                            color: badgeColor,
                            border: badgeBorder,
                            fontSize: '9px',
                            fontWeight: status === 'current' ? 700 : 500,
                            whiteSpace: 'nowrap',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '3px',
                          }}
                        >
                          {status === 'completed' && <CheckCircle2 size={10} />}
                          {status === 'current' && (
                            <span
                              style={{
                                width: '5px',
                                height: '5px',
                                borderRadius: '50%',
                                background: '#38bdf8',
                                display: 'inline-block',
                                animation: 'pulse 1.5s infinite',
                              }}
                            />
                          )}
                          <span>{st}</span>
                        </div>
                        {!isLast && <ChevronRight size={10} style={{ color: '#475569', flexShrink: 0 }} />}
                      </React.Fragment>
                    )
                  })}
                </div>
              </div>

              {/* Plain-Language Stuck Reason Warning Card */}
              {order.stuck_reason && (
                <div
                  style={{
                    backgroundColor: 'rgba(234, 179, 8, 0.12)',
                    border: '1px solid rgba(234, 179, 8, 0.3)',
                    borderRadius: '6px',
                    padding: '6px 10px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    color: '#facc15',
                    fontSize: '11px',
                  }}
                >
                  <AlertTriangle size={14} style={{ flexShrink: 0 }} />
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
              padding: '24px',
              textAlign: 'center',
              color: '#64748b',
              fontSize: '12px',
              background: 'rgba(255,255,255,0.02)',
              borderRadius: '8px',
              border: '1px dashed rgba(255,255,255,0.1)',
            }}
          >
            <Boxes size={24} style={{ margin: '0 auto 8px auto', opacity: 0.5 }} />
            <div>No orders match the current filter.</div>
            <div style={{ fontSize: '10px', marginTop: '4px', opacity: 0.7 }}>
              Create an order from the Task Panel to see it progress through the multi-agent pipeline!
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
