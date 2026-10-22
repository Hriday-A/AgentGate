// Hand-written types for the GraphQL responses this dashboard uses (see api/.../schema.graphqls).

export interface User { id: string; login: string; name: string | null; avatarUrl: string | null }

export interface DifficultyStat { difficulty: string; tasks: number; passed: number; successRate: number }

export interface RunSummary {
  id: string;
  label: string;
  model: string | null;
  agentKind: string | null;
  gitSha: string | null;
  gitBranch: string | null;
  createdAt: string;
  tasks: number;
  passed: number;
  successRate: number;
  fieldAccuracy: number | null;
  avgSteps: number;
  p50Seconds: number;
  p95Seconds: number;
  costPerTask: number | null;
}

export interface TrendPoint {
  runId: string;
  label: string;
  createdAt: string;
  successRate: number;
  fieldAccuracy: number | null;
  p95Seconds: number;
  costPerTask: number | null;
}

export interface Agent {
  id: string;
  name: string;
  description: string | null;
  apiKeyPrefix: string;
  createdAt: string;
  latestRun?: RunSummary | null;
}

export interface TaskRow {
  id: string;
  taskId: string;
  kind: string;
  vendor: string;
  difficulty: string;
  passed: boolean;
  failure: string | null;
  detail: string | null;
  fieldAccuracy: number | null;
  steps: number;
  replans: number;
  seconds: number;
}

export interface Step {
  stepNo: number;
  action: string | null;
  args: string | null;
  thought: string | null;
  outcome: string | null;
  ok: boolean | null;
  verified: boolean | null;
  feedback: string | null;
  url: string | null;
  screenshotUrl: string | null;
}
