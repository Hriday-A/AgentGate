"use client";

import { useEffect, useState } from "react";

interface Providers { github: boolean; dev: boolean }

export function SignIn() {
  const [providers, setProviders] = useState<Providers | null>(null);
  const [login, setLogin] = useState("demo");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/auth/providers").then((r) => r.json()).then(setProviders).catch(() => setError("API unreachable."));
  }, []);

  async function devLogin(e: React.FormEvent) {
    e.preventDefault();
    const resp = await fetch(`/dev/login?login=${encodeURIComponent(login)}`, { method: "POST" });
    if (resp.ok) window.location.reload();
    else setError("Dev sign-in failed.");
  }

  return (
    <section className="card narrow">
      <h1>AgentGate</h1>
      <p className="muted">Evaluation runs, traces and trends for your browser agents.</p>
      {providers?.github && <a className="button primary" href="/oauth2/authorization/github">Sign in with GitHub</a>}
      {providers?.dev && (
        <form onSubmit={devLogin} className="row">
          <input value={login} onChange={(e) => setLogin(e.target.value)} aria-label="Dev login name" />
          <button type="submit">Dev sign-in</button>
        </form>
      )}
      {providers && !providers.github && !providers.dev && (
        <p className="muted">No sign-in method is configured on the API.</p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
