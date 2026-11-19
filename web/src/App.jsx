import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { loadGoogleMaps } from "./googleMaps.js";
import { createD3Overlay } from "./d3Overlay.js";
import { BAND_COLORS, BAND_LABELS, LANDCOVER_COLORS, LANDCOVER_LABELS, makeScoreColor, REVIEW_COLOR } from "./scale.js";
import { DEFAULT_WEIGHTS, FACTORS } from "./weights.js";
import SidePanel from "./SidePanel.jsx";
import Tooltip from "./Tooltip.jsx";
import Methods from "./Methods.jsx";

const SUBMARKETS = [
  { id: "tysons", label: "Tysons" },
  { id: "reston_herndon", label: "Reston–Herndon" },
  { id: "richmond_hwy", label: "Richmond Hwy" },
];

export default function App() {
  const mapDiv = useRef(null);
  const mapRef = useRef(null);
  const overlayRef = useRef(null);
  const googleRef = useRef(null);
  const [baseHexes, setBaseHexes] = useState(null);
  const [ranking, setRanking] = useState(null); // {hex: {score, rank}} from /api/rank, or null = pipeline defaults
  const [weights, setWeights] = useState(DEFAULT_WEIGHTS);
  const [minAcres, setMinAcres] = useState(0);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [highlight, setHighlight] = useState(null);
  const [hover, setHover] = useState(null);
  const [error, setError] = useState(null);
  const [layer, setLayer] = useState("score"); // "score" | "landcover"
  const [landcover, setLandcover] = useState(null);
  const [showMethods, setShowMethods] = useState(false);

  // Load data and the map once.
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.hexes(), loadGoogleMaps()])
      .then(async ([fc, google]) => {
        if (cancelled) return;
        googleRef.current = google;
        const { Map } = await google.maps.importLibrary("maps");
        const map = new Map(mapDiv.current, {
          center: { lat: 38.922, lng: -77.232 },
          zoom: 14,
          mapTypeId: "satellite",
          tilt: 0, // straight-down imagery, so hexagons line up with what you see
          streetViewControl: false,
          fullscreenControl: false,
          mapTypeControlOptions: { mapTypeIds: ["satellite", "hybrid", "roadmap"] },
        });
        mapRef.current = map;
        overlayRef.current = createD3Overlay(google, map, { onSelect: setSelected, onHover: setHover });
        setBaseHexes(fc.features);
      })
      .catch((e) => setError(e.message));
    return () => {
      cancelled = true;
    };
  }, []);

  // Re-rank through the API whenever weights or the land filter change (debounced).
  useEffect(() => {
    if (!baseHexes) return;
    const isDefault = FACTORS.every((k) => weights[k] === DEFAULT_WEIGHTS[k]) && minAcres === 0;
    if (isDefault) return setRanking(null);
    if (FACTORS.every((k) => weights[k] === 0)) return; // the API rejects all-zero weights
    setBusy(true);
    const t = setTimeout(() => {
      const body = Object.fromEntries(FACTORS.map((k) => [k, weights[k] / 100]));
      api
        .rank({ ...body, min_acres: minAcres })
        .then((r) => setRanking(Object.fromEntries(r.top.map((x) => [x.hex, { score: x.score, rank: x.rank }]))))
        .catch((e) => setError(e.message))
        .finally(() => setBusy(false));
    }, 250);
    return () => clearTimeout(t);
  }, [weights, minAcres, baseHexes]);

  // Hexagons as displayed: pipeline values, or the custom ranking (hexagons filtered out get no fill).
  const hexes = useMemo(() => {
    if (!baseHexes) return null;
    if (!ranking) return baseHexes.map((f) => ({ ...f, properties: { ...f.properties, filtered: !(f.properties.cand_land_m2 > 0) } }));
    return baseHexes.map((f) => {
      const r = ranking[f.properties.hex];
      return { ...f, properties: { ...f.properties, score: r ? r.score : 0, rank: r ? r.rank : null, filtered: !r } };
    });
  }, [baseHexes, ranking]);

  const color = useMemo(() => makeScoreColor((hexes || []).filter((f) => !f.properties.filtered)), [hexes]);

  useEffect(() => {
    if (overlayRef.current && hexes) overlayRef.current.setData({ hexes, color, highlight });
  }, [hexes, color, highlight]);

  // Land-cover layer: load the tiles the first time it is switched on.
  useEffect(() => {
    const overlay = overlayRef.current;
    if (!overlay) return;
    if (layer === "landcover" && !landcover) {
      api
        .landcover()
        .then((fc) => {
          const tiles = fc.features.map((f) => {
            const xs = f.geometry.coordinates[0].map((c) => c[0]), ys = f.geometry.coordinates[0].map((c) => c[1]);
            return { ...f, bbox: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] };
          });
          setLandcover(tiles);
        })
        .catch((e) => setError(e.message));
      return;
    }
    overlay.setData({ showLandcover: layer === "landcover", landcover: landcover || [] });
  }, [layer, landcover]);

  // Selecting a hexagon: fetch its parcels, draw them, and zoom to it.
  useEffect(() => {
    const overlay = overlayRef.current;
    if (!overlay) return;
    if (!selected) {
      setDetail(null);
      overlay.setData({ parcels: [], selected: null });
      return;
    }
    let cancelled = false;
    overlay.setData({ selected });
    api
      .hex(selected)
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
        overlay.setData({ parcels: d.parcels.features });
        fitTo(d.hex.geometry.coordinates[0]);
      })
      .catch((e) => setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [selected]);

  function fitTo(ring) {
    const google = googleRef.current;
    const b = new google.maps.LatLngBounds();
    ring.forEach(([lng, lat]) => b.extend({ lat, lng }));
    mapRef.current.fitBounds(b, 80);
  }

  function goToSubmarket(id) {
    setSelected(null);
    const ring = (baseHexes || []).filter((f) => f.properties.submarket === id).flatMap((f) => f.geometry.coordinates[0]);
    if (ring.length) fitTo(ring);
  }

  const ranked = useMemo(
    () => (hexes || []).filter((f) => !f.properties.filtered && f.properties.rank != null).sort((a, b) => a.properties.rank - b.properties.rank),
    [hexes]
  );
  const selectedProps = hexes?.find((f) => f.properties.hex === selected)?.properties;

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <strong>Atlas</strong> <span className="muted">· Opportunity map for Larkspur Commercial Partners (fictional client)</span>
        </div>
        <nav>
          <button className="primary" onClick={() => setShowMethods(true)}>Methods & results</button>
          {SUBMARKETS.map((s) => (
            <button key={s.id} onClick={() => goToSubmarket(s.id)}>
              {s.label}
            </button>
          ))}
        </nav>
      </header>
      <main className="body">
        <div className="map-wrap">
          <div ref={mapDiv} className="map" />
          {!hexes && !error && <div className="center-note">Loading map and scores…</div>}
          {error && <div className="center-note error">Couldn't load: {error}</div>}
          {highlight && (
            <div className="map-chip">
              {highlight.size} zones highlighted from the chart · <button className="link" onClick={() => setHighlight(null)}>clear</button>
            </div>
          )}
          <div className="layer-switch" role="radiogroup" aria-label="Map layer">
            <label><input type="radio" checked={layer === "score"} onChange={() => setLayer("score")} /> Opportunity score</label>
            <label><input type="radio" checked={layer === "landcover"} onChange={() => setLayer("landcover")} /> Land cover (imagery AI)</label>
          </div>
          {layer === "score" ? <Legend custom={!!ranking} /> : <LandcoverLegend loading={!landcover} />}
        </div>
        <SidePanel
          hexes={hexes || []}
          ranked={ranked}
          color={color}
          detail={detail}
          selected={selected}
          selectedProps={selectedProps}
          onSelect={setSelected}
          onBrush={setHighlight}
          onHover={setHover}
          weights={weights}
          onWeights={setWeights}
          minAcres={minAcres}
          onMinAcres={setMinAcres}
          busy={busy}
        />
      </main>
      <Tooltip hover={hover} />
      {showMethods && <Methods onClose={() => setShowMethods(false)} />}
    </div>
  );
}

