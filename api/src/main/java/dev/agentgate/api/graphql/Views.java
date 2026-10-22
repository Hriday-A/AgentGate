package dev.agentgate.api.graphql;

import java.time.Instant;

/** Computed GraphQL output types that don't map 1:1 to a table. */
public final class Views {

    private Views() {
    }

    public record CreatedAgent(dev.agentgate.api.domain.Agent agent, String apiKey) {
    }

    public record DifficultyStat(String difficulty, int tasks, int passed, double successRate) {
    }

    public record TrendPoint(Long runId, String label, Instant createdAt, double successRate, Double fieldAccuracy,
                             double p95Seconds, Double costPerTask) {
    }
}
