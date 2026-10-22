import { render, screen } from "@testing-library/react";
import { Badge } from "@/components/Badge";
import { DifficultyBars } from "@/components/DifficultyBars";
import { StepCard } from "@/components/StepCard";
import { TrendChart } from "@/components/TrendChart";
import type { Step, TrendPoint } from "@/lib/types";

const point = (runId: string, successRate: number): TrendPoint => ({
  runId, label: `run ${runId}`, createdAt: "2026-10-22T00:00:00Z", successRate,
  fieldAccuracy: null, p95Seconds: 10, costPerTask: null,
});

test("trend chart plots one point per run, higher success drawn higher", () => {
  render(<TrendChart points={[point("1", 0.2), point("2", 0.9)]} />);
  const dots = screen.getAllByTestId("trend-point");
  expect(dots).toHaveLength(2);
  // SVG y grows downwards, so a better run has a smaller cy.
  expect(Number(dots[1].getAttribute("cy"))).toBeLessThan(Number(dots[0].getAttribute("cy")));
});

test("trend chart handles no runs", () => {
  render(<TrendChart points={[]} />);
  expect(screen.getByText("No runs yet.")).toBeInTheDocument();
});

test("badge shows the failure reason", () => {
  render(<><Badge passed /><Badge passed={false} failure="max_steps" /></>);
  expect(screen.getByText("pass")).toBeInTheDocument();
  expect(screen.getByText("max_steps")).toBeInTheDocument();
});

test("difficulty bars are as wide as the success rate", () => {
  const { container } = render(<DifficultyBars stats={[{ difficulty: "hard", tasks: 4, passed: 1, successRate: 0.25 }]} />);
  expect(screen.getByText(/25%/)).toBeInTheDocument();
  expect(container.querySelector(".bar-fill")).toHaveStyle({ width: "25%" });
});

const step = (over: Partial<Step>): Step => ({
  stepNo: 3, action: "click", args: '{"element": 7}', thought: null, outcome: "Clicked [7].", ok: true,
  verified: true, feedback: null, url: "http://x/e/1/acme/invoices", screenshotUrl: "/api/artifacts/1/t/step_003.jpg", ...over,
});

test("a verified step shows its action and screenshot", () => {
  render(<StepCard step={step({})} />);
  expect(screen.getByText("click")).toBeInTheDocument();
  expect(screen.getByText("element: 7")).toBeInTheDocument();
  expect(screen.getByRole("img")).toHaveAttribute("src", "/api/artifacts/1/t/step_003.jpg");
  expect(screen.queryByText("failed")).not.toBeInTheDocument();
});

test("a failed verification is flagged with its feedback", () => {
  render(<StepCard step={step({ verified: false, feedback: "The page now shows: Invalid username or password." })} />);
  expect(screen.getByText("failed")).toBeInTheDocument();
  expect(screen.getByRole("note")).toHaveTextContent("Invalid username or password");
  expect(screen.getByTestId("step")).toHaveClass("step-failed");
});
