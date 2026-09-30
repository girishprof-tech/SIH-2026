import React, { useState, useEffect } from 'react'
import {
  Layers,
  MapPin,
  Bot,
  Package,
  Sparkles,
  Edit3,
  Play,
  CheckCircle,
  FileCode,
  ArrowRight,
  Database,
} from 'lucide-react'
import type { MapPreset } from '../types'
import { api } from '../api'

interface LaunchScreenProps {
  onUseBuiltIn: () => Promise<void>
  onOpenEditor: () => void
  onSelectPreset: (preset: MapPreset) => Promise<void>
  theme?: 'light' | 'dark'
}

export function LaunchScreen({
  onUseBuiltIn,
  onOpenEditor,
  onSelectPreset,
  theme = 'light',
}: LaunchScreenProps) {
  const [presets, setPresets] = useState<MapPreset[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedPresetId, setSelectedPresetId] = useState<string | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  useEffect(() => {
    api.mapPresets()
      .then((res: any) => {
        setPresets(Array.isArray(res) ? res : res?.presets || [])
      })
      .catch((err) => {
        console.warn('Could not load map presets:', err)
      })
  }, [])

  const handleLaunchDefault = async () => {
    setLoading(true)
    setErrorMsg(null)
    try {
      await onUseBuiltIn()
    } catch (e: any) {
      setErrorMsg(e?.message || 'Failed to launch built-in map')
      setLoading(false)
    }
  }

  const handleLaunchPreset = async (preset: MapPreset) => {
    setLoading(true)
    setSelectedPresetId(preset.id)
    setErrorMsg(null)
    try {
      await onSelectPreset(preset)
    } catch (e: any) {
      setErrorMsg(e?.message || `Failed to launch preset: ${preset.name}`)
      setLoading(false)
    }
  }

  return (
    <div className={`launch-screen-container ${theme}`}>
      <div className="launch-screen-card">
        {/* Header */}
        <div className="launch-screen-header">
          <h1 className="launch-title">Warehouse Topology & Fleet Initialization</h1>
          <p className="launch-subtitle">
            Every AMR runs an independent execution loop with signed P2P mesh consensus and Contract-Net task auctions.
            Select or design a layout to synthesize the decentralized fleet.
          </p>
        </div>

        {errorMsg && (
          <div className="launch-error-banner" role="alert">
            <span>{errorMsg}</span>
          </div>
        )}

        {/* Main Options Grid */}
        <div className="launch-options-grid">
          {/* Option 1: Built-in Map */}
          <div className="launch-option-card standard-option">
            <div className="option-header">
              <div className="option-icon-box standard">
                <Database size={24} />
              </div>
              <div className="option-title-group">
                <h3>Built-in Standard Warehouse</h3>
                <span className="option-tag">Recommended Benchmark</span>
              </div>
            </div>

            <p className="option-description">
              30×30 fulfillment layout with 4 modular pod banks (160 shelf slots), 8 sortation put-wall chutes,
              3 pick stations, 8 charging alcoves, and a 10-AMR heterogeneous decentralized fleet.
            </p>

            <div className="option-specs">
              <div className="spec-item">
                <Layers size={14} />
                <span>30 × 30 Grid</span>
              </div>
              <div className="spec-item">
                <Package size={14} />
                <span>160 Pods</span>
              </div>
              <div className="spec-item">
                <Bot size={14} />
                <span>10 AMRs</span>
              </div>
              <div className="spec-item">
                <MapPin size={14} />
                <span>8 Chutes</span>
              </div>
            </div>

            <button
              id="btn-launch-builtin"
              className="launch-action-btn primary"
              onClick={handleLaunchDefault}
              disabled={loading}
            >
              {loading && !selectedPresetId ? (
                <span>Synthesizing Fleet...</span>
              ) : (
                <>
                  <span>Use Built-in Test Map</span>
                  <ArrowRight size={16} />
                </>
              )}
            </button>
          </div>

          {/* Option 2: Custom Map Designer */}
          <div className="launch-option-card custom-option">
            <div className="option-header">
              <div className="option-icon-box custom">
                <Edit3 size={24} />
              </div>
              <div className="option-title-group">
                <h3>Draw Warehouse Map</h3>
                <span className="option-tag custom">Interactive 2D CAD</span>
              </div>
            </div>

            <p className="option-description">
              Custom visual warehouse editor. Draw custom shelf clusters, inbound/outbound dock gates, sorting chutes,
              custom AMR fleet starting positions, and run real-time topological reachability validation.
            </p>

            <div className="option-specs">
              <div className="spec-item">
                <Sparkles size={14} />
                <span>Custom Dimensions</span>
              </div>
              <div className="spec-item">
                <CheckCircle size={14} />
                <span>Aisle Reachability Rules</span>
              </div>
              <div className="spec-item">
                <FileCode size={14} />
                <span>JSON Export/Import</span>
              </div>
            </div>

            <button
              id="btn-open-map-editor"
              className="launch-action-btn secondary"
              onClick={onOpenEditor}
              disabled={loading}
            >
              <span>Draw My Warehouse Map</span>
              <Edit3 size={16} />
            </button>
          </div>
        </div>

        {/* Presets List (if any additional presets exist) */}
        {presets.length > 0 && (
          <div className="launch-presets-section">
            <h4 className="presets-heading">Available Map Presets</h4>
            <div className="presets-list">
              {presets.map((preset) => (
                <div
                  key={preset.id}
                  className={`preset-card ${selectedPresetId === preset.id ? 'active' : ''}`}
                  onClick={() => handleLaunchPreset(preset)}
                >
                  <div className="preset-info">
                    <span className="preset-name">{preset.name}</span>
                    <span className="preset-details">
                      {preset.width}×{preset.height} • {preset.shelves_count} shelves • {preset.robots_count} AMRs • {preset.chutes_count} chutes
                    </span>
                  </div>
                  <button className="preset-load-btn" disabled={loading}>
                    <Play size={14} />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

      </div>
    </div>
  )
}
