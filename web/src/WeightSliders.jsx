import { DEFAULT_WEIGHTS, FACTOR_LABELS, FACTORS } from "./weights.js";

// Sliders for the six factor weights + a minimum land filter. Weights are relative (normalised by the API).
export default function WeightSliders({ weights, minAcres, onChange, onMinAcres, busy }) {
  const total = FACTORS.reduce((s, k) => s + weights[k], 0) || 1;
  const isDefault = FACTORS.every((k) => weights[k] === DEFAULT_WEIGHTS[k]) && minAcres === 0;
  return (
    <details className="weights" open>
      <summary>
        Adjust what matters {busy && <span className="muted small">· re-ranking…</span>}
      </summary>
      {FACTORS.map((k) => (
        <label key={k} className="slider-row">
          <span>{FACTOR_LABELS[k]}</span>
          <input type="range" min="0" max="50" step="1" value={weights[k]}
                 onChange={(e) => onChange({ ...weights, [k]: Number(e.target.value) })} />
          <span className="num">{Math.round((weights[k] / total) * 100)}%</span>
        </label>
      ))}
      <label className="slider-row">
        <span>Min. buildable land</span>
        <input type="range" min="0" max="10" step="0.5" value={minAcres} onChange={(e) => onMinAcres(Number(e.target.value))} />
        <span className="num">{minAcres} ac</span>
      </label>
      <button className="link" disabled={isDefault} onClick={() => { onChange(DEFAULT_WEIGHTS); onMinAcres(0); }}>
        Reset to defaults
      </button>
    </details>
  );
}
