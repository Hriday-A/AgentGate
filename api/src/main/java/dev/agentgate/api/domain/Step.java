package dev.agentgate.api.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;

/** One agent action in a task's trace. */
@Entity
@Table(name = "steps")
public class Step {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(optional = false)
    @JoinColumn(name = "task_result_id")
    private TaskResult taskResult;

    @Column(name = "step_no")
    private int stepNo;
    private String action;
    private String args;
    private String thought;
    private String outcome;
    private Boolean ok;
    private Boolean verified;
    private String feedback;
    private String url;
    private String screenshot;

    protected Step() {
    }

    public Step(TaskResult taskResult, int stepNo, String action, String args, String thought, String outcome,
                Boolean ok, Boolean verified, String feedback, String url, String screenshot) {
        this.taskResult = taskResult;
        this.stepNo = stepNo;
        this.action = action;
        this.args = args;
        this.thought = thought;
        this.outcome = outcome;
        this.ok = ok;
        this.verified = verified;
        this.feedback = feedback;
        this.url = url;
        this.screenshot = screenshot;
    }

    public Long getId() { return id; }
    public TaskResult getTaskResult() { return taskResult; }
    public int getStepNo() { return stepNo; }
    public String getAction() { return action; }
    public String getArgs() { return args; }
    public String getThought() { return thought; }
    public String getOutcome() { return outcome; }
    public Boolean getOk() { return ok; }
    public Boolean getVerified() { return verified; }
    public String getFeedback() { return feedback; }
    public String getUrl() { return url; }
    public String getScreenshot() { return screenshot; }
}
