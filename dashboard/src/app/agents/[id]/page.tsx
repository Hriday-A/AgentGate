"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@apollo/client/react";
import { Stat } from "@/components/Stat";
import { TrendChart } from "@/components/TrendChart";
import { pct, secs, usd, when } from "@/lib/format";
import { AGENT } from "@/lib/queries";
import type { RunSummary, TrendPoint } from "@/lib/types";

interface Data {
  agent: { id: string; name: string; description: string | null; apiKeyPrefix: string; runs: RunSummary[]; trend: TrendPoint[] } | null;
}

export default function AgentPage() {
  const { id } = useParams<{ id: string }>();
  const { data, loading, error } = useQuery<Data>(AGENT, { variables: { id } });
  if (loading) return <p className="muted">Loading…</p>;
  if (error || !data?.agent) return <p className="error">{error?.message ?? "Agent not found."}</p>;
  const { agent } = data;
  const latest = agent.runs[0];
  const previous = agent.runs[1];
  const delta = latest && previous ? latest.successRate - previous.successRate : null;

  return (
    <>
      <p className="crumbs"><Link href="/">Agents</Link> / {agent.name}</p>
      <h1>{agent.name}</h1>
      {agent.description && <p className="muted">{agent.description}</p>}
      {latest && (
        <section className="stats">
          <Stat label="Success (latest)" value={pct(latest.successRate)}
                hint={delta === null ? undefined : `${delta >= 0 ? "+" : ""}${Math.round(delta * 100)} pts vs previous`} />
          <Stat label="Field accuracy" value={pct(latest.fieldAccuracy)} />
          <Stat label="p95 latency" value={secs(latest.p95Seconds)} />
          <Stat label="Cost / task" value={usd(latest.costPerTask)} />
        </section>
      )}
      <section className="card">
        <h2>Success rate across runs</h2>
        <TrendChart points={agent.trend} />
      </section>
      <section className="card">
        <h2>Runs</h2>
        <table>
          <thead><tr><th>Run</th><th>Branch</th><th>Tasks</th><th>Success</th><th>Field acc.</th><th>Steps</th><th>p50</th><th>p95</th><th>When</th></tr></thead>
          <tbody>
            {agent.runs.map((r) => (
              <tr key={r.id}>
                <td><Link href={`/runs/${r.id}`}>{r.label}</Link></td>
                <td className="muted small">{r.gitBranch ?? ""} {r.gitSha?.slice(0, 7) ?? ""}</td>
                <td>{r.tasks}</td>
                <td>{pct(r.successRate)}</td>
                <td>{pct(r.fieldAccuracy)}</td>
                <td>{r.avgSteps.toFixed(1)}</td>
                <td>{secs(r.p50Seconds)}</td>
                <td>{secs(r.p95Seconds)}</td>
                <td className="muted">{when(r.createdAt)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
