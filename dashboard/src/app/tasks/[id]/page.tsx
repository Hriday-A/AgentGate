"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@apollo/client/react";
import { Badge } from "@/components/Badge";
import { StepCard } from "@/components/StepCard";
import { pct, secs } from "@/lib/format";
import { TASK } from "@/lib/queries";
import type { Step, TaskRow } from "@/lib/types";

interface Data {
  taskResult: (TaskRow & { answer: string | null; run: { id: string; label: string }; trace: Step[] }) | null;
}

/** Replay of one task: every action, its verification result, and the screen afterwards. */
export default function TaskPage() {
  const { id } = useParams<{ id: string }>();
  const { data, loading, error } = useQuery<Data>(TASK, { variables: { id } });
  if (loading) return <p className="muted">Loading…</p>;
  if (error || !data?.taskResult) return <p className="error">{error?.message ?? "Task not found."}</p>;
  const t = data.taskResult;

  return (
    <>
      <p className="crumbs"><Link href="/">Agents</Link> / <Link href={`/runs/${t.run.id}`}>{t.run.label}</Link> / {t.taskId}</p>
      <h1>{t.taskId} <Badge passed={t.passed} failure={t.failure} /></h1>
      <p className="muted">
        {t.kind} · {t.vendor} · {t.difficulty} · {t.steps} steps · {t.replans} re-plans · {secs(t.seconds)}
        {t.fieldAccuracy !== null && ` · fields ${pct(t.fieldAccuracy)}`}
      </p>
      {!t.passed && t.detail && <p className="feedback">{t.detail}</p>}
      {t.answer && <p>Answer: <code>{t.answer}</code></p>}
      <section className="trace">
        {t.trace.length === 0 ? <p className="muted">No trace was uploaded for this task.</p>
          : t.trace.map((s) => <StepCard key={s.stepNo} step={s} />)}
      </section>
    </>
  );
}
