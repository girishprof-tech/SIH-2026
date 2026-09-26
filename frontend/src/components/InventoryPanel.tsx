import React, { useState, useMemo } from 'react'
import { Package, Search, ShieldCheck, Clock, RefreshCw, AlertTriangle } from 'lucide-react'
import type { ShelfRecord } from '../types'

interface Props {
  inventory: ShelfRecord[]
  currentTick: number
  onSelectShelf?: (shelfId: string) => void
}

export function InventoryPanel({ inventory, currentTick, onSelectShelf }: Props) {
  const [search, setSearch] = useState('')
  const [filterStale, setFilterStale] = useState(false)

  const filteredShelves = useMemo(() => {
    let list = inventory
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter((s) => {
        if (s.shelf_id.toLowerCase().includes(q)) return true
        return Object.keys(s.sku_manifest || {}).some((sku) => sku.toLowerCase().includes(q))
      })
    }
    if (filterStale) {
      list = list.filter((s) => s.confidence < 0.75)
    }
    return list
  }, [inventory, search, filterStale])

  const stats = useMemo(() => {
    const totalPods = inventory.length
    const totalBoxes = inventory.reduce((acc, s) => acc + (s.current_box_count || 0), 0)
    const avgConfidence = totalPods > 0 ? (inventory.reduce((acc, s) => acc + s.confidence, 0) / totalPods) * 100 : 100
    const staleCount = inventory.filter((s) => s.confidence < 0.75).length
    return { totalPods, totalBoxes, avgConfidence: Math.round(avgConfidence), staleCount }
  }, [inventory])

  return (
    <div className="panel inventory-panel">
      <div className="panel-header">
        <div className="panel-title-group">
          <Package className="panel-icon" size={16} />
          <span className="panel-title">Decentralized Inventory</span>
          <span className="badge badge-accent">{stats.totalPods} Pods</span>
        </div>
      </div>

      {/* KPI Stats Bar */}
      <div className="inventory-stats-grid">
        <div className="inv-stat-card">
          <span className="inv-stat-label">Total Storage</span>
          <span className="inv-stat-value">{stats.totalBoxes.toLocaleString()} <small>boxes</small></span>
        </div>
        <div className="inv-stat-card">
          <span className="inv-stat-label">Avg Confidence</span>
          <span className={`inv-stat-value ${stats.avgConfidence >= 80 ? 'text-emerald' : stats.avgConfidence >= 60 ? 'text-amber' : 'text-rose'}`}>
            {stats.avgConfidence}%
          </span>
        </div>
        <div className="inv-stat-card">
          <span className="inv-stat-label">Needs Audit</span>
          <span className={`inv-stat-value ${stats.staleCount > 0 ? 'text-amber' : 'text-dim'}`}>
            {stats.staleCount}
          </span>
        </div>
      </div>

      {/* Search & Filter Bar */}
      <div className="inventory-search-bar">
        <div className="search-input-wrapper">
          <Search size={14} className="search-icon" />
          <input
            type="text"
            placeholder="Search shelf (e.g. POD-A01) or SKU..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="inventory-search-input"
          />
        </div>
        <button
          className={`filter-btn ${filterStale ? 'filter-active' : ''}`}
          onClick={() => setFilterStale(!filterStale)}
          title="Filter low-confidence stale shelves"
        >
          <AlertTriangle size={13} />
          <span>Stale</span>
        </button>
      </div>

      {/* Shelves List */}
      <div className="inventory-list custom-scrollbar">
        {filteredShelves.length === 0 ? (
          <div className="empty-state-text">
            {inventory.length === 0 ? 'Loading inventory ledger...' : 'No matching shelf pods found.'}
          </div>
        ) : (
          filteredShelves.map((shelf) => {
            const confPct = Math.round(shelf.confidence * 100)
            const confColor =
              confPct >= 85 ? 'var(--color-emerald)' : confPct >= 65 ? 'var(--color-amber)' : 'var(--color-rose)'
            const topSkus = Object.entries(shelf.sku_manifest || {})
              .slice(0, 3)
              .map(([sku, qty]) => `${sku}: ${qty}`)
              .join(' • ')

            const ticksSinceAudit = Math.max(0, currentTick - (shelf.last_audited_tick || 0))

            return (
              <div
                key={shelf.shelf_id}
                className="shelf-card"
                onClick={() => onSelectShelf?.(shelf.shelf_id)}
              >
                <div className="shelf-card-header">
                  <div className="shelf-id-badge">
                    <span className="shelf-name">{shelf.shelf_id}</span>
                    <span className="shelf-coords">({shelf.x}, {shelf.y})</span>
                  </div>
                  <div
                    className="confidence-badge"
                    style={{ borderColor: confColor, color: confColor }}
                  >
                    <ShieldCheck size={12} />
                    <span>{confPct}% conf</span>
                  </div>
                </div>

                {/* Box Capacity Progress Bar */}
                <div className="shelf-capacity-row">
                  <div className="shelf-capacity-label">
                    <span>Capacity ({shelf.current_box_count} / {shelf.capacity_boxes})</span>
                    <span>{Math.round((shelf.current_box_count / shelf.capacity_boxes) * 100)}%</span>
                  </div>
                  <div className="progress-track">
                    <div
                      className="progress-fill"
                      style={{
                        width: `${Math.min(100, (shelf.current_box_count / shelf.capacity_boxes) * 100)}%`,
                        backgroundColor: 'var(--color-accent-blue)',
                      }}
                    />
                  </div>
                </div>

                {/* Top SKUs */}
                {topSkus && (
                  <div className="shelf-skus-text">
                    <strong>SKUs:</strong> {topSkus}
                  </div>
                )}

                {/* Audit meta */}
                <div className="shelf-meta-row">
                  <span className="shelf-meta-item">
                    <Clock size={11} />
                    {shelf.last_audited_tick > 0 ? `Audited ${ticksSinceAudit}t ago` : 'Initial Seed'}
                  </span>
                  {shelf.last_audited_by && (
                    <span className="shelf-meta-item">
                      by {shelf.last_audited_by}
                    </span>
                  )}
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
