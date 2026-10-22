"use client";

import { useMutation } from "@apollo/client/react";
import { useState } from "react";
import { AGENTS, CREATE_AGENT } from "@/lib/queries";

interface Result { createAgent: { apiKey: string; agent: { id: string; name: string } } }

/** Registers an agent and shows its API key exactly once (the API stores only a hash). */
export function CreateAgent() {
  const [name, setName] = useState("");
  const [create, { data, loading, error }] = useMutation<Result>(CREATE_AGENT, { refetchQueries: [AGENTS] });

  return (
    <section className="card">
      <h2>Register an agent</h2>
      <form className="row" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create({ variables: { name } }); }}>
        <input placeholder="invoice-bot" value={name} onChange={(e) => setName(e.target.value)} aria-label="Agent name" />
        <button type="submit" disabled={loading}>Create</button>
      </form>
      {error && <p className="error" role="alert">{error.message}</p>}
      {data && (
        <div className="key" role="status">
          <p>API key for <strong>{data.createAgent.agent.name}</strong>. Copy it now; it won&apos;t be shown again:</p>
          <code data-testid="api-key">{data.createAgent.apiKey}</code>
          <pre className="small">{`export AGENTGATE_API_KEY=${data.createAgent.apiKey}
python -m agentgate run --smoke && python -m agentgate publish runs/<run-id>`}</pre>
        </div>
      )}
    </section>
  );
}
