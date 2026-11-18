import { acres, pct, titleCase } from "./format.js";

// Hover tooltip for hexagons and parcels on the map.
export default function Tooltip({ hover }) {
  if (!hover) return null;
  const style = { left: hover.x + 14, top: hover.y + 14 };
  if (hover.parcel) {
    const p = hover.parcel;
    return (
      <div className="tooltip" style={style}>
        <div className="tt-title">{p.address || p.PIN}</div>
        <div>{p.LUC_DESC}</div>
        <div className="muted">
          {acres(p.acres)} · zone {p.zone_map || "–"} · est. FAR {p.est_far?.toFixed(2)}
        </div>
        {p.record_check && <div className="badge warn">⚠ tax record vs footprints disagree</div>}
      </div>
    );
  }
  const p = hover.p;
  return (
    <div className="tooltip" style={style}>
      <div className="tt-title">
        {!(p.cand_land_m2 > 0) ? "No candidate land" : p.filtered ? "Filtered out by your settings" : `Score ${p.score.toFixed(1)} · #${p.rank}`}
      </div>
      <div className="muted">
        {p.cand_parcels} candidate parcels · {acres(p.cand_land_m2 / 4046.86)} buildable
      </div>
      <div className="muted">
        imagery parking {pct(p.imagery_parking_share)} · {Math.round(p.metro_m)} m to {titleCase(p.metro_station)}
      </div>
      {p.needs_review && <div className="badge warn">⚠ needs analyst review</div>}
    </div>
  );
}
