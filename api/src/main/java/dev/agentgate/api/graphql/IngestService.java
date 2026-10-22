package dev.agentgate.api.graphql;

import dev.agentgate.api.auth.Errors;
import dev.agentgate.api.domain.Agent;
import dev.agentgate.api.domain.Run;
import dev.agentgate.api.domain.RunRepository;
import dev.agentgate.api.domain.Step;
import dev.agentgate.api.domain.StepRepository;
import dev.agentgate.api.domain.TaskResult;
import dev.agentgate.api.domain.TaskResultRepository;
import dev.agentgate.api.graphql.Inputs.RunInput;
import dev.agentgate.api.graphql.Inputs.StepInput;
import dev.agentgate.api.graphql.Inputs.TaskResultInput;
import java.util.regex.Pattern;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** Stores a runner's results.json (plus traces) as one transaction. */
@Service
public class IngestService {

    static final Pattern SAFE_ID = Pattern.compile("[A-Za-z0-9._-]{1,100}");
    private static final int MAX_TASKS = 2000;
    private static final int MAX_STEPS = 500;

    private final RunRepository runs;
    private final TaskResultRepository results;
    private final StepRepository steps;

    public IngestService(RunRepository runs, TaskResultRepository results, StepRepository steps) {
        this.runs = runs;
        this.results = results;
        this.steps = steps;
    }

    @Transactional
    public Run ingest(Agent agent, RunInput in) {
        validate(in);
        // Idempotent: re-sending the same run (e.g. a retried CI job) updates it in place. The run keeps
        // its id, so dashboard links and stored screenshots stay valid; its task results are replaced.
        Run run = runs.findByAgentAndExternalId(agent, in.externalId()).orElse(null);
        if (run == null) {
            run = new Run(agent, in.externalId(), in.label());
        } else {
            results.deleteAll(results.findByRunOrderByTaskId(run));  // steps go with them (on delete cascade)
            results.flush();
            run.setLabel(in.label());
        }
        run.setConfig(in.agentKind(), in.model(), in.extractModel(), in.gitSha(), in.gitBranch());
        var s = in.summary();
        run.setMetrics(s.tasks(), s.passed(), s.successRate(), s.fieldAccuracy(), s.avgSteps(), s.p50Seconds(),
                s.p95Seconds(), (long) s.inputTokens(), (long) s.outputTokens(), s.totalCostUsd());
        runs.save(run);

        for (TaskResultInput t : in.results()) {
            TaskResult tr = results.save(new TaskResult(run, t.taskId(), t.kind(), t.vendor(), t.difficulty(),
                    t.passed(), t.failure(), t.detail(), t.answer(), t.fieldAccuracy(), t.steps(),
                    t.replans() == null ? 0 : t.replans(), t.seconds(), (long) t.inputTokens(),
                    (long) t.outputTokens(), t.costUsd()));
            if (t.trace() != null) {
                for (StepInput st : t.trace()) {
                    steps.save(new Step(tr, st.stepNo(), st.action(), st.args(), st.thought(), st.outcome(), st.ok(),
                            st.verified(), st.feedback(), truncate(st.url(), 2000), st.screenshot()));
                }
            }
        }
        return run;
    }

    private static void validate(RunInput in) {
        if (in.externalId() == null || !SAFE_ID.matcher(in.externalId()).matches()) {
            throw new Errors.BadRequest("externalId must be 1–100 characters of letters, digits, '.', '_' or '-'.");
        }
        if (in.label() == null || in.label().isBlank() || in.label().length() > 200) {
            throw new Errors.BadRequest("label must be 1–200 characters.");
        }
        if (in.results().size() > MAX_TASKS) {
            throw new Errors.BadRequest("Too many task results (max " + MAX_TASKS + ").");
        }
        for (TaskResultInput t : in.results()) {
            if (!SAFE_ID.matcher(t.taskId()).matches()) {
                throw new Errors.BadRequest("Invalid taskId: " + t.taskId());
            }
            if (t.trace() != null && t.trace().size() > MAX_STEPS) {
                throw new Errors.BadRequest("Too many steps in " + t.taskId() + " (max " + MAX_STEPS + ").");
            }
            if (t.trace() != null) {
                for (StepInput st : t.trace()) {
                    if (st.screenshot() != null && !SAFE_ID.matcher(st.screenshot()).matches()) {
                        throw new Errors.BadRequest("Invalid screenshot name in " + t.taskId());
                    }
                }
            }
        }
    }

    private static String truncate(String s, int max) {
        return s == null || s.length() <= max ? s : s.substring(0, max);
    }
}