function Legend({ custom }) {
  return (
    <div className="legend" aria-label="Map legend">
      <div className="legend-title">Opportunity score {custom ? "(your weights)" : ""}</div>
      {BAND_COLORS.slice().reverse().map((c, i) => (
        <div className="legend-row" key={c}>
          <span className="swatch" style={{ background: c }} />
          {BAND_LABELS[BAND_LABELS.length - 1 - i]} of zones
        </div>
      ))}
      <div className="legend-row">
        <svg width="22" height="12" aria-hidden="true">
          <rect x="1.5" y="1.5" width="19" height="9" fill="none" stroke={REVIEW_COLOR} strokeWidth="2.5" strokeDasharray="6 4" />
        </svg>
        needs analyst review
      </div>
      <div className="legend-row">
        <svg width="22" height="12" aria-hidden="true">
          <rect x="1" y="1" width="20" height="10" fill="none" stroke="#999" strokeOpacity="0.6" />
        </svg>
        no candidate land / filtered out
      </div>
    </div>
  );
}

function LandcoverLegend({ loading }) {
  return (
    <div className="legend" aria-label="Land-cover legend">
      <div className="legend-title">2025 land cover, per 60 m tile {loading ? "(loading…)" : ""}</div>
      {Object.entries(LANDCOVER_COLORS).map(([k, c]) => (
        <div className="legend-row" key={k}>
          <span className="swatch" style={{ background: c }} />
          {LANDCOVER_LABELS[k]}
        </div>
      ))}
      <div className="legend-row">
        <span className="swatch" style={{ background: "#2a78d6", opacity: 0.35 }} />
        faded = low confidence, sent to review
      </div>
      <div className="muted small" style={{ marginTop: 4 }}>Zoom in to see individual tiles.</div>
    </div>
  );
}
