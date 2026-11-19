import { useState } from "react";

// Swipe comparison of our own stitched NAIP imagery for one hexagon: 2018 (left of the handle) vs 2025.
export default function BeforeAfter({ hex }) {
  const [pos, setPos] = useState(50);
  const [missing, setMissing] = useState(false);
  if (missing) return null;
  return (
    <figure className="chart">
      <figcaption>Aerial imagery, same spot: drag the handle (USDA NAIP, processed by Atlas)</figcaption>
      <div className="swipe">
        <img src={`/imagery/2025/${hex}.jpg`} alt="2025 aerial photo of this zone" onError={() => setMissing(true)} />
        <img src={`/imagery/2018/${hex}.jpg`} alt="2018 aerial photo of this zone" className="top"
             style={{ clipPath: `inset(0 ${100 - pos}% 0 0)` }} onError={() => setMissing(true)} />
        <div className="swipe-line" style={{ left: `${pos}%` }} />
        {pos > 12 && <span className="swipe-tag left">◀ 2018 · Oct–Nov · 0.6 m</span>}
        {pos < 88 && <span className="swipe-tag right">2025 · Aug · 0.3 m ▶</span>}
        <input type="range" min="0" max="100" value={pos} onChange={(e) => setPos(Number(e.target.value))}
               aria-label="Compare 2018 and 2025" />
      </div>
      <p className="muted small">
        Showing: {pos >= 99 ? "all 2018" : pos <= 1 ? "all 2025" : `2018 on the left, 2025 on the right`}. Grey = outside
        the study area. Brightened for display; autumn 2018 vs summer 2025.
      </p>
    </figure>
  );
}
