package dev.agentgate.api.domain;

import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface AgentRepository extends JpaRepository<Agent, Long> {
    List<Agent> findByOwnerOrderByCreatedAtDesc(AppUser owner);

    Optional<Agent> findByApiKeyHash(String apiKeyHash);

    boolean existsByOwnerAndName(AppUser owner, String name);

    Optional<Agent> findByIdAndOwner(Long id, AppUser owner);
}
