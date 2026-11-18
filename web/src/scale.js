// Colour bands for the score: five quintiles of the hexagons that have candidate land
// (light = bottom 20%, dark = top 20%), from the reference sequential blue ramp.
import * as d3 from "d3";

export const BAND_COLORS = ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"];
export const BAND_LABELS = ["bottom 20%", "20–40%", "40–60%", "60–80%", "top 20%"];
export const REVIEW_COLOR = "#fab219"; // status "warning" - always paired with a label, never colour alone

export function makeScoreColor(features) {
  const scores = features.filter((f) => f.properties.cand_land_m2 > 0).map((f) => f.properties.score);
  const q = d3.scaleQuantile().domain(scores.length ? scores : [0, 100]).range(BAND_COLORS);
  const fn = (s) => q(s);
  fn.thresholds = q.quantiles();
  return fn;
}
