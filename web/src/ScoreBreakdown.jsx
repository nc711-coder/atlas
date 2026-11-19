// D3 bar chart: how many points each factor contributes to the selected hexagon's score.
// Grey track = the most that factor could contribute at the current weights.
import { useEffect, useRef } from "react";
import * as d3 from "d3";
import { FACTOR_LABELS, FACTORS } from "./weights.js";

export default function ScoreBreakdown({ props, weights }) {
  const ref = useRef(null);
  useEffect(() => {
    const total = FACTORS.reduce((s, k) => s + weights[k], 0) || 1;
    const rows = FACTORS.map((k) => ({
      k,
      label: FACTOR_LABELS[k],
      max: (weights[k] / total) * 100,
      pts: (weights[k] / total) * 100 * (props[`f_${k}`] || 0),
    }));
    const W = 368, rowH = 26, left = 150, right = 40, H = rows.length * rowH + 6;
    const x = d3.scaleLinear().domain([0, Math.max(1, d3.max(rows, (r) => r.max))]).range([0, W - left - right]);
    const svg = d3.select(ref.current).attr("viewBox", `0 0 ${W} ${H}`).attr("width", "100%").attr("height", null);
    svg.selectAll("*").remove();
    const g = svg.selectAll("g.row").data(rows).join("g").attr("transform", (_, i) => `translate(0,${i * rowH + 3})`);
    g.append("text").attr("x", 0).attr("y", 14).attr("class", "chart-label").text((r) => r.label);
    g.append("rect").attr("x", left).attr("y", 4).attr("height", 14).attr("rx", 4)
      .attr("width", (r) => x(r.max)).attr("fill", "#f0efec");
    g.append("rect").attr("x", left).attr("y", 4).attr("height", 14).attr("rx", 4)
      .attr("width", (r) => x(r.pts)).attr("fill", "#2a78d6")
      .append("title").text((r) => `${r.label}: ${r.pts.toFixed(1)} of ${r.max.toFixed(1)} possible points`);
    g.append("text").attr("x", (r) => left + x(r.max) + 6).attr("y", 15).attr("class", "chart-value")
      .text((r) => r.pts.toFixed(1));
  }, [props, weights]);
  return (
    <figure className="chart">
      <figcaption>Points each factor adds (grey = most possible at current weights)</figcaption>
      <svg ref={ref} role="img" aria-label="Score breakdown by factor" />
    </figure>
  );
}
