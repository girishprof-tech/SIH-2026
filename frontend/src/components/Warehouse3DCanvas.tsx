import React, { useRef, useMemo, useEffect } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { OrbitControls, Text, Html, Line as DreiLine } from '@react-three/drei'
import * as THREE from 'three'
import type { Conflict, Point, Robot, Task, TempObstacle, World } from '../types'
import { ROBOT_TYPE_COLORS, STATE_COLORS } from '../state-meta'
import type { FleetStore } from '../hooks/useFleetSocket'

export type Palette = {
  background: string
  gridSoft: string
  steel: string
  steelTop: string
  steelDark: string
  floor: string
  floorAlt: string
  floorShadow: string
  import: string
  export: string
  charger: string
  hazard: string
  text: string
}

export type Warehouse3DProps = {
  world: World
  robots: Robot[]
  tasks?: Task[]
  conflicts?: Conflict[]
  obstacles?: TempObstacle[]
  tick: number
  selected: string | null
  showMeshLinks?: boolean
  cameraFollow?: boolean
  theme?: 'light' | 'dark'
  palette: Palette
  storeRef?: React.RefObject<FleetStore>
  onRobot: (robot: Robot) => void
  onCell: (point: Point) => void
  onResetViewRef?: React.MutableRefObject<(() => void) | null>
  onFitWarehouseRef?: React.MutableRefObject<(() => void) | null>
  onTopDownRef?: React.MutableRefObject<(() => void) | null>
  onZoomInRef?: React.MutableRefObject<(() => void) | null>
  onZoomOutRef?: React.MutableRefObject<(() => void) | null>
  onUserInteraction?: () => void
}

export const HEADING_ROTATION: Record<string, number> = {
  NORTH: Math.PI,
  SOUTH: 0,
  EAST: Math.PI / 2,
  WEST: -Math.PI / 2,
}

export function shortestAngleDiff(target: number, current: number): number {
  let diff = (target - current) % (2 * Math.PI)
  if (diff > Math.PI) diff -= 2 * Math.PI
  if (diff < -Math.PI) diff += 2 * Math.PI
  return diff
}

interface RobotMotionState {
  prevX: number
  prevZ: number
  prevRotation: number
  targetX: number
  targetZ: number
  targetRotation: number
  rotationDiff: number
  tickStartTime: number
  tickDurationMs: number
  lastTick: number
  hasInitialized: boolean
  isPaused: boolean
  pausedElapsed: number
}

// ── Camera Controller for Smooth Follow, Free Pan & Presets ───────────────────
function CameraController({
  selectedRobot,
  selectedId,
  storeRef,
  cameraFollow,
  controlsRef,
  offsetX,
  offsetZ,
  worldWidth,
  worldHeight,
  onUserInteraction,
}: {
  selectedRobot: Robot | undefined
  selectedId: string | null
  storeRef?: React.RefObject<FleetStore>
  cameraFollow: boolean
  controlsRef: React.RefObject<any>
  offsetX: number
  offsetZ: number
  worldWidth: number
  worldHeight: number
  onUserInteraction?: () => void
}) {
  const { camera } = useThree()
  const keysDown = useRef<Record<string, boolean>>({})

  // Release camera-follow immediately when user interacts with mouse or touch
  useEffect(() => {
    const controls = controlsRef.current
    if (!controls) return
    const handleStart = () => {
      onUserInteraction?.()
    }
    controls.addEventListener('start', handleStart)
    return () => controls.removeEventListener('start', handleStart)
  }, [controlsRef, onUserInteraction])

  // Keyboard pan listener (WASD / Arrows)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (['KeyW', 'KeyA', 'KeyS', 'KeyD', 'ArrowUp', 'ArrowLeft', 'ArrowDown', 'ArrowRight'].includes(e.code)) {
        if (document.activeElement === document.body || (document.activeElement && document.activeElement.tagName === 'CANVAS')) {
          e.preventDefault()
        }
        keysDown.current[e.code] = true
        onUserInteraction?.()
      }
    }
    const handleKeyUp = (e: KeyboardEvent) => {
      keysDown.current[e.code] = false
    }
    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('keyup', handleKeyUp)
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('keyup', handleKeyUp)
    }
  }, [onUserInteraction])

  useFrame((_, delta) => {
    const controls = controlsRef.current
    if (!controls) return

    // 1. Follow Selected Robot
    if (cameraFollow) {
      const live = (selectedId && storeRef?.current?.robots.get(selectedId)) || selectedRobot
      if (live) {
        const targetPos = new THREE.Vector3(
          live.position.x - offsetX,
          0.35,
          live.position.y - offsetZ
        )
        controls.target.lerp(targetPos, 0.08)
        controls.update()
      }
    }

    // 2. Keyboard Ground-Plane Panning (WASD & Arrows)
    const keys = keysDown.current
    const isKeyPressed = keys['KeyW'] || keys['KeyS'] || keys['KeyA'] || keys['KeyD'] ||
      keys['ArrowUp'] || keys['ArrowDown'] || keys['ArrowLeft'] || keys['ArrowRight']

    if (isKeyPressed) {
      const forward = new THREE.Vector3()
      camera.getWorldDirection(forward)
      forward.y = 0
      forward.normalize()
      const right = new THREE.Vector3().crossVectors(forward, new THREE.Vector3(0, 1, 0)).normalize()

      const panDelta = new THREE.Vector3()
      if (keys['KeyW'] || keys['ArrowUp']) panDelta.add(forward)
      if (keys['KeyS'] || keys['ArrowDown']) panDelta.sub(forward)
      if (keys['KeyD'] || keys['ArrowRight']) panDelta.add(right)
      if (keys['KeyA'] || keys['ArrowLeft']) panDelta.sub(right)

      if (panDelta.lengthSq() > 0) {
        const panSpeed = Math.max(14, Math.min(worldWidth, worldHeight)) * delta * 1.2
        panDelta.normalize().multiplyScalar(panSpeed)
        controls.target.add(panDelta)
        camera.position.add(panDelta)
        controls.update()
      }
    }

    // 3. Ground Plane Clamping (Target clamped to warehouse floor + margin)
    const margin = 8
    const minX = -offsetX - margin
    const maxX = offsetX + margin
    const minZ = -offsetZ - margin
    const maxZ = offsetZ + margin

    if (Number.isFinite(controls.target.x)) {
      controls.target.x = THREE.MathUtils.clamp(controls.target.x, minX, maxX)
    } else {
      controls.target.x = 0
    }
    if (Number.isFinite(controls.target.z)) {
      controls.target.z = THREE.MathUtils.clamp(controls.target.z, minZ, maxZ)
    } else {
      controls.target.z = 0
    }
    controls.target.y = 0.0 // Ground plane anchor

    // 4. Ensure camera never dips below the floor (y >= 0.5) and no NaN
    if (Number.isFinite(camera.position.y)) {
      camera.position.y = Math.max(camera.position.y, 0.5)
    } else {
      camera.position.set(26, 26, 26)
    }
    if (!Number.isFinite(camera.position.x) || !Number.isFinite(camera.position.z)) {
      camera.position.set(26, 26, 26)
    }
  })

  return (
    <OrbitControls
      ref={controlsRef}
      makeDefault
      target={[0, 0, 0]}
      enableDamping
      dampingFactor={0.08}
      minDistance={4}
      maxDistance={90}
      screenSpacePanning={false}
      maxPolarAngle={Math.PI / 2 - 0.05}
      mouseButtons={{
        LEFT: THREE.MOUSE.ROTATE,
        MIDDLE: THREE.MOUSE.DOLLY,
        RIGHT: THREE.MOUSE.PAN,
      }}
      touches={{
        ONE: THREE.TOUCH.ROTATE,
        TWO: THREE.TOUCH.DOLLY_PAN,
      }}
    />
  )
}

