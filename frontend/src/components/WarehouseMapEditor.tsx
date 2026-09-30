import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react'
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
  Minus,
  RefreshCw,
  Save,
  FileCode,
  Package,
  Eye,
  Info,
} from 'lucide-react'
import type { WarehouseMap, MapValidationResult, RobotType, Point } from '../types'
import { api } from '../api'

export const DEFAULT_CATALOG = [
  { sku: 'SKU-A10', name: 'Standard Bolt Pack', weight_kg: 2.5 },
  { sku: 'SKU-A20', name: 'Precision Bearings', weight_kg: 1.8 },
  { sku: 'SKU-B10', name: 'Hydraulic Seals', weight_kg: 0.9 },
  { sku: 'SKU-B20', name: 'Motor Brushes', weight_kg: 1.2 },
  { sku: 'SKU-C10', name: 'Control Cables', weight_kg: 3.1 },
  { sku: 'SKU-C20', name: 'Optical Sensors', weight_kg: 0.5 },
  { sku: 'SKU-D10', name: 'Lithium Battery Cells', weight_kg: 4.0 },
  { sku: 'SKU-D20', name: 'Terminal Relays', weight_kg: 1.1 },
  { sku: 'SKU-E10', name: 'Industrial Fasteners', weight_kg: 2.2 },
  { sku: 'SKU-E20', name: 'Servo Couplers', weight_kg: 1.4 },
  { sku: 'SKU-F10', name: 'Fiber Optic Patch', weight_kg: 0.3 },
  { sku: 'SKU-F20', name: 'Pneumatic Valve Kit', weight_kg: 2.8 },
]

export function getAutoStockForCell(x: number, y: number, catalog = DEFAULT_CATALOG): Record<string, number> {
  const seed = (x * 7919 + y * 65537) >>> 0
  const count = 1 + (seed % 3) // 1 to 3 SKUs
  const stock: Record<string, number> = {}
  for (let i = 0; i < count; i++) {
    const idx = (seed + i * 31) % catalog.length
    const sku = catalog[idx].sku
    const qty = 10 + ((seed + i * 17) % 15) // 10 to 24 units
    stock[sku] = qty
  }
  return stock
}

export type EditorTool =
  | 'shelf'
  | 'wall'
  | 'charger'
  | 'g2p_robot'
  | 'sorting_robot'
  | 'audit_robot'
  | 'sorting_station'
  | 'pick_station'
  | 'entry_gate'
  | 'exit_gate'
  | 'eraser'

interface WarehouseMapEditorProps {
  initialMap?: WarehouseMap | null
  onLaunch: (launchedMap: WarehouseMap) => void
  onCancel?: () => void
  theme?: 'dark' | 'light'
}

