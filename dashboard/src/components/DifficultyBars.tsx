import type { DifficultyStat } from "@/lib/types";
import { pct } from "@/lib/format";

export function DifficultyBars({ stats }: { stats: DifficultyStat[] }) {
  return (
    <div className="bars">
      {stats.map((s) => (
        <div key={s.difficulty} className="bar-row">
          <span className="bar-label">{s.difficulty}</span>
          <span className="bar-track">
            <span className="bar-fill" style={{ width: `${s.successRate * 100}%` }} />
          </span>
          <span className="bar-value">{pct(s.successRate)} <span className="muted">({s.passed}/{s.tasks})</span></span>
        </div>
      ))}
    </div>
  );
}
