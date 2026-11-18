// D3 drawn directly on the Google map: hexagons (coloured by score band) and the selected hexagon's parcels.
// A google.maps.OverlayView gives us the map's projection; D3 turns GeoJSON into SVG paths in that
// projection on every pan/zoom, so the shapes stay glued to the satellite imagery.
import * as d3 from "d3";
import { REVIEW_COLOR } from "./scale.js";

export function createD3Overlay(google, map, { onSelect, onHover }) {
  class D3Overlay extends google.maps.OverlayView {
    constructor() {
      super();
      this.hexes = [];
      this.parcels = [];
      this.selected = null;
      this.highlight = null; // Set of hex ids picked in the scatter plot, or null
      this.color = () => "#3987e5";
    }

    onAdd() {
      this.div = document.createElement("div");
      this.div.style.position = "absolute";
      this.svg = d3.select(this.div).append("svg").style("position", "absolute").style("pointer-events", "none");
      this.gHex = this.svg.append("g");
      this.gReview = this.svg.append("g"); // amber review outlines drawn on top
      this.gParcel = this.svg.append("g");
      this.getPanes().overlayMouseTarget.appendChild(this.div);
    }

    onRemove() {
      this.div?.remove();
    }

    opacity(d) {
      const id = d.properties.hex;
      if (this.selected) return id === this.selected ? 0.08 : 0.3; // selected: nearly clear to show the land
      if (this.highlight) return this.highlight.has(id) ? 0.75 : 0.12;
      return 0.6;
    }

    draw() {
      const proj = this.getProjection();
      const bounds = this.getMap()?.getBounds();
      if (!proj || !bounds || !this.div) return;
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
      const hasLand = (d) => d.properties.cand_land_m2 > 0;

      this.gHex
        .selectAll("path")
        .data(this.hexes, (d) => d.properties.hex)
        .join("path")
        .attr("d", path)
        .attr("fill", (d) => (hasLand(d) ? this.color(d.properties.score) : "none"))
        .attr("fill-opacity", (d) => this.opacity(d))
        .attr("stroke", (d) => (d.properties.hex === this.selected ? "#ffffff" : "#0b0b0b"))
        .attr("stroke-width", (d) => (d.properties.hex === this.selected ? 3 : 1))
        .attr("stroke-opacity", (d) => (hasLand(d) ? 0.55 : 0.3))
        .style("pointer-events", "visiblePainted")
        .style("cursor", "pointer")
        .on("click", (_, d) => onSelect(d.properties.hex))
        .on("mousemove", (e, d) => onHover({ x: e.clientX, y: e.clientY, p: d.properties }))
        .on("mouseleave", () => onHover(null));

      this.gReview
        .selectAll("path")
        .data(this.hexes.filter((d) => d.properties.needs_review), (d) => d.properties.hex)
        .join("path")
        .attr("d", path)
        .attr("fill", "none")
        .attr("stroke", REVIEW_COLOR)
        .attr("stroke-width", 2.5)
        .attr("stroke-dasharray", "6 4")
        .style("pointer-events", "none");

      this.gParcel
        .selectAll("path")
        .data(this.parcels, (d) => d.properties.PIN)
        .join("path")
        .attr("d", path)
        .attr("fill", "none")
        .attr("stroke", (d) => (d.properties.record_check ? REVIEW_COLOR : "#ffffff"))
        .attr("stroke-width", 2)
        .attr("stroke-dasharray", (d) => (d.properties.record_check ? "4 3" : null))
        .style("pointer-events", "visibleStroke")
        .on("mousemove", (e, d) => onHover({ x: e.clientX, y: e.clientY, parcel: d.properties }))
        .on("mouseleave", () => onHover(null));
    }

    setData(next) {
      Object.assign(this, next);
      this.draw();
    }
  }

  const overlay = new D3Overlay();
  overlay.setMap(map);
  return overlay;
}
