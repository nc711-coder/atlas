import { useEffect, useState } from "react";
import { api } from "./api.js";

// "Methods & results": every number is read from /api/meta, which copies it from the pipeline's reports.
const p = (x) => (x == null ? "–" : `${(x * 100).toFixed(1)}%`);
const ci = (a) => (a ? `${(a[0] * 100).toFixed(0)}–${(a[1] * 100).toFixed(0)}%` : "");

export default function Methods({ onClose }) {
  const [m, setM] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    api.meta().then(setM).catch((e) => setErr(e.message));
  }, []);
  const r = m?.measured || {};
  const ho = r.landcover_heldout_final;
  const ch = r.imagery_change_detection_not_used;
  const nb = r.county_new_buildings_check;
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Methods and results">
        <button className="link close" onClick={onClose}>Close ✕</button>
        <h2>Methods & measured results</h2>
        {err && <p>Couldn't load: {err}</p>}
        {!m && !err && <p className="muted">Loading…</p>}
        {m && (
          <>
            <p className="muted small">{m.client}. Data: {m.data.county}. Imagery: {m.data.imagery}.</p>
            <h3>Land-cover classifier (aerial imagery only)</h3>
            {ho && (
              <table className="results">
                <tbody>
                  <tr><td>Accuracy, model</td><td>{p(ho.model_accuracy)} <span className="muted">(95% range {ci(ho.model_accuracy_95ci)})</span></td></tr>
                  <tr><td>Accuracy, county-map rules</td><td>{p(ho.rules_accuracy)} <span className="muted">(95% range {ci(ho.rules_accuracy_95ci)})</span></td></tr>
                  <tr><td>Sent to review (low confidence)</td><td>{p(ho.flagged_for_review_share)} of tiles</td></tr>
                  <tr><td>Accuracy when confident / when flagged</td><td>{p(ho.accuracy_confident)} / {p(ho.accuracy_flagged)}</td></tr>
                </tbody>
              </table>
            )}
            <p className="small muted">
              Measured once on {ho?.tiles} hand-labelled held-out tiles (60 m, {ho?.classes?.join(", ")}), from neighbourhoods
              never used for training. The model matches the rules without needing county maps, and its confidence
              reliably separates good calls from doubtful ones.
            </p>
            <h3>Change since 2018</h3>
            <p className="small">
              Comparing 2018 and 2025 classifications: only {p(ch?.precision_flagged_really_changed)} of flagged tiles had
              really changed ({ch?.pairs_labelled} hand-checked pairs), so it is <b>not used</b>. Instead, footprints the
              county added or re-mapped in its 2024 update: {nb?.check_raw_counts?.county_flag_and_really_changed} of{" "}
              {(nb?.check_raw_counts?.county_flag_and_really_changed ?? 0) + (nb?.check_raw_counts?.county_flag_but_no_change ?? 0)} flagged
              pairs really changed (weighted precision {p(nb?.precision_weighted)}); used only as a zone-level signal.
            </p>
            <h3>Data joins & cleaning</h3>
            <p className="small">
              {r.parcel_join?.parcels_matched_assessed_values_pct}% of {r.parcel_join?.parcels_total?.toLocaleString()} parcels matched their
              tax assessment; {r.shapes_repaired?.invalid_parcel_shapes_repaired} parcel and {r.shapes_repaired?.invalid_zoning_shapes_repaired} zoning
              shapes repaired. {r.protected_land?.protected_candidate_acres_removed} acres of protected stream buffer removed from
              candidate land ({r.protected_land?.candidates_dropped_80pct_protected} parcels dropped).
            </p>
            <h3>The score</h3>
            <ul className="small">
              {Object.entries(m.factors).map(([k, v]) => (
                <li key={k}><b>{k}</b> ({Math.round((m.weights_default?.[k] ?? 0) * 100)}% by default): {v}</li>
              ))}
            </ul>
            <p className="small muted">
              The score ranks physical opportunity. It does not know whether an owner will sell, and floodplains are not
              yet excluded. Spot-checked on satellite imagery: 3 of the 4 top zones checked were classic redevelopment
              sites; the 4th led to excluding protected stream valleys.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
