import React, { useRef, useMemo } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { OrbitControls, Text, Html, Line as DreiLine } from '@react-three/drei'
import * as THREE from 'three'
import type { Conflict, Point, Robot, Task, TempObstacle, World } from '../types'
import { ROBOT_TYPE_COLORS, STATE_COLORS } from '../state-meta'

type Props = {
  world: World
  robots: Robot[]
  tasks?: Task[]
  conflicts: Conflict[]
  obstacles: TempObstacle[]
  tick: number
  selected: string | null
  showMeshLinks?: boolean
  cameraFollow?: boolean
  theme?: 'light' | 'dark'
  onRobot: (robot: Robot) => void
  onCell: (point: Point) => void
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
  cameraFollow,
}: {
  selectedRobot: Robot | undefined
  cameraFollow: boolean
}) {
  const controlsRef = useRef<any>(null)

  useFrame(({ camera }) => {
    if (cameraFollow && selectedRobot) {
      const targetPos = new THREE.Vector3(selectedRobot.position.x - 15, 0.5, selectedRobot.position.y - 15)
      // Smoothly pan camera controls target to robot
      if (controlsRef.current) {
        controlsRef.current.target.lerp(targetPos, 0.08)
        controlsRef.current.update()
      }
      // Position camera slightly behind and above robot
      const rot = HEADING_ROTATION[selectedRobot.heading] ?? 0
      const desiredCamPos = new THREE.Vector3(
        targetPos.x - Math.sin(rot) * 7,
        targetPos.y + 8,
        targetPos.z - Math.cos(rot) * 7
      )
      camera.position.lerp(desiredCamPos, 0.05)
    }
  })

  return (
    <OrbitControls
      ref={controlsRef}
      makeDefault
      enableDamping
      dampingFactor={0.08}
      minDistance={4}
      maxDistance={65}
      maxPolarAngle={Math.PI / 2 - 0.05}
    />
  )
}

