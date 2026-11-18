// D3 scatter plot linked to the map: each hexagon by estimated floor-area ratio (x) and surface parking
// seen in the imagery (y). Drag a box to highlight those hexagons on the map; click a dot to open it.
import { useEffect, useRef } from "react";
import * as d3 from "d3";

const W = 368, H = 250, M = { top: 10, right: 12, bottom: 34, left: 40 };
const FAR_CAP = 3; // a few towers go far beyond; they sit on the right edge

export default function ScatterBrush({ features, color, selected, onBrush, onSelect, onHover }) {
  const ref = useRef(null);
  useEffect(() => {
    const pts = features
      .filter((f) => f.properties.cand_land_m2 > 0 && f.properties.est_far != null)
      .map((f) => f.properties);
    const x = d3.scaleLinear().domain([0, FAR_CAP]).range([M.left, W - M.right]);
    const y = d3.scaleLinear().domain([0, 1]).range([H - M.bottom, M.top]);
    const svg = d3.select(ref.current).attr("viewBox", `0 0 ${W} ${H}`).attr("width", W).attr("height", H);
    svg.selectAll("*").remove();

    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${H - M.bottom})`)
      .call(d3.axisBottom(x).ticks(6).tickSizeOuter(0));
    svg.append("g").attr("class", "axis").attr("transform", `translate(${M.left},0)`)
      .call(d3.axisLeft(y).ticks(5).tickFormat(d3.format(".0%")).tickSizeOuter(0));
    svg.append("text").attr("class", "chart-label").attr("x", W - M.right).attr("y", H - 4).attr("text-anchor", "end")
      .text("estimated floor-area ratio →  (lower = more underused)");
    svg.append("text").attr("class", "chart-label").attr("x", 4).attr("y", M.top + 2).attr("dy", "0.7em")
      .text("parking ↑");

    const brush = d3.brush()
      .extent([[M.left, M.top], [W - M.right, H - M.bottom]])
      .on("end", ({ selection }) => {
        if (!selection) return onBrush(null);
        const [[x0, y0], [x1, y1]] = selection;
        const ids = pts.filter((p) => {
          const px = x(Math.min(p.est_far, FAR_CAP)), py = y(p.imagery_parking_share || 0);
          return px >= x0 && px <= x1 && py >= y0 && py <= y1;
        }).map((p) => p.hex);
        onBrush(new Set(ids));
      });
    svg.append("g").attr("class", "brush").call(brush);

    // dots drawn after the brush so they stay clickable and hoverable
    svg.append("g").selectAll("circle").data(pts, (p) => p.hex).join("circle")
      .attr("cx", (p) => x(Math.min(p.est_far, FAR_CAP)))
      .attr("cy", (p) => y(p.imagery_parking_share || 0))
      .attr("r", (p) => (p.hex === selected ? 7 : 4.5))
      .attr("fill", (p) => color(p.score))
      .attr("stroke", (p) => (p.hex === selected ? "#0b0b0b" : "#fcfcfb"))
      .attr("stroke-width", (p) => (p.hex === selected ? 2 : 1.5))
      .style("cursor", "pointer")
      .on("click", (_, p) => onSelect(p.hex))
      .on("mousemove", (e, p) => onHover({ x: e.clientX, y: e.clientY, p }))
      .on("mouseleave", () => onHover(null));
  }, [features, color, selected]);

  return (
    <figure className="chart">
      <figcaption>Underuse vs parking: drag a box to highlight those zones on the map</figcaption>
      <svg ref={ref} role="img" aria-label="Scatter plot of zones by floor-area ratio and parking share" />
    </figure>
  );
}
