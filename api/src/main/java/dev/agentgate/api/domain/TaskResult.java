package dev.agentgate.api.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;

@Entity
@Table(name = "task_results")
public class TaskResult {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(optional = false)
    @JoinColumn(name = "run_id")
    private Run run;

    @Column(name = "task_id")
    private String taskId;
    private String kind;
    private String vendor;
    private String difficulty;
    private boolean passed;
    private String failure;
    private String detail;
    private String answer;
    @Column(name = "field_accuracy")
    private Double fieldAccuracy;
    private int steps;
    private int replans;
    private double seconds;
    @Column(name = "input_tokens")
    private long inputTokens;
    @Column(name = "output_tokens")
    private long outputTokens;
    @Column(name = "cost_usd")
    private Double costUsd;

    protected TaskResult() {
    }

    public TaskResult(Run run, String taskId, String kind, String vendor, String difficulty, boolean passed,
                      String failure, String detail, String answer, Double fieldAccuracy, int steps, int replans,
                      double seconds, long inputTokens, long outputTokens, Double costUsd) {
        this.run = run;
        this.taskId = taskId;
        this.kind = kind;
        this.vendor = vendor;
        this.difficulty = difficulty;
        this.passed = passed;
        this.failure = failure;
        this.detail = detail;
        this.answer = answer;
        this.fieldAccuracy = fieldAccuracy;
        this.steps = steps;
        this.replans = replans;
        this.seconds = seconds;
        this.inputTokens = inputTokens;
        this.outputTokens = outputTokens;
        this.costUsd = costUsd;
    }

    public Long getId() { return id; }
    public Run getRun() { return run; }
    public String getTaskId() { return taskId; }
    public String getKind() { return kind; }
    public String getVendor() { return vendor; }
    public String getDifficulty() { return difficulty; }
    public boolean isPassed() { return passed; }
    public String getFailure() { return failure; }
    public String getDetail() { return detail; }
    public String getAnswer() { return answer; }
    public Double getFieldAccuracy() { return fieldAccuracy; }
    public int getSteps() { return steps; }
    public int getReplans() { return replans; }
    public double getSeconds() { return seconds; }
    public long getInputTokens() { return inputTokens; }
    public long getOutputTokens() { return outputTokens; }
    public Double getCostUsd() { return costUsd; }
}
