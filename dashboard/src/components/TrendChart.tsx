import type { TrendPoint } from "@/lib/types";

interface Props {
  points: TrendPoint[];
  width?: number;
  height?: number;
}

const PAD = { top: 12, right: 12, bottom: 24, left: 36 };

/** Success rate across runs, oldest → newest. Plain SVG: y is 0–100%, x is run order. */
export function TrendChart({ points, width = 640, height = 200 }: Props) {
  if (points.length === 0) {
    return <p className="muted">No runs yet.</p>;
  }
  const w = width - PAD.left - PAD.right;
  const h = height - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (points.length === 1 ? w / 2 : (i / (points.length - 1)) * w);
  const y = (v: number) => PAD.top + (1 - v) * h;
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.successRate).toFixed(1)}`).join(" ");

  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Success rate by run" className="chart">
      {[0, 0.5, 1].map((v) => (
        <g key={v}>
          <line x1={PAD.left} x2={width - PAD.right} y1={y(v)} y2={y(v)} className="grid" />
          <text x={PAD.left - 6} y={y(v) + 4} textAnchor="end" className="axis">{`${v * 100}%`}</text>
        </g>
      ))}
      <path d={path} className="line" fill="none" />
      {points.map((p, i) => (
        <circle key={p.runId} cx={x(i)} cy={y(p.successRate)} r={4} className="dot" data-testid="trend-point">
          <title>{`${p.label}: ${Math.round(p.successRate * 100)}%`}</title>
        </circle>
      ))}
    </svg>
  );
}
