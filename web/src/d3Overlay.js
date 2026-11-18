// D3 drawn directly on the Google map: hexagons (coloured by score) and the selected hexagon's parcels.
// A google.maps.OverlayView gives us the map's projection; D3 turns GeoJSON into SVG paths in that
// projection on every pan/zoom, so the shapes stay glued to the satellite imagery.
import * as d3 from "d3";

// Sequential blue ramp (light = low score, dark = high), from the reference palette.
const RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
export const scoreColor = d3.scaleSequential(d3.piecewise(d3.interpolateRgb, RAMP)).domain([0, 100]);

export function createD3Overlay(google, map, { onSelect, onHover }) {
  class D3Overlay extends google.maps.OverlayView {
    constructor() {
      super();
      this.hexes = [];
      this.parcels = [];
      this.selected = null;
    }

    onAdd() {
      this.div = document.createElement("div");
      this.div.style.position = "absolute";
      this.svg = d3.select(this.div).append("svg").style("position", "absolute").style("pointer-events", "none");
      this.gHex = this.svg.append("g");
      this.gParcel = this.svg.append("g");
      this.getPanes().overlayMouseTarget.appendChild(this.div);
    }

    onRemove() {
      this.div?.remove();
    }

    draw() {
      const proj = this.getProjection();
      const bounds = this.getMap()?.getBounds();
      if (!proj || !bounds) return;
      // Cover the visible map (plus a margin) with one SVG positioned in the map's pixel space.
      const sw = proj.fromLatLngToDivPixel(bounds.getSouthWest());
      const ne = proj.fromLatLngToDivPixel(bounds.getNorthEast());
      const pad = 300;
      const x0 = sw.x - pad, y0 = ne.y - pad;
      this.div.style.left = `${x0}px`;
      this.div.style.top = `${y0}px`;
      this.svg.attr("width", ne.x - sw.x + 2 * pad).attr("height", sw.y - ne.y + 2 * pad);

      const path = d3.geoPath(
        d3.geoTransform({
          point(lng, lat) {
            const p = proj.fromLatLngToDivPixel(new google.maps.LatLng(lat, lng));
            this.stream.point(p.x - x0, p.y - y0);
          },
        })
      );

      this.gHex
        .selectAll("path")
        .data(this.hexes, (d) => d.properties.hex)
        .join("path")
        .attr("d", path)
        .attr("fill", (d) => (d.properties.cand_land_m2 > 0 ? scoreColor(d.properties.score) : "none"))
        // nothing selected: 0.6; selected hexagon nearly clear so the imagery and parcels show; others dimmed
        .attr("fill-opacity", (d) => (!this.selected ? 0.6 : d.properties.hex === this.selected ? 0.08 : 0.3))
        .attr("stroke", (d) => (d.properties.hex === this.selected ? "#ffffff" : "#0b0b0b"))
        .attr("stroke-width", (d) => (d.properties.hex === this.selected ? 3 : 1))
        .attr("stroke-opacity", (d) => (d.properties.cand_land_m2 > 0 ? 0.6 : 0.3))
        .attr("stroke-dasharray", (d) => (d.properties.needs_review ? "5 3" : null))
        .style("pointer-events", "visiblePainted")
        .style("cursor", "pointer")
        .on("click", (_, d) => onSelect(d.properties.hex))
        .on("mousemove", (e, d) => onHover({ x: e.clientX, y: e.clientY, p: d.properties }))
        .on("mouseleave", () => onHover(null));

      this.gParcel
        .selectAll("path")
        .data(this.parcels, (d) => d.properties.PIN)
        .join("path")
        .attr("d", path)
        .attr("fill", "none")
        .attr("stroke", (d) => (d.properties.record_check ? "#fab219" : "#ffffff"))
        .attr("stroke-width", 2)
        .attr("stroke-dasharray", (d) => (d.properties.record_check ? "4 3" : null))
        .style("pointer-events", "visibleStroke")
        .on("mousemove", (e, d) => onHover({ x: e.clientX, y: e.clientY, parcel: d.properties }))
        .on("mouseleave", () => onHover(null));
    }

    setData({ hexes, parcels, selected }) {
      if (hexes) this.hexes = hexes;
      if (parcels) this.parcels = parcels;
      if (selected !== undefined) this.selected = selected;
      this.draw();
    }
  }

  const overlay = new D3Overlay();
  overlay.setMap(map);
  return overlay;
}