// ── 3D Robot AMR Model with Tick-Timestamped Smooth Interpolation ────────────
function Robot3D({
  robot,
  isSelected,
  palette,
  theme,
  storeRef,
  offsetX,
  offsetZ,
  onClick,
}: {
  robot: Robot
  isSelected: boolean
  palette: Palette
  theme: 'light' | 'dark'
  storeRef?: React.RefObject<FleetStore>
  offsetX: number
  offsetZ: number
  onClick: () => void
}) {
  const meshRef = useRef<THREE.Group>(null)
  const carriedPodRef = useRef<THREE.Group>(null)
  const carriedCartonRef = useRef<THREE.Mesh>(null)

  const initialX = robot.position.x - offsetX
  const initialZ = robot.position.y - offsetZ
  const initialRotation = HEADING_ROTATION[robot.heading] ?? 0

  useFrame((state, delta) => {
    if (!meshRef.current) return
    const live = storeRef?.current?.robots.get(robot.robot_id) ?? robot
    const isFleetRunning = storeRef?.current?.fleet_status?.running !== false

    const targetWorldX = live.position.x - offsetX
    const targetWorldZ = live.position.y - offsetZ
    const targetHeadingAngle = HEADING_ROTATION[live.heading] ?? 0

    const currentX = meshRef.current.position.x
    const currentZ = meshRef.current.position.z
    const dx = targetWorldX - currentX
    const dz = targetWorldZ - currentZ
    const dist = Math.hypot(dx, dz)

    // Teleport / Map Reset check (> 3.5 grid cells snap immediately)
    if (dist > 3.5) {
      meshRef.current.position.x = targetWorldX
      meshRef.current.position.z = targetWorldZ
      meshRef.current.rotation.y = targetHeadingAngle
      return
    }

    // Dynamic smoothing: fluid movement that drives smoothly across grid cells
    const lerpRate = isFleetRunning ? 8.5 : 14.0
    const lerpFactor = 1.0 - Math.exp(-lerpRate * delta)
    meshRef.current.position.x = THREE.MathUtils.lerp(currentX, targetWorldX, lerpFactor)
    meshRef.current.position.z = THREE.MathUtils.lerp(currentZ, targetWorldZ, lerpFactor)

    // Smooth heading rotation slerp
    const currentRot = meshRef.current.rotation.y
    const rotDiff = shortestAngleDiff(targetHeadingAngle, currentRot)
    const rotFactor = 1.0 - Math.exp(-12.0 * delta)
    meshRef.current.rotation.y = currentRot + rotDiff * rotFactor

    // Physical industrial walking/driving suspension dynamics:
    // When driving (dist > 0.03), add subtle suspension bobbing and chassis roll
    if (dist > 0.03 && isFleetRunning) {
      const timeSec = state.clock.getElapsedTime() * 14.0
      meshRef.current.position.y = Math.sin(timeSec) * 0.005 // 5mm mechanical suspension bob
      meshRef.current.rotation.z = Math.sin(timeSec * 0.5) * 0.006 // subtle chassis roll
    } else {
      meshRef.current.position.y = THREE.MathUtils.lerp(meshRef.current.position.y, 0.0, 0.2)
      meshRef.current.rotation.z = THREE.MathUtils.lerp(meshRef.current.rotation.z, 0.0, 0.2)
    }

    // Sync carried payload visibility in real-time
    if (carriedPodRef.current) {
      carriedPodRef.current.visible = Boolean(live.carrying_pod_id)
    }
    if (carriedCartonRef.current) {
      carriedCartonRef.current.visible = !live.carrying_pod_id && live.robot_type === 'SORTING' && live.state === 'EN_ROUTE_DROPOFF'
    }
  })

  const baseColor = ROBOT_TYPE_COLORS[robot.robot_type] || palette.steel
  const stateColor = STATE_COLORS[robot.state] || '#94a3b8'

  return (
    <group
      ref={meshRef}
      position={[initialX, 0.0, initialZ]}
      onClick={(e) => {
        e.stopPropagation()
        onClick()
      }}
      onPointerOver={(e) => {
        e.stopPropagation()
        document.body.style.cursor = 'pointer'
      }}
      onPointerOut={() => {
        document.body.style.cursor = 'auto'
      }}
    >
      {/* Selection Halo on Floor (y=0.01) */}
      {isSelected && (
        <mesh position={[0, 0.01, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[0.65, 0.82, 32]} />
          <meshBasicMaterial color="#38bdf8" side={THREE.DoubleSide} transparent opacity={0.85} />
        </mesh>
      )}

      {/* Main AMR Chassis: sits at local y=0.14, height 0.22 -> local bottom at 0.03, deck at 0.25 */}
      <mesh castShadow receiveShadow position={[0, 0.14, 0]}>
        <boxGeometry args={[0.78, 0.22, 0.86]} />
        <meshStandardMaterial
          color={baseColor}
          metalness={0.5}
          roughness={0.3}
          emissive={isSelected ? baseColor : '#000000'}
          emissiveIntensity={isSelected ? 0.35 : 0}
        />
      </mesh>

      {/* Front Headlights / Direction Indicator */}
      <mesh position={[0.22, 0.16, 0.43]}>
        <sphereGeometry args={[0.055, 12, 12]} />
        <meshStandardMaterial color="#fef08a" emissive="#fef08a" emissiveIntensity={1.5} />
      </mesh>
      <mesh position={[-0.22, 0.16, 0.43]}>
        <sphereGeometry args={[0.055, 12, 12]} />
        <meshStandardMaterial color="#fef08a" emissive="#fef08a" emissiveIntensity={1.5} />
      </mesh>

      {/* State Status Light Ring on Top (local y=0.27) */}
      <mesh position={[0, 0.27, 0]}>
        <cylinderGeometry args={[0.18, 0.18, 0.04, 20]} />
        <meshStandardMaterial color={stateColor} emissive={stateColor} emissiveIntensity={1.2} />
      </mesh>

      {/* Wheels: Cylinder radius 0.09 at local y=0.09 -> lowest point is exactly at local y=0.00 (world y=0.00)! */}
      <mesh position={[-0.41, 0.09, 0.2]} rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.09, 0.09, 0.07, 16]} />
        <meshStandardMaterial color={palette.steelDark} roughness={0.9} />
      </mesh>
      <mesh position={[0.41, 0.09, 0.2]} rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.09, 0.09, 0.07, 16]} />
        <meshStandardMaterial color={palette.steelDark} roughness={0.9} />
      </mesh>
      <mesh position={[-0.41, 0.09, -0.2]} rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.09, 0.09, 0.07, 16]} />
        <meshStandardMaterial color={palette.steelDark} roughness={0.9} />
      </mesh>
      <mesh position={[0.41, 0.09, -0.2]} rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.09, 0.09, 0.07, 16]} />
        <meshStandardMaterial color={palette.steelDark} roughness={0.9} />
      </mesh>

      {/* Carried Pod Payload if lifting: Defined lift height above robot deck (deck y=0.25, lift clearance 0.08 -> bottom at 0.33, center at 0.88) */}
      <group ref={carriedPodRef} position={[0, 0.88, 0]} visible={Boolean(robot.carrying_pod_id)}>
        <mesh castShadow receiveShadow>
          <boxGeometry args={[0.74, 1.1, 0.74]} />
          <meshStandardMaterial color={palette.steelTop} metalness={0.25} roughness={0.5} />
        </mesh>
        <mesh position={[0, 0.56, 0]}>
          <boxGeometry args={[0.76, 0.04, 0.76]} />
          <meshStandardMaterial color={palette.steel} metalness={0.4} roughness={0.4} />
        </mesh>
        <Text position={[0, 0, 0.38]} fontSize={0.15} color={palette.text}>
          {robot.carrying_pod_id || ''}
        </Text>
      </group>

      {/* Carried Carton (for SORTING AMR): sits directly on robot deck (deck y=0.25, carton height 0.22 -> center at 0.36) */}
      <mesh
        ref={carriedCartonRef}
        position={[0, 0.36, 0]}
        castShadow
        visible={!robot.carrying_pod_id && robot.robot_type === 'SORTING' && robot.state === 'EN_ROUTE_DROPOFF'}
      >
        <boxGeometry args={[0.42, 0.22, 0.42]} />
        <meshStandardMaterial color="#d97706" roughness={0.8} />
      </mesh>

      {/* Floating Robot ID Tag & Battery Mini Gauge */}
      <Html
        position={[0, 1.4 + (robot.carrying_pod_id ? 0.7 : 0), 0]}
        center
        distanceFactor={18}
        zIndexRange={isSelected ? [15, 20] : [1, 10]}
      >
        <div
          style={{
            background: isSelected
              ? 'rgba(14, 165, 233, 0.95)'
              : theme === 'light'
              ? 'rgba(255, 255, 255, 0.92)'
              : 'rgba(20, 20, 20, 0.90)',
            border: `1px solid ${isSelected ? '#38bdf8' : palette.steel}`,
            borderRadius: '4px',
            padding: '2px 5px',
            color: isSelected ? '#ffffff' : palette.text,
            fontSize: '10px',
            fontWeight: 700,
            whiteSpace: 'nowrap',
            boxShadow: '0 2px 8px rgba(0,0,0,0.4)',
            pointerEvents: 'none',
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
          }}
        >
          <span>{robot.robot_id}</span>
          <span
            style={{
              color: robot.battery_pct < 25 ? palette.hazard : palette.charger,
              fontSize: '9px',
            }}
          >
            {Math.round(robot.battery_pct)}%
          </span>
        </div>
      </Html>
    </group>
  )
}

