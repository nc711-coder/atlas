import { acres, meters, pct, SUBMARKET_LABEL, titleCase, usd } from "./format.js";

// Right-hand panel: the top-10 list, or the selected hexagon's facts and parcels.
export default function SidePanel({ top10, detail, selected, onSelect }) {
  if (!selected) {
    return (
      <aside className="panel">
        <h2>Top 10 opportunity zones</h2>
        <p className="muted small">
          Each hexagon (~0.1 km²) is scored on underused land, surface parking seen in 2025 aerial imagery, Metro
          access, land worth more than its buildings, rezoning activity and land available. Click one on the map or below.
        </p>
        <ol className="toplist">
          {top10.map((f) => {
            const p = f.properties;
            return (
              <li key={p.hex}>
                <button onClick={() => onSelect(p.hex)}>
                  <span className="rank">#{p.rank}</span>
                  <span className="grow">
                    {SUBMARKET_LABEL[p.submarket]} · near {titleCase(p.metro_station)}
                    <span className="muted small block">
                      {acres(p.cand_land_m2 / 4046.86)} buildable · parking {pct(p.imagery_parking_share)}
                      {p.needs_review ? " · ⚠ review" : ""}
                    </span>
                  </span>
                  <span className="score">{p.score.toFixed(1)}</span>
                </button>
              </li>
            );
          })}
        </ol>
        <Footnote />
      </aside>
    );
  }

  if (!detail) {
    return (
      <aside className="panel">
        <button className="link" onClick={() => onSelect(null)}>← All zones</button>
        <p className="muted">Loading…</p>
      </aside>
    );
  }

  const p = detail.hex.properties;
  const parcels = [...detail.parcels.features].sort((a, b) => (b.properties.acres || 0) - (a.properties.acres || 0));
  return (
    <aside className="panel">
      <button className="link" onClick={() => onSelect(null)}>← All zones</button>
      <h2>
        {SUBMARKET_LABEL[p.submarket]} zone #{p.rank}
      </h2>
      <div className="score-big">
        {p.score.toFixed(1)} <span className="muted small">/ 100 opportunity score</span>
      </div>
      {p.needs_review && (
        <div className="badge warn block">
          ⚠ Needs analyst review: {p.imagery_lowconf_share > 0.4 ? "many low-confidence imagery calls" : "several tax records disagree with building footprints"}
        </div>
      )}

      <dl className="facts">
        <dt>Candidate land (buildable)</dt>
        <dd>{acres(p.cand_land_m2 / 4046.86)} in {p.cand_parcels} parcels</dd>
        <dt>Estimated floor-area ratio</dt>
        <dd>{p.est_far?.toFixed(2) ?? "–"}</dd>
        <dt>Surface parking (2025 imagery)</dt>
        <dd>{pct(p.imagery_parking_share)} of tiles</dd>
        <dt>Nearest Metro</dt>
        <dd>{titleCase(p.metro_station)}, {meters(p.metro_m)}</dd>
        <dt>Building worth &lt; half the land</dt>
        <dd>{pct(p.low_value_share)} of candidate land</dd>
        <dt>Rezoning activity (2015+)</dt>
        <dd>{p.recent_rezoning_parcels} {p.recent_rezoning_parcels === 1 ? "parcel" : "parcels"} in a case</dd>
        <dt>Footprints added in 2024 county update</dt>
        <dd>{p.footprints_2024_update}</dd>
        {p.protected_m2 > 0 && (
          <>
            <dt>Protected stream buffer excluded</dt>
            <dd>{acres(p.protected_m2 / 4046.86)}</dd>
          </>
        )}
      </dl>

      <h3>Candidate parcels ({parcels.length})</h3>
      <table className="parcels">
        <thead>
          <tr>
            <th>Parcel</th>
            <th>Size</th>
            <th>Est. FAR</th>
            <th>Land / bldg value</th>
          </tr>
        </thead>
        <tbody>
          {parcels.map(({ properties: q }) => (
            <tr key={q.PIN}>
              <td>
                {q.address || q.PIN}
                <span className="muted small block">{q.LUC_DESC} · {q.zone_map}</span>
                {q.record_check && <span className="badge warn small">⚠ check record</span>}
              </td>
              <td>{acres(q.acres)}</td>
              <td>{q.est_far?.toFixed(2)}</td>
              <td>
                {usd(q.APRLAND)} / {usd(q.APRBLDG)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <Footnote />
    </aside>
  );
}

function Footnote() {
  return (
    <p className="muted small footnote">
      Scores rank physical opportunity, not whether an owner will sell. Fictional client; real Fairfax County open data
      and USDA NAIP aerial imagery.
    </p>
  );
}
