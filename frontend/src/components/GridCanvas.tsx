import React, { useRef, useState } from 'react'
import { Maximize, Minimize, RotateCcw, ZoomIn, ZoomOut, Crosshair } from 'lucide-react'
import type { Conflict, Point, Robot, Task, TempObstacle, World } from '../types'
import { Warehouse3DCanvas } from './Warehouse3DCanvas'
import type { FleetStore } from '../hooks/useFleetSocket'

export const darkPalette = {
  background: '#111111',
  gridSoft: 'rgba(255, 255, 255, 0.05)',
  steel: '#262626',
  steelTop: '#383838',
  steelDark: '#1A1A1A',
  floor: '#171717',
  floorAlt: '#1E1E1E',
  floorShadow: '#0A0A0A',
  import: '#FF6B35',
  export: '#FFC300',
  charger: '#16A34A',
  hazard: '#DC2626',
  text: '#F5F5F5',
}

export const lightPalette = {
  background: '#E5E5E5',
  gridSoft: 'rgba(0, 0, 0, 0.06)',
  steel: '#8A8A8A',
  steelTop: '#B5B5B5',
  steelDark: '#5C5C5C',
  floor: '#F5F5F5',
  floorAlt: '#EBEBEB',
  floorShadow: '#D1D1D1',
  import: '#FF6B35',
  export: '#FFC300',
  charger: '#16A34A',
  hazard: '#DC2626',
  text: '#141414',
}

export type Palette = typeof darkPalette

type Props = {
  world: World
  robots: Robot[]
  tasks?: Task[]
  conflicts: Conflict[]
  obstacles: TempObstacle[]
  tick: number
  tickMs?: number
  selected: string | null
  showMeshLinks?: boolean
  theme?: 'light' | 'dark'
  isFullscreen?: boolean
  onToggleFullscreen?: () => void
  onRobot: (robot: Robot) => void
  onCell: (point: Point) => void
  cameraFollow?: boolean
  onToggleCameraFollow?: () => void
  storeRef?: React.RefObject<FleetStore>
}

export function GridCanvas({
  world,
  robots,
  tasks = [],
  conflicts,
  obstacles,
  tick,
  selected,
  showMeshLinks = true,
  theme = 'dark',
  isFullscreen = false,
  onToggleFullscreen,
  onRobot,
  onCell,
  cameraFollow: propCameraFollow,
  onToggleCameraFollow,
  storeRef,
}: Props) {
  const [internalCameraFollow, setInternalCameraFollow] = useState(true)
  const cameraFollow = propCameraFollow !== undefined ? propCameraFollow : internalCameraFollow
  const handleToggleCameraFollow = onToggleCameraFollow ?? (() => setInternalCameraFollow((prev) => !prev))

  const onResetViewRef = useRef<(() => void) | null>(null)
  const onZoomInRef = useRef<(() => void) | null>(null)
  const onZoomOutRef = useRef<(() => void) | null>(null)

  const palette = theme === 'light' ? lightPalette : darkPalette

  const handleZoomIn = () => {
    onZoomInRef.current?.()
  }

  const handleZoomOut = () => {
    onZoomOutRef.current?.()
  }

  const handleResetView = () => {
    onResetViewRef.current?.()
  }

  return (
    <div className={`grid-shell ${isFullscreen ? 'fullscreen' : ''}`}>
      <div className="grid-caption">
        <div className="caption-left">
          <span className="live-indicator">
            <i className="legend-dot" style={{ backgroundColor: 'var(--accent-primary)' }} /> Live warehouse floor
          </span>
          <span className="grid-dimensions">
            {world.width} × {world.height} grid, isometric
          </span>
          {isFullscreen && (
            <span className="fullscreen-badge">
              FULLSCREEN SIMULATION ACTIVE
            </span>
          )}
        </div>
        <div className="caption-right">
          {isFullscreen && onToggleFullscreen && (
            <button
              className="fullscreen-exit-btn"
              onClick={onToggleFullscreen}
              title="Exit Full Screen (Esc)"
              aria-label="Exit Full Screen"
            >
              <Minimize size={13} />
              <span>Exit Full Screen (Esc)</span>
            </button>
          )}
        </div>
      </div>

      <div className="canvas-view-container">
        <Warehouse3DCanvas
          world={world}
          robots={robots}
          tasks={tasks}
          conflicts={conflicts}
          obstacles={obstacles}
          tick={tick}
          selected={selected}
          showMeshLinks={showMeshLinks}
          cameraFollow={cameraFollow}
          theme={theme}
          palette={palette}
          storeRef={storeRef}
          onRobot={onRobot}
          onCell={onCell}
          onResetViewRef={onResetViewRef}
          onZoomInRef={onZoomInRef}
          onZoomOutRef={onZoomOutRef}
        />
      </div>

      {/* Floating Canvas Control HUD */}
      <div className="canvas-hud-controls">
        {selected && (
          <button
            className={`canvas-hud-btn ${cameraFollow ? 'active' : ''}`}
            onClick={handleToggleCameraFollow}
            title={cameraFollow ? 'Disable Camera Follow' : 'Follow Selected AMR'}
            aria-label="Toggle Camera Follow"
          >
            <Crosshair size={14} />
          </button>
        )}

        <button
          className="canvas-hud-btn"
          onClick={handleZoomIn}
          title="Zoom In (+)"
          aria-label="Zoom in canvas"
        >
          <ZoomIn size={14} />
        </button>
        <button
          className="canvas-hud-btn"
          onClick={handleZoomOut}
          title="Zoom Out (-)"
          aria-label="Zoom out canvas"
        >
          <ZoomOut size={14} />
        </button>
        <button
          className="canvas-hud-btn"
          onClick={handleResetView}
          title="Reset View / Isometric Center"
          aria-label="Reset canvas view"
        >
          <RotateCcw size={13} />
        </button>

        {onToggleFullscreen && (
          <button
            className={`canvas-hud-btn ${isFullscreen ? 'active' : ''}`}
            onClick={onToggleFullscreen}
            title={isFullscreen ? 'Exit Full Screen (Esc)' : 'View Simulation in Full Screen (F)'}
            aria-label={isFullscreen ? 'Exit Full Screen' : 'Toggle Full Screen'}
          >
            {isFullscreen ? <Minimize size={14} /> : <Maximize size={14} />}
          </button>
        )}
      </div>

      {isFullscreen && (
        <div className="fullscreen-watermark">
          <span>KINETIX AUTONOMOUS FLEET SIMULATOR</span>
          <span>10 NODES P2P MESH • TICK #{tick}</span>
        </div>
      )}
    </div>
  )
}
