package dev.agentgate.api.domain;

import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface UserRepository extends JpaRepository<AppUser, Long> {
    Optional<AppUser> findByGithubId(Long githubId);

    Optional<AppUser> findByLogin(String login);
}
