import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { loadGoogleMaps } from "./googleMaps.js";
import { createD3Overlay, scoreColor } from "./d3Overlay.js";
import SidePanel from "./SidePanel.jsx";
import Tooltip from "./Tooltip.jsx";

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
  const [hexes, setHexes] = useState(null);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [hover, setHover] = useState(null);
  const [error, setError] = useState(null);

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
        setHexes(fc.features);
      })
      .catch((e) => setError(e.message));
    return () => {
      cancelled = true;
    };
  }, []);

  // Push hexagons to the D3 overlay.
  useEffect(() => {
    if (overlayRef.current && hexes) overlayRef.current.setData({ hexes });
  }, [hexes]);

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
    const ring = (hexes || []).filter((f) => f.properties.submarket === id).flatMap((f) => f.geometry.coordinates[0]);
    if (ring.length) fitTo(ring);
  }

  const top10 = useMemo(
    () => (hexes || []).filter((f) => f.properties.cand_land_m2 > 0).sort((a, b) => a.properties.rank - b.properties.rank).slice(0, 10),
    [hexes]
  );

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <strong>Atlas</strong> <span className="muted">· Opportunity map for Larkspur Commercial Partners (fictional client)</span>
        </div>
        <nav>
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
          <Legend />
        </div>
        <SidePanel top10={top10} detail={detail} selected={selected} onSelect={setSelected} />
      </main>
      <Tooltip hover={hover} />
    </div>
  );
}

function Legend() {
  const stops = [0, 25, 50, 75, 100];
  return (
    <div className="legend" aria-label="Map legend">
      <div className="legend-title">Opportunity score</div>
      <div className="legend-bar" style={{ background: `linear-gradient(to right, ${stops.map((s) => scoreColor(s)).join(",")})` }} />
      <div className="legend-ticks">
        <span>0</span>
        <span>50</span>
        <span>100</span>
      </div>
      <div className="legend-row">
        <svg width="22" height="12" aria-hidden="true">
          <rect x="1" y="1" width="20" height="10" fill="none" stroke="#0b0b0b" strokeDasharray="5 3" />
        </svg>
        needs analyst review
      </div>
      <div className="legend-row">
        <svg width="22" height="12" aria-hidden="true">
          <rect x="1" y="1" width="20" height="10" fill="none" stroke="#999" strokeOpacity="0.6" />
        </svg>
        no candidate land
      </div>
    </div>
  );
}
