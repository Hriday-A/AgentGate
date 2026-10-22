"use client";

import Link from "next/link";
import { useQuery } from "@apollo/client/react";
import { CreateAgent } from "@/components/CreateAgent";
import { SignIn } from "@/components/SignIn";
import { pct, secs, when } from "@/lib/format";
import { AGENTS, ME } from "@/lib/queries";
import type { Agent, User } from "@/lib/types";

function AgentList() {
  const { data, loading, error } = useQuery<{ agents: Agent[] }>(AGENTS);
  if (loading) return <p className="muted">Loading…</p>;
  if (error) return <p className="error">{error.message}</p>;
  const agents = data?.agents ?? [];
  return (
    <section className="card">
      <h2>Agents</h2>
      {agents.length === 0 ? <p className="muted">No agents yet. Register one below.</p> : (
        <table>
          <thead><tr><th>Agent</th><th>Latest run</th><th>Success</th><th>Field acc.</th><th>p95</th><th>When</th></tr></thead>
          <tbody>
            {agents.map((a) => (
              <tr key={a.id}>
                <td><Link href={`/agents/${a.id}`}>{a.name}</Link> <span className="muted small">{a.apiKeyPrefix}…</span></td>
                <td>{a.latestRun ? <Link href={`/runs/${a.latestRun.id}`}>{a.latestRun.label}</Link> : <span className="muted">–</span>}</td>
                <td>{pct(a.latestRun?.successRate)}</td>
                <td>{pct(a.latestRun?.fieldAccuracy)}</td>
                <td>{secs(a.latestRun?.p95Seconds)}</td>
                <td className="muted">{a.latestRun ? when(a.latestRun.createdAt) : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export default function Home() {
  const { data, loading } = useQuery<{ me: User | null }>(ME);
  if (loading) return <p className="muted">Loading…</p>;
  if (!data?.me) return <SignIn />;
  return (
    <>
      <AgentList />
      <CreateAgent />
    </>
  );
}