// ── Path Trail Line ─────────────────────────────────────────────────────────
function PathTrail({ path, color, offsetX, offsetZ }: { path: Array<Point & { t?: number }>; color: string; offsetX: number; offsetZ: number }) {
  const points = useMemo(() => {
    return path.map((p) => [p.x - offsetX, 0.08, p.y - offsetZ] as [number, number, number])
  }, [path, offsetX, offsetZ])

  if (points.length < 2) return null

  return (
    <DreiLine
      points={points}
      color={color}
      lineWidth={2.2}
      transparent
      opacity={0.8}
    />
  )
}

// ── Storage Pod Racks ───────────────────────────────────────────────────────
function StorageRacks({
  podSlots,
  staticObstacles,
  robots,
  palette,
  theme,
  offsetX,
  offsetZ,
}: {
  podSlots?: Array<{ shelf_id: string; x: number; y: number }>
  staticObstacles?: Point[]
  robots: Robot[]
  palette: Palette
  theme: 'light' | 'dark'
  offsetX: number
  offsetZ: number
}) {
  const carriedPodIds = useMemo(() => {
    return new Set(robots.map((r) => r.carrying_pod_id).filter(Boolean))
  }, [robots])

  return (
    <group>
      {/* 1. Movable Pod Racks: Base footing local bottom is at -0.58 - 0.015 = -0.595. Group at y=0.595 -> lowest vertex = 0.000 */}
      {podSlots &&
        podSlots.map((slot) => {
          const x = slot.x - offsetX
          const z = slot.y - offsetZ
          const isCarried = carriedPodIds.has(slot.shelf_id)

          if (isCarried) {
            // Empty slot footprint (dashed ground frame)
            return (
              <group key={slot.shelf_id} position={[x, 0.01, z]}>
                <mesh rotation={[-Math.PI / 2, 0, 0]}>
                  <ringGeometry args={[0.34, 0.38, 4]} />
                  <meshBasicMaterial color={theme === 'light' ? '#d97706' : '#fb923c'} transparent opacity={0.5} />
                </mesh>
              </group>
            )
          }

          return (
            <group key={slot.shelf_id} position={[x, 0.595, z]}>
              {/* Rack body */}
              <mesh castShadow receiveShadow>
                <boxGeometry args={[0.75, 1.15, 0.75]} />
                <meshStandardMaterial color={palette.steel} metalness={0.35} roughness={0.55} />
              </mesh>
              {/* Top cap */}
              <mesh position={[0, 0.59, 0]}>
                <boxGeometry args={[0.78, 0.04, 0.78]} />
                <meshStandardMaterial color={palette.steelTop} metalness={0.25} roughness={0.4} />
              </mesh>
              {/* Base footing */}
              <mesh position={[0, -0.58, 0]}>
                <boxGeometry args={[0.78, 0.03, 0.78]} />
                <meshStandardMaterial color={palette.steelDark} roughness={0.8} />
              </mesh>
              {/* Shelf tier totes */}
              <mesh position={[0, 0.15, 0.385]}>
                <boxGeometry args={[0.55, 0.12, 0.03]} />
                <meshStandardMaterial color="#d97706" roughness={0.6} />
              </mesh>
              <mesh position={[0, -0.22, 0.385]}>
                <boxGeometry args={[0.55, 0.12, 0.03]} />
                <meshStandardMaterial color="#ea580c" roughness={0.6} />
              </mesh>
              <Text position={[0, 0.68, 0]} fontSize={0.13} color={palette.text} anchorX="center">
                {slot.shelf_id}
              </Text>
            </group>
          )
        })}

      {/* 2. Static Obstacle Pallet Racks: Base footing local bottom at -0.595 -> group at y=0.595 gives bottom at 0.000 */}
      {staticObstacles &&
        staticObstacles.map((pt, i) => {
          const x = pt.x - offsetX
          const z = pt.y - offsetZ
          return (
            <group key={`obs-${i}`} position={[x, 0.595, z]}>
              <mesh castShadow receiveShadow>
                <boxGeometry args={[0.78, 1.18, 0.78]} />
                <meshStandardMaterial color={palette.steel} metalness={0.35} roughness={0.55} />
              </mesh>
              <mesh position={[0, 0.6, 0]}>
                <boxGeometry args={[0.8, 0.04, 0.8]} />
                <meshStandardMaterial color={palette.steelTop} metalness={0.25} roughness={0.4} />
              </mesh>
              <mesh position={[0, -0.58, 0]}>
                <boxGeometry args={[0.8, 0.03, 0.8]} />
                <meshStandardMaterial color={palette.steelDark} roughness={0.8} />
              </mesh>
            </group>
          )
        })}
    </group>
  )
}

