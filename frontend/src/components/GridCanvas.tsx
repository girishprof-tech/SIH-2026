import { useEffect, useMemo, useRef, useState } from 'react'
import { Maximize, Minimize, RotateCcw, ZoomIn, ZoomOut, Crosshair, Box } from 'lucide-react'
import { api } from '../api'
import type { Conflict, Point, Robot, Task, TempObstacle, World } from '../types'
import { ROBOT_TYPE_COLORS, STATE_COLORS } from '../state-meta'
import { Warehouse3DCanvas } from './Warehouse3DCanvas'

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
}
type View = { zoom: number; panX: number; panY: number; drag: boolean; x: number; y: number; moved: boolean }
type Projection = { originX: number; originY: number; tileW: number; tileH: number; lift: number }
type Motion = { from: Point; to: Point; fromAngle: number; toAngle: number; started: number; duration: number }

const darkPalette = {
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

const lightPalette = {
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

const headingAngle = { NORTH: 0, EAST: Math.PI / 2, SOUTH: Math.PI, WEST: -Math.PI / 2 }

function diamond(ctx: CanvasRenderingContext2D, cx: number, cy: number, width: number, height: number) {
  ctx.beginPath()
  ctx.moveTo(cx, cy - height / 2)
  ctx.lineTo(cx + width / 2, cy)
  ctx.lineTo(cx, cy + height / 2)
  ctx.lineTo(cx - width / 2, cy)
  ctx.closePath()
}

function roundedBox(ctx: CanvasRenderingContext2D, x: number, y: number, width: number, height: number, radius: number) {
  const r = Math.min(radius, width / 2, height / 2)
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.lineTo(x + width - r, y)
  ctx.quadraticCurveTo(x + width, y, x + width, y + r)
  ctx.lineTo(x + width, y + height - r)
  ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height)
  ctx.lineTo(x + r, y + height)
  ctx.quadraticCurveTo(x, y + height, x, y + height - r)
  ctx.lineTo(x, y + r)
  ctx.quadraticCurveTo(x, y, x + r, y)
  ctx.closePath()
}

function drawArrow(ctx: CanvasRenderingContext2D, x: number, y: number, direction: 'in' | 'out', size: number) {
  const sign = direction === 'in' ? 1 : -1
  ctx.strokeStyle = '#ffffff'
  ctx.lineWidth = Math.max(1.5, size * 0.07)
  ctx.lineCap = 'square'
  ctx.beginPath()
  ctx.moveTo(x - sign * size * 0.32, y)
  ctx.lineTo(x + sign * size * 0.28, y)
  ctx.moveTo(x + sign * size * 0.28, y)
  ctx.lineTo(x + sign * size * 0.05, y - size * 0.2)
  ctx.moveTo(x + sign * size * 0.28, y)
  ctx.lineTo(x + sign * size * 0.05, y + size * 0.2)
  ctx.stroke()
}

export function GridCanvas({
  world,
  robots,
  tasks = [],
  conflicts,
  obstacles,
  tick,
  tickMs = 500,
  selected,
  showMeshLinks = true,
  theme = 'dark',
  isFullscreen = false,
  onToggleFullscreen,
  onRobot,
  onCell,
  cameraFollow: propCameraFollow,
  onToggleCameraFollow,
}: Props) {
  const ref = useRef<HTMLCanvasElement>(null)
  const view = useRef<View>({ zoom: 1, panX: 0, panY: 0, drag: false, x: 0, y: 0, moved: false })
  const motion = useRef<Map<string, Motion>>(new Map())
  const [viewVersion, setViewVersion] = useState(0)
  const [duration, setDuration] = useState(tickMs)
  const [is3D, setIs3D] = useState(true)
  const [internalCameraFollow, setInternalCameraFollow] = useState(true)

  const cameraFollow = propCameraFollow !== undefined ? propCameraFollow : internalCameraFollow
  const handleToggleCameraFollow = onToggleCameraFollow ?? (() => setInternalCameraFollow((prev) => !prev))


  const handleZoomIn = () => {
    view.current.zoom = Math.min(2.5, Number((view.current.zoom + 0.15).toFixed(2)))
    setViewVersion((v) => v + 1)
  }

  const handleZoomOut = () => {
    view.current.zoom = Math.max(0.5, Number((view.current.zoom - 0.15).toFixed(2)))
    setViewVersion((v) => v + 1)
  }

  const handleResetView = () => {
    view.current.zoom = 1
    view.current.panX = 0
    view.current.panY = 0
    setViewVersion((v) => v + 1)
  }

  useEffect(() => {
    const handleResize = () => {
      setViewVersion((v) => v + 1)
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  const palette = theme === 'light' ? lightPalette : darkPalette

  // Precompute warehouse zones for ultra-fast rendering
  const { chargingSet, pickupSet, dropoffSet, nearObstacleSet } = useMemo(() => {
    const chg = new Set(world.charging_stations.map((p) => `${p.x},${p.y}`))
    const pic = new Set(world.pickup_stations.map((p) => `${p.x},${p.y}`))
    const drp = new Set(world.dropoff_stations.map((p) => `${p.x},${p.y}`))
    const near = new Set<string>()
    world.static_obstacles.forEach((p) => {
      near.add(`${p.x + 1},${p.y}`)
      near.add(`${p.x - 1},${p.y}`)
      near.add(`${p.x},${p.y + 1}`)
      near.add(`${p.x},${p.y - 1}`)
    })
    return { chargingSet: chg, pickupSet: pic, dropoffSet: drp, nearObstacleSet: near }
  }, [world])

  useEffect(() => {
    void api.status().then((status) => setDuration(status.tick_ms)).catch(() => undefined)
  }, [])

  useEffect(() => {
    const now = performance.now()
    robots.forEach((robot) => {
      const nextAngle = headingAngle[robot.heading]
      const previous = motion.current.get(robot.robot_id)
      if (!previous) {
        motion.current.set(robot.robot_id, {
          from: robot.position,
          to: robot.position,
          fromAngle: nextAngle,
          toAngle: nextAngle,
          started: now,
          duration,
        })
        return
      }
      const elapsed = Math.min(1, Math.max(0, (now - previous.started) / previous.duration))
      const currentAngle = previous.fromAngle + (previous.toAngle - previous.fromAngle) * elapsed
      const currentPosition = {
        x: previous.from.x + (previous.to.x - previous.from.x) * elapsed,
        y: previous.from.y + (previous.to.y - previous.from.y) * elapsed,
      }
      const angleDelta = Math.atan2(Math.sin(nextAngle - currentAngle), Math.cos(nextAngle - currentAngle))
      motion.current.set(robot.robot_id, {
        from: currentPosition,
        to: robot.position,
        fromAngle: currentAngle,
        toAngle: currentAngle + angleDelta,
        started: now,
        duration: Math.max(50, duration),
      })
    })
    const activeIds = new Set(robots.map((robot) => robot.robot_id))
    motion.current.forEach((_, robotId) => {
      if (!activeIds.has(robotId)) motion.current.delete(robotId)
    })
  }, [robots, tick, duration])

  useEffect(() => {
    let frame = 0
    const animate = () => {
      setViewVersion((version) => version + 1)
      frame = window.requestAnimationFrame(animate)
    }
    frame = window.requestAnimationFrame(animate)
    return () => window.cancelAnimationFrame(frame)
  }, [])

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const parent = canvas.parentElement
    if (!parent) return
    const dpr = window.devicePixelRatio || 1
    const width = parent.clientWidth
    const height = parent.clientHeight
    canvas.width = width * dpr
    canvas.height = height * dpr
    canvas.style.width = `${width}px`
    canvas.style.height = `${height}px`
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.fillStyle = palette.background
    ctx.fillRect(0, 0, width, height)

    const current = view.current
    const base =
      Math.min((width / (world.width + world.height)) * 2.05, (height / (world.width + world.height)) * 1.62) *
      current.zoom
    const tileW = base * 1.38
    const tileH = base * 0.72
    const lift = base * 0.9
    // Vertically and horizontally center the 30x30 isometric floor on canvas
    const originX = width / 2 + current.panX
    const originY = height / 2 - ((world.width + world.height - 2) * tileH) / 4 + current.panY
    const projection: Projection = {
      originX,
      originY,
      tileW,
      tileH,
      lift,
    }
    const at = (point: Point, z = 0) => ({
      x: projection.originX + ((point.x - point.y) * projection.tileW) / 2,
      y: projection.originY + ((point.x + point.y) * projection.tileH) / 2 - z,
    })
    const cellCenter = (point: Point) => at({ x: point.x + 0.5, y: point.y + 0.5 })

    // 1. Warehouse Grid Mesh with subtle zone tinting and shadow cues
    ctx.lineWidth = 1
    for (let x = 0; x < world.width; x++) {
      for (let y = 0; y < world.height; y++) {
        const center = cellCenter({ x, y })
        const key = `${x},${y}`
        diamond(ctx, center.x, center.y, projection.tileW, projection.tileH)

        if (nearObstacleSet.has(key)) {
          ctx.fillStyle = palette.floorShadow
        } else if ((x + y) % 2) {
          ctx.fillStyle = palette.floor
        } else {
          ctx.fillStyle = palette.floorAlt
        }
        ctx.fill()

        // Subtle zone tints
        if (pickupSet.has(key)) {
          ctx.fillStyle = theme === 'light' ? 'rgba(255, 107, 53, 0.14)' : 'rgba(255, 107, 53, 0.18)'
          ctx.fill()
        } else if (dropoffSet.has(key)) {
          ctx.fillStyle = theme === 'light' ? 'rgba(255, 195, 0, 0.14)' : 'rgba(255, 195, 0, 0.18)'
          ctx.fill()
        } else if (chargingSet.has(key)) {
          ctx.fillStyle = theme === 'light' ? 'rgba(22, 163, 74, 0.15)' : 'rgba(22, 163, 74, 0.20)'
          ctx.fill()
        }

        // Industrial safety cross-highway chevron hash markings at intersection junctions
        const isJunction =
          (x === 10 || x === 19) &&
          (y === 5 || y === 8 || y === 10 || y === 13 || y === 15 || y === 18 || y === 20 || y === 23)
        if (isJunction) {
          ctx.save()
          ctx.beginPath()
          diamond(ctx, center.x, center.y, projection.tileW * 0.9, projection.tileH * 0.9)
          ctx.clip()
          ctx.strokeStyle = theme === 'light' ? 'rgba(255, 107, 53, 0.45)' : 'rgba(255, 195, 0, 0.38)'
          ctx.lineWidth = 1.8
          const tw = projection.tileW * 0.5
          for (let s = -tw; s <= tw; s += 6) {
            ctx.beginPath()
            ctx.moveTo(center.x + s - 6, center.y - projection.tileH * 0.45)
            ctx.lineTo(center.x + s + 6, center.y + projection.tileH * 0.45)
            ctx.stroke()
          }
          ctx.restore()
        }

        ctx.strokeStyle = palette.gridSoft
        ctx.stroke()
      }
    }

    // 2. Fixed Stations & Perimeter Warehouse Loading Docks
    const getGateLabel = (point: Point, label: 'in' | 'out'): { bayLabel: string; isCenter: boolean } => {
      if (label === 'in') {
        if (point.y >= 8 && point.y <= 10) return { bayLabel: 'IN-1', isCenter: point.y === 9 }
        if (point.y >= 13 && point.y <= 15) return { bayLabel: 'IN-2', isCenter: point.y === 14 }
        if (point.y >= 18 && point.y <= 20) return { bayLabel: 'IN-3', isCenter: point.y === 19 }
        return { bayLabel: 'IN', isCenter: true }
      } else {
        if (point.y >= 8 && point.y <= 10) return { bayLabel: 'OUT-1', isCenter: point.y === 9 }
        if (point.y >= 13 && point.y <= 15) return { bayLabel: 'OUT-2', isCenter: point.y === 14 }
        if (point.y >= 18 && point.y <= 20) return { bayLabel: 'OUT-3', isCenter: point.y === 19 }
        return { bayLabel: 'OUT', isCenter: true }
      }
    }

    const drawStationBlock = (point: Point, color: string, label: 'in' | 'out') => {
      const center = cellCenter(point)
      const w = projection.tileW * 0.72
      const h = projection.tileH * 0.60
      const depth = projection.lift * 0.28
      const { bayLabel, isCenter } = getGateLabel(point, label)

      // Soft contact shadow
      diamond(ctx, center.x, center.y, w * 1.15, h * 1.15)
      ctx.fillStyle = theme === 'light' ? 'rgba(0,0,0,0.06)' : 'rgba(0,0,0,0.35)'
      ctx.fill()

      // Primary dock platform plate
      ctx.fillStyle = color
      diamond(ctx, center.x, center.y - depth, w, h)
      ctx.fill()
      ctx.fillStyle = theme === 'light' ? '#737373' : '#262626'
      ctx.beginPath()
      ctx.moveTo(center.x - w / 2, center.y - depth)
      ctx.lineTo(center.x, center.y - depth + h / 2)
      ctx.lineTo(center.x, center.y + h / 2)
      ctx.lineTo(center.x - w / 2, center.y)
      ctx.closePath()
      ctx.fill()
      ctx.fillStyle = theme === 'light' ? '#525252' : '#171717'
      ctx.beginPath()
      ctx.moveTo(center.x, center.y - depth + h / 2)
      ctx.lineTo(center.x + w / 2, center.y - depth)
      ctx.lineTo(center.x + w / 2, center.y)
      ctx.lineTo(center.x, center.y + h / 2)
      ctx.closePath()
      ctx.fill()

      drawArrow(ctx, center.x, center.y - depth, label, Math.min(w, h) * 0.65)
      ctx.fillStyle = '#ffffff'
      ctx.font = isCenter
        ? `700 ${Math.max(8, projection.tileH * 0.16)}px 'JetBrains Mono', monospace`
        : `600 ${Math.max(6, projection.tileH * 0.12)}px 'JetBrains Mono', monospace`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(isCenter ? bayLabel : label === 'in' ? '▲ IN' : '▼ OUT', center.x, center.y + projection.tileH * 0.18)
    }

    world.charging_stations.forEach((point, idx) => {
      const center = cellCenter(point)
      const w = projection.tileW * 0.48
      const h = projection.tileH * 0.42
      const depth = projection.lift * 0.22

      // Soft contact shadow
      diamond(ctx, center.x, center.y, w * 1.2, h * 1.2)
      ctx.fillStyle = theme === 'light' ? 'rgba(0,0,0,0.06)' : 'rgba(0,0,0,0.4)'
      ctx.fill()

      // Outer safety green charging pad ring
      diamond(ctx, center.x, center.y - depth * 0.5, w * 1.1, h * 1.1)
      ctx.fillStyle = theme === 'light' ? 'rgba(22, 163, 74, 0.25)' : 'rgba(22, 163, 74, 0.35)'
      ctx.fill()
      ctx.strokeStyle = palette.charger
      ctx.lineWidth = 1.5
      ctx.stroke()

      // Primary charging pad surface
      ctx.fillStyle = palette.charger
      diamond(ctx, center.x, center.y - depth, w, h)
      ctx.fill()

      // Pad beveled edge left
      ctx.fillStyle = theme === 'light' ? '#737373' : '#262626'
      ctx.beginPath()
      ctx.moveTo(center.x - w / 2, center.y - depth)
      ctx.lineTo(center.x, center.y - depth + h / 2)
      ctx.lineTo(center.x, center.y + h / 2)
      ctx.lineTo(center.x - w / 2, center.y)
      ctx.closePath()
      ctx.fill()

      // Pad beveled edge right
      ctx.fillStyle = theme === 'light' ? '#525252' : '#171717'
      ctx.beginPath()
      ctx.moveTo(center.x, center.y - depth + h / 2)
      ctx.lineTo(center.x + w / 2, center.y - depth)
      ctx.lineTo(center.x + w / 2, center.y)
      ctx.lineTo(center.x, center.y + h / 2)
      ctx.closePath()
      ctx.fill()

      // Electric Lightning Bolt Symbol on Pad
      ctx.save()
      const bx = center.x
      const by = center.y - depth
      const bs = Math.min(w, h) * 0.36
      ctx.fillStyle = '#fef08a'
      ctx.strokeStyle = '#eab308'
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(bx + bs * 0.08, by - bs * 0.5)
      ctx.lineTo(bx - bs * 0.32, by + bs * 0.04)
      ctx.lineTo(bx, by + bs * 0.04)
      ctx.lineTo(bx - bs * 0.08, by + bs * 0.5)
      ctx.lineTo(bx + bs * 0.32, by - bs * 0.04)
      ctx.lineTo(bx, by - bs * 0.04)
      ctx.closePath()
      ctx.fill()
      ctx.stroke()
      ctx.restore()

      // Label under pad
      ctx.fillStyle = '#ffffff'
      ctx.font = `700 ${Math.max(6, projection.tileH * 0.14)}px 'JetBrains Mono', monospace`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(`⚡ CHG-${idx + 1}`, center.x, center.y + projection.tileH * 0.2)
    })

    world.pickup_stations.forEach((point) => drawStationBlock(point, palette.import, 'in'))
    world.dropoff_stations.forEach((point) => drawStationBlock(point, palette.export, 'out'))

    // 3. Static Warehouse Pallet Racking (Taller, Multi-Tier Shelves with Totes)
    world.static_obstacles.forEach((point) => {
      const center = cellCenter(point)
      const w = projection.tileW * 0.82
      const h = projection.tileH * 0.72
      const depth = projection.lift * 1.35

      // Ground contact drop shadow
      diamond(ctx, center.x + 2, center.y + 2, w * 1.08, h * 1.08)
      ctx.fillStyle = theme === 'light' ? 'rgba(0,0,0,0.08)' : 'rgba(0,0,0,0.45)'
      ctx.fill()

      // Top roof/cap of racking
      diamond(ctx, center.x, center.y - depth, w, h)
      ctx.fillStyle = palette.steelTop
      ctx.fill()
      ctx.strokeStyle = palette.steel
      ctx.stroke()

      // Left face of the rack
      ctx.fillStyle = palette.steelDark
      ctx.beginPath()
      ctx.moveTo(center.x - w / 2, center.y - depth)
      ctx.lineTo(center.x, center.y - depth + h / 2)
      ctx.lineTo(center.x, center.y + h / 2)
      ctx.lineTo(center.x - w / 2, center.y)
      ctx.closePath()
      ctx.fill()

      // Right face of the rack
      ctx.fillStyle = theme === 'light' ? '#737373' : '#171717'
      ctx.beginPath()
      ctx.moveTo(center.x, center.y - depth + h / 2)
      ctx.lineTo(center.x + w / 2, center.y - depth)
      ctx.lineTo(center.x + w / 2, center.y)
      ctx.lineTo(center.x, center.y + h / 2)
      ctx.closePath()
      ctx.fill()

      // Visible Shelf Tiers & Uprights (Industrial Racking Realism)
      ctx.strokeStyle = theme === 'light' ? '#d4d4d4' : '#404040'
      ctx.lineWidth = 1.2
      const tiers = [0.3, 0.65, 0.95]
      tiers.forEach((tier) => {
        const tierOffset = depth * tier
        // Left horizontal beam
        ctx.beginPath()
        ctx.moveTo(center.x - w / 2, center.y - tierOffset)
        ctx.lineTo(center.x, center.y - tierOffset + h / 2)
        ctx.stroke()
        // Right horizontal beam
        ctx.beginPath()
        ctx.moveTo(center.x, center.y - tierOffset + h / 2)
        ctx.lineTo(center.x + w / 2, center.y - tierOffset)
        ctx.stroke()

        // Storage totes on shelf tier
        const toteW = w * 0.16
        const toteH = h * 0.16
        // Left shelf tote (warm amber/orange)
        ctx.fillStyle = tier === 0.3 ? '#d97706' : tier === 0.65 ? '#ea580c' : '#737373'
        roundedBox(ctx, center.x - w * 0.28, center.y - tierOffset + h * 0.1, toteW, toteH, 2)
        ctx.fill()
        // Right shelf tote
        ctx.fillStyle = tier === 0.3 ? '#737373' : '#d97706'
        roundedBox(ctx, center.x + w * 0.12, center.y - tierOffset + h * 0.1, toteW, toteH, 2)
        ctx.fill()
      })

      // Vertical corner upright struts
      ctx.strokeStyle = theme === 'light' ? '#a3a3a3' : '#525252'
      ctx.lineWidth = 1.5
      ctx.beginPath()
      ctx.moveTo(center.x, center.y - depth + h / 2)
      ctx.lineTo(center.x, center.y + h / 2)
      ctx.moveTo(center.x - w / 2, center.y - depth)
      ctx.lineTo(center.x - w / 2, center.y)
      ctx.moveTo(center.x + w / 2, center.y - depth)
      ctx.lineTo(center.x + w / 2, center.y)
      ctx.stroke()
    })

    // 3.5. Pod Storage Yard (Industrial Multi-Tier Shelves / Movable Pods)
    if (world.pod_slots && Array.isArray(world.pod_slots)) {
      world.pod_slots.forEach((slot) => {
        const center = cellCenter({ x: slot.x, y: slot.y })
        const w = projection.tileW * 0.82
        const h = projection.tileH * 0.72
        const depth = projection.lift * 1.35

        // Check if pod is currently carried by any AMR
        const isCarried = robots.some((r) => r.carrying_pod_id === slot.shelf_id)

        if (isCarried) {
          // Empty slot footprint (walkable cell with subtle dashed floor marking)
          ctx.save()
          diamond(ctx, center.x, center.y, w * 0.85, h * 0.85)
          ctx.strokeStyle = theme === 'light' ? 'rgba(217, 119, 6, 0.35)' : 'rgba(251, 146, 60, 0.35)'
          ctx.lineWidth = 1.2
          ctx.setLineDash([3, 2])
          ctx.stroke()
          ctx.fillStyle = theme === 'light' ? 'rgba(217, 119, 6, 0.04)' : 'rgba(251, 146, 60, 0.06)'
          ctx.fill()
          ctx.restore()
        } else {
          // Resting industrial shelf pod (Pallet Racking with Steel Uprights & Storage Totes)
          diamond(ctx, center.x + 2, center.y + 2, w * 1.08, h * 1.08)
          ctx.fillStyle = theme === 'light' ? 'rgba(0,0,0,0.08)' : 'rgba(0,0,0,0.45)'
          ctx.fill()

          // Top roof/cap of racking
          diamond(ctx, center.x, center.y - depth, w, h)
          ctx.fillStyle = palette.steelTop
          ctx.fill()
          ctx.strokeStyle = palette.steel
          ctx.stroke()

          // Left face of the rack
          ctx.fillStyle = palette.steelDark
          ctx.beginPath()
          ctx.moveTo(center.x - w / 2, center.y - depth)
          ctx.lineTo(center.x, center.y - depth + h / 2)
          ctx.lineTo(center.x, center.y + h / 2)
          ctx.lineTo(center.x - w / 2, center.y)
          ctx.closePath()
          ctx.fill()

          // Right face of the rack
          ctx.fillStyle = theme === 'light' ? '#737373' : '#171717'
          ctx.beginPath()
          ctx.moveTo(center.x, center.y - depth + h / 2)
          ctx.lineTo(center.x + w / 2, center.y - depth)
          ctx.lineTo(center.x + w / 2, center.y)
          ctx.lineTo(center.x, center.y + h / 2)
          ctx.closePath()
          ctx.fill()

          // Visible Shelf Tiers & Uprights (Industrial Racking Realism)
          ctx.strokeStyle = theme === 'light' ? '#d4d4d4' : '#404040'
          ctx.lineWidth = 1.2
          const tiers = [0.3, 0.65, 0.95]
          tiers.forEach((tier) => {
            const tierOffset = depth * tier
            ctx.beginPath()
            ctx.moveTo(center.x - w / 2, center.y - tierOffset)
            ctx.lineTo(center.x, center.y - tierOffset + h / 2)
            ctx.stroke()
            ctx.beginPath()
            ctx.moveTo(center.x, center.y - tierOffset + h / 2)
            ctx.lineTo(center.x + w / 2, center.y - tierOffset)
            ctx.stroke()

            // Storage totes on shelf tier
            const toteW = w * 0.16
            const toteH = h * 0.16
            ctx.fillStyle = tier === 0.3 ? '#d97706' : tier === 0.65 ? '#ea580c' : '#737373'
            roundedBox(ctx, center.x - w * 0.28, center.y - tierOffset + h * 0.1, toteW, toteH, 2)
            ctx.fill()
            ctx.fillStyle = tier === 0.3 ? '#737373' : '#d97706'
            roundedBox(ctx, center.x + w * 0.12, center.y - tierOffset + h * 0.1, toteW, toteH, 2)
            ctx.fill()
          })

          // Vertical corner upright struts
          ctx.strokeStyle = theme === 'light' ? '#a3a3a3' : '#525252'
          ctx.lineWidth = 1.5
          ctx.beginPath()
          ctx.moveTo(center.x, center.y - depth + h / 2)
          ctx.lineTo(center.x, center.y + h / 2)
          ctx.moveTo(center.x - w / 2, center.y - depth)
          ctx.lineTo(center.x - w / 2, center.y)
          ctx.moveTo(center.x + w / 2, center.y - depth)
          ctx.lineTo(center.x + w / 2, center.y)
          ctx.stroke()
        }
      })
    }

    // 3.6. Sortation Chutes (Put-wall destination bins)
    if (world.sortation_chutes && typeof world.sortation_chutes === 'object') {
      Object.entries(world.sortation_chutes).forEach(([chuteId, info]) => {
        const center = cellCenter({ x: info.x, y: info.y })
        const w = projection.tileW * 0.72
        const h = projection.tileH * 0.58
        const depth = projection.lift * 0.35

        diamond(ctx, center.x, center.y - depth, w, h)
        ctx.fillStyle = '#0284c7'
        ctx.fill()
        ctx.strokeStyle = '#38bdf8'
        ctx.lineWidth = 1.2
        ctx.stroke()

        ctx.fillStyle = '#ffffff'
        ctx.font = `700 ${Math.max(6, projection.tileH * 0.12)}px 'JetBrains Mono', monospace`
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillText(chuteId.replace('CHUTE-', 'C'), center.x, center.y - depth)
      })
    }

    // 4. Temporary Dynamic Obstacles
    obstacles.forEach((obstacle) => {
      const center = cellCenter(obstacle.position)
      const remaining = Math.max(0, obstacle.expires_at_tick - tick)

      diamond(ctx, center.x, center.y, projection.tileW * 0.65, projection.tileH * 0.52)
      ctx.fillStyle = theme === 'light' ? 'rgba(0,0,0,0.08)' : 'rgba(0,0,0,0.4)'
      ctx.fill()

      ctx.fillStyle = remaining < 10 ? '#ea580c' : palette.hazard
      diamond(ctx, center.x, center.y - projection.lift * 0.14, projection.tileW * 0.62, projection.tileH * 0.5)
      ctx.fill()
      ctx.fillStyle = '#ffffff'
      ctx.font = `600 ${Math.max(8, base * 0.18)}px 'JetBrains Mono', monospace`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(String(remaining), center.x, center.y - projection.lift * 0.14)
    })

    // 5. Active Task Pickup & Dropoff Specific Markers
    tasks.forEach((task) => {
      if (task.status === 'COMPLETED' || task.status === 'CANCELLED') return
      const pCenter = cellCenter(task.pickup)
      const dCenter = cellCenter(task.dropoff)

      // Pickup Target Marker
      ctx.strokeStyle = '#ea580c'
      ctx.lineWidth = 1.5
      diamond(ctx, pCenter.x, pCenter.y - 2, projection.tileW * 0.45, projection.tileH * 0.45)
      ctx.stroke()
      ctx.fillStyle = '#ea580c'
      ctx.font = `600 ${Math.max(7, base * 0.12)}px 'JetBrains Mono', monospace`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(`P:${task.task_id.slice(-4)}`, pCenter.x, pCenter.y - projection.tileH * 0.3)

      // Dropoff Target Marker
      ctx.strokeStyle = '#d97706'
      ctx.lineWidth = 1.5
      diamond(ctx, dCenter.x, dCenter.y - 2, projection.tileW * 0.45, projection.tileH * 0.45)
      ctx.stroke()
      ctx.fillStyle = '#d97706'
      ctx.font = `600 ${Math.max(7, base * 0.12)}px 'JetBrains Mono', monospace`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(`D:${task.task_id.slice(-4)}`, dCenter.x, dCenter.y - projection.tileH * 0.3)
    })

    // 6. Decentralized Conflict Warnings & P2P Arbitration Beacons
    conflicts.forEach((conflict) => {
      const center = cellCenter(conflict.cell)
      const markerW = projection.tileW * 0.76
      const markerH = projection.tileH * 0.62

      const now = performance.now()
      const pulse1 = (now % 1000) / 1000
      const pulse2 = ((now + 500) % 1000) / 1000

      // Outer pulsing arbitration rings
      ctx.save()
      ctx.beginPath()
      ctx.ellipse(center.x, center.y - projection.lift * 0.13, markerW * (0.6 + pulse1 * 0.8), markerH * (0.6 + pulse1 * 0.8), 0, 0, Math.PI * 2)
      ctx.strokeStyle = `rgba(239, 68, 68, ${Math.max(0, 1 - pulse1)})`
      ctx.lineWidth = 1.8
      ctx.stroke()

      ctx.beginPath()
      ctx.ellipse(center.x, center.y - projection.lift * 0.13, markerW * (0.6 + pulse2 * 0.8), markerH * (0.6 + pulse2 * 0.8), 0, 0, Math.PI * 2)
      ctx.strokeStyle = `rgba(245, 158, 11, ${Math.max(0, 1 - pulse2)})`
      ctx.lineWidth = 1.4
      ctx.stroke()
      ctx.restore()

      ctx.fillStyle = theme === 'light' ? '#ea580c' : '#c2410c'
      diamond(ctx, center.x, center.y - projection.lift * 0.13, markerW, markerH)
      ctx.fill()
      ctx.strokeStyle = '#fed7aa'
      ctx.lineWidth = 1.5
      ctx.stroke()
      ctx.fillStyle = '#ffffff'
      ctx.font = `700 ${Math.max(7, base * 0.13)}px 'JetBrains Mono', monospace`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText('P2P ARBITRATION', center.x, center.y - projection.lift * 0.13 - markerH * 0.12)
      ctx.font = `600 ${Math.max(6, base * 0.1)}px 'JetBrains Mono', monospace`
      ctx.fillText(
        (conflict.robot_ids ?? []).join(' ⇄ ') || 'Resolving',
        center.x,
        center.y - projection.lift * 0.13 + markerH * 0.18
      )
    })

    // 6.5. Autonomous P2P Edge Mesh Communication Beams & Traveling UDP Packets
    if (showMeshLinks) {
      const now = performance.now()
      for (let i = 0; i < robots.length; i++) {
        for (let j = i + 1; j < robots.length; j++) {
          const r1 = robots[i]
          const r2 = robots[j]
          const dx = Math.abs(r1.position.x - r2.position.x)
          const dy = Math.abs(r1.position.y - r2.position.y)
          const manhattan = dx + dy
          if (manhattan <= 6) {
            const t1 = motion.current.get(r1.robot_id)
            const p1 = t1 ? Math.min(1, Math.max(0, (now - t1.started) / t1.duration)) : 1
            const pos1 = t1 ? { x: t1.from.x + (t1.to.x - t1.from.x) * p1, y: t1.from.y + (t1.to.y - t1.from.y) * p1 } : r1.position

            const t2 = motion.current.get(r2.robot_id)
            const p2 = t2 ? Math.min(1, Math.max(0, (now - t2.started) / t2.duration)) : 1
            const pos2 = t2 ? { x: t2.from.x + (t2.to.x - t2.from.x) * p2, y: t2.from.y + (t2.to.y - t2.from.y) * p2 } : r2.position

            const c1 = cellCenter(pos1)
            const c2 = cellCenter(pos2)
            const zOffset = projection.lift * 0.28

            const isConflicted = r1.state === 'CONFLICT_NEGOTIATING' || r2.state === 'CONFLICT_NEGOTIATING'
            const linkColor = isConflicted
              ? 'rgba(220, 38, 38, 0.75)'
              : 'rgba(255, 195, 0, 0.55)'

            ctx.save()
            ctx.beginPath()
            ctx.moveTo(c1.x, c1.y - zOffset)
            ctx.lineTo(c2.x, c2.y - zOffset)
            ctx.strokeStyle = linkColor
            ctx.lineWidth = isConflicted ? 2.2 : 1.4
            if (!isConflicted) {
              ctx.setLineDash([4, 4])
            }
            ctx.stroke()

            // Traveling UDP Claim Packet
            const packetPhase = ((now * 0.0016 + (i * 3 + j * 5)) % 1)
            const px = c1.x + (c2.x - c1.x) * packetPhase
            const py = (c1.y - zOffset) + ((c2.y - zOffset) - (c1.y - zOffset)) * packetPhase

            ctx.beginPath()
            ctx.arc(px, py, isConflicted ? 3.5 : 2.5, 0, Math.PI * 2)
            ctx.fillStyle = isConflicted ? '#DC2626' : '#FFC300'
            ctx.shadowColor = isConflicted ? '#B91C1C' : '#FF6B35'
            ctx.shadowBlur = 6
            ctx.fill()
            ctx.restore()
          }
        }
      }
    }

    // Map active tasks to assigned robots for destination guide lines
    const taskMap = new Map<string, Task>()
    tasks.forEach((t) => {
      if (t.assigned_robot_id && t.status !== 'COMPLETED' && t.status !== 'CANCELLED') {
        taskMap.set(t.assigned_robot_id, t)
      }
    })

    // 7. Robots: Motion Interpolation, Planned Paths, Carried Shelves, and Sprites
    robots.forEach((robot) => {
      const transition = motion.current.get(robot.robot_id)
      const progress = transition
        ? Math.min(1, Math.max(0, (performance.now() - transition.started) / transition.duration))
        : 1
      const renderedPosition = transition
        ? {
            x: transition.from.x + (transition.to.x - transition.from.x) * progress,
            y: transition.from.y + (transition.to.y - transition.from.y) * progress,
          }
        : robot.position
      const renderedAngle = transition
        ? transition.fromAngle + (transition.toAngle - transition.fromAngle) * progress
        : headingAngle[robot.heading]
      const center = cellCenter(renderedPosition)
      const color = ROBOT_TYPE_COLORS[robot.robot_type] || palette.steel
      const isSelected = robot.robot_id === selected

      // Draw Robot Planned Path Trail
      if (robot.path && robot.path.length > 1) {
        ctx.save()
        ctx.beginPath()
        ctx.moveTo(center.x, center.y)
        robot.path.forEach((node, idx) => {
          const pt = cellCenter(node)
          ctx.lineTo(pt.x, pt.y)
        })
        ctx.strokeStyle = color
        ctx.globalAlpha = isSelected ? 0.75 : 0.35
        ctx.lineWidth = isSelected ? 2.5 : 1.5
        ctx.setLineDash([4, 3])
        ctx.stroke()
        ctx.setLineDash([])

        // Waypoint nodes along the path
        robot.path.forEach((node, idx) => {
          if (idx === 0) return
          const pt = cellCenter(node)
          ctx.beginPath()
          ctx.arc(pt.x, pt.y, isSelected ? 3 : 2, 0, Math.PI * 2)
          ctx.fillStyle = color
          ctx.globalAlpha = Math.max(0.2, (isSelected ? 0.8 : 0.45) - idx * 0.04)
          ctx.fill()
        })
        ctx.restore()
      }

      // Guide line connecting assigned robot to task destination - ONLY when robot is selected
      if (isSelected) {
        const activeTask = taskMap.get(robot.robot_id)
        if (activeTask) {
          const isHeadingToDropoff = robot.state === 'EN_ROUTE_DROPOFF' || robot.state === 'DROPPING'
          const targetPt = cellCenter(isHeadingToDropoff ? activeTask.dropoff : activeTask.pickup)
          ctx.save()
          ctx.beginPath()
          ctx.moveTo(center.x, center.y)
          ctx.lineTo(targetPt.x, targetPt.y)
          ctx.strokeStyle = isHeadingToDropoff ? '#FFC300' : '#FF6B35'
          ctx.lineWidth = 1.6
          ctx.globalAlpha = 0.7
          ctx.setLineDash([3, 3])
          ctx.stroke()
          ctx.restore()
        }
      }

      // Robot ground contact: Soft ambient occlusion shadow + sharp contact shadow
      diamond(ctx, center.x, center.y + 3, projection.tileW * 0.52, projection.tileH * 0.40)
      ctx.fillStyle = theme === 'light' ? 'rgba(0,0,0,0.10)' : 'rgba(0,0,0,0.55)'
      ctx.fill()

      diamond(ctx, center.x, center.y + 1, projection.tileW * 0.38, projection.tileH * 0.28)
      ctx.fillStyle = theme === 'light' ? 'rgba(0,0,0,0.20)' : 'rgba(0,0,0,0.70)'
      ctx.fill()

      const podW = projection.tileW * 0.38
      const podH = projection.tileH * 0.32
      const z = projection.lift * 0.34

      // AMR 2.5D Layered Chassis
      ctx.save()
      ctx.translate(center.x, center.y - z)
      ctx.rotate(renderedAngle)

      // 1. Four corner casters / wheels peeking out from under the chassis body
      const casterW = podW * 0.16
      const casterH = podH * 0.24
      const cxOffset = podW * 0.40
      const cyOffset = podH * 0.36
      ctx.fillStyle = '#0D0D0D'
      // Top-left caster
      roundedBox(ctx, -cxOffset - casterW / 2, -cyOffset - casterH / 2, casterW, casterH, 2)
      ctx.fill()
      // Top-right caster
      roundedBox(ctx, cxOffset - casterW / 2, -cyOffset - casterH / 2, casterW, casterH, 2)
      ctx.fill()
      // Bottom-left caster
      roundedBox(ctx, -cxOffset - casterW / 2, cyOffset - casterH / 2, casterW, casterH, 2)
      ctx.fill()
      // Bottom-right caster
      roundedBox(ctx, cxOffset - casterW / 2, cyOffset - casterH / 2, casterW, casterH, 2)
      ctx.fill()
      // Metallic wheel hubs
      ctx.fillStyle = '#282828'
      ctx.fillRect(-cxOffset - casterW * 0.2, -cyOffset - casterH * 0.3, casterW * 0.4, casterH * 0.6)
      ctx.fillRect(cxOffset - casterW * 0.2, -cyOffset - casterH * 0.3, casterW * 0.4, casterH * 0.6)
      ctx.fillRect(-cxOffset - casterW * 0.2, cyOffset - casterH * 0.3, casterW * 0.4, casterH * 0.6)
      ctx.fillRect(cxOffset - casterW * 0.2, cyOffset - casterH * 0.3, casterW * 0.4, casterH * 0.6)

      // 2. Protective lower bumper skirt
      ctx.fillStyle = theme === 'light' ? '#262626' : '#141414'
      roundedBox(ctx, -podW * 0.52, -podH * 0.52 + 2, podW * 1.04, podH * 1.04, podH * 0.28)
      ctx.fill()

      // 3. Main AMR Body Hull (Solid Saturated Depot Signal color)
      ctx.fillStyle = color
      roundedBox(ctx, -podW * 0.48, -podH * 0.48, podW * 0.96, podH * 0.96, podH * 0.24)
      ctx.fill()

      // 4. Distinct Top Plate Deck with subtle bevel highlight edge
      const topW = podW * 0.82
      const topH = podH * 0.80
      ctx.fillStyle = theme === 'light' ? '#D5D5D5' : '#222222'
      roundedBox(ctx, -topW / 2, -topH / 2, topW, topH, topH * 0.20)
      ctx.fill()

      // Top plate subtle bevel/highlight edge
      ctx.strokeStyle = theme === 'light' ? 'rgba(255, 255, 255, 0.90)' : 'rgba(255, 255, 255, 0.28)'
      ctx.lineWidth = 1.2
      ctx.stroke()

      // Turntable docking ring on deck
      ctx.beginPath()
      ctx.arc(0, 0, topH * 0.26, 0, Math.PI * 2)
      ctx.strokeStyle = theme === 'light' ? '#A3A3A3' : '#383838'
      ctx.lineWidth = 1
      ctx.stroke()

      ctx.restore()

      // 5. Separate, Elevated Directional Chevron floating above chassis
      // Bold, clearly legible status+heading combo rotating smoothly with heading
      const chevronZ = z + projection.lift * 0.30
      const chevLen = projection.tileW * 0.17
      const chevWidth = projection.tileH * 0.22
      const stateColor = STATE_COLORS[robot.state] || '#16A34A'

      ctx.save()
      ctx.translate(center.x, center.y - chevronZ)
      ctx.rotate(renderedAngle)

      // Drop shadow underneath the elevated floating chevron
      ctx.shadowColor = 'rgba(0, 0, 0, 0.55)'
      ctx.shadowBlur = 4
      ctx.shadowOffsetY = 2

      // Swept-back bold directional chevron
      ctx.beginPath()
      ctx.moveTo(chevLen * 0.9, 0)
      ctx.lineTo(-chevLen * 0.55, -chevWidth)
      ctx.lineTo(-chevLen * 0.10, 0)
      ctx.lineTo(-chevLen * 0.55, chevWidth)
      ctx.closePath()

      ctx.fillStyle = stateColor
      ctx.fill()

      // High contrast outline so chevron pops from any distance
      ctx.shadowColor = 'transparent'
      ctx.shadowBlur = 0
      ctx.shadowOffsetY = 0
      ctx.strokeStyle = '#111111'
      ctx.lineWidth = 1.6
      ctx.stroke()

      ctx.restore()

      // Goods-To-Person AMR Visually Carrying Inventory Shelf Pod
      const isCarryingPod =
        Boolean(robot.carrying_pod_id) ||
        (robot.robot_type === 'GOODS_TO_PERSON' &&
          (robot.state === 'EN_ROUTE_DROPOFF' || robot.state === 'DROPPING'))

      if (isCarryingPod) {
        const podLabel = robot.carrying_pod_id || 'POD'
        const shelfLift = z + projection.lift * 0.38
        const sW = projection.tileW * 0.44
        const sH = projection.tileH * 0.38
        const sDepth = projection.lift * 0.38

        // Shadow cast on AMR top surface
        diamond(ctx, center.x, center.y - z + 1, sW * 0.8, sH * 0.8)
        ctx.fillStyle = 'rgba(0, 0, 0, 0.4)'
        ctx.fill()

        // Raised warehouse inventory pod
        diamond(ctx, center.x, center.y - shelfLift - sDepth, sW, sH)
        ctx.fillStyle = '#fb923c'
        ctx.fill()
        ctx.strokeStyle = '#c2410c'
        ctx.lineWidth = 1
        ctx.stroke()

        // Pod left face with parcel accents
        ctx.fillStyle = '#ea580c'
        ctx.beginPath()
        ctx.moveTo(center.x - sW / 2, center.y - shelfLift - sDepth)
        ctx.lineTo(center.x, center.y - shelfLift - sDepth + sH / 2)
        ctx.lineTo(center.x, center.y - shelfLift + sH / 2)
        ctx.lineTo(center.x - sW / 2, center.y - shelfLift)
        ctx.closePath()
        ctx.fill()

        // Pod right face
        ctx.fillStyle = '#c2410c'
        ctx.beginPath()
        ctx.moveTo(center.x, center.y - shelfLift - sDepth + sH / 2)
        ctx.lineTo(center.x + sW / 2, center.y - shelfLift - sDepth)
        ctx.lineTo(center.x + sW / 2, center.y - shelfLift)
        ctx.lineTo(center.x, center.y - shelfLift + sH / 2)
        ctx.closePath()
        ctx.fill()

        // Pod ID text label on top face
        ctx.fillStyle = '#ffffff'
        ctx.font = `700 ${Math.max(6, base * 0.1)}px 'JetBrains Mono', monospace`
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillText(podLabel, center.x, center.y - shelfLift - sDepth)
      }

      // State Ring Indicator
      ctx.fillStyle = STATE_COLORS[robot.state] || '#737373'
      ctx.beginPath()
      ctx.arc(center.x, center.y + projection.tileH * 0.27, Math.max(3.5, base * 0.08), 0, Math.PI * 2)
      ctx.fill()

      // Selection or Conflict Indicator
      if (isSelected || robot.state === 'CONFLICT_NEGOTIATING') {
        ctx.strokeStyle = isSelected ? '#FF6B35' : '#DC2626'
        ctx.lineWidth = isSelected ? 2.5 : 1.8
        diamond(ctx, center.x, center.y - projection.lift * 0.34, projection.tileW * 0.57, projection.tileH * 0.45)
        ctx.stroke()
      }

      // Robot Numerical ID Tag
      ctx.fillStyle = palette.text
      ctx.font = `700 ${Math.max(8, base * 0.16)}px 'JetBrains Mono', monospace`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(robot.robot_id.replace('AMR-', ''), center.x, center.y + projection.tileH * 0.44)
    })
  }, [
    world,
    robots,
    tasks,
    conflicts,
    obstacles,
    tick,
    selected,
    viewVersion,
    theme,
    palette,
    chargingSet,
    pickupSet,
    dropoffSet,
    nearObstacleSet,
    isFullscreen,
  ])

  const hitPoint = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = ref.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const current = view.current
    if (current.moved) {
      current.moved = false
      return
    }
    const base =
      Math.min((rect.width / (world.width + world.height)) * 2.05, (rect.height / (world.width + world.height)) * 1.62) *
      current.zoom
    const tileW = base * 1.38
    const tileH = base * 0.72
    const originX = rect.width / 2 + current.panX
    const originY = rect.height / 2 - ((world.width + world.height - 2) * tileH) / 4 + current.panY
    const dx = event.clientX - rect.left - originX
    const dy = event.clientY - rect.top - originY
    const u = (2 * dx) / tileW
    const v = (2 * dy) / tileH - 1
    const x = Math.floor((u + v) / 2)
    const y = Math.floor((v - u) / 2)
    if (x < 0 || x >= world.width || y < 0 || y >= world.height) return
    const clickedRobot = robots.find((robot) => robot.position.x === x && robot.position.y === y)
    clickedRobot ? onRobot(clickedRobot) : onCell({ x, y })
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
      {is3D ? (
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
          onRobot={onRobot}
          onCell={onCell}
        />
      ) : (
        <canvas
          ref={ref}
          onClick={hitPoint}
          onPointerDown={(event) => {
            view.current.drag = true
            view.current.x = event.clientX
            view.current.y = event.clientY
            view.current.moved = false
            event.currentTarget.setPointerCapture(event.pointerId)
          }}
          onPointerMove={(event) => {
            const current = view.current
            if (!current.drag) return
            const dx = event.clientX - current.x
            const dy = event.clientY - current.y
            if (Math.abs(dx) + Math.abs(dy) > 2) current.moved = true
            current.panX += dx
            current.panY += dy
            current.x = event.clientX
            current.y = event.clientY
            setViewVersion((version) => version + 1)
          }}
          onPointerUp={(event) => {
            view.current.drag = false
            event.currentTarget.releasePointerCapture(event.pointerId)
          }}
          onWheel={(event) => {
            event.preventDefault()
            view.current.zoom = Math.max(0.5, Math.min(2.5, view.current.zoom + (event.deltaY > 0 ? -0.08 : 0.08)))
            setViewVersion((version) => version + 1)
          }}
        />
      )}

      {/* Floating Canvas Control HUD */}
      <div className="canvas-hud-controls">
        <button
          className={`canvas-hud-btn ${is3D ? 'active' : ''}`}
          onClick={() => setIs3D((prev) => !prev)}
          title={is3D ? 'Switch to 2D Plan View' : 'Switch to 3D Orbit View'}
          aria-label="Toggle 2D / 3D View"
          style={{ fontWeight: 700, fontSize: '11px', width: 'auto', padding: '0 8px', letterSpacing: '0.04em' }}
        >
          {is3D ? '3D VIEW' : '2D VIEW'}
        </button>

        {is3D && selected && (
          <button
            className={`canvas-hud-btn ${cameraFollow ? 'active' : ''}`}
            onClick={handleToggleCameraFollow}
            title={cameraFollow ? 'Disable Camera Follow' : 'Follow Selected AMR'}
            aria-label="Toggle Camera Follow"
          >
            <Crosshair size={14} />
          </button>
        )}

        {!is3D && (
          <>
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
              title="Reset View / Center"
              aria-label="Reset canvas view"
            >
              <RotateCcw size={13} />
            </button>
          </>
        )}

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
