import React, { useState, useEffect, useRef, useCallback } from 'react'
import {
  Boxes,
  LogIn,
  LogOut,
  Layers,
  Target,
  Bot,
  BatteryCharging,
  Square,
  Eraser,
  Undo2,
  Redo2,
  Trash2,
  Download,
  Upload,
  Play,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Maximize2,
  Minimize2,
  Sliders,
  Grid as GridIcon,
  X,
  Plus,
  RefreshCw,
} from 'lucide-react'
import type { WarehouseMap, MapValidationResult, RobotType, Point } from '../types'
import { api } from '../api'

type EditorTool =
  | 'select'
  | 'shelf'
  | 'entry_gate'
  | 'exit_gate'
  | 'sorting_station'
  | 'pick_station'
  | 'robot_start'
  | 'charger'
  | 'wall'
  | 'eraser'

interface WarehouseMapEditorProps {
  initialMap?: WarehouseMap | null
  onLaunch: (launchedMap: WarehouseMap) => void
  onCancel?: () => void
  theme?: 'dark' | 'light'
}

export function WarehouseMapEditor({ initialMap, onLaunch, onCancel, theme = 'dark' }: WarehouseMapEditorProps) {
  // ── Grid State ─────────────────────────────────────────────────────────────
  const [width, setWidth] = useState<number>(initialMap?.grid.width || 30)
  const [height, setHeight] = useState<number>(initialMap?.grid.height || 30)
  const [mapName, setMapName] = useState<string>(initialMap?.name || 'Custom Warehouse Map')

  const [activeTool, setActiveTool] = useState<EditorTool>('shelf')
  const [selectedRobotType, setSelectedRobotType] = useState<RobotType>('GOODS_TO_PERSON')
  const [zoom, setZoom] = useState<number>(20) // cell size in px

  // Entities
  const [blockedCells, setBlockedCells] = useState<Point[]>(initialMap?.blocked_cells || [])
  const [shelves, setShelves] = useState<WarehouseMap['shelves']>(initialMap?.shelves || [])
  const [entryGates, setEntryGates] = useState<WarehouseMap['entry_gates']>(initialMap?.entry_gates || [])
  const [exitGates, setExitGates] = useState<WarehouseMap['exit_gates']>(initialMap?.exit_gates || [])
  const [sortingStations, setSortingStations] = useState<WarehouseMap['sorting_stations']>(initialMap?.sorting_stations || [])
  const [pickStations, setPickStations] = useState<WarehouseMap['pick_stations']>(initialMap?.pick_stations || [])
  const [chargers, setChargers] = useState<WarehouseMap['chargers']>(initialMap?.chargers || [])
  const [robotStarts, setRobotStarts] = useState<WarehouseMap['robot_starts']>(initialMap?.robot_starts || [])

  // Undo / Redo history
  const [history, setHistory] = useState<WarehouseMap[]>([])
  const [historyIndex, setHistoryIndex] = useState<number>(-1)

  // Validation State
  const [validation, setValidation] = useState<MapValidationResult>({
    valid: false,
    errors: [],
    warnings: [],
  })
  const [isValidating, setIsValidating] = useState<boolean>(false)
  const [isLaunching, setIsLaunching] = useState<boolean>(false)

  // Mouse interaction state
  const [isMouseDown, setIsMouseDown] = useState<boolean>(false)
  const [hoveredCell, setHoveredCell] = useState<Point | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Construct current map object
  const getCurrentMapObject = useCallback((): WarehouseMap => {
    return {
      schema_version: '1.0.0',
      name: mapName,
      description: `Custom ${width}x${height} layout designed in Warehouse Map Editor`,
      grid: { width, height, cell_size_m: 1.0 },
      blocked_cells: blockedCells,
      shelves,
      entry_gates: entryGates,
      exit_gates: exitGates,
      sorting_stations: sortingStations,
      pick_stations: pickStations,
      chargers,
      robot_starts: robotStarts,
    }
  }, [width, height, mapName, blockedCells, shelves, entryGates, exitGates, sortingStations, pickStations, chargers, robotStarts])

  // Push state to history
  const recordHistory = useCallback(() => {
    const current = getCurrentMapObject()
    setHistory((prev) => [...prev.slice(0, historyIndex + 1), current])
    setHistoryIndex((prev) => prev + 1)
  }, [getCurrentMapObject, historyIndex])

  // Run validation whenever map changes
  useEffect(() => {
    let cancel = false
    const runValidation = async () => {
      setIsValidating(true)
      try {
        const currentMap = getCurrentMapObject()
        const res = await api.mapValidate(currentMap)
        if (!cancel) setValidation(res)
      } catch (err) {
        if (!cancel) {
          setValidation({
            valid: false,
            errors: [err instanceof Error ? err.message : 'Validation server unreachable'],
            warnings: [],
          })
        }
      } finally {
        if (!cancel) setIsValidating(false)
      }
    }

    const timer = setTimeout(runValidation, 250)
    return () => {
      cancel = true
      clearTimeout(timer)
    }
  }, [getCurrentMapObject])

  // ── Cell Operations ────────────────────────────────────────────────────────
  const applyToolToCell = (x: number, y: number) => {
    if (x < 0 || x >= width || y < 0 || y >= height) return

    // 1. Eraser
    if (activeTool === 'eraser') {
      setBlockedCells((prev) => prev.filter((c) => c.x !== x || c.y !== y))
      setShelves((prev) => prev.filter((s) => s.x !== x || s.y !== y))
      setChargers((prev) => prev.filter((c) => c.x !== x || c.y !== y))
      setRobotStarts((prev) => prev.filter((r) => r.x !== x || r.y !== y))
      setSortingStations((prev) => prev.filter((s) => s.x !== x || s.y !== y))
      setPickStations((prev) => prev.filter((p) => p.x !== x || p.y !== y))
      setEntryGates((prev) =>
        prev
          .map((g) => ({ ...g, cells: g.cells.filter((c) => c.x !== x || c.y !== y) }))
          .filter((g) => g.cells.length > 0)
      )
      setExitGates((prev) =>
        prev
          .map((g) => ({ ...g, cells: g.cells.filter((c) => c.x !== x || c.y !== y) }))
          .filter((g) => g.cells.length > 0)
      )
      return
    }

    // Clear any existing entity at this cell before adding new
    setBlockedCells((prev) => prev.filter((c) => c.x !== x || c.y !== y))
    setShelves((prev) => prev.filter((s) => s.x !== x || s.y !== y))
    setChargers((prev) => prev.filter((c) => c.x !== x || c.y !== y))
    setRobotStarts((prev) => prev.filter((r) => r.x !== x || r.y !== y))
    setSortingStations((prev) => prev.filter((s) => s.x !== x || s.y !== y))
    setPickStations((prev) => prev.filter((p) => p.x !== x || p.y !== y))

    // 2. Wall / Blocked
    if (activeTool === 'wall') {
      setBlockedCells((prev) => [...prev, { x, y }])
    }

    // 3. Shelf
    else if (activeTool === 'shelf') {
      const sid = `POD-${String.fromCharCode(65 + Math.floor(y / 5))}${shelves.length + 1}`
      setShelves((prev) => [...prev, { id: sid, x, y, capacity: 4 }])
    }

    // 4. Charger
    else if (activeTool === 'charger') {
      const cid = `CHG-${chargers.length + 1}`
      setChargers((prev) => [...prev, { id: cid, x, y, assigned_robot_id: null }])
    }

    // 5. Robot Start
    else if (activeTool === 'robot_start') {
      const rid = `AMR-${String(robotStarts.length + 1).padStart(2, '0')}`
      setRobotStarts((prev) => [
        ...prev,
        { id: rid, type: selectedRobotType, x, y, battery_pct: 100.0, urgency: 3 },
      ])
    }

    // 6. Sorting Station
    else if (activeTool === 'sorting_station') {
      const sid = `CHUTE-${String(sortingStations.length + 1).padStart(2, '0')}`
      setSortingStations((prev) => [
        ...prev,
        { id: sid, name: sid, x, y, destination_zone: 'ZONE_DEFAULT', capacity: 10 },
      ])
    }

    // 7. Pick Station
    else if (activeTool === 'pick_station') {
      const pid = `PICK-${String(pickStations.length + 1).padStart(2, '0')}`
      setPickStations((prev) => [...prev, { id: pid, name: pid, x, y, capacity: 4 }])
    }

    // 8. Entry Gate
    else if (activeTool === 'entry_gate') {
      const gid = `IN-${entryGates.length + 1}`
      setEntryGates((prev) => [...prev, { id: gid, name: `Import Dock ${gid}`, cells: [{ x, y }] }])
    }

    // 9. Exit Gate
    else if (activeTool === 'exit_gate') {
      const gid = `OUT-${exitGates.length + 1}`
      setExitGates((prev) => [...prev, { id: gid, name: `Shipping Dock ${gid}`, cells: [{ x, y }] }])
    }
  }

  // ── Undo / Redo ────────────────────────────────────────────────────────────
  const handleUndo = () => {
    if (historyIndex > 0) {
      const prev = history[historyIndex - 1]
      setHistoryIndex((idx) => idx - 1)
      loadMapState(prev)
    }
  }

  const handleRedo = () => {
    if (historyIndex < history.length - 1) {
      const next = history[historyIndex + 1]
      setHistoryIndex((idx) => idx + 1)
      loadMapState(next)
    }
  }

  const loadMapState = (map: WarehouseMap) => {
    setWidth(map.grid.width)
    setHeight(map.grid.height)
    setMapName(map.name)
    setBlockedCells(map.blocked_cells || [])
    setShelves(map.shelves || [])
    setEntryGates(map.entry_gates || [])
    setExitGates(map.exit_gates || [])
    setSortingStations(map.sorting_stations || [])
    setPickStations(map.pick_stations || [])
    setChargers(map.chargers || [])
    setRobotStarts(map.robot_starts || [])
  }

  // Clear Grid
  const handleClear = () => {
    if (window.confirm('Clear all entities on the map?')) {
      recordHistory()
      setBlockedCells([])
      setShelves([])
      setEntryGates([])
      setExitGates([])
      setSortingStations([])
      setPickStations([])
      setChargers([])
      setRobotStarts([])
    }
  }

  // Export JSON
  const handleExportJSON = () => {
    const current = getCurrentMapObject()
    const dataStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(current, null, 2))
    const downloadAnchor = document.createElement('a')
    downloadAnchor.setAttribute('href', dataStr)
    downloadAnchor.setAttribute('download', `${mapName.toLowerCase().replace(/\s+/g, '_')}.json`)
    document.body.appendChild(downloadAnchor)
    downloadAnchor.click()
    downloadAnchor.remove()
  }

  // Import JSON
  const handleImportFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (event) => {
      try {
        const parsed = JSON.parse(event.target?.result as string)
        recordHistory()
        loadMapState(parsed)
      } catch (err) {
        alert('Invalid JSON file format.')
      }
    }
    reader.readAsText(file)
  }

  // Launch Map
  const handleLaunch = async () => {
    if (!validation.valid) return
    setIsLaunching(true)
    try {
      const current = getCurrentMapObject()
      await api.mapLaunch(current)
      onLaunch(current)
    } catch (err) {
      alert(`Launch error: ${err instanceof Error ? err.message : 'Unknown error'}`)
    } finally {
      setIsLaunching(false)
    }
  }

  // ── Palette & Styles ───────────────────────────────────────────────────────
  const isDark = theme === 'dark'
  const bgColor = isDark ? '#111111' : '#F5F5F5'
  const panelBg = isDark ? '#1A1A1A' : '#FFFFFF'
  const borderColor = isDark ? '#333333' : '#E0E0E0'
  const textColor = isDark ? '#E5E5E5' : '#1F2937'
  const mutedText = isDark ? '#9CA3AF' : '#6B7280'

  // Lookups for fast grid cell rendering
  const blockedMap = new Set(blockedCells.map((c) => `${c.x},${c.y}`))
  const shelfMap = new Map(shelves.map((s) => [`${s.x},${s.y}`, s]))
  const chargerMap = new Map(chargers.map((c) => [`${c.x},${c.y}`, c]))
  const robotMap = new Map(robotStarts.map((r) => [`${r.x},${r.y}`, r]))
  const chuteMap = new Map(sortingStations.map((s) => [`${s.x},${s.y}`, s]))
  const pickMap = new Map(pickStations.map((p) => [`${p.x},${p.y}`, p]))
  const gateMap = new Map<string, { type: 'entry' | 'exit'; id: string }>()
  entryGates.forEach((g) => g.cells.forEach((c) => gateMap.set(`${c.x},${c.y}`, { type: 'entry', id: g.id })))
  exitGates.forEach((g) => g.cells.forEach((c) => gateMap.set(`${c.x},${c.y}`, { type: 'exit', id: g.id })))

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 9999,
        display: 'flex',
        flexDirection: 'column',
        backgroundColor: bgColor,
        color: textColor,
        fontFamily: 'Inter, system-ui, sans-serif',
      }}
    >
      {/* ── Top Header Toolbar ───────────────────────────────────────────────── */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '10px 20px',
          backgroundColor: panelBg,
          borderBottom: `1px solid ${borderColor}`,
          gap: 16,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <GridIcon size={20} color="#FF6B35" />
          <input
            type="text"
            value={mapName}
            onChange={(e) => setMapName(e.target.value)}
            style={{
              fontSize: 16,
              fontWeight: 700,
              background: 'transparent',
              border: 'none',
              color: textColor,
              outline: 'none',
              borderBottom: '1px dashed transparent',
            }}
            onFocus={(e) => (e.target.style.borderBottomColor = '#FF6B35')}
            onBlur={(e) => (e.target.style.borderBottomColor = 'transparent')}
          />
          <span style={{ fontSize: 12, color: mutedText }}>({width} × {height})</span>
        </div>

        {/* Action Controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <button
            onClick={handleUndo}
            disabled={historyIndex <= 0}
            title="Undo"
            style={{ padding: '6px 10px', background: isDark ? '#262626' : '#E5E5E5', border: 'none', borderRadius: 6, color: textColor, cursor: 'pointer', opacity: historyIndex <= 0 ? 0.4 : 1 }}
          >
            <Undo2 size={16} />
          </button>
          <button
            onClick={handleRedo}
            disabled={historyIndex >= history.length - 1}
            title="Redo"
            style={{ padding: '6px 10px', background: isDark ? '#262626' : '#E5E5E5', border: 'none', borderRadius: 6, color: textColor, cursor: 'pointer', opacity: historyIndex >= history.length - 1 ? 0.4 : 1 }}
          >
            <Redo2 size={16} />
          </button>
          <button
            onClick={handleClear}
            title="Clear all"
            style={{ padding: '6px 10px', background: '#DC262622', border: '1px solid #DC262655', borderRadius: 6, color: '#EF4444', cursor: 'pointer' }}
          >
            <Trash2 size={16} />
          </button>

          <div style={{ width: 1, height: 20, backgroundColor: borderColor }} />

          <input ref={fileInputRef} type="file" accept=".json" style={{ display: 'none' }} onChange={handleImportFile} />
          <button
            onClick={() => fileInputRef.current?.click()}
            style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', background: isDark ? '#262626' : '#E5E5E5', border: 'none', borderRadius: 6, color: textColor, cursor: 'pointer', fontSize: 13 }}
          >
            <Upload size={14} /> Import JSON
          </button>
          <button
            onClick={handleExportJSON}
            style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', background: isDark ? '#262626' : '#E5E5E5', border: 'none', borderRadius: 6, color: textColor, cursor: 'pointer', fontSize: 13 }}
          >
            <Download size={14} /> Export JSON
          </button>

          <div style={{ width: 1, height: 20, backgroundColor: borderColor }} />

          {/* Launch Button */}
          <button
            onClick={handleLaunch}
            disabled={!validation.valid || isLaunching}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '8px 18px',
              backgroundColor: validation.valid ? '#16A34A' : '#4B5563',
              color: '#FFFFFF',
              border: 'none',
              borderRadius: 6,
              fontWeight: 600,
              fontSize: 13,
              cursor: validation.valid ? 'pointer' : 'not-allowed',
              opacity: validation.valid ? 1 : 0.6,
              boxShadow: validation.valid ? '0 0 14px rgba(22, 163, 74, 0.4)' : 'none',
            }}
          >
            <Play size={15} fill="currentColor" /> {isLaunching ? 'Launching Fleet...' : 'Launch Warehouse'}
          </button>

          {onCancel && (
            <button
              onClick={onCancel}
              style={{ padding: '6px', background: 'transparent', border: 'none', color: mutedText, cursor: 'pointer' }}
            >
              <X size={20} />
            </button>
          )}
        </div>
      </div>

      {/* ── Main Workspace ───────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Left Tool Palette */}
        <div
          style={{
            width: 220,
            backgroundColor: panelBg,
            borderRight: `1px solid ${borderColor}`,
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
            padding: 16,
            overflowY: 'auto',
          }}
        >
          <div style={{ fontSize: 11, fontWeight: 700, color: mutedText, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Palette Tools
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {[
              { id: 'shelf', label: 'Storage Shelf', icon: Boxes, color: '#3B82F6' },
              { id: 'wall', label: 'Wall / Obstacle', icon: Square, color: '#6B7280' },
              { id: 'charger', label: 'Charging Pad', icon: BatteryCharging, color: '#16A34A' },
              { id: 'robot_start', label: 'Robot Start', icon: Bot, color: '#EC4899' },
              { id: 'sorting_station', label: 'Sorting Chute', icon: Layers, color: '#F59E0B' },
              { id: 'pick_station', label: 'Pick Station', icon: Target, color: '#8B5CF6' },
              { id: 'entry_gate', label: 'Inbound Gate', icon: LogIn, color: '#FF6B35' },
              { id: 'exit_gate', label: 'Shipping Gate', icon: LogOut, color: '#EAB308' },
              { id: 'eraser', label: 'Erase Tile', icon: Eraser, color: '#EF4444' },
            ].map((tool) => {
              const Icon = tool.icon
              const isActive = activeTool === tool.id
              return (
                <button
                  key={tool.id}
                  onClick={() => {
                    recordHistory()
                    setActiveTool(tool.id as EditorTool)
                  }}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    padding: '8px 12px',
                    borderRadius: 6,
                    border: `1px solid ${isActive ? tool.color : 'transparent'}`,
                    backgroundColor: isActive ? `${tool.color}1A` : 'transparent',
                    color: isActive ? tool.color : textColor,
                    cursor: 'pointer',
                    fontSize: 13,
                    fontWeight: isActive ? 600 : 400,
                    textAlign: 'left',
                    transition: 'all 0.15s ease',
                  }}
                >
                  <Icon size={16} color={tool.color} />
                  <span>{tool.label}</span>
                </button>
              )
            })}
          </div>

          {/* Sub-selector for Robot Start Type */}
          {activeTool === 'robot_start' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, paddingTop: 10, borderTop: `1px solid ${borderColor}` }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: mutedText }}>AMR Role Type</div>
              {[
                { type: 'GOODS_TO_PERSON', label: 'G2P Fetcher' },
                { type: 'SORTING', label: 'Sorting AMR' },
                { type: 'SCANNING_AUDIT', label: 'Audit Drone' },
              ].map((r) => (
                <button
                  key={r.type}
                  onClick={() => setSelectedRobotType(r.type as RobotType)}
                  style={{
                    padding: '6px 10px',
                    fontSize: 12,
                    borderRadius: 4,
                    border: `1px solid ${selectedRobotType === r.type ? '#EC4899' : borderColor}`,
                    background: selectedRobotType === r.type ? '#EC489922' : 'transparent',
                    color: selectedRobotType === r.type ? '#EC4899' : textColor,
                    cursor: 'pointer',
                  }}
                >
                  {r.label}
                </button>
              ))}
            </div>
          )}

          {/* Grid Dimensions Setting */}
          <div style={{ marginTop: 'auto', paddingTop: 16, borderTop: `1px solid ${borderColor}`, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: mutedText }}>Grid Dimensions</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <label style={{ fontSize: 12, color: mutedText }}>W:</label>
              <input
                type="number"
                min={10}
                max={100}
                value={width}
                onChange={(e) => {
                  recordHistory()
                  setWidth(Math.max(10, Math.min(100, parseInt(e.target.value) || 30)))
                }}
                style={{ width: 60, padding: '4px 6px', background: isDark ? '#262626' : '#E5E5E5', border: 'none', borderRadius: 4, color: textColor }}
              />
              <label style={{ fontSize: 12, color: mutedText }}>H:</label>
              <input
                type="number"
                min={10}
                max={100}
                value={height}
                onChange={(e) => {
                  recordHistory()
                  setHeight(Math.max(10, Math.min(100, parseInt(e.target.value) || 30)))
                }}
                style={{ width: 60, padding: '4px 6px', background: isDark ? '#262626' : '#E5E5E5', border: 'none', borderRadius: 4, color: textColor }}
              />
            </div>
          </div>
        </div>

        {/* Center Interactive Canvas */}
        <div
          style={{
            flex: 1,
            overflow: 'auto',
            padding: 24,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: bgColor,
            userSelect: 'none',
          }}
          onMouseUp={() => setIsMouseDown(false)}
        >
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: `repeat(${width}, ${zoom}px)`,
              gridTemplateRows: `repeat(${height}, ${zoom}px)`,
              gap: 1,
              backgroundColor: isDark ? '#222222' : '#D1D5DB',
              padding: 2,
              borderRadius: 4,
              boxShadow: '0 8px 30px rgba(0,0,0,0.5)',
            }}
          >
            {Array.from({ length: height }).map((_, y) =>
              Array.from({ length: width }).map((_, x) => {
                const coordKey = `${x},${y}`
                const isBlocked = blockedMap.has(coordKey)
                const shelf = shelfMap.get(coordKey)
                const charger = chargerMap.get(coordKey)
                const robot = robotMap.get(coordKey)
                const chute = chuteMap.get(coordKey)
                const pick = pickMap.get(coordKey)
                const gate = gateMap.get(coordKey)

                let cellBg = isDark ? '#171717' : '#FFFFFF'
                let cellContent: React.ReactNode = null

                if (isBlocked) {
                  cellBg = '#4B5563'
                } else if (shelf) {
                  cellBg = '#1E3A8A'
                  cellContent = <Boxes size={zoom * 0.65} color="#93C5FD" />
                } else if (charger) {
                  cellBg = '#065F46'
                  cellContent = <BatteryCharging size={zoom * 0.65} color="#6EE7B7" />
                } else if (robot) {
                  cellBg = '#831843'
                  cellContent = <Bot size={zoom * 0.65} color="#F472B6" />
                } else if (chute) {
                  cellBg = '#78350F'
                  cellContent = <Layers size={zoom * 0.65} color="#FCD34D" />
                } else if (pick) {
                  cellBg = '#581C87'
                  cellContent = <Target size={zoom * 0.65} color="#D8B4FE" />
                } else if (gate) {
                  cellBg = gate.type === 'entry' ? '#7C2D12' : '#713F12'
                  cellContent = gate.type === 'entry' ? (
                    <LogIn size={zoom * 0.65} color="#FDBA74" />
                  ) : (
                    <LogOut size={zoom * 0.65} color="#FEF08A" />
                  )
                }

                return (
                  <div
                    key={coordKey}
                    onMouseDown={() => {
                      setIsMouseDown(true)
                      recordHistory()
                      applyToolToCell(x, y)
                    }}
                    onMouseEnter={() => {
                      setHoveredCell({ x, y })
                      if (isMouseDown) applyToolToCell(x, y)
                    }}
                    style={{
                      width: zoom,
                      height: zoom,
                      backgroundColor: cellBg,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      cursor: activeTool === 'eraser' ? 'crosshair' : 'pointer',
                      transition: 'background-color 0.05s ease',
                    }}
                  >
                    {cellContent}
                  </div>
                )
              })
            )}
          </div>
        </div>

        {/* Right Validation & Diagnostics Sidebar */}
        <div
          style={{
            width: 280,
            backgroundColor: panelBg,
            borderLeft: `1px solid ${borderColor}`,
            display: 'flex',
            flexDirection: 'column',
            padding: 16,
            gap: 16,
            overflowY: 'auto',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: 11, fontWeight: 700, color: mutedText, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Validation Status
            </span>
            {isValidating ? (
              <RefreshCw size={14} className="animate-spin" color="#FF6B35" />
            ) : validation.valid ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 4, color: '#16A34A', fontSize: 12, fontWeight: 600 }}>
                <CheckCircle2 size={16} /> Ready
              </div>
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', gap: 4, color: '#EF4444', fontSize: 12, fontWeight: 600 }}>
                <XCircle size={16} /> Blocked
              </div>
            )}
          </div>

          {/* Errors list */}
          {validation.errors.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: '#EF4444' }}>
                Errors ({validation.errors.length})
              </div>
              {validation.errors.map((err, i) => (
                <div
                  key={i}
                  style={{
                    fontSize: 12,
                    padding: '8px 10px',
                    borderRadius: 6,
                    backgroundColor: '#DC262615',
                    border: '1px solid #DC262633',
                    color: '#F87171',
                    lineHeight: 1.4,
                  }}
                >
                  {err}
                </div>
              ))}
            </div>
          )}

          {/* Warnings list */}
          {validation.warnings.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: '#F59E0B' }}>
                Warnings ({validation.warnings.length})
              </div>
              {validation.warnings.map((warn, i) => (
                <div
                  key={i}
                  style={{
                    fontSize: 12,
                    padding: '8px 10px',
                    borderRadius: 6,
                    backgroundColor: '#F59E0B15',
                    border: '1px solid #F59E0B33',
                    color: '#FBBF24',
                    lineHeight: 1.4,
                  }}
                >
                  {warn}
                </div>
              ))}
            </div>
          )}

          {/* Summary Stats */}
          <div style={{ marginTop: 'auto', borderTop: `1px solid ${borderColor}`, paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12, color: mutedText }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>Robots:</span>
              <b style={{ color: textColor }}>{robotStarts.length}</b>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>Shelves:</span>
              <b style={{ color: textColor }}>{shelves.length}</b>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>Chargers:</span>
              <b style={{ color: textColor }}>{chargers.length}</b>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>Chutes:</span>
              <b style={{ color: textColor }}>{sortingStations.length}</b>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>Gates:</span>
              <b style={{ color: textColor }}>{entryGates.length + exitGates.length}</b>
            </div>
            {hoveredCell && (
              <div style={{ marginTop: 6, paddingTop: 6, borderTop: `1px dashed ${borderColor}`, fontSize: 11 }}>
                Cursor: ({hoveredCell.x}, {hoveredCell.y})
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