export function WarehouseMapEditor({ initialMap, onLaunch, onCancel, theme = 'light' }: WarehouseMapEditorProps) {
  // ── 4a Isolation: Lock Body Scroll ──────────────────────────────────────────
  useEffect(() => {
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = prevOverflow
    }
  }, [])

  // ── Grid & Dimension State ──────────────────────────────────────────────────
  const [width, setWidth] = useState<number>(initialMap?.grid.width || 30)
  const [height, setHeight] = useState<number>(initialMap?.grid.height || 30)
  const [mapName, setMapName] = useState<string>(initialMap?.name || 'Custom Warehouse Map')

  const [activeTool, setActiveTool] = useState<EditorTool>('shelf')
  const [zoom, setZoom] = useState<number>(22) // cell size in px
  const [pan, setPan] = useState<{ x: number; y: number }>({ x: 0, y: 0 })
  const [isPanning, setIsPanning] = useState<boolean>(false)
  const panStartRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 })

  // Entities
  const [blockedCells, setBlockedCells] = useState<Point[]>(initialMap?.blocked_cells || [])
  const [shelves, setShelves] = useState<WarehouseMap['shelves']>(() => {
    return (initialMap?.shelves || []).map((s) => ({
      ...s,
      stock: s.stock || getAutoStockForCell(s.x, s.y),
    }))
  })
  const [entryGates, setEntryGates] = useState<WarehouseMap['entry_gates']>(initialMap?.entry_gates || [])
  const [exitGates, setExitGates] = useState<WarehouseMap['exit_gates']>(initialMap?.exit_gates || [])
  const [sortingStations, setSortingStations] = useState<WarehouseMap['sorting_stations']>(initialMap?.sorting_stations || [])
  const [pickStations, setPickStations] = useState<WarehouseMap['pick_stations']>(initialMap?.pick_stations || [])
  const [chargers, setChargers] = useState<WarehouseMap['chargers']>(initialMap?.chargers || [])
  const [robotStarts, setRobotStarts] = useState<WarehouseMap['robot_starts']>(initialMap?.robot_starts || [])

  // Selected Entity for Inspector / Edit
  const [selectedShelf, setSelectedShelf] = useState<{ id: string; x: number; y: number; stock: Record<string, number> } | null>(null)
  const [highlightedCell, setHighlightedCell] = useState<Point | null>(null)

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
  const [saveStatus, setSaveStatus] = useState<string | null>(null)

  // Mouse interaction state
  const [isMouseDown, setIsMouseDown] = useState<boolean>(false)
  const [hoveredCell, setHoveredCell] = useState<Point | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const paintedThisDragRef = useRef<Set<string>>(new Set())

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
      catalog: DEFAULT_CATALOG,
    }
  }, [width, height, mapName, blockedCells, shelves, entryGates, exitGates, sortingStations, pickStations, chargers, robotStarts])

  // Record history
  const recordHistory = useCallback(() => {
    const current = getCurrentMapObject()
    setHistory((prev) => [...prev.slice(0, historyIndex + 1), current])
    setHistoryIndex((prev) => prev + 1)
  }, [getCurrentMapObject, historyIndex])

  // Validation debounce (200ms)
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

    const timer = setTimeout(runValidation, 200)
    return () => {
      cancel = true
      clearTimeout(timer)
    }
  }, [getCurrentMapObject])

  // ── Keyboard Shortcuts (4g) ────────────────────────────────────────────────
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't trigger if user is typing in an input
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as HTMLElement)?.tagName)) return

      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        if (e.shiftKey) handleRedo()
        else handleUndo()
        return
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') {
        e.preventDefault()
        handleRedo()
        return
      }

      switch (e.key.toLowerCase()) {
        case 's': setActiveTool('shelf'); break
        case 'w': setActiveTool('wall'); break
        case 'c': setActiveTool('charger'); break
        case 'g': setActiveTool('g2p_robot'); break
        case 'o': setActiveTool('sorting_robot'); break
        case 'a': setActiveTool('audit_robot'); break
        case 't': setActiveTool('sorting_station'); break
        case 'p': setActiveTool('pick_station'); break
        case 'i': setActiveTool('entry_gate'); break
        case 'x': setActiveTool('exit_gate'); break
        case 'e':
        case 'backspace':
        case 'delete':
          setActiveTool('eraser'); break
        case '+':
        case '=':
          setZoom((prev) => Math.min(48, prev + 2)); break
        case '-':
        case '_':
          setZoom((prev) => Math.max(12, prev - 2)); break
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [historyIndex, history])

  // ── Tool Application with Deterministic IDs & No Stale Closure (4b, 4c, 4d, 4e) ──
  const applyToolToCell = useCallback((x: number, y: number) => {
    if (x < 0 || x >= width || y < 0 || y >= height) return
    const coordKey = `${x},${y}`

    // Prevent re-processing the same cell in the same drag gesture
    if (paintedThisDragRef.current.has(coordKey)) return
    paintedThisDragRef.current.add(coordKey)

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
      if (selectedShelf && selectedShelf.x === x && selectedShelf.y === y) {
        setSelectedShelf(null)
      }
      return
    }

    // Clear overlapping entities at (x, y) before placing new
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

    const rY = String(y).padStart(2, '0')
    const cX = String(x).padStart(2, '0')

    // 2. Wall
    if (activeTool === 'wall') {
      setBlockedCells((prev) => [...prev, { x, y }])
    }

    // 3. Shelf (4e: auto-stocked from product catalog)
    else if (activeTool === 'shelf') {
      const sid = `POD-R${rY}C${cX}`
      const stock = getAutoStockForCell(x, y)
      setShelves((prev) => [...prev, { id: sid, x, y, capacity: 4, stock }])
    }

    // 4. Charger (4b: CHG-R..C..)
    else if (activeTool === 'charger') {
      const cid = `CHG-R${rY}C${cX}`
      setChargers((prev) => [...prev, { id: cid, x, y, assigned_robot_id: null }])
    }

    // 5. Robots (Distinct Categories with G2P-XX, SORT-XX, AUDIT-XX)
    else if (activeTool === 'g2p_robot') {
      setRobotStarts((prev) => {
        const count = prev.filter((r) => r.type === 'GOODS_TO_PERSON').length + 1
        const rid = `G2P-${String(count).padStart(2, '0')}`
        return [
          ...prev,
          { id: rid, type: 'GOODS_TO_PERSON', x, y, battery_pct: 100.0, urgency: 1 },
        ]
      })
    } else if (activeTool === 'sorting_robot') {
      setRobotStarts((prev) => {
        const count = prev.filter((r) => r.type === 'SORTING').length + 1
        const rid = `SORT-${String(count).padStart(2, '0')}`
        return [
          ...prev,
          { id: rid, type: 'SORTING', x, y, battery_pct: 100.0, urgency: 1 },
        ]
      })
    } else if (activeTool === 'audit_robot') {
      setRobotStarts((prev) => {
        const count = prev.filter((r) => r.type === 'SCANNING_AUDIT').length + 1
        const rid = `AUDIT-${String(count).padStart(2, '0')}`
        return [
          ...prev,
          { id: rid, type: 'SCANNING_AUDIT', x, y, battery_pct: 100.0, urgency: 1 },
        ]
      })
    }

    // 6. Sorting Chute (4c: feeds exit gate)
    else if (activeTool === 'sorting_station') {
      const sid = `CHUTE-R${rY}C${cX}`
      // Resolve nearest exit gate default
      setExitGates((currentExitGates) => {
        let nearestGateId = currentExitGates.length > 0 ? currentExitGates[0].id : 'OUT-1'
        let minDist = Infinity
        for (const eg of currentExitGates) {
          for (const cell of eg.cells) {
            const d = Math.abs(x - cell.x) + Math.abs(y - cell.y)
            if (d < minDist) {
              minDist = d
              nearestGateId = eg.id
            }
          }
        }
        setSortingStations((prev) => [
          ...prev,
          {
            id: sid,
            name: sid,
            x,
            y,
            destination_zone: nearestGateId,
            gate_id: nearestGateId,
            capacity: 10,
          },
        ])
        return currentExitGates
      })
    }

    // 7. Pick Station (4b: PICK-R..C..)
    else if (activeTool === 'pick_station') {
      const pid = `PICK-R${rY}C${cX}`
      setPickStations((prev) => [...prev, { id: pid, name: pid, x, y, capacity: 4 }])
    }

    // 8. Entry Gate (4c: Dragging along edge creates ONE gate with many cells)
    else if (activeTool === 'entry_gate') {
      setEntryGates((prev) => {
        if (prev.length === 0) {
          return [{ id: 'IN-1', name: 'Inbound Dock IN-1', cells: [{ x, y }] }]
        }
        const targetGate = prev[0]
        if (targetGate.cells.some((c) => c.x === x && c.y === y)) return prev
        return [{ ...targetGate, cells: [...targetGate.cells, { x, y }] }, ...prev.slice(1)]
      })
    }

    // 9. Exit Gate (4c: Dragging along edge creates ONE gate with many cells)
    else if (activeTool === 'exit_gate') {
      setExitGates((prev) => {
        if (prev.length === 0) {
          return [{ id: 'OUT-1', name: 'Shipping Dock OUT-1', cells: [{ x, y }] }]
        }
        const targetGate = prev[0]
        if (targetGate.cells.some((c) => c.x === x && c.y === y)) return prev
        return [{ ...targetGate, cells: [...targetGate.cells, { x, y }] }, ...prev.slice(1)]
      })
    }
  }, [width, height, activeTool, selectedShelf])

  // ── Undo / Redo ────────────────────────────────────────────────────────────
  const handleUndo = useCallback(() => {
    if (historyIndex > 0) {
      const prev = history[historyIndex - 1]
      setHistoryIndex((idx) => idx - 1)
      loadMapState(prev)
    }
  }, [history, historyIndex])

  const handleRedo = useCallback(() => {
    if (historyIndex < history.length - 1) {
      const next = history[historyIndex + 1]
      setHistoryIndex((idx) => idx + 1)
      loadMapState(next)
    }
  }, [history, historyIndex])

  const loadMapState = (map: WarehouseMap) => {
    setWidth(map.grid.width)
    setHeight(map.grid.height)
    setMapName(map.name)
    setBlockedCells(map.blocked_cells || [])
    setShelves(
      (map.shelves || []).map((s) => ({
        ...s,
        stock: s.stock || getAutoStockForCell(s.x, s.y),
      }))
    )
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
      setSelectedShelf(null)
    }
  }

  // 4g Grid Resize with Confirmation
  const handleResizeGrid = (newW: number, newH: number) => {
    const clampedW = Math.max(8, Math.min(100, newW))
    const clampedH = Math.max(8, Math.min(100, newH))
    if (clampedW === width && clampedH === height) return

    if (clampedW < width || clampedH < height) {
      const cutCount =
        blockedCells.filter((c) => c.x >= clampedW || c.y >= clampedH).length +
        shelves.filter((s) => s.x >= clampedW || s.y >= clampedH).length +
        chargers.filter((c) => c.x >= clampedW || c.y >= clampedH).length +
        robotStarts.filter((r) => r.x >= clampedW || r.y >= clampedH).length +
        sortingStations.filter((s) => s.x >= clampedW || s.y >= clampedH).length +
        pickStations.filter((p) => p.x >= clampedW || p.y >= clampedH).length

      if (cutCount > 0) {
        if (!window.confirm(`Shrinking the grid to ${clampedW}x${clampedH} will delete ${cutCount} entities outside the new bounds. Continue?`)) {
          return
        }
      }
    }

    recordHistory()
    setWidth(clampedW)
    setHeight(clampedH)
    setBlockedCells((prev) => prev.filter((c) => c.x < clampedW && c.y < clampedH))
    setShelves((prev) => prev.filter((s) => s.x < clampedW && s.y < clampedH))
    setChargers((prev) => prev.filter((c) => c.x < clampedW && c.y < clampedH))
    setRobotStarts((prev) => prev.filter((r) => r.x < clampedW && r.y < clampedH))
    setSortingStations((prev) => prev.filter((s) => s.x < clampedW && s.y < clampedH))
    setPickStations((prev) => prev.filter((p) => p.x < clampedW && p.y < clampedH))
  }

  // 4g Load Built-in as Template
  const handleLoadTemplate = async () => {
    try {
      const template = await api.mapTemplate()
      recordHistory()
      loadMapState(template)
    } catch (err) {
      alert(`Failed to load template map: ${err instanceof Error ? err.message : 'Unknown error'}`)
    }
  }

  // 4g Save Named Map to Backend maps/
  const handleSaveNamedMap = async () => {
    try {
      const current = getCurrentMapObject()
      const res = await api.mapSave(mapName, current)
      setSaveStatus(`Saved as '${res.filename}'`)
      setTimeout(() => setSaveStatus(null), 3500)
    } catch (err) {
      alert(`Failed to save map: ${err instanceof Error ? err.message : 'Unknown error'}`)
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

  // Import JSON with duplicate ID normalization (4b)
  const handleImportFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = async (event) => {
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
  const bgColor = isDark ? '#0f1117' : '#F5F5F5'
  const panelBg = isDark ? '#161922' : '#FFFFFF'
  const borderColor = isDark ? '#262c3a' : '#E0E0E0'
  const textColor = isDark ? '#E2E8F0' : '#1F2937'
  const mutedText = isDark ? '#8892b0' : '#6B7280'

  // Lookups for fast grid cell rendering
  const blockedMap = useMemo(() => new Set(blockedCells.map((c) => `${c.x},${c.y}`)), [blockedCells])
  const shelfMap = useMemo(() => new Map(shelves.map((s) => [`${s.x},${s.y}`, s])), [shelves])
  const chargerMap = useMemo(() => new Map(chargers.map((c) => [`${c.x},${c.y}`, c])), [chargers])
  const robotMap = useMemo(() => new Map(robotStarts.map((r) => [`${r.x},${r.y}`, r])), [robotStarts])
  const chuteMap = useMemo(() => new Map(sortingStations.map((s) => [`${s.x},${s.y}`, s])), [sortingStations])
  const pickMap = useMemo(() => new Map(pickStations.map((p) => [`${p.x},${p.y}`, p])), [pickStations])
  const gateMap = useMemo(() => {
    const map = new Map<string, { type: 'entry' | 'exit'; id: string }>()
    entryGates.forEach((g) => g.cells.forEach((c) => map.set(`${c.x},${c.y}`, { type: 'entry', id: g.id })))
    exitGates.forEach((g) => g.cells.forEach((c) => map.set(`${c.x},${c.y}`, { type: 'exit', id: g.id })))
    return map
  }, [entryGates, exitGates])

  // 4f Group Validation Items by Root Cause
  const groupedErrors = useMemo(() => {
    const groups: { [key: string]: { message: string; count: number; cell?: Point } } = {}
    validation.errors.forEach((err) => {
      // Extract coordinate if present e.g. at (5, 6) or cell (5, 6)
      const coordMatch = err.match(/\((\d+),\s*(\d+)\)/)
      const cell = coordMatch ? { x: parseInt(coordMatch[1]), y: parseInt(coordMatch[2]) } : undefined
      // Normalize message key
      const key = err.replace(/\(.*\)/, '(*)').replace(/'[^']+'/, "'...'")
      if (!groups[key]) {
        groups[key] = { message: err, count: 1, cell }
      } else {
        groups[key].count += 1
      }
    })
    return Object.values(groups)
  }, [validation.errors])

  const groupedWarnings = useMemo(() => {
    const groups: { [key: string]: { message: string; count: number } } = {}
    validation.warnings.forEach((warn) => {
      const key = warn.replace(/\(.*\)/, '(*)').replace(/'[^']+'/, "'...'")
      if (!groups[key]) {
        groups[key] = { message: warn, count: 1 }
      } else {
        groups[key].count += 1
      }
    })
    return Object.values(groups)
  }, [validation.warnings])

  // Primary Blocker Reason (4f)
  const primaryBlocker = useMemo(() => {
    if (validation.valid) return null
    if (validation.errors.length === 0) return null
    const first = validation.errors[0]
    return first
  }, [validation])

  // Cell Click / Mouse Interaction
  const handleCellMouseDown = (x: number, y: number, e: React.MouseEvent) => {
    if (e.button === 1 || e.altKey) {
      // Middle click or Alt+click starts panning
      setIsPanning(true)
      panStartRef.current = { x: e.clientX - pan.x, y: e.clientY - pan.y }
      return
    }
    setIsMouseDown(true)
    paintedThisDragRef.current.clear()
    recordHistory()
    applyToolToCell(x, y)

    // If shelf, select it for stock inspection
    const shelf = shelfMap.get(`${x},${y}`)
    if (shelf) {
      setSelectedShelf({ id: shelf.id, x: shelf.x, y: shelf.y, stock: shelf.stock || {} })
    }
  }

  const handleCellMouseEnter = (x: number, y: number) => {
    setHoveredCell({ x, y })
    if (isMouseDown && !isPanning) {
      applyToolToCell(x, y)
    }
  }

  const handleContainerMouseMove = (e: React.MouseEvent) => {
    if (isPanning) {
      setPan({
        x: e.clientX - panStartRef.current.x,
        y: e.clientY - panStartRef.current.y,
      })
    }
  }

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
        overflow: 'hidden',
      }}
      onMouseUp={() => {
        setIsMouseDown(false)
        setIsPanning(false)
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
          zIndex: 10,
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
          {saveStatus && (
            <span style={{ fontSize: 12, color: '#10B981', fontWeight: 600 }}>{saveStatus}</span>
          )}
        </div>

        {/* Action Controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <button
            onClick={handleUndo}
            disabled={historyIndex <= 0}
            title="Undo (Ctrl+Z)"
            style={{ padding: '6px 10px', background: isDark ? '#222736' : '#E5E5E5', border: 'none', borderRadius: 6, color: textColor, cursor: 'pointer', opacity: historyIndex <= 0 ? 0.4 : 1 }}
          >
            <Undo2 size={16} />
          </button>
          <button
            onClick={handleRedo}
            disabled={historyIndex >= history.length - 1}
            title="Redo (Ctrl+Y)"
            style={{ padding: '6px 10px', background: isDark ? '#222736' : '#E5E5E5', border: 'none', borderRadius: 6, color: textColor, cursor: 'pointer', opacity: historyIndex >= history.length - 1 ? 0.4 : 1 }}
          >
            <Redo2 size={16} />
          </button>
          <button
            onClick={handleClear}
            title="Clear all entities"
            style={{ padding: '6px 10px', background: '#DC262622', border: '1px solid #DC262655', borderRadius: 6, color: '#EF4444', cursor: 'pointer' }}
          >
            <Trash2 size={16} />
          </button>

          <div style={{ width: 1, height: 20, backgroundColor: borderColor }} />

          <button
            onClick={handleLoadTemplate}
            title="Load built-in map as editable template"
            style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', background: isDark ? '#222736' : '#E5E5E5', border: 'none', borderRadius: 6, color: textColor, cursor: 'pointer', fontSize: 13 }}
          >
            <FileCode size={14} color="#60A5FA" /> Load Template
          </button>

          <button
            onClick={handleSaveNamedMap}
            title="Save map into backend maps/ directory"
            style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', background: isDark ? '#222736' : '#E5E5E5', border: 'none', borderRadius: 6, color: textColor, cursor: 'pointer', fontSize: 13 }}
          >
            <Save size={14} color="#34D399" /> Save Map
          </button>

          <input ref={fileInputRef} type="file" accept=".json" style={{ display: 'none' }} onChange={handleImportFile} />
          <button
            onClick={() => fileInputRef.current?.click()}
            style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', background: isDark ? '#222736' : '#E5E5E5', border: 'none', borderRadius: 6, color: textColor, cursor: 'pointer', fontSize: 13 }}
          >
            <Upload size={14} /> Import JSON
          </button>
          <button
            onClick={handleExportJSON}
            style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', background: isDark ? '#222736' : '#E5E5E5', border: 'none', borderRadius: 6, color: textColor, cursor: 'pointer', fontSize: 13 }}
          >
            <Download size={14} /> Export JSON
          </button>

          <div style={{ width: 1, height: 20, backgroundColor: borderColor }} />

          {/* Launch Button */}
          <button
            id="btn-editor-launch"
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
              title="Close Editor"
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
            width: 240,
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
              { id: 'shelf', label: 'Storage Shelf', hotkey: 'S', icon: Boxes, color: '#3B82F6', badge: 'POD' },
              { id: 'wall', label: 'Wall / Obstacle', hotkey: 'W', icon: Square, color: '#6B7280', badge: 'WALL' },
              { id: 'charger', label: 'Charging Pad', hotkey: 'C', icon: BatteryCharging, color: '#16A34A', badge: 'CHG' },
              { id: 'g2p_robot', label: 'G2P Robot', hotkey: 'G', icon: Bot, color: '#F59E0B', badge: 'G2P' },
              { id: 'sorting_robot', label: 'Sorting Robot', hotkey: 'O', icon: Bot, color: '#06B6D4', badge: 'SORT' },
              { id: 'audit_robot', label: 'Audit Robot', hotkey: 'A', icon: Bot, color: '#A855F7', badge: 'AUDIT' },
              { id: 'sorting_station', label: 'Sorting Chute', hotkey: 'T', icon: Layers, color: '#EC4899', badge: 'CHUTE' },
              { id: 'pick_station', label: 'Pick Station', hotkey: 'P', icon: Target, color: '#8B5CF6', badge: 'PICK' },
              { id: 'entry_gate', label: 'Inbound Dock', hotkey: 'I', icon: LogIn, color: '#FF6B35', badge: 'IN' },
              { id: 'exit_gate', label: 'Shipping Dock', hotkey: 'X', icon: LogOut, color: '#EAB308', badge: 'OUT' },
              { id: 'eraser', label: 'Erase Tile', hotkey: 'E', icon: Eraser, color: '#EF4444', badge: 'DEL' },
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
                    justifyContent: 'space-between',
                    padding: '8px 12px',
                    borderRadius: 6,
                    border: `1px solid ${isActive ? tool.color : 'transparent'}`,
                    backgroundColor: isActive ? `${tool.color}1E` : 'transparent',
                    color: isActive ? tool.color : textColor,
                    cursor: 'pointer',
                    fontSize: 13,
                    fontWeight: isActive ? 600 : 400,
                    textAlign: 'left',
                    transition: 'all 0.15s ease',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <Icon size={16} color={tool.color} />
                    <span>{tool.label}</span>
                  </div>
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 700,
                      padding: '2px 5px',
                      borderRadius: 4,
                      backgroundColor: `${tool.color}22`,
                      color: tool.color,
                    }}
                  >
                    {tool.hotkey}
                  </span>
                </button>
              )
            })}
          </div>

          {/* Grid Dimensions Setting (4g: With confirmation) */}
          <div style={{ marginTop: 'auto', paddingTop: 16, borderTop: `1px solid ${borderColor}`, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: mutedText }}>Grid Dimensions</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <label style={{ fontSize: 12, color: mutedText }}>W:</label>
              <input
                type="number"
                min={8}
                max={100}
                value={width}
                onChange={(e) => handleResizeGrid(parseInt(e.target.value) || width, height)}
                style={{ width: 60, padding: '4px 6px', background: isDark ? '#222736' : '#E5E5E5', border: 'none', borderRadius: 4, color: textColor }}
              />
              <label style={{ fontSize: 12, color: mutedText }}>H:</label>
              <input
                type="number"
                min={8}
                max={100}
                value={height}
                onChange={(e) => handleResizeGrid(width, parseInt(e.target.value) || height)}
                style={{ width: 60, padding: '4px 6px', background: isDark ? '#222736' : '#E5E5E5', border: 'none', borderRadius: 4, color: textColor }}
              />
            </div>

            {/* Zoom & Pan Controls */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8 }}>
              <span style={{ fontSize: 11, color: mutedText }}>Zoom:</span>
              <button
                onClick={() => setZoom((z) => Math.max(12, z - 2))}
                style={{ padding: '3px 8px', background: isDark ? '#222736' : '#E5E5E5', border: 'none', borderRadius: 4, color: textColor, cursor: 'pointer' }}
              >
                <Minus size={12} />
              </button>
              <span style={{ fontSize: 11, minWidth: 28, textAlign: 'center' }}>{zoom}px</span>
              <button
                onClick={() => setZoom((z) => Math.min(48, z + 2))}
                style={{ padding: '3px 8px', background: isDark ? '#222736' : '#E5E5E5', border: 'none', borderRadius: 4, color: textColor, cursor: 'pointer' }}
              >
                <Plus size={12} />
              </button>
              <button
                onClick={() => { setZoom(22); setPan({ x: 0, y: 0 }) }}
                title="Reset Zoom & Pan"
                style={{ marginLeft: 'auto', padding: '3px 6px', fontSize: 10, background: isDark ? '#222736' : '#E5E5E5', border: 'none', borderRadius: 4, color: mutedText, cursor: 'pointer' }}
              >
                Reset
              </button>
            </div>
          </div>
        </div>

        {/* Center Interactive Canvas with Pan & Zoom */}
        <div
          style={{
            flex: 1,
            overflow: 'hidden',
            position: 'relative',
            backgroundColor: bgColor,
            userSelect: 'none',
            cursor: isPanning ? 'grabbing' : 'default',
          }}
          onMouseMove={handleContainerMouseMove}
          onWheel={(e) => {
            if (e.ctrlKey || e.metaKey) {
              e.preventDefault()
              setZoom((z) => (e.deltaY < 0 ? Math.min(48, z + 2) : Math.max(12, z - 2)))
            }
          }}
        >
          <div
            style={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              transform: `translate(${pan.x}px, ${pan.y}px)`,
              transition: isPanning ? 'none' : 'transform 0.05s ease-out',
            }}
          >
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: `repeat(${width}, ${zoom}px)`,
                gridTemplateRows: `repeat(${height}, ${zoom}px)`,
                gap: 1,
                backgroundColor: isDark ? '#1a1e29' : '#D1D5DB',
                padding: 4,
                borderRadius: 6,
                boxShadow: '0 12px 40px rgba(0,0,0,0.6)',
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
                  const isHighlighted = highlightedCell && highlightedCell.x === x && highlightedCell.y === y
                  const isSelected = selectedShelf && selectedShelf.x === x && selectedShelf.y === y

                  let cellBg = isDark ? '#12151e' : '#FFFFFF'
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
                    if (robot.type === 'GOODS_TO_PERSON') {
                      cellBg = '#854D0E'
                      cellContent = <Bot size={zoom * 0.65} color="#FDE047" />
                    } else if (robot.type === 'SORTING') {
                      cellBg = '#164E63'
                      cellContent = <Bot size={zoom * 0.65} color="#67E8F9" />
                    } else {
                      cellBg = '#581C87'
                      cellContent = <Bot size={zoom * 0.65} color="#D8B4FE" />
                    }
                  } else if (chute) {
                    cellBg = '#831843'
                    cellContent = <Layers size={zoom * 0.65} color="#F472B6" />
                  } else if (pick) {
                    cellBg = '#4C1D95'
                    cellContent = <Target size={zoom * 0.65} color="#C4B5FD" />
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
                      onMouseDown={(e) => handleCellMouseDown(x, y, e)}
                      onMouseEnter={() => handleCellMouseEnter(x, y)}
                      style={{
                        width: zoom,
                        height: zoom,
                        backgroundColor: cellBg,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        cursor: activeTool === 'eraser' ? 'crosshair' : 'pointer',
                        outline: isHighlighted
                          ? '2px solid #F59E0B'
                          : isSelected
                          ? '2px solid #38BDF8'
                          : 'none',
                        zIndex: isHighlighted || isSelected ? 5 : 1,
                        position: 'relative',
                        transition: 'background-color 0.05s ease',
                      }}
                      title={shelf ? `${shelf.id} (${Object.keys(shelf.stock || {}).length} SKUs)` : `(${x}, ${y})`}
                    >
                      {cellContent}
                    </div>
                  )
                })
              )}
            </div>
          </div>
        </div>

        {/* Right Validation, Shelf Inspector & Diagnostics Sidebar */}
        <div
          style={{
            width: 320,
            backgroundColor: panelBg,
            borderLeft: `1px solid ${borderColor}`,
            display: 'flex',
            flexDirection: 'column',
            padding: 16,
            gap: 16,
            overflowY: 'auto',
          }}
        >
          {/* Validation Header */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: 11, fontWeight: 700, color: mutedText, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Validation Status
            </span>
            {isValidating ? (
              <RefreshCw size={14} className="animate-spin" color="#FF6B35" />
            ) : validation.valid ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 4, color: '#16A34A', fontSize: 12, fontWeight: 600 }}>
                <CheckCircle2 size={16} /> Ready to Launch
              </div>
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', gap: 4, color: '#EF4444', fontSize: 12, fontWeight: 600 }}>
                <XCircle size={16} /> Launch Blocked
              </div>
            )}
          </div>

          {/* 4f: Plain-Language Blocker Card */}
          {primaryBlocker && (
            <div
              style={{
                backgroundColor: '#DC262615',
                border: '1px solid #DC262644',
                borderRadius: 6,
                padding: '10px 12px',
                display: 'flex',
                gap: 8,
                alignItems: 'flex-start',
              }}
            >
              <AlertTriangle size={16} color="#EF4444" style={{ flexShrink: 0, marginTop: 2 }} />
              <div>
                <div style={{ fontSize: 11, fontWeight: 700, color: '#EF4444', textTransform: 'uppercase' }}>
                  Why Launch is Disabled
                </div>
                <div style={{ fontSize: 12, color: '#FCA5A5', marginTop: 2, lineHeight: 1.4 }}>
                  {primaryBlocker}
                </div>
              </div>
            </div>
          )}

          {/* 4f: Errors List Grouped by Root Cause */}
          {groupedErrors.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: '#EF4444' }}>
                Errors ({validation.errors.length})
              </div>
              {groupedErrors.map((group, i) => (
                <div
                  key={i}
                  onClick={() => {
                    if (group.cell) {
                      setHighlightedCell(group.cell)
                      setTimeout(() => setHighlightedCell(null), 3000)
                    }
                  }}
                  style={{
                    fontSize: 12,
                    padding: '8px 10px',
                    borderRadius: 6,
                    backgroundColor: '#DC262610',
                    border: '1px solid #DC262625',
                    color: '#F87171',
                    lineHeight: 1.4,
                    cursor: group.cell ? 'pointer' : 'default',
                  }}
                  title={group.cell ? `Click to jump to (${group.cell.x}, ${group.cell.y})` : undefined}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span>{group.message}</span>
                    {group.count > 1 && (
                      <span style={{ fontSize: 10, fontWeight: 700, background: '#DC262633', padding: '1px 5px', borderRadius: 4 }}>
                        ×{group.count}
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* 4f: Warnings List Grouped by Cause */}
          {groupedWarnings.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: '#F59E0B' }}>
                Warnings ({validation.warnings.length})
              </div>
              {groupedWarnings.map((group, i) => (
                <div
                  key={i}
                  style={{
                    fontSize: 12,
                    padding: '8px 10px',
                    borderRadius: 6,
                    backgroundColor: '#F59E0B10',
                    border: '1px solid #F59E0B25',
                    color: '#FBBF24',
                    lineHeight: 1.4,
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span>{group.message}</span>
                    {group.count > 1 && (
                      <span style={{ fontSize: 10, fontWeight: 700, background: '#F59E0B33', padding: '1px 5px', borderRadius: 4 }}>
                        ×{group.count}
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* 4e: Shelf Stock Inspector (when shelf is selected) */}
          {selectedShelf && (
            <div
              style={{
                backgroundColor: isDark ? '#1a202c' : '#F3F4F6',
                border: `1px solid ${borderColor}`,
                borderRadius: 6,
                padding: 12,
                display: 'flex',
                flexDirection: 'column',
                gap: 8,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span style={{ fontSize: 12, fontWeight: 700, color: '#38BDF8' }}>
                  Shelf {selectedShelf.id}
                </span>
                <button
                  onClick={() => setSelectedShelf(null)}
                  style={{ background: 'transparent', border: 'none', color: mutedText, cursor: 'pointer' }}
                >
                  <X size={14} />
                </button>
              </div>
              <span style={{ fontSize: 11, color: mutedText }}>Location: ({selectedShelf.x}, {selectedShelf.y})</span>
              <div style={{ fontSize: 11, fontWeight: 700, color: textColor, marginTop: 4 }}>Stock Manifest:</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {Object.entries(selectedShelf.stock).map(([sku, qty]) => (
                  <div
                    key={sku}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      fontSize: 11,
                      padding: '4px 6px',
                      background: isDark ? '#12151e' : '#FFFFFF',
                      borderRadius: 4,
                    }}
                  >
                    <span>{sku}</span>
                    <input
                      type="number"
                      min={0}
                      max={80}
                      value={qty}
                      onChange={(e) => {
                        const val = parseInt(e.target.value) || 0
                        setSelectedShelf((prev) => prev ? { ...prev, stock: { ...prev.stock, [sku]: val } } : null)
                        setShelves((prev) =>
                          prev.map((s) =>
                            s.id === selectedShelf.id ? { ...s, stock: { ...(s.stock || {}), [sku]: val } } : s
                          )
                        )
                      }}
                      style={{
                        width: 48,
                        padding: '2px 4px',
                        fontSize: 11,
                        background: isDark ? '#262c3a' : '#E5E5E5',
                        border: 'none',
                        borderRadius: 3,
                        color: textColor,
                        textAlign: 'right',
                      }}
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Summary Stats */}
          <div style={{ marginTop: 'auto', borderTop: `1px solid ${borderColor}`, paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12, color: mutedText }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>Robots:</span>
              <b style={{ color: textColor }}>
                {robotStarts.length} (G2P: {robotStarts.filter((r) => r.type === 'GOODS_TO_PERSON').length}, Sort: {robotStarts.filter((r) => r.type === 'SORTING').length}, Audit: {robotStarts.filter((r) => r.type === 'SCANNING_AUDIT').length})
              </b>
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
