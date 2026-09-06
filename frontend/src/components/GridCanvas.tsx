import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Conflict, Point, Robot, TempObstacle, World } from '../types'
import { ROBOT_TYPE_COLORS, STATE_COLORS } from '../state-meta'

type Props = { world: World; robots: Robot[]; conflicts: Conflict[]; obstacles: TempObstacle[]; tick: number; selected: string | null; onRobot: (robot: Robot) => void; onCell: (point: Point) => void }
type View = { zoom: number; panX: number; panY: number; drag: boolean; x: number; y: number; moved: boolean }
type Projection = { originX: number; originY: number; tileW: number; tileH: number; lift: number }
type Motion = { from: Point; to: Point; fromAngle: number; toAngle: number; started: number; duration: number }

const palette = { background: '#101a21', gridSoft: 'rgba(98, 126, 133, .25)', steel: '#60747d', steelTop: '#8a9ba0', steelDark: '#34454d', floor: '#17262d', floorAlt: '#1b2c34', import: '#b8783e', export: '#3f8b86', charger: '#55718a', hazard: '#b95c50', text: '#c8d4d5' }
const headingAngle = { NORTH: 0, EAST: Math.PI / 2, SOUTH: Math.PI, WEST: -Math.PI / 2 }

function diamond(ctx: CanvasRenderingContext2D, cx: number, cy: number, width: number, height: number) {
  ctx.beginPath(); ctx.moveTo(cx, cy - height / 2); ctx.lineTo(cx + width / 2, cy); ctx.lineTo(cx, cy + height / 2); ctx.lineTo(cx - width / 2, cy); ctx.closePath()
}

function roundedBox(ctx: CanvasRenderingContext2D, x: number, y: number, width: number, height: number, radius: number) {
  const r = Math.min(radius, width / 2, height / 2)
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.lineTo(x + width - r, y); ctx.quadraticCurveTo(x + width, y, x + width, y + r); ctx.lineTo(x + width, y + height - r); ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height); ctx.lineTo(x + r, y + height); ctx.quadraticCurveTo(x, y + height, x, y + height - r); ctx.lineTo(x, y + r); ctx.quadraticCurveTo(x, y, x + r, y); ctx.closePath()
}

function drawArrow(ctx: CanvasRenderingContext2D, x: number, y: number, direction: 'in' | 'out', size: number) {
  const sign = direction === 'in' ? 1 : -1
  ctx.strokeStyle = '#e7dfd2'; ctx.lineWidth = Math.max(1.5, size * .07); ctx.lineCap = 'square'; ctx.beginPath(); ctx.moveTo(x - sign * size * .32, y); ctx.lineTo(x + sign * size * .28, y); ctx.moveTo(x + sign * size * .28, y); ctx.lineTo(x + sign * size * .05, y - size * .2); ctx.moveTo(x + sign * size * .28, y); ctx.lineTo(x + sign * size * .05, y + size * .2); ctx.stroke()
}

