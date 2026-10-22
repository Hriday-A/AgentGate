import { gql } from "@apollo/client";

const RUN_SUMMARY = gql`
  fragment RunSummary on Run {
    id label model agentKind gitSha gitBranch createdAt
    tasks passed successRate fieldAccuracy avgSteps p50Seconds p95Seconds costPerTask
  }
`;

export const ME = gql`
  query Me { me { id login name avatarUrl } }
`;

export const AGENTS = gql`
  ${RUN_SUMMARY}
  query Agents {
    agents { id name description apiKeyPrefix createdAt latestRun { ...RunSummary } }
  }
`;

export const AGENT = gql`
  ${RUN_SUMMARY}
  query Agent($id: ID!) {
    agent(id: $id) {
      id name description apiKeyPrefix createdAt
      runs(limit: 50) { ...RunSummary }
      trend(limit: 30) { runId label createdAt successRate fieldAccuracy p95Seconds costPerTask }
    }
  }
`;

export const RUN = gql`
  ${RUN_SUMMARY}
  query Run($id: ID!) {
    run(id: $id) {
      ...RunSummary
      extractModel inputTokens outputTokens totalCostUsd
      byDifficulty { difficulty tasks passed successRate }
      results { id taskId kind vendor difficulty passed failure detail fieldAccuracy steps replans seconds }
    }
  }
`;

export const TASK = gql`
  query Task($id: ID!) {
    taskResult(id: $id) {
      id taskId kind vendor difficulty passed failure detail answer fieldAccuracy steps replans seconds
      run { id label }
      trace { stepNo action args thought outcome ok verified feedback url screenshotUrl }
    }
  }
`;

export const CREATE_AGENT = gql`
  mutation CreateAgent($name: String!, $description: String) {
    createAgent(name: $name, description: $description) { apiKey agent { id name apiKeyPrefix } }
  }
`;

export const ROTATE_KEY = gql`
  mutation RotateKey($agentId: ID!) { rotateApiKey(agentId: $agentId) { apiKey agent { id apiKeyPrefix } } }
`;
