package dev.agentgate.api.domain;

import java.util.List;
import java.util.Optional;
import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.JpaRepository;

public interface RunRepository extends JpaRepository<Run, Long> {
    List<Run> findByAgentOrderByCreatedAtDesc(Agent agent, Limit limit);

    Optional<Run> findByAgentAndExternalId(Agent agent, String externalId);

    /** Ownership is part of the query, so another user's run is simply "not found". */
    Optional<Run> findByIdAndAgent_Owner(Long id, AppUser owner);
}
