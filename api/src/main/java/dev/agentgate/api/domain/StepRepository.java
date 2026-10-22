package dev.agentgate.api.domain;

import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface StepRepository extends JpaRepository<Step, Long> {
    List<Step> findByTaskResultOrderByStepNo(TaskResult taskResult);
}