// ── 3D Robot AMR Model ───────────────────────────────────────────────────────
function Robot3D({
  robot,
  isSelected,
  onClick,
}: {
  robot: Robot
  isSelected: boolean
  onClick: () => void
}) {
  const meshRef = useRef<THREE.Group>(null)
  const targetX = robot.position.x - 15
  const targetZ = robot.position.y - 15
  const targetRotation = HEADING_ROTATION[robot.heading] ?? 0

  useFrame(() => {
    if (meshRef.current) {
      meshRef.current.position.x = THREE.MathUtils.lerp(meshRef.current.position.x, targetX, 0.25)
      meshRef.current.position.z = THREE.MathUtils.lerp(meshRef.current.position.z, targetZ, 0.25)
      meshRef.current.rotation.y = THREE.MathUtils.lerp(meshRef.current.rotation.y, targetRotation, 0.25)
    }
  })

  const baseColor = ROBOT_TYPE_COLORS[robot.robot_type] || '#3b82f6'
  const stateColor = STATE_COLORS[robot.state] || '#94a3b8'

  return (
    <group
      ref={meshRef}
      position={[targetX, 0.2, targetZ]}
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
      {/* Selection Halo */}
      {isSelected && (
        <mesh position={[0, -0.05, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[0.7, 0.85, 32]} />
          <meshBasicMaterial color="#38bdf8" side={THREE.DoubleSide} transparent opacity={0.8} />
        </mesh>
      )}

      {/* Main AMR Chassis */}
      <mesh castShadow receiveShadow position={[0, 0.15, 0]}>
        <boxGeometry args={[0.8, 0.25, 0.9]} />
        <meshStandardMaterial
          color={baseColor}
          metalness={0.6}
          roughness={0.25}
          emissive={isSelected ? baseColor : '#000000'}
          emissiveIntensity={isSelected ? 0.35 : 0}
        />
      </mesh>

      {/* Front Headlights / Heading Indicator */}
      <mesh position={[0.22, 0.18, 0.45]}>
        <sphereGeometry args={[0.06, 12, 12]} />
        <meshStandardMaterial color="#fef08a" emissive="#fef08a" emissiveIntensity={1.5} />
      </mesh>
      <mesh position={[-0.22, 0.18, 0.45]}>
        <sphereGeometry args={[0.06, 12, 12]} />
        <meshStandardMaterial color="#fef08a" emissive="#fef08a" emissiveIntensity={1.5} />
      </mesh>

      {/* State Status Light Ring on Top */}
      <mesh position={[0, 0.29, 0]}>
        <cylinderGeometry args={[0.2, 0.2, 0.05, 20]} />
        <meshStandardMaterial color={stateColor} emissive={stateColor} emissiveIntensity={1.2} />
      </mesh>

      {/* Wheels */}
      <mesh position={[-0.42, 0.1, 0.2]} rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.1, 0.1, 0.08, 16]} />
        <meshStandardMaterial color="#1f2937" roughness={0.9} />
      </mesh>
      <mesh position={[0.42, 0.1, 0.2]} rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.1, 0.1, 0.08, 16]} />
        <meshStandardMaterial color="#1f2937" roughness={0.9} />
      </mesh>
      <mesh position={[-0.42, 0.1, -0.2]} rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.1, 0.1, 0.08, 16]} />
        <meshStandardMaterial color="#1f2937" roughness={0.9} />
      </mesh>
      <mesh position={[0.42, 0.1, -0.2]} rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.1, 0.1, 0.08, 16]} />
        <meshStandardMaterial color="#1f2937" roughness={0.9} />
      </mesh>

      {/* Carried Pod Payload if lifting */}
      {robot.carrying_pod_id && (
        <group position={[0, 0.9, 0]}>
          <mesh castShadow receiveShadow>
            <boxGeometry args={[0.7, 1.1, 0.7]} />
            <meshStandardMaterial color="#0284c7" metalness={0.3} roughness={0.6} />
          </mesh>
          <Text position={[0, 0, 0.36]} fontSize={0.16} color="#ffffff">
            {robot.carrying_pod_id}
          </Text>
        </group>
      )}

      {/* Carried Carton (for SORTING AMR) */}
      {!robot.carrying_pod_id && robot.robot_type === 'SORTING' && robot.state === 'EN_ROUTE_DROPOFF' && (
        <mesh position={[0, 0.42, 0]} castShadow>
          <boxGeometry args={[0.45, 0.25, 0.45]} />
          <meshStandardMaterial color="#d97706" roughness={0.8} />
        </mesh>
      )}

      {/* Floating Robot ID Tag & Battery Mini Gauge */}
      <Html position={[0, 1.3 + (robot.carrying_pod_id ? 0.7 : 0), 0]} center distanceFactor={18}>
        <div
          style={{
            background: isSelected ? 'rgba(14, 165, 233, 0.95)' : 'rgba(15, 23, 42, 0.85)',
            border: `1px solid ${isSelected ? '#38bdf8' : 'rgba(255, 255, 255, 0.2)'}`,
            borderRadius: '4px',
            padding: '2px 5px',
            color: '#fff',
            fontSize: '10px',
            fontWeight: 700,
            whiteSpace: 'nowrap',
            boxShadow: '0 2px 8px rgba(0,0,0,0.5)',
            pointerEvents: 'none',
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
          }}
        >
          <span>{robot.robot_id}</span>
          <span
            style={{
              color: robot.battery_pct < 25 ? '#ef4444' : '#22c55e',
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
function PathTrail({ path, color }: { path: Array<Point & { t: number }>; color: string }) {
  const points = useMemo(() => {
    return path.map((p) => [p.x - 15, 0.08, p.y - 15] as [number, number, number])
  }, [path])

  if (points.length < 2) return null

  return (
    <DreiLine
      points={points}
      color={color}
      lineWidth={2.5}
      transparent
      opacity={0.85}
    />
  )
}

// ── Storage Pod Racks ───────────────────────────────────────────────────────
function StorageRacks({ podSlots }: { podSlots?: Array<{ shelf_id: string; x: number; y: number }> }) {
  if (!podSlots || podSlots.length === 0) return null

  return (
    <group>
      {podSlots.map((slot) => {
        const x = slot.x - 15
        const z = slot.y - 15
        return (
          <group key={slot.shelf_id} position={[x, 0.6, z]}>
            <mesh castShadow receiveShadow>
              <boxGeometry args={[0.75, 1.2, 0.75]} />
              <meshStandardMaterial color="#334155" metalness={0.4} roughness={0.5} />
            </mesh>
            {/* Shelf highlight bar */}
            <mesh position={[0, 0.25, 0.38]}>
              <boxGeometry args={[0.65, 0.08, 0.02]} />
              <meshStandardMaterial color="#0284c7" emissive="#0284c7" emissiveIntensity={0.4} />
            </mesh>
          </group>
        )
      })}
    </group>
  )
}

// ── Bounded Sortation Zone with 8 Relocated Chutes ───────────────────────────
function SortationZone3D({ chutes }: { chutes?: Record<string, { destination_zone: string; x: number; y: number }> }) {
  // Sortation Zone Rectangle: x in [22, 27], y in [2, 5] -> center is ((22+27)/2, (2+5)/2) = (24.5, 3.5)
  const zoneCenterX = 24.5 - 15
  const zoneCenterZ = 3.5 - 15
  const zoneWidth = 6.0
  const zoneHeight = 4.0

  return (
    <group>
      {/* Zone Floor Tint */}
      <mesh position={[zoneCenterX, 0.02, zoneCenterZ]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[zoneWidth, zoneHeight]} />
        <meshStandardMaterial color="#0284c7" transparent opacity={0.18} />
      </mesh>

      {/* Sortation Zone Boundary Glow Borders */}
      <mesh position={[zoneCenterX, 0.04, zoneCenterZ]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[zoneWidth / 2 - 0.06, zoneWidth / 2, 4]} />
        <meshBasicMaterial color="#38bdf8" />
      </mesh>

      {/* Zone Title Label */}
      <Text position={[zoneCenterX, 1.8, zoneCenterZ - 2.2]} fontSize={0.4} color="#38bdf8" anchorX="center">
        SORTATION PUT-WALL ZONE
      </Text>

      {/* Entrance Markers at (21, 3) and (21, 4) */}
      <mesh position={[21 - 15, 0.03, 3.5 - 15]}>
        <boxGeometry args={[0.3, 0.05, 1.6]} />
        <meshStandardMaterial color="#22c55e" emissive="#22c55e" emissiveIntensity={0.8} />
      </mesh>
      <Text position={[21 - 15 - 0.4, 0.6, 3.5 - 15]} fontSize={0.22} color="#22c55e" rotation={[0, Math.PI / 2, 0]}>
        SORT ENTRY
      </Text>

      {/* 8 Put-Wall Chutes */}
      {chutes &&
        Object.entries(chutes).map(([chuteId, c]) => {
          const cx = c.x - 15
          const cz = c.y - 15
          return (
            <group key={chuteId} position={[cx, 0.5, cz]}>
              <mesh castShadow receiveShadow>
                <boxGeometry args={[0.7, 0.8, 0.7]} />
                <meshStandardMaterial color="#1e293b" metalness={0.7} roughness={0.3} />
              </mesh>
              {/* Chute opening ramp */}
              <mesh position={[0, 0.2, 0]} rotation={[0.4, 0, 0]}>
                <boxGeometry args={[0.55, 0.1, 0.55]} />
                <meshStandardMaterial color="#8b5cf6" emissive="#8b5cf6" emissiveIntensity={0.5} />
              </mesh>
              <Text position={[0, 0.65, 0]} fontSize={0.16} color="#e2e8f0" anchorX="center">
                {chuteId}
              </Text>
              <Text position={[0, 0.5, 0.36]} fontSize={0.11} color="#a78bfa" anchorX="center">
                {c.destination_zone.replace('ZONE_', '')}
              </Text>
            </group>
          )
        })}
    </group>
  )
}

// ── Pick Stations at Yard East Boundary (x=20) ──────────────────────────────
function PickStations3D() {
  const stations = [
    { id: 'PICK-01', x: 20, y: 2, bufferCount: 2 },
    { id: 'PICK-02', x: 20, y: 3, bufferCount: 1 },
    { id: 'PICK-03', x: 20, y: 4, bufferCount: 3 },
  ]

  return (
    <group>
      {stations.map((st) => {
        const sx = st.x - 15
        const sz = st.y - 15
        return (
          <group key={st.id} position={[sx, 0.35, sz]}>
            {/* Table / Stand */}
            <mesh castShadow receiveShadow>
              <boxGeometry args={[0.8, 0.6, 0.8]} />
              <meshStandardMaterial color="#0f766e" metalness={0.4} roughness={0.5} />
            </mesh>
            {/* Buffer Cartons Stacked */}
            {Array.from({ length: st.bufferCount }).map((_, idx) => (
              <mesh key={idx} position={[0, 0.38 + idx * 0.18, 0]} castShadow>
                <boxGeometry args={[0.4, 0.16, 0.4]} />
                <meshStandardMaterial color="#f59e0b" roughness={0.7} />
              </mesh>
            ))}
            <Text position={[0, 0.85 + st.bufferCount * 0.18, 0]} fontSize={0.15} color="#2dd4bf" anchorX="center">
              {st.id} (Buf: {st.bufferCount}/4)
            </Text>
          </group>
        )
      })}
    </group>
  )
}

// ── Charging Alcoves ────────────────────────────────────────────────────────
function ChargingStations3D({ stations }: { stations: Point[] }) {
  return (
    <group>
      {stations.map((st, i) => (
        <group key={i} position={[st.x - 15, 0.02, st.y - 15]}>
          <mesh rotation={[-Math.PI / 2, 0, 0]}>
            <circleGeometry args={[0.45, 24]} />
            <meshStandardMaterial color="#15803d" emissive="#16a34a" emissiveIntensity={0.6} />
          </mesh>
          <mesh position={[0, 0.1, 0]}>
            <cylinderGeometry args={[0.08, 0.12, 0.2, 12]} />
            <meshStandardMaterial color="#4ade80" emissive="#4ade80" emissiveIntensity={1.2} />
          </mesh>
        </group>
      ))}
    </group>
  )
}

// ── Inbound / Outbound Dock Gates ───────────────────────────────────────────
function Gates3D() {
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
        <group key={g.label} position={[g.x - 15, 0.05, g.y - 15]}>
          <mesh rotation={[-Math.PI / 2, 0, 0]}>
            <planeGeometry args={[0.9, 2.8]} />
            <meshStandardMaterial color="#f97316" transparent opacity={0.35} />
          </mesh>
          <Text position={[0.6, 1.2, 0]} fontSize={0.3} color="#f97316" rotation={[0, Math.PI / 2, 0]}>
            RECEIVING {g.label}
          </Text>
        </group>
      ))}
      {outGates.map((g) => (
        <group key={g.label} position={[g.x - 15, 0.05, g.y - 15]}>
          <mesh rotation={[-Math.PI / 2, 0, 0]}>
            <planeGeometry args={[0.9, 2.8]} />
            <meshStandardMaterial color="#eab308" transparent opacity={0.35} />
          </mesh>
          <Text position={[-0.6, 1.2, 0]} fontSize={0.3} color="#eab308" rotation={[0, -Math.PI / 2, 0]}>
            SHIPPING {g.label}
          </Text>
        </group>
      ))}
    </group>
  )
}

