package dev.agentgate.api.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import java.time.Instant;

/** One eval run. Summary metrics are copied from the runner's results.json at ingest time. */
@Entity
@Table(name = "runs")
public class Run {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(optional = false)
    @JoinColumn(name = "agent_id")
    private Agent agent;

    @Column(name = "external_id")
    private String externalId;
    private String label;
    @Column(name = "agent_kind")
    private String agentKind;
    private String model;
    @Column(name = "extract_model")
    private String extractModel;
    @Column(name = "git_sha")
    private String gitSha;
    @Column(name = "git_branch")
    private String gitBranch;

    private int tasks;
    private int passed;
    @Column(name = "success_rate")
    private double successRate;
    @Column(name = "field_accuracy")
    private Double fieldAccuracy;
    @Column(name = "avg_steps")
    private double avgSteps;
    @Column(name = "p50_seconds")
    private double p50Seconds;
    @Column(name = "p95_seconds")
    private double p95Seconds;
    @Column(name = "input_tokens")
    private long inputTokens;
    @Column(name = "output_tokens")
    private long outputTokens;
    @Column(name = "total_cost_usd")
    private Double totalCostUsd;

    @Column(name = "created_at", insertable = false, updatable = false)
    private Instant createdAt;

    protected Run() {
    }

    public Run(Agent agent, String externalId, String label) {
        this.agent = agent;
        this.externalId = externalId;
        this.label = label;
    }

    public void setLabel(String label) {
        this.label = label;
    }

    public void setConfig(String agentKind, String model, String extractModel, String gitSha, String gitBranch) {
        this.agentKind = agentKind;
        this.model = model;
        this.extractModel = extractModel;
        this.gitSha = gitSha;
        this.gitBranch = gitBranch;
    }

    public void setMetrics(int tasks, int passed, double successRate, Double fieldAccuracy, double avgSteps,
                           double p50Seconds, double p95Seconds, long inputTokens, long outputTokens,
                           Double totalCostUsd) {
        this.tasks = tasks;
        this.passed = passed;
        this.successRate = successRate;
        this.fieldAccuracy = fieldAccuracy;
        this.avgSteps = avgSteps;
        this.p50Seconds = p50Seconds;
        this.p95Seconds = p95Seconds;
        this.inputTokens = inputTokens;
        this.outputTokens = outputTokens;
        this.totalCostUsd = totalCostUsd;
    }

    public Double getCostPerTask() {
        return totalCostUsd == null || tasks == 0 ? null : totalCostUsd / tasks;
    }

    public Long getId() { return id; }
    public Agent getAgent() { return agent; }
    public String getExternalId() { return externalId; }
    public String getLabel() { return label; }
    public String getAgentKind() { return agentKind; }
    public String getModel() { return model; }
    public String getExtractModel() { return extractModel; }
    public String getGitSha() { return gitSha; }
    public String getGitBranch() { return gitBranch; }
    public int getTasks() { return tasks; }
    public int getPassed() { return passed; }
    public double getSuccessRate() { return successRate; }
    public Double getFieldAccuracy() { return fieldAccuracy; }
    public double getAvgSteps() { return avgSteps; }
    public double getP50Seconds() { return p50Seconds; }
    public double getP95Seconds() { return p95Seconds; }
    public long getInputTokens() { return inputTokens; }
    public long getOutputTokens() { return outputTokens; }
    public Double getTotalCostUsd() { return totalCostUsd; }
    public Instant getCreatedAt() { return createdAt; }
}