// ── Bounded Sortation Zone with Relocated Chutes ───────────────────────────
function SortationZone3D({
  chutes,
  palette,
  offsetX,
  offsetZ,
}: {
  chutes?: Record<string, { destination_zone: string; x: number; y: number }>
  palette: Palette
  offsetX: number
  offsetZ: number
}) {
  const chuteList = useMemo(() => Object.entries(chutes || {}), [chutes])
  if (chuteList.length === 0) return null

  const xs = chuteList.map(([_, c]) => c.x)
  const ys = chuteList.map(([_, c]) => c.y)
  const minX = Math.min(...xs) - 0.5
  const maxX = Math.max(...xs) + 0.5
  const minY = Math.min(...ys) - 0.5
  const maxY = Math.max(...ys) + 0.5
  const zoneWidth = Math.max(3.0, maxX - minX + 1.2)
  const zoneHeight = Math.max(3.0, maxY - minY + 1.2)
  const zoneCenterX = (minX + maxX) / 2 - offsetX
  const zoneCenterZ = (minY + maxY) / 2 - offsetZ

  return (
    <group>
      {/* Zone Floor Tint */}
      <mesh position={[zoneCenterX, 0.01, zoneCenterZ]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[zoneWidth, zoneHeight]} />
        <meshStandardMaterial color={palette.export} transparent opacity={0.14} />
      </mesh>

      {/* Zone Title Label */}
      <Text position={[zoneCenterX, 1.6, zoneCenterZ - zoneHeight / 2 - 0.3]} fontSize={0.36} color={palette.export} anchorX="center">
        SORTATION PUT-WALL ZONE
      </Text>

      {/* Put-Wall Chutes: Box height 0.75 -> group at y=0.375 gives bottom at 0.000 */}
      {chuteList.map(([chuteId, c]) => {
        const cx = c.x - offsetX
        const cz = c.y - offsetZ
        return (
          <group key={chuteId} position={[cx, 0.375, cz]}>
            <mesh castShadow receiveShadow>
              <boxGeometry args={[0.7, 0.75, 0.7]} />
              <meshStandardMaterial color={palette.steel} metalness={0.6} roughness={0.35} />
            </mesh>
            {/* Chute opening ramp */}
            <mesh position={[0, 0.18, 0]} rotation={[0.35, 0, 0]}>
              <boxGeometry args={[0.54, 0.08, 0.54]} />
              <meshStandardMaterial color={palette.export} emissive={palette.export} emissiveIntensity={0.4} />
            </mesh>
            <Text position={[0, 0.58, 0]} fontSize={0.15} color={palette.text} anchorX="center">
              {chuteId}
            </Text>
            <Text position={[0, 0.45, 0.36]} fontSize={0.11} color={palette.export} anchorX="center">
              {(c.destination_zone || '').replace('ZONE_', '')}
            </Text>
          </group>
        )
      })}
    </group>
  )
}

