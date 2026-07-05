import React from 'react';
import { type FnoViewState } from './viewModel';
import { NA, fmt, fmtStr, Row, Card, BiasBadge } from './FnoSidebarPrimitives';

interface FnoMetricsHudProps {
  viewState: FnoViewState & { kind: 'ready' | 'partial' };
}

export default function FnoMetricsHud({ viewState }: FnoMetricsHudProps) {
  const { hud } = viewState;

  return (
    <>
      {/* Agent Bias */}
      <div className="flex items-center justify-between px-3 py-2 bg-surface">
        <div className="flex flex-col gap-0.5">
          <span className="text-[8px] font-bold uppercase tracking-widest text-text-muted">Options Bias</span>
          <span className="text-[9px] text-text-secondary">
            {hud.context.underlying} · {hud.context.expiry || 'Nearest'}
          </span>
        </div>
        <BiasBadge state={hud.biasState} />
      </div>

      {/* Key Metrics HUD */}
      <div className="flex flex-col gap-2 p-2 bg-surface/10">
        <Card title="Options Analytics">
          <Row label="PCR (OI)">{fmt(hud.pcrOi)}</Row>
          <Row label="PCR (Volume)">{fmt(hud.pcrVolume)}</Row>
          <Row label="Max Pain">
            {hud.maxPain !== null ? (
              <span className="font-mono font-bold text-amber-400">
                ₹{hud.maxPain.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
              </span>
            ) : (
              <NA />
            )}
          </Row>
          <Row label="Futures Basis">{fmt(hud.futuresBasis)}</Row>
        </Card>

        <Card title="OI Walls">
          <Row label="Support">
            {hud.walls.support !== null ? (
              <span className="font-mono font-bold text-emerald-400">
                ₹{hud.walls.support.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
              </span>
            ) : (
              <NA />
            )}
          </Row>
          <Row label="Resistance">
            {hud.walls.resistance !== null ? (
              <span className="font-mono font-bold text-rose-400">
                ₹{hud.walls.resistance.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
              </span>
            ) : (
              <NA />
            )}
          </Row>
        </Card>

        <Card title="Aggregate OI Bias">
          <Row label="Call Buildup">{fmtStr(hud.aggregateOiBias.call)}</Row>
          <Row label="Put Buildup">{fmtStr(hud.aggregateOiBias.put)}</Row>
        </Card>

        <Card title="IV Skew">
          {hud.ivSkew === null ? (
            <div className="px-3 py-2">
              <NA />
            </div>
          ) : (
            <>
              <Row label="Put − Call">{fmt(hud.ivSkew.putMinusCall)}</Row>
              <Row label="Slope">{fmt(hud.ivSkew.slope, 4)}</Row>
              <Row label="ATM IV">{fmt(hud.ivSkew.atmIv)}</Row>
            </>
          )}
        </Card>

        {/* Driving signals */}
        {hud.biasSignals !== null && Object.keys(hud.biasSignals).length > 0 && (
          <Card title="Driving Signals">
            {Object.entries(hud.biasSignals).map(([key, value]) => (
              <Row key={key} label={key.replace(/_/g, ' ')}>
                <span className="font-mono text-text-primary text-right">
                  {typeof value === 'number'
                    ? Number.isFinite(value)
                      ? value.toLocaleString(undefined, { maximumFractionDigits: 4 })
                      : '—'
                    : String(value ?? '—')}
                </span>
              </Row>
            ))}
          </Card>
        )}
      </div>
    </>
  );
}
