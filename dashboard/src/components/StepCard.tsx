import type { Step } from "@/lib/types";
import { prettyArgs } from "@/lib/format";

/** One action in a trace: what the agent did, what happened, and the screen afterwards. */
export function StepCard({ step }: { step: Step }) {
  const failed = step.verified === false || step.ok === false;
  return (
    <article className={`step ${failed ? "step-failed" : ""}`} data-testid="step">
      <header>
        <span className="step-no">#{step.stepNo}</span>
        <code>{step.action ?? "no action"}</code>
        <span className="muted">{prettyArgs(step.args)}</span>
        {failed && <span className="badge fail">failed</span>}
      </header>
      {step.thought && <p className="thought">{step.thought}</p>}
      {step.outcome && <p className="small">{step.outcome}</p>}
      {step.feedback && <p className="feedback" role="note">⚠ {step.feedback}</p>}
      {step.url && <p className="muted small url">{step.url}</p>}
      {step.screenshotUrl && (
        // eslint-disable-next-line @next/next/no-img-element -- auth-protected API images, not static assets
        <img src={step.screenshotUrl} alt={`Screen after step ${step.stepNo}`} loading="lazy" />
      )}
    </article>
  );
}