// ── Pick Stations ───────────────────────────────────────────────────────────
function PickStations3D({
  stations,
  palette,
  offsetX,
  offsetZ,
}: {
  stations?: Array<{ id: string; x: number; y: number; bufferCount?: number }>
  palette: Palette
  offsetX: number
  offsetZ: number
}) {
  const stationList = useMemo(() => {
    if (stations && stations.length > 0) return stations
    return [
      { id: 'PICK-01', x: 20, y: 2, bufferCount: 2 },
      { id: 'PICK-02', x: 20, y: 3, bufferCount: 1 },
      { id: 'PICK-03', x: 20, y: 4, bufferCount: 3 },
    ]
  }, [stations])

  return (
    <group>
      {stationList.map((st) => {
        const sx = st.x - offsetX
        const sz = st.y - offsetZ
        const bufCount = st.bufferCount ?? 1
        // Stand height 0.55 -> center at y=0.275 gives bottom at 0.000
        return (
          <group key={st.id} position={[sx, 0.275, sz]}>
            {/* Table / Stand */}
            <mesh castShadow receiveShadow>
              <boxGeometry args={[0.8, 0.55, 0.8]} />
              <meshStandardMaterial color={palette.steel} metalness={0.4} roughness={0.5} />
            </mesh>
            {/* Buffer Cartons Stacked directly on table surface (table top is at local y=0.275) */}
            {Array.from({ length: bufCount }).map((_, idx) => (
              <mesh key={idx} position={[0, 0.345 + idx * 0.14, 0]} castShadow>
                <boxGeometry args={[0.38, 0.14, 0.38]} />
                <meshStandardMaterial color="#d97706" roughness={0.7} />
              </mesh>
            ))}
            <Text position={[0, 0.75 + bufCount * 0.14, 0]} fontSize={0.14} color={palette.text} anchorX="center">
              {st.id} ({bufCount}/4)
            </Text>
          </group>
        )
      })}
    </group>
  )
}

// ── Charging Alcoves ────────────────────────────────────────────────────────
function ChargingStations3D({ stations, palette, offsetX, offsetZ }: { stations: Point[]; palette: Palette; offsetX: number; offsetZ: number }) {
  return (
    <group>
      {stations.map((st, i) => (
        <group key={i} position={[st.x - offsetX, 0.005, st.y - offsetZ]}>
          <mesh rotation={[-Math.PI / 2, 0, 0]}>
            <circleGeometry args={[0.44, 24]} />
            <meshStandardMaterial color={palette.charger} emissive={palette.charger} emissiveIntensity={0.5} />
          </mesh>
          <mesh position={[0, 0.08, 0]}>
            <cylinderGeometry args={[0.07, 0.1, 0.16, 12]} />
            <meshStandardMaterial color="#fef08a" emissive="#fef08a" emissiveIntensity={1.0} />
          </mesh>
          <Text position={[0, 0.32, 0]} fontSize={0.14} color={palette.charger} anchorX="center">
            ⚡ CHG-{i + 1}
          </Text>
        </group>
      ))}
    </group>
  )
}

