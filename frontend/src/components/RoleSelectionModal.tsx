import { ArrowDownToLine, ArrowUpFromLine, ShieldCheck, Radio, Server, CheckCircle2 } from 'lucide-react'
import type { FC } from 'react'

export type OperatorRole = 'IMPORT' | 'EXPORT' | 'AUTHORITY'

interface RoleSelectionModalProps {
  currentRole: OperatorRole | null
  onSelectRole: (role: OperatorRole) => void
  isOpen: boolean
  onClose?: () => void
}

export const RoleSelectionModal: FC<RoleSelectionModalProps> = ({
  currentRole,
  onSelectRole,
  isOpen,
  onClose,
}) => {
  if (!isOpen) return null

  return (
    <div className="role-modal-overlay">
      <div className="role-modal-container">
        <div className="role-modal-header">
          <div className="role-modal-badge">
            <Radio size={13} />
            <span>Decentralized Fixed-Station Terminal</span>
          </div>
          <h1>Select Operator Command Scope</h1>
          <p>
            Choose your station terminal. AMRs enforce zero-trust cryptographic authorization at the receiving end
            via HMAC-SHA256 allowlists. Full 3D simulation remains live across all roles.
          </p>
        </div>

        <div className="role-cards-grid">
          {/* Card 1: Import Station */}
          <div
            className={`role-card import-card ${currentRole === 'IMPORT' ? 'active-card' : ''}`}
            onClick={() => onSelectRole('IMPORT')}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => e.key === 'Enter' && onSelectRole('IMPORT')}
          >
            <div className="role-card-top">
              <div className="role-icon-box import-icon-box">
                <ArrowDownToLine size={22} />
              </div>
              <div className="role-card-pill import-pill">PORT 9601 • IN-1..3</div>
            </div>

            <div className="role-card-body">
              <h3>Import Station Alpha</h3>
              <span className="role-zone-tag">West Inbound Receiving Gates</span>
              <p>
                Command scope limited strictly to import dock batch induction, SKU unloads, and West staging AMRs.
              </p>

              <div className="role-specs">
                <div className="role-spec-row">
                  <span className="spec-label">Authorized Tasks:</span>
                  <span className="spec-value authorized">INDUCT_BATCH, IN-1..3</span>
                </div>
                <div className="role-spec-row">
                  <span className="spec-label">Prohibited Scope:</span>
                  <span className="spec-value prohibited">Export & Sortation (Blocked by AMR)</span>
                </div>
                <div className="role-spec-row">
                  <span className="spec-label">HaLow Uplink:</span>
                  <span className="spec-value">150 kbps Tagged Channel</span>
                </div>
              </div>
            </div>

            <button type="button" className="role-select-btn import-btn">
              {currentRole === 'IMPORT' ? (
                <>
                  <CheckCircle2 size={15} /> Active Console
                </>
              ) : (
                'Launch Import Console'
              )}
            </button>
          </div>

          {/* Card 2: Export Station */}
          <div
            className={`role-card export-card ${currentRole === 'EXPORT' ? 'active-card' : ''}`}
            onClick={() => onSelectRole('EXPORT')}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => e.key === 'Enter' && onSelectRole('EXPORT')}
          >
            <div className="role-card-top">
              <div className="role-icon-box export-icon-box">
                <ArrowUpFromLine size={22} />
              </div>
              <div className="role-card-pill export-pill">PORT 9602 • OUT-1..3</div>
            </div>

            <div className="role-card-body">
              <h3>Export Station Omega</h3>
              <span className="role-zone-tag">East Outbound Gates & Chutes</span>
              <p>
                Command scope limited strictly to consolidation missions, sortation decanting, and East gates.
              </p>

              <div className="role-specs">
                <div className="role-spec-row">
                  <span className="spec-label">Authorized Tasks:</span>
                  <span className="spec-value export-tasks">CONSOLIDATE_EXPORT, CHUTE-01..08</span>
                </div>
                <div className="role-spec-row">
                  <span className="spec-label">Prohibited Scope:</span>
                  <span className="spec-value prohibited">Import Dock (Blocked by AMR)</span>
                </div>
                <div className="role-spec-row">
                  <span className="spec-label">HaLow Uplink:</span>
                  <span className="spec-value">150 kbps Tagged Channel</span>
                </div>
              </div>
            </div>

            <button type="button" className="role-select-btn export-btn">
              {currentRole === 'EXPORT' ? (
                <>
                  <CheckCircle2 size={15} /> Active Console
                </>
              ) : (
                'Launch Export Console'
              )}
            </button>
          </div>

          {/* Card 3: Central Authority */}
          <div
            className={`role-card authority-card ${currentRole === 'AUTHORITY' ? 'active-card' : ''}`}
            onClick={() => onSelectRole('AUTHORITY')}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => e.key === 'Enter' && onSelectRole('AUTHORITY')}
          >
            <div className="role-card-top">
              <div className="role-icon-box authority-icon-box">
                <ShieldCheck size={22} />
              </div>
              <div className="role-card-pill authority-pill">PORT 9603 • FULL SCOPE</div>
            </div>

            <div className="role-card-body">
              <h3>Central Authority Station</h3>
              <span className="role-zone-tag">Neutral Central Corridor (Row 14)</span>
              <p>
                Single point of authority for unresolvable fault escalations and master emergency overrides.
              </p>

              <div className="role-specs">
                <div className="role-spec-row">
                  <span className="spec-label">Command Scope:</span>
                  <span className="spec-value authority-scope">Unrestricted Across All AMRs</span>
                </div>
                <div className="role-spec-row">
                  <span className="spec-label">Architecture:</span>
                  <span className="spec-value spec-meta">Out of Critical Path (AMRs survive failure)</span>
                </div>
                <div className="role-spec-row">
                  <span className="spec-label">Authority:</span>
                  <span className="spec-value spec-meta">Global Supervisory Override</span>
                </div>
              </div>
            </div>

            <button type="button" className="role-select-btn authority-btn">
              {currentRole === 'AUTHORITY' ? (
                <>
                  <CheckCircle2 size={15} /> Active Console
                </>
              ) : (
                'Launch Full Control'
              )}
            </button>
          </div>
        </div>

        <div className="role-modal-footer">
          <div className="role-footer-note">
            <Server size={14} />
            <span>
              Real-time peer-to-peer UDP mesh active across 10 AMRs and 3 Station Nodes. Zero central dispatch
              dependency.
            </span>
          </div>
          {currentRole && onClose && (
            <button type="button" className="role-dismiss-btn" onClick={onClose}>
              Continue with Current Role
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
