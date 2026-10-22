package dev.agentgate.api.graphql;

import dev.agentgate.api.auth.CurrentUser;
import dev.agentgate.api.auth.Errors;
import dev.agentgate.api.domain.Run;
import dev.agentgate.api.domain.RunRepository;
import dev.agentgate.api.domain.Step;
import dev.agentgate.api.domain.StepRepository;
import dev.agentgate.api.domain.TaskResult;
import dev.agentgate.api.domain.TaskResultRepository;
import dev.agentgate.api.graphql.Inputs.RunInput;
import dev.agentgate.api.graphql.Views.DifficultyStat;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import org.springframework.graphql.data.method.annotation.Argument;
import org.springframework.graphql.data.method.annotation.MutationMapping;
import org.springframework.graphql.data.method.annotation.QueryMapping;
import org.springframework.graphql.data.method.annotation.SchemaMapping;
import org.springframework.stereotype.Controller;

@Controller
public class RunController {

    private static final Map<String, Integer> DIFFICULTY_ORDER = Map.of("easy", 0, "medium", 1, "hard", 2);

    private final CurrentUser currentUser;
    private final RunRepository runs;
    private final TaskResultRepository results;
    private final StepRepository steps;
    private final IngestService ingest;

    public RunController(CurrentUser currentUser, RunRepository runs, TaskResultRepository results,
                         StepRepository steps, IngestService ingest) {
        this.currentUser = currentUser;
        this.runs = runs;
        this.results = results;
        this.steps = steps;
        this.ingest = ingest;
    }

    @QueryMapping
    public Run run(@Argument Long id) {
        return runs.findByIdAndAgent_Owner(id, currentUser.require())
                .orElseThrow(() -> new Errors.NotFound("Run not found."));
    }

    @QueryMapping
    public TaskResult taskResult(@Argument Long id) {
        return results.findByIdAndRun_Agent_Owner(id, currentUser.require())
                .orElseThrow(() -> new Errors.NotFound("Task result not found."));
    }

    @MutationMapping
    public Run ingestRun(@Argument RunInput input) {
        return ingest.ingest(currentUser.requireAgent(), input);
    }

    @SchemaMapping(typeName = "Run")
    public List<DifficultyStat> byDifficulty(Run run) {
        return results.difficultyStats(run).stream()
                .map(row -> {
                    int tasks = ((Number) row[1]).intValue();
                    int passed = ((Number) row[2]).intValue();
                    return new DifficultyStat((String) row[0], tasks, passed, tasks == 0 ? 0 : (double) passed / tasks);
                })
                .sorted(Comparator.comparing(s -> DIFFICULTY_ORDER.getOrDefault(s.difficulty(), 9)))
                .toList();
    }

    @SchemaMapping(typeName = "Run")
    public List<TaskResult> results(Run run, @Argument Boolean onlyFailed) {
        return Boolean.TRUE.equals(onlyFailed)
                ? results.findByRunAndPassedFalseOrderByTaskId(run)
                : results.findByRunOrderByTaskId(run);
    }

    @SchemaMapping(typeName = "TaskResult")
    public List<Step> trace(TaskResult taskResult) {
        return steps.findByTaskResultOrderByStepNo(taskResult);
    }

    @SchemaMapping(typeName = "Step")
    public String screenshotUrl(Step step) {
        if (step.getScreenshot() == null) {
            return null;
        }
        TaskResult tr = step.getTaskResult();
        return "/api/artifacts/" + tr.getRun().getId() + "/" + tr.getTaskId() + "/" + step.getScreenshot();
    }
}
