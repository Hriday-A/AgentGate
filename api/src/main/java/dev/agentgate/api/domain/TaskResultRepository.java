package dev.agentgate.api.domain;

import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

public interface TaskResultRepository extends JpaRepository<TaskResult, Long> {
    List<TaskResult> findByRunOrderByTaskId(Run run);

    Optional<TaskResult> findByIdAndRun_Agent_Owner(Long id, AppUser owner);

    Optional<TaskResult> findByRunAndTaskId(Run run, String taskId);

    List<TaskResult> findByRunAndPassedFalseOrderByTaskId(Run run);

    /** Rows of (difficulty, tasks, passed) for one run. */
    @Query("""
            select t.difficulty, count(t), sum(case when t.passed = true then 1 else 0 end)
            from TaskResult t where t.run = :run group by t.difficulty""")
    List<Object[]> difficultyStats(Run run);
}