// ── Interactive Ground Plane ────────────────────────────────────────────────
function GroundGrid({
  width,
  height,
  onCellClick,
}: {
  width: number
  height: number
  onCellClick: (point: Point) => void
}) {
  return (
    <group>
      <mesh
        rotation={[-Math.PI / 2, 0, 0]}
        position={[0, 0, 0]}
        receiveShadow
        onClick={(e) => {
          const point = e.point
          const cellX = Math.floor(point.x + 15 + 0.5)
          const cellY = Math.floor(point.z + 15 + 0.5)
          if (cellX >= 0 && cellX < width && cellY >= 0 && cellY < height) {
            onCellClick({ x: cellX, y: cellY })
          }
        }}
      >
        <planeGeometry args={[width, height]} />
        <meshStandardMaterial color="#0f172a" roughness={0.8} metalness={0.1} />
      </mesh>
      <gridHelper args={[width, width, '#334155', '#1e293b']} position={[0, 0.01, 0]} />
    </group>
  )
}

// ── Main Warehouse3DCanvas Component ─────────────────────────────────────────
export function Warehouse3DCanvas({
  world,
  robots,
  selected,
  cameraFollow = true,
  theme = 'dark',
  onRobot,
  onCell,
}: Props) {
  const selectedRobot = useMemo(() => {
    return robots.find((r) => r.robot_id === selected)
  }, [robots, selected])

  const bgColor = theme === 'light' ? '#E5E5E5' : '#111111'

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative', background: bgColor }}>
      <Canvas
        shadows
        camera={{ position: [0, 22, 28], fov: 45 }}
        style={{ width: '100%', height: '100%' }}
      >
        <ambientLight intensity={0.65} />
        <directionalLight
          position={[15, 30, 20]}
          intensity={1.2}
          castShadow
          shadow-mapSize-width={2048}
          shadow-mapSize-height={2048}
          shadow-camera-left={-20}
          shadow-camera-right={20}
          shadow-camera-top={20}
          shadow-camera-bottom={-20}
        />
        <pointLight position={[-10, 15, -10]} intensity={0.5} />

        {/* Orbit Controls & Smooth Camera Follow */}
        <CameraController selectedRobot={selectedRobot} cameraFollow={cameraFollow} />

        {/* Warehouse Floor */}
        <GroundGrid width={world.width} height={world.height} onCellClick={onCell} />

        {/* Pod Racks */}
        <StorageRacks podSlots={world.pod_slots} />

        {/* Bounded Sortation Put-Wall Zone */}
        <SortationZone3D chutes={world.sortation_chutes} />

        {/* Pick Stations with Buffer Cartons */}
        <PickStations3D />

        {/* Charging Stations */}
        <ChargingStations3D stations={world.charging_stations} />

        {/* Receiving and Shipping Gates */}
        <Gates3D />

        {/* Active AMRs */}
        {robots.map((robot) => (
          <Robot3D
            key={robot.robot_id}
            robot={robot}
            isSelected={robot.robot_id === selected}
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
              color={isSel ? '#38bdf8' : ROBOT_TYPE_COLORS[robot.robot_type] || '#94a3b8'}
            />
          )
        })}
      </Canvas>
    </div>
  )
}