// ── Inbound / Outbound Dock Gates ───────────────────────────────────────────
function Gates3D({
  entryGates,
  exitGates,
  palette,
  offsetX,
  offsetZ,
  width,
}: {
  entryGates?: Array<{ id: string; cells: Point[] }>
  exitGates?: Array<{ id: string; cells: Point[] }>
  palette: Palette
  offsetX: number
  offsetZ: number
  width: number
}) {
  const inGates = useMemo(() => {
    if (entryGates && entryGates.length > 0) {
      return entryGates.map((g) => ({
        label: g.id,
        x: g.cells[0]?.x ?? 0,
        y: g.cells[0]?.y ?? 0,
        height: Math.max(1, g.cells.length),
      }))
    }
    return [
      { label: 'IN-1', x: 0, y: 9, height: 3 },
      { label: 'IN-2', x: 0, y: 14, height: 3 },
      { label: 'IN-3', x: 0, y: 19, height: 3 },
    ]
  }, [entryGates])

  const outGates = useMemo(() => {
    if (exitGates && exitGates.length > 0) {
      return exitGates.map((g) => ({
        label: g.id,
        x: g.cells[0]?.x ?? width - 1,
        y: g.cells[0]?.y ?? 0,
        height: Math.max(1, g.cells.length),
      }))
    }
    return [
      { label: 'OUT-1', x: width - 1, y: 9, height: 3 },
      { label: 'OUT-2', x: width - 1, y: 14, height: 3 },
      { label: 'OUT-3', x: width - 1, y: 19, height: 3 },
    ]
  }, [exitGates, width])

  return (
    <group>
      {inGates.map((g) => (
        <group key={g.label} position={[g.x - offsetX, 0.01, g.y - offsetZ]}>
          <mesh rotation={[-Math.PI / 2, 0, 0]}>
            <planeGeometry args={[0.9, g.height * 0.9]} />
            <meshStandardMaterial color={palette.import} transparent opacity={0.35} />
          </mesh>
          <Text position={[0.55, 1.0, 0]} fontSize={0.28} color={palette.import} rotation={[0, Math.PI / 2, 0]}>
            RECEIVING {g.label}
          </Text>
        </group>
      ))}
      {outGates.map((g) => (
        <group key={g.label} position={[g.x - offsetX, 0.01, g.y - offsetZ]}>
          <mesh rotation={[-Math.PI / 2, 0, 0]}>
            <planeGeometry args={[0.9, g.height * 0.9]} />
            <meshStandardMaterial color={palette.export} transparent opacity={0.35} />
          </mesh>
          <Text position={[-0.55, 1.0, 0]} fontSize={0.28} color={palette.export} rotation={[0, -Math.PI / 2, 0]}>
            SHIPPING {g.label}
          </Text>
        </group>
      ))}
    </group>
  )
}

// ── Temporary Dynamic Obstacles ──────────────────────────────────────────────
function DynamicObstacles3D({ obstacles, palette, tick, offsetX, offsetZ }: { obstacles?: TempObstacle[]; palette: Palette; tick: number; offsetX: number; offsetZ: number }) {
  if (!obstacles || obstacles.length === 0) return null

  return (
    <group>
      {obstacles.map((obs, i) => {
        const remaining = Math.max(0, obs.expires_at_tick - tick)
        const x = obs.position.x - offsetX
        const z = obs.position.y - offsetZ
        // Cylinder height 0.6 -> center at y=0.300 gives bottom at 0.000
        return (
          <group key={`obs-dyn-${i}`} position={[x, 0.3, z]}>
            <mesh castShadow receiveShadow>
              <cylinderGeometry args={[0.35, 0.42, 0.6, 6]} />
              <meshStandardMaterial color={palette.hazard} emissive={palette.hazard} emissiveIntensity={0.4} />
            </mesh>
            <Text position={[0, 0.45, 0]} fontSize={0.18} color="#ffffff" anchorX="center">
              {remaining}
            </Text>
          </group>
        )
      })}
    </group>
  )
}

// ── Conflict Arbitration Beacons ────────────────────────────────────────────
function ConflictBeacons3D({ conflicts, palette, offsetX, offsetZ }: { conflicts?: Conflict[]; palette: Palette; offsetX: number; offsetZ: number }) {
  if (!conflicts || conflicts.length === 0) return null

  return (
    <group>
      {conflicts.map((c, i) => {
        const x = c.cell.x - offsetX
        const z = c.cell.y - offsetZ
        return (
          <group key={`conf-${i}`} position={[x, 0.02, z]}>
            <mesh rotation={[-Math.PI / 2, 0, 0]}>
              <ringGeometry args={[0.5, 0.72, 24]} />
              <meshBasicMaterial color={palette.hazard} transparent opacity={0.8} />
            </mesh>
            <Text position={[0, 0.6, 0]} fontSize={0.16} color={palette.hazard} anchorX="center">
              P2P ARBITRATION
            </Text>
          </group>
        )
      })}
    </group>
  )
}

// ── P2P Mesh Communication Links ────────────────────────────────────────────
function MeshLinks3D({ robots, showMeshLinks, palette, offsetX, offsetZ }: { robots: Robot[]; showMeshLinks?: boolean; palette: Palette; offsetX: number; offsetZ: number }) {
  const links = useMemo(() => {
    if (!showMeshLinks || robots.length < 2) return []
    const res: Array<{ from: [number, number, number]; to: [number, number, number]; isConflicted: boolean }> = []
    for (let i = 0; i < robots.length; i++) {
      for (let j = i + 1; j < robots.length; j++) {
        const r1 = robots[i]
        const r2 = robots[j]
        const dx = Math.abs(r1.position.x - r2.position.x)
        const dy = Math.abs(r1.position.y - r2.position.y)
        if (dx + dy <= 6) {
          const isConflicted = r1.state === 'CONFLICT_NEGOTIATING' || r2.state === 'CONFLICT_NEGOTIATING'
          res.push({
            from: [r1.position.x - offsetX, 0.28, r1.position.y - offsetZ],
            to: [r2.position.x - offsetX, 0.28, r2.position.y - offsetZ],
            isConflicted,
          })
        }
      }
    }
    return res
  }, [robots, showMeshLinks, offsetX, offsetZ])

  if (!showMeshLinks || links.length === 0) return null

  return (
    <group>
      {links.map((link, idx) => (
        <DreiLine
          key={`mesh-${idx}`}
          points={[link.from, link.to]}
          color={link.isConflicted ? palette.hazard : palette.export}
          lineWidth={1.2}
          transparent
          opacity={0.45}
        />
      ))}
    </group>
  )
}