export function GridCanvas({ world, robots, conflicts, obstacles, tick, tickMs = 500, selected, onRobot, onCell }: Props & { tickMs?: number }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const view = useRef<View>({ zoom: 1, panX: 0, panY: 0, drag: false, x: 0, y: 0, moved: false })
  const motion = useRef<Map<string, Motion>>(new Map())
  const [viewVersion, setViewVersion] = useState(0)
  const [duration, setDuration] = useState(tickMs)

  useEffect(() => { void api.status().then(status => setDuration(status.tick_ms)).catch(() => undefined) }, [])

  useEffect(() => {
    const now = performance.now()
    robots.forEach(robot => {
      const nextAngle = headingAngle[robot.heading]
      const previous = motion.current.get(robot.robot_id)
      if (!previous) {
        motion.current.set(robot.robot_id, { from: robot.position, to: robot.position, fromAngle: nextAngle, toAngle: nextAngle, started: now, duration })
        return
      }
      const elapsed = Math.min(1, Math.max(0, (now - previous.started) / previous.duration))
      const currentAngle = previous.fromAngle + (previous.toAngle - previous.fromAngle) * elapsed
      const currentPosition = { x: previous.from.x + (previous.to.x - previous.from.x) * elapsed, y: previous.from.y + (previous.to.y - previous.from.y) * elapsed }
      const angleDelta = Math.atan2(Math.sin(nextAngle - currentAngle), Math.cos(nextAngle - currentAngle))
      motion.current.set(robot.robot_id, { from: currentPosition, to: robot.position, fromAngle: currentAngle, toAngle: currentAngle + angleDelta, started: now, duration: Math.max(50, duration) })
    })
    const activeIds = new Set(robots.map(robot => robot.robot_id))
    motion.current.forEach((_, robotId) => { if (!activeIds.has(robotId)) motion.current.delete(robotId) })
  }, [robots, tick, duration])

  useEffect(() => {
    let frame = 0
    const animate = () => { setViewVersion(version => version + 1); frame = window.requestAnimationFrame(animate) }
    frame = window.requestAnimationFrame(animate)
    return () => window.cancelAnimationFrame(frame)
  }, [])

  useEffect(() => {
    const canvas = ref.current; if (!canvas) return
    const parent = canvas.parentElement; if (!parent) return
    const dpr = window.devicePixelRatio || 1; const width = parent.clientWidth; const height = parent.clientHeight
    canvas.width = width * dpr; canvas.height = height * dpr; canvas.style.width = `${width}px`; canvas.style.height = `${height}px`
    const ctx = canvas.getContext('2d'); if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.fillStyle = palette.background; ctx.fillRect(0, 0, width, height)
    const current = view.current; const base = Math.min(width / (world.width + world.height) * 1.72, height / (world.width + world.height) * 1.25) * current.zoom
    const projection: Projection = { originX: width / 2 + current.panX, originY: height * .51 + current.panY, tileW: base * 1.38, tileH: base * .72, lift: base * .9 }
    const at = (point: Point, z = 0) => ({ x: projection.originX + (point.x - point.y) * projection.tileW / 2, y: projection.originY + (point.x + point.y) * projection.tileH / 2 - z })
    const cellCenter = (point: Point) => at({ x: point.x + .5, y: point.y + .5 })

    ctx.lineWidth = 1
    for (let x = 0; x < world.width; x++) for (let y = 0; y < world.height; y++) {
      const center = cellCenter({ x, y }); diamond(ctx, center.x, center.y, projection.tileW, projection.tileH); ctx.fillStyle = (x + y) % 2 ? palette.floor : palette.floorAlt; ctx.fill(); ctx.strokeStyle = palette.gridSoft; ctx.stroke()
    }
    const drawBlock = (point: Point, color: string, label?: 'in' | 'out') => {
      const center = cellCenter(point); const w = projection.tileW * .68; const h = projection.tileH * .57; const depth = projection.lift * .28
      ctx.fillStyle = color; diamond(ctx, center.x, center.y - depth, w, h); ctx.fill()
      ctx.fillStyle = '#263a42'; ctx.beginPath(); ctx.moveTo(center.x - w / 2, center.y - depth); ctx.lineTo(center.x, center.y - depth + h / 2); ctx.lineTo(center.x, center.y + h / 2); ctx.lineTo(center.x - w / 2, center.y); ctx.closePath(); ctx.fill()
      ctx.fillStyle = '#20323a'; ctx.beginPath(); ctx.moveTo(center.x, center.y - depth + h / 2); ctx.lineTo(center.x + w / 2, center.y - depth); ctx.lineTo(center.x + w / 2, center.y); ctx.lineTo(center.x, center.y + h / 2); ctx.closePath(); ctx.fill()
      if (label) { drawArrow(ctx, center.x, center.y - depth, label, Math.min(w, h) * .7); ctx.fillStyle = '#e7dfd2'; ctx.font = `600 ${Math.max(6, projection.tileH * .13)}px monospace`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(label === 'in' ? 'IN' : 'OUT', center.x, center.y + projection.tileH * .18) }
    }
    world.charging_stations.forEach(point => { const center = cellCenter(point); const w = projection.tileW * .42; const h = projection.tileH * .38; const depth = projection.lift * .2; ctx.fillStyle = palette.charger; diamond(ctx, center.x, center.y - depth, w, h); ctx.fill(); ctx.fillStyle = '#304a5e'; ctx.beginPath(); ctx.moveTo(center.x - w / 2, center.y - depth); ctx.lineTo(center.x, center.y - depth + h / 2); ctx.lineTo(center.x, center.y + h / 2); ctx.lineTo(center.x - w / 2, center.y); ctx.closePath(); ctx.fill(); ctx.fillStyle = '#d9e2e2'; ctx.fillRect(center.x - 1, center.y - depth - h * .18, 2, h * .3); ctx.fillStyle = '#d9e2e2'; ctx.font = `600 ${Math.max(6, projection.tileH * .13)}px monospace`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText('CHG', center.x, center.y + projection.tileH * .18) })
    world.pickup_stations.forEach(point => drawBlock(point, palette.import, 'in')); world.dropoff_stations.forEach(point => drawBlock(point, palette.export, 'out'))
    world.static_obstacles.forEach(point => { const center = cellCenter(point); const w = projection.tileW * .88; const h = projection.tileH * .78; const depth = projection.lift * .75; diamond(ctx, center.x, center.y - depth, w, h); ctx.fillStyle = palette.steelTop; ctx.fill(); ctx.strokeStyle = '#a4b3b4'; ctx.stroke(); ctx.fillStyle = palette.steelDark; ctx.beginPath(); ctx.moveTo(center.x - w / 2, center.y - depth); ctx.lineTo(center.x, center.y - depth + h / 2); ctx.lineTo(center.x, center.y + h / 2); ctx.lineTo(center.x - w / 2, center.y); ctx.closePath(); ctx.fill(); ctx.fillStyle = '#435861'; ctx.beginPath(); ctx.moveTo(center.x, center.y - depth + h / 2); ctx.lineTo(center.x + w / 2, center.y - depth); ctx.lineTo(center.x + w / 2, center.y); ctx.lineTo(center.x, center.y + h / 2); ctx.closePath(); ctx.fill(); ctx.strokeStyle = '#71858a'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(center.x - w * .25, center.y - depth * .85); ctx.lineTo(center.x - w * .25, center.y - depth * .15); ctx.moveTo(center.x + w * .14, center.y - depth * .75); ctx.lineTo(center.x + w * .14, center.y - depth * .05); ctx.stroke() })
    obstacles.forEach(obstacle => { const center = cellCenter(obstacle.position); const remaining = Math.max(0, obstacle.expires_at_tick - tick); ctx.fillStyle = remaining < 10 ? '#c78653' : palette.hazard; diamond(ctx, center.x, center.y - projection.lift * .12, projection.tileW * .62, projection.tileH * .5); ctx.fill(); ctx.fillStyle = '#f0dfc9'; ctx.font = `600 ${Math.max(8, base * .18)}px monospace`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(String(remaining), center.x, center.y - projection.lift * .12) })
    conflicts.forEach(conflict => { const center = cellCenter(conflict.cell); const markerW = projection.tileW * .72; const markerH = projection.tileH * .58; ctx.fillStyle = '#9d5b43'; diamond(ctx, center.x, center.y - projection.lift * .13, markerW, markerH); ctx.fill(); ctx.strokeStyle = '#e0aa75'; ctx.lineWidth = 1.5; ctx.stroke(); ctx.fillStyle = '#f4e4ce'; ctx.font = `600 ${Math.max(6, base * .13)}px monospace`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText('WARN', center.x, center.y - projection.lift * .13 - markerH * .12); ctx.font = `500 ${Math.max(5, base * .1)}px monospace`; ctx.fillText((conflict.robot_ids ?? []).join(' / ') || 'CONFLICT', center.x, center.y - projection.lift * .13 + markerH * .18) })
    robots.forEach(robot => { const transition = motion.current.get(robot.robot_id); const progress = transition ? Math.min(1, Math.max(0, (performance.now() - transition.started) / transition.duration)) : 1; const renderedPosition = transition ? { x: transition.from.x + (transition.to.x - transition.from.x) * progress, y: transition.from.y + (transition.to.y - transition.from.y) * progress } : robot.position; const renderedAngle = transition ? transition.fromAngle + (transition.toAngle - transition.fromAngle) * progress : headingAngle[robot.heading]; const center = cellCenter(renderedPosition); const color = ROBOT_TYPE_COLORS[robot.robot_type] || palette.steel; const podW = projection.tileW * .38; const podH = projection.tileH * .32; const z = projection.lift * .34; const nose = projection.tileW * .13; ctx.save(); ctx.translate(center.x, center.y - z); ctx.rotate(renderedAngle); ctx.fillStyle = '#263840'; roundedBox(ctx, -podW / 2, -podH / 2 + 3, podW, podH, podH * .28); ctx.fill(); ctx.fillStyle = color; roundedBox(ctx, -podW / 2, -podH / 2, podW, podH * .72, podH * .25); ctx.fill(); ctx.fillStyle = '#d7e0dd'; ctx.beginPath(); ctx.moveTo(-nose * .2, -podH * .36); ctx.lineTo(nose, 0); ctx.lineTo(-nose * .2, podH * .36); ctx.closePath(); ctx.fill(); ctx.restore(); ctx.fillStyle = STATE_COLORS[robot.state] || '#9ba9aa'; ctx.beginPath(); ctx.arc(center.x, center.y + projection.tileH * .27, Math.max(3, base * .07), 0, Math.PI * 2); ctx.fill(); if (robot.robot_id === selected || robot.state === 'CONFLICT_NEGOTIATING') { ctx.strokeStyle = robot.robot_id === selected ? '#e6d9b2' : '#d18b5a'; ctx.lineWidth = robot.robot_id === selected ? 2 : 1.5; diamond(ctx, center.x, center.y - projection.lift * .34, projection.tileW * .57, projection.tileH * .45); ctx.stroke() }; ctx.fillStyle = palette.text; ctx.font = `600 ${Math.max(7, base * .15)}px monospace`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(robot.robot_id.replace('AMR-', ''), center.x, center.y + projection.tileH * .43) })
  }, [world, robots, conflicts, obstacles, tick, selected, viewVersion])

  const hitPoint = (event: React.MouseEvent<HTMLCanvasElement>) => { const canvas = ref.current; if (!canvas) return; const rect = canvas.getBoundingClientRect(); const current = view.current; if (current.moved) { current.moved = false; return }; const base = Math.min(rect.width / (world.width + world.height) * 1.72, rect.height / (world.width + world.height) * 1.25) * current.zoom; const tileW = base * 1.38; const tileH = base * .72; const dx = event.clientX - rect.left - rect.width / 2 - current.panX; const dy = event.clientY - rect.top - rect.height * .51 - current.panY; const sum = 2 * dx / tileW - 1; const difference = 2 * dy / tileH - 1; const x = Math.floor((sum - difference) / 2); const y = Math.floor((sum + difference) / 2); if (x < 0 || x >= world.width || y < 0 || y >= world.height) return; const clickedRobot = robots.find(robot => robot.position.x === x && robot.position.y === y); clickedRobot ? onRobot(clickedRobot) : onCell({ x, y }) }
  return <div className="grid-shell"><div className="grid-caption"><span><i className="legend-dot" style={{ background: '#b8783e' }} /> LIVE WAREHOUSE DIGITAL TWIN</span><span>{world.width} × {world.height} CELLS · ISOMETRIC VIEW</span></div><canvas ref={ref} onClick={hitPoint} onPointerDown={event => { view.current.drag = true; view.current.x = event.clientX; view.current.y = event.clientY; view.current.moved = false; event.currentTarget.setPointerCapture(event.pointerId) }} onPointerMove={event => { const current = view.current; if (!current.drag) return; const dx = event.clientX - current.x; const dy = event.clientY - current.y; if (Math.abs(dx) + Math.abs(dy) > 2) current.moved = true; current.panX += dx; current.panY += dy; current.x = event.clientX; current.y = event.clientY; setViewVersion(version => version + 1) }} onPointerUp={event => { view.current.drag = false; event.currentTarget.releasePointerCapture(event.pointerId) }} onWheel={event => { event.preventDefault(); view.current.zoom = Math.max(.65, Math.min(2.2, view.current.zoom + (event.deltaY > 0 ? -.08 : .08))); setViewVersion(version => version + 1) }} /></div>
}
