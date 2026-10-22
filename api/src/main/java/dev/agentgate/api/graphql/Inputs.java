package dev.agentgate.api.graphql;

import java.util.List;

/** GraphQL input types, bound from the request by Spring for GraphQL (see schema.graphqls). */
public final class Inputs {

    private Inputs() {
    }

    public record RunInput(String externalId, String label, String agentKind, String model, String extractModel,
                           String gitSha, String gitBranch, SummaryInput summary, List<TaskResultInput> results) {
    }

    public record SummaryInput(int tasks, int passed, double successRate, Double fieldAccuracy, double avgSteps,
                               double p50Seconds, double p95Seconds, double inputTokens, double outputTokens,
                               Double totalCostUsd) {
    }

    public record TaskResultInput(String taskId, String kind, String vendor, String difficulty, boolean passed,
                                  String failure, String detail, String answer, Double fieldAccuracy, int steps,
                                  Integer replans, double seconds, double inputTokens, double outputTokens,
                                  Double costUsd, List<StepInput> trace) {
    }

    public record StepInput(int stepNo, String action, String args, String thought, String outcome, Boolean ok,
                            Boolean verified, String feedback, String url, String screenshot) {
    }
}