// ── Interactive Ground Plane with 1-Draw-Call Instanced Checkerboard ─────────
function GroundGrid({
  width,
  height,
  palette,
  nearObstacleSet,
  pickupSet,
  dropoffSet,
  chargingSet,
  offsetX,
  offsetZ,
  onCellClick,
}: {
  width: number
  height: number
  palette: Palette
  nearObstacleSet: Set<string>
  pickupSet: Set<string>
  dropoffSet: Set<string>
  chargingSet: Set<string>
  offsetX: number
  offsetZ: number
  onCellClick: (point: Point) => void
}) {
  const meshRef = useRef<THREE.InstancedMesh>(null)

  useEffect(() => {
    if (!meshRef.current) return
    const dummy = new THREE.Object3D()
    const color = new THREE.Color()

    let idx = 0
    for (let x = 0; x < width; x++) {
      for (let y = 0; y < height; y++) {
        const key = `${x},${y}`
        dummy.position.set(x - offsetX, -0.015, y - offsetZ)
        dummy.scale.set(0.96, 0.03, 0.96)
        dummy.updateMatrix()
        meshRef.current.setMatrixAt(idx, dummy.matrix)

        if (pickupSet.has(key)) {
          color.set(palette.import).lerp(new THREE.Color(palette.floor), 0.72)
        } else if (dropoffSet.has(key)) {
          color.set(palette.export).lerp(new THREE.Color(palette.floor), 0.72)
        } else if (chargingSet.has(key)) {
          color.set(palette.charger).lerp(new THREE.Color(palette.floor), 0.72)
        } else if (nearObstacleSet.has(key)) {
          color.set(palette.floorShadow)
        } else if ((x + y) % 2 === 1) {
          color.set(palette.floor)
        } else {
          color.set(palette.floorAlt)
        }

        meshRef.current.setColorAt(idx, color)
        idx++
      }
    }
    meshRef.current.instanceMatrix.needsUpdate = true
    if (meshRef.current.instanceColor) {
      meshRef.current.instanceColor.needsUpdate = true
    }
  }, [width, height, palette, nearObstacleSet, pickupSet, dropoffSet, chargingSet, offsetX, offsetZ])

  return (
    <group>
      {/* Tile Checkerboard Instanced Mesh (Top surface at y=0.000) */}
      <instancedMesh
        ref={meshRef}
        args={[undefined, undefined, width * height]}
        receiveShadow
      >
        <boxGeometry args={[1, 1, 1]} />
        <meshStandardMaterial roughness={0.85} metalness={0.1} />
      </instancedMesh>

      {/* Sub-floor slab plate */}
      <mesh position={[0, -0.04, 0]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[width + 1.2, height + 1.2]} />
        <meshStandardMaterial color={palette.steelDark} roughness={0.9} />
      </mesh>

      {/* Invisible Interactive Click Plane */}
      <mesh
        position={[0, 0.02, 0]}
        rotation={[-Math.PI / 2, 0, 0]}
        visible={false}
        onClick={(e) => {
          e.stopPropagation()
          const pt = e.point
          const cellX = Math.floor(pt.x + offsetX + 0.5)
          const cellY = Math.floor(pt.z + offsetZ + 0.5)
          if (cellX >= 0 && cellX < width && cellY >= 0 && cellY < height) {
            onCellClick({ x: cellX, y: cellY })
          }
        }}
      >
        <planeGeometry args={[width, height]} />
        <meshBasicMaterial />
      </mesh>
    </group>
  )
}

