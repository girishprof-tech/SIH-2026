import React, { useRef, useMemo, useEffect } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
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
  onZoomInRef?: React.MutableRefObject<(() => void) | null>
  onZoomOutRef?: React.MutableRefObject<(() => void) | null>
}

const HEADING_ROTATION: Record<string, number> = {
  NORTH: Math.PI,
  SOUTH: 0,
  EAST: Math.PI / 2,
  WEST: -Math.PI / 2,
}

// ── Camera Controller for Smooth Follow / Lerping ─────────────────────────────
function CameraController({
  selectedRobot,
  selectedId,
  storeRef,
  cameraFollow,
  controlsRef,
}: {
  selectedRobot: Robot | undefined
  selectedId: string | null
  storeRef?: React.RefObject<FleetStore>
  cameraFollow: boolean
  controlsRef: React.RefObject<any>
}) {
  useFrame(() => {
    if (cameraFollow && controlsRef.current) {
      const live = (selectedId && storeRef?.current?.robots.get(selectedId)) || selectedRobot
      if (live) {
        const targetPos = new THREE.Vector3(
          live.position.x - 14.5,
          0.35,
          live.position.y - 14.5
        )
        controlsRef.current.target.lerp(targetPos, 0.08)
        controlsRef.current.update()
      }
    }
  })

  return (
    <OrbitControls
      ref={controlsRef}
      makeDefault
      target={[0, 0, 0]}
      enableDamping
      dampingFactor={0.08}
      minDistance={5}
      maxDistance={85}
      maxPolarAngle={Math.PI / 2 - 0.05}
    />
  )
}

