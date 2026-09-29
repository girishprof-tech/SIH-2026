import { useEffect, useRef, useState } from 'react'
import { Activity, ArrowRight, Cpu, Radio } from 'lucide-react'
import { api } from '../api'

interface LoadingScreenProps {
  theme?: 'light' | 'dark'
  durationMs?: number
  onComplete?: () => void
}

const BOOT_STAGES = [
  { threshold: 0, title: 'Network Initialization', subtitle: 'Connecting to edge-mesh coordination layer...', code: 'NET_SYN' },
  { threshold: 20, title: 'Spatial Mapping', subtitle: 'Synchronizing 30×30 warehouse grid & charging bays...', code: 'GEO_SYNC' },
  { threshold: 44, title: 'Consensus Engine', subtitle: 'Verifying decentralized conflict resolution protocol...', code: 'P2P_MESH' },
  { threshold: 68, title: 'Digital Twin Calibration', subtitle: 'Validating 10 AMR kinematic states & reservations...', code: 'AMR_SYNC' },
  { threshold: 88, title: 'Telemetry Locked', subtitle: 'Activating live 2.5D visualizer & mission dispatcher...', code: 'SYS_READY' },
]

export function LoadingScreen({ theme = 'dark', durationMs = 5000, onComplete }: LoadingScreenProps) {
  const [progress, setProgress] = useState(0)
  const [fading, setFading] = useState(false)
  const onCompleteRef = useRef(onComplete)
  onCompleteRef.current = onComplete

  useEffect(() => {
    const startTime = Date.now()
    let completed = false
    let backendReady = false

    // Poll /health immediately and every 200ms
    const checkHealth = () => {
      api.health()
        .then(() => {
          backendReady = true
        })
        .catch(() => {
          /* Will keep polling */
        })
    }
    checkHealth()
    const healthInterval = window.setInterval(checkHealth, 200)

    const interval = window.setInterval(() => {
      const elapsed = Date.now() - startTime
      setProgress((prev) => {
        let next: number
        if (backendReady) {
          // Accelerate quickly to 100% once backend is ready
          next = Math.min(100, prev + 12)
        } else {
          // Progress smoothly up to 75% while awaiting /health
          const targetPct = Math.min(75, Math.round((elapsed / durationMs) * 75))
          next = Math.max(prev, targetPct)
        }

        // Cap at 100% if elapsed >= durationMs even if health didn't respond
        if (elapsed >= durationMs) {
          next = 100
        }

        if (next >= 100 && !completed) {
          completed = true
          window.clearInterval(interval)
          window.clearInterval(healthInterval)
          setFading(true)
          window.setTimeout(() => {
            onCompleteRef.current?.()
          }, 200)
        }
        return next
      })
    }, 35)

    return () => {
      window.clearInterval(interval)
      window.clearInterval(healthInterval)
    }
  }, [durationMs])

  const handleSkip = () => {
    setFading(true)
    window.setTimeout(() => {
      onCompleteRef.current?.()
    }, 150)
  }

  // Current active boot stage
  const currentStage = [...BOOT_STAGES].reverse().find((s) => progress >= s.threshold) || BOOT_STAGES[0]

  return (
    <div className={`loading-screen ${theme} ${fading ? 'fading-out' : ''}`}>
      <div className="loading-container">
        <div className="loading-video-frame">
          <video
            className="loading-video"
            src="/loading-robot.mp4"
            autoPlay
            muted
            loop
            playsInline
          />
        </div>

        <div className="loading-content-stack">
          <div className="loading-text-group">
            <div className="loading-brand-row">
              <span className="loading-badge">KINETIX FLEET OS</span>
              <span className="loading-code-badge">{currentStage.code}</span>
            </div>
            <h1 className="loading-title">Kinetix</h1>
            <p className="loading-stage-title">{currentStage.title}</p>
            <p className="loading-subtitle">{currentStage.subtitle}</p>
          </div>

          <div className="loading-meter-group">
            <div className="loading-bar-track">
              <div className="loading-bar-fill" style={{ width: `${progress}%` }} />
            </div>
            <div className="loading-progress-meta">
              <span className="loading-status-text">SYSTEM BOOT SEQUENCE</span>
              <span className="loading-pct">{progress}%</span>
            </div>
          </div>

          <div className="loading-telemetry-hud">
            <div className="hud-metric">
              <Radio size={12} />
              <span>MESH: 10/10 NODES</span>
            </div>
            <div className="hud-metric">
              <Cpu size={12} />
              <span>DISPATCH: P2P</span>
            </div>
            <div className="hud-metric">
              <Activity size={12} />
              <span>TICK: 500MS</span>
            </div>
          </div>

          <button type="button" className="loading-skip-btn" onClick={handleSkip}>
            <span>Enter console</span>
            <ArrowRight size={13} />
          </button>
        </div>
      </div>
    </div>
  )
}