// ── Main Warehouse3DCanvas Component ─────────────────────────────────────────
export function Warehouse3DCanvas({
  world,
  robots,
  conflicts = [],
  obstacles = [],
  tick,
  selected,
  showMeshLinks = true,
  cameraFollow = true,
  theme = 'dark',
  palette,
  storeRef,
  onRobot,
  onCell,
  onResetViewRef,
  onFitWarehouseRef,
  onTopDownRef,
  onZoomInRef,
  onZoomOutRef,
  onUserInteraction,
}: Warehouse3DProps) {
  const controlsRef = useRef<any>(null)

  const offsetX = (world.width - 1) / 2
  const offsetZ = (world.height - 1) / 2

  const selectedRobot = useMemo(() => {
    return robots.find((r) => r.robot_id === selected)
  }, [robots, selected])

  // Precompute zone sets
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

  // Wire HUD control buttons to OrbitControls
  useEffect(() => {
    if (onResetViewRef) {
      onResetViewRef.current = () => {
        if (controlsRef.current) {
          controlsRef.current.target.set(0, 0, 0)
          controlsRef.current.object.position.set(26, 26, 26)
          controlsRef.current.object.up.set(0, 1, 0)
          controlsRef.current.update()
        }
      }
    }
    if (onFitWarehouseRef) {
      onFitWarehouseRef.current = () => {
        if (controlsRef.current) {
          controlsRef.current.target.set(0, 0, 0)
          const maxDim = Math.max(world.width, world.height)
          controlsRef.current.object.position.set(maxDim * 0.9, maxDim * 1.15, maxDim * 0.9)
          controlsRef.current.object.up.set(0, 1, 0)
          controlsRef.current.update()
        }
      }
    }
    if (onTopDownRef) {
      onTopDownRef.current = () => {
        if (controlsRef.current) {
          controlsRef.current.target.set(0, 0, 0)
          const maxDim = Math.max(world.width, world.height)
          // Top-down preset: overhead looking straight down with North up (-Z)
          controlsRef.current.object.position.set(0, maxDim * 1.45, 0.001)
          controlsRef.current.object.up.set(0, 0, -1)
          controlsRef.current.update()
        }
      }
    }
    if (onZoomInRef) {
      onZoomInRef.current = () => {
        if (controlsRef.current) {
          const cam = controlsRef.current.object
          const target = controlsRef.current.target
          const offset = cam.position.clone().sub(target)
          if (offset.length() > 5) {
            offset.multiplyScalar(0.82)
            cam.position.copy(target).add(offset)
            controlsRef.current.update()
          }
        }
      }
    }
    if (onZoomOutRef) {
      onZoomOutRef.current = () => {
        if (controlsRef.current) {
          const cam = controlsRef.current.object
          const target = controlsRef.current.target
          const offset = cam.position.clone().sub(target)
          if (offset.length() < 88) {
            offset.multiplyScalar(1.2)
            cam.position.copy(target).add(offset)
            controlsRef.current.update()
          }
        }
      }
    }
  }, [world.width, world.height, onResetViewRef, onFitWarehouseRef, onTopDownRef, onZoomInRef, onZoomOutRef])

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative', background: palette.background }}>
      <Canvas
        shadows
        camera={{ position: [26, 26, 26], fov: 42 }}
        style={{ width: '100%', height: '100%' }}
      >
        <color attach="background" args={[palette.background]} />

        {/* Ambient & Directional Lighting */}
        <ambientLight intensity={theme === 'light' ? 0.8 : 0.65} />
        <directionalLight
          position={[20, 35, 25]}
          intensity={theme === 'light' ? 1.4 : 1.15}
          castShadow
          shadow-mapSize-width={2048}
          shadow-mapSize-height={2048}
          shadow-camera-left={-22}
          shadow-camera-right={22}
          shadow-camera-top={22}
          shadow-camera-bottom={-22}
        />
        <pointLight position={[-15, 20, -15]} intensity={0.4} />

        {/* Orbit Controls & Smooth Camera Follow */}
        <CameraController
          selectedRobot={selectedRobot}
          selectedId={selected}
          storeRef={storeRef}
          cameraFollow={cameraFollow}
          controlsRef={controlsRef}
          offsetX={offsetX}
          offsetZ={offsetZ}
          worldWidth={world.width}
          worldHeight={world.height}
          onUserInteraction={onUserInteraction}
        />

        {/* Warehouse Checkerboard Floor Grid */}
        <GroundGrid
          width={world.width}
          height={world.height}
          palette={palette}
          nearObstacleSet={nearObstacleSet}
          pickupSet={pickupSet}
          dropoffSet={dropoffSet}
          chargingSet={chargingSet}
          offsetX={offsetX}
          offsetZ={offsetZ}
          onCellClick={onCell}
        />

        {/* Pod Racks & Static Obstacles */}
        <StorageRacks
          podSlots={world.pod_slots}
          staticObstacles={world.static_obstacles}
          robots={robots}
          palette={palette}
          theme={theme}
          offsetX={offsetX}
          offsetZ={offsetZ}
        />

        {/* Bounded Sortation Put-Wall Zone */}
        <SortationZone3D chutes={world.sortation_chutes} palette={palette} offsetX={offsetX} offsetZ={offsetZ} />

        {/* Pick Stations with Buffer Cartons */}
        <PickStations3D stations={world.pick_stations} palette={palette} offsetX={offsetX} offsetZ={offsetZ} />

        {/* Charging Stations */}
        <ChargingStations3D stations={world.charging_stations} palette={palette} offsetX={offsetX} offsetZ={offsetZ} />

        {/* Inbound & Outbound Dock Gates */}
        <Gates3D entryGates={world.entry_gates} exitGates={world.exit_gates} palette={palette} offsetX={offsetX} offsetZ={offsetZ} width={world.width} />

        {/* Temporary Dynamic Obstacles */}
        <DynamicObstacles3D obstacles={obstacles} palette={palette} tick={tick} offsetX={offsetX} offsetZ={offsetZ} />

        {/* Decentralized Conflict Arbitration Beacons */}
        <ConflictBeacons3D conflicts={conflicts} palette={palette} offsetX={offsetX} offsetZ={offsetZ} />

        {/* P2P Mesh Communication Links */}
        <MeshLinks3D robots={robots} showMeshLinks={showMeshLinks} palette={palette} offsetX={offsetX} offsetZ={offsetZ} />

        {/* Active AMRs */}
        {robots.map((robot) => (
          <Robot3D
            key={robot.robot_id}
            robot={robot}
            isSelected={robot.robot_id === selected}
            palette={palette}
            theme={theme}
            storeRef={storeRef}
            offsetX={offsetX}
            offsetZ={offsetZ}
            onClick={() => onRobot(robot)}
          />
        ))}

        {/* 3D Path Trails for Active Robots */}
        {robots.map((robot) => {
          if (!robot.path || robot.path.length < 2) return null
          const isSel = robot.robot_id === selected
          return (
            <PathTrail
              key={`path-${robot.robot_id}`}
              path={robot.path}
              color={isSel ? '#38bdf8' : ROBOT_TYPE_COLORS[robot.robot_type] || palette.steel}
              offsetX={offsetX}
              offsetZ={offsetZ}
            />
          )
        })}
      </Canvas>
    </div>
  )
}