// ── 3D Robot AMR Model ───────────────────────────────────────────────────────
function Robot3D({
  robot,
  isSelected,
  palette,
  theme,
  storeRef,
  onClick,
}: {
  robot: Robot
  isSelected: boolean
  palette: Palette
  theme: 'light' | 'dark'
  storeRef?: React.RefObject<FleetStore>
  onClick: () => void
}) {
  const meshRef = useRef<THREE.Group>(null)
  const initialX = robot.position.x - 14.5
  const initialZ = robot.position.y - 14.5

  useFrame(() => {
    if (meshRef.current) {
      const live = storeRef?.current?.robots.get(robot.robot_id) ?? robot
      const targetX = live.position.x - 14.5
      const targetZ = live.position.y - 14.5
      const targetRotation = HEADING_ROTATION[live.heading] ?? 0

      meshRef.current.position.x = THREE.MathUtils.lerp(meshRef.current.position.x, targetX, 0.25)
      meshRef.current.position.z = THREE.MathUtils.lerp(meshRef.current.position.z, targetZ, 0.25)
      meshRef.current.rotation.y = THREE.MathUtils.lerp(meshRef.current.rotation.y, targetRotation, 0.25)
    }
  })

  const baseColor = ROBOT_TYPE_COLORS[robot.robot_type] || palette.steel
  const stateColor = STATE_COLORS[robot.state] || '#94a3b8'

  return (
    <group
      ref={meshRef}
      position={[initialX, 0.18, initialZ]}
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
      {/* Selection Halo on Floor */}
      {isSelected && (
        <mesh position={[0, -0.08, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[0.65, 0.82, 32]} />
          <meshBasicMaterial color="#38bdf8" side={THREE.DoubleSide} transparent opacity={0.85} />
        </mesh>
      )}

      {/* Main AMR Chassis */}
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

      {/* State Status Light Ring on Top */}
      <mesh position={[0, 0.26, 0]}>
        <cylinderGeometry args={[0.18, 0.18, 0.04, 20]} />
        <meshStandardMaterial color={stateColor} emissive={stateColor} emissiveIntensity={1.2} />
      </mesh>

      {/* Wheels */}
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

      {/* Carried Pod Payload if lifting */}
      {robot.carrying_pod_id && (
        <group position={[0, 0.85, 0]}>
          <mesh castShadow receiveShadow>
            <boxGeometry args={[0.74, 1.1, 0.74]} />
            <meshStandardMaterial color={palette.steelTop} metalness={0.25} roughness={0.5} />
          </mesh>
          <mesh position={[0, 0.56, 0]}>
            <boxGeometry args={[0.76, 0.04, 0.76]} />
            <meshStandardMaterial color={palette.steel} metalness={0.4} roughness={0.4} />
          </mesh>
          <Text position={[0, 0, 0.38]} fontSize={0.15} color={palette.text}>
            {robot.carrying_pod_id}
          </Text>
        </group>
      )}

      {/* Carried Carton (for SORTING AMR) */}
      {!robot.carrying_pod_id && robot.robot_type === 'SORTING' && robot.state === 'EN_ROUTE_DROPOFF' && (
        <mesh position={[0, 0.38, 0]} castShadow>
          <boxGeometry args={[0.42, 0.22, 0.42]} />
          <meshStandardMaterial color="#d97706" roughness={0.8} />
        </mesh>
      )}

      {/* Floating Robot ID Tag & Battery Mini Gauge */}
      <Html position={[0, 1.25 + (robot.carrying_pod_id ? 0.7 : 0), 0]} center distanceFactor={18}>
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
function PathTrail({ path, color }: { path: Array<Point & { t?: number }>; color: string }) {
  const points = useMemo(() => {
    return path.map((p) => [p.x - 14.5, 0.08, p.y - 14.5] as [number, number, number])
  }, [path])

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
}: {
  podSlots?: Array<{ shelf_id: string; x: number; y: number }>
  staticObstacles?: Point[]
  robots: Robot[]
  palette: Palette
  theme: 'light' | 'dark'
}) {
  const carriedPodIds = useMemo(() => {
    return new Set(robots.map((r) => r.carrying_pod_id).filter(Boolean))
  }, [robots])

  return (
    <group>
      {/* 1. Movable Pod Racks */}
      {podSlots &&
        podSlots.map((slot) => {
          const x = slot.x - 14.5
          const z = slot.y - 14.5
          const isCarried = carriedPodIds.has(slot.shelf_id)

          if (isCarried) {
            // Empty slot footprint (dashed ground frame)
            return (
              <group key={slot.shelf_id} position={[x, 0.02, z]}>
                <mesh rotation={[-Math.PI / 2, 0, 0]}>
                  <ringGeometry args={[0.34, 0.38, 4]} />
                  <meshBasicMaterial color={theme === 'light' ? '#d97706' : '#fb923c'} transparent opacity={0.5} />
                </mesh>
              </group>
            )
          }

          return (
            <group key={slot.shelf_id} position={[x, 0.6, z]}>
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

      {/* 2. Static Obstacle Pallet Racks */}
      {staticObstacles &&
        staticObstacles.map((pt, i) => {
          const x = pt.x - 14.5
          const z = pt.y - 14.5
          return (
            <group key={`obs-${i}`} position={[x, 0.6, z]}>
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

// ── Bounded Sortation Zone with 8 Relocated Chutes ───────────────────────────
function SortationZone3D({
  chutes,
  palette,
}: {
  chutes?: Record<string, { destination_zone: string; x: number; y: number }>
  palette: Palette
}) {
  const zoneCenterX = 24.5 - 14.5
  const zoneCenterZ = 3.5 - 14.5
  const zoneWidth = 6.0
  const zoneHeight = 4.0

  return (
    <group>
      {/* Zone Floor Tint */}
      <mesh position={[zoneCenterX, 0.015, zoneCenterZ]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[zoneWidth, zoneHeight]} />
        <meshStandardMaterial color={palette.export} transparent opacity={0.14} />
      </mesh>

      {/* Zone Title Label */}
      <Text position={[zoneCenterX, 1.6, zoneCenterZ - 2.2]} fontSize={0.36} color={palette.export} anchorX="center">
        SORTATION PUT-WALL ZONE
      </Text>

      {/* Entrance Marker at (21, 3.5) */}
      <mesh position={[21 - 14.5, 0.025, 3.5 - 14.5]}>
        <boxGeometry args={[0.25, 0.04, 1.6]} />
        <meshStandardMaterial color={palette.charger} emissive={palette.charger} emissiveIntensity={0.6} />
      </mesh>
      <Text position={[21 - 14.5 - 0.35, 0.5, 3.5 - 14.5]} fontSize={0.2} color={palette.charger} rotation={[0, Math.PI / 2, 0]}>
        SORT ENTRY
      </Text>

      {/* Put-Wall Chutes */}
      {chutes &&
        Object.entries(chutes).map(([chuteId, c]) => {
          const cx = c.x - 14.5
          const cz = c.y - 14.5
          return (
            <group key={chuteId} position={[cx, 0.45, cz]}>
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
                {c.destination_zone.replace('ZONE_', '')}
              </Text>
            </group>
          )
        })}
    </group>
  )
}

// ── Pick Stations at Yard East Boundary (x=20) ──────────────────────────────
function PickStations3D({ palette }: { palette: Palette }) {
  const stations = [
    { id: 'PICK-01', x: 20, y: 2, bufferCount: 2 },
    { id: 'PICK-02', x: 20, y: 3, bufferCount: 1 },
    { id: 'PICK-03', x: 20, y: 4, bufferCount: 3 },
  ]

  return (
    <group>
      {stations.map((st) => {
        const sx = st.x - 14.5
        const sz = st.y - 14.5
        return (
          <group key={st.id} position={[sx, 0.32, sz]}>
            {/* Table / Stand */}
            <mesh castShadow receiveShadow>
              <boxGeometry args={[0.8, 0.55, 0.8]} />
              <meshStandardMaterial color={palette.steel} metalness={0.4} roughness={0.5} />
            </mesh>
            {/* Buffer Cartons Stacked */}
            {Array.from({ length: st.bufferCount }).map((_, idx) => (
              <mesh key={idx} position={[0, 0.34 + idx * 0.16, 0]} castShadow>
                <boxGeometry args={[0.38, 0.14, 0.38]} />
                <meshStandardMaterial color="#d97706" roughness={0.7} />
              </mesh>
            ))}
            <Text position={[0, 0.75 + st.bufferCount * 0.16, 0]} fontSize={0.14} color={palette.text} anchorX="center">
              {st.id} ({st.bufferCount}/4)
            </Text>
          </group>
        )
      })}
    </group>
  )
}

// ── Charging Alcoves ────────────────────────────────────────────────────────
function ChargingStations3D({ stations, palette }: { stations: Point[]; palette: Palette }) {
  return (
    <group>
      {stations.map((st, i) => (
        <group key={i} position={[st.x - 14.5, 0.02, st.y - 14.5]}>
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
function Gates3D({ palette }: { palette: Palette }) {
  const inGates = [
    { label: 'IN-1', x: 0, y: 9 },
    { label: 'IN-2', x: 0, y: 14 },
    { label: 'IN-3', x: 0, y: 19 },
  ]
  const outGates = [
    { label: 'OUT-1', x: 29, y: 9 },
    { label: 'OUT-2', x: 29, y: 14 },
    { label: 'OUT-3', x: 29, y: 19 },
  ]

  return (
    <group>
      {inGates.map((g) => (
        <group key={g.label} position={[g.x - 14.5, 0.03, g.y - 14.5]}>
          <mesh rotation={[-Math.PI / 2, 0, 0]}>
            <planeGeometry args={[0.9, 2.7]} />
            <meshStandardMaterial color={palette.import} transparent opacity={0.35} />
          </mesh>
          <Text position={[0.55, 1.0, 0]} fontSize={0.28} color={palette.import} rotation={[0, Math.PI / 2, 0]}>
            RECEIVING {g.label}
          </Text>
        </group>
      ))}
      {outGates.map((g) => (
        <group key={g.label} position={[g.x - 14.5, 0.03, g.y - 14.5]}>
          <mesh rotation={[-Math.PI / 2, 0, 0]}>
            <planeGeometry args={[0.9, 2.7]} />
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
function DynamicObstacles3D({ obstacles, palette, tick }: { obstacles?: TempObstacle[]; palette: Palette; tick: number }) {
  if (!obstacles || obstacles.length === 0) return null

  return (
    <group>
      {obstacles.map((obs, i) => {
        const remaining = Math.max(0, obs.expires_at_tick - tick)
        const x = obs.position.x - 14.5
        const z = obs.position.y - 14.5
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
function ConflictBeacons3D({ conflicts, palette }: { conflicts?: Conflict[]; palette: Palette }) {
  if (!conflicts || conflicts.length === 0) return null

  return (
    <group>
      {conflicts.map((c, i) => {
        const x = c.cell.x - 14.5
        const z = c.cell.y - 14.5
        return (
          <group key={`conf-${i}`} position={[x, 0.04, z]}>
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
function MeshLinks3D({ robots, showMeshLinks, palette }: { robots: Robot[]; showMeshLinks?: boolean; palette: Palette }) {
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
            from: [r1.position.x - 14.5, 0.28, r1.position.y - 14.5],
            to: [r2.position.x - 14.5, 0.28, r2.position.y - 14.5],
            isConflicted,
          })
        }
      }
    }
    return res
  }, [robots, showMeshLinks])

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
  onCellClick,
}: {
  width: number
  height: number
  palette: Palette
  nearObstacleSet: Set<string>
  pickupSet: Set<string>
  dropoffSet: Set<string>
  chargingSet: Set<string>
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
        dummy.position.set(x - 14.5, -0.015, y - 14.5)
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
  }, [width, height, palette, nearObstacleSet, pickupSet, dropoffSet, chargingSet])

  return (
    <group>
      {/* 900 Tile Checkerboard Instanced Mesh */}
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
          const cellX = Math.floor(pt.x + 15)
          const cellY = Math.floor(pt.z + 15)
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
  onZoomInRef,
  onZoomOutRef,
}: Warehouse3DProps) {
  const controlsRef = useRef<any>(null)

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
          if (offset.length() > 7) {
            offset.multiplyScalar(0.84)
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
          if (offset.length() < 78) {
            offset.multiplyScalar(1.18)
            cam.position.copy(target).add(offset)
            controlsRef.current.update()
          }
        }
      }
    }
  }, [onResetViewRef, onZoomInRef, onZoomOutRef])

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
          onCellClick={onCell}
        />

        {/* Pod Racks & Static Obstacles */}
        <StorageRacks
          podSlots={world.pod_slots}
          staticObstacles={world.static_obstacles}
          robots={robots}
          palette={palette}
          theme={theme}
        />

        {/* Bounded Sortation Put-Wall Zone */}
        <SortationZone3D chutes={world.sortation_chutes} palette={palette} />

        {/* Pick Stations with Buffer Cartons */}
        <PickStations3D palette={palette} />

        {/* Charging Stations */}
        <ChargingStations3D stations={world.charging_stations} palette={palette} />

        {/* Inbound & Outbound Dock Gates */}
        <Gates3D palette={palette} />

        {/* Temporary Dynamic Obstacles */}
        <DynamicObstacles3D obstacles={obstacles} palette={palette} tick={tick} />

        {/* Decentralized Conflict Arbitration Beacons */}
        <ConflictBeacons3D conflicts={conflicts} palette={palette} />

        {/* P2P Mesh Communication Links */}
        <MeshLinks3D robots={robots} showMeshLinks={showMeshLinks} palette={palette} />

        {/* Active AMRs */}
        {robots.map((robot) => (
          <Robot3D
            key={robot.robot_id}
            robot={robot}
            isSelected={robot.robot_id === selected}
            palette={palette}
            theme={theme}
            storeRef={storeRef}
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
            />
          )
        })}
      </Canvas>
    </div>
  )
}
