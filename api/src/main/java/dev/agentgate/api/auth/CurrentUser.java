package dev.agentgate.api.auth;

import dev.agentgate.api.domain.Agent;
import dev.agentgate.api.domain.AgentRepository;
import dev.agentgate.api.domain.AppUser;
import dev.agentgate.api.domain.UserRepository;
import java.util.Optional;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.client.authentication.OAuth2AuthenticationToken;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/** Resolves "who is calling" for both kinds of caller: a signed-in person, or an agent's API key. */
@Component
public class CurrentUser {

    private final UserRepository users;
    private final AgentRepository agents;

    public CurrentUser(UserRepository users, AgentRepository agents) {
        this.users = users;
        this.agents = agents;
    }

    /** The signed-in person, if any. GitHub profiles are upserted so name/avatar stay current. */
    @Transactional
    public Optional<AppUser> find() {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth instanceof OAuth2AuthenticationToken token) {
            var attrs = token.getPrincipal().getAttributes();
            Long githubId = ((Number) attrs.get("id")).longValue();
            String login = (String) attrs.get("login");
            String name = (String) attrs.get("name");
            String avatar = (String) attrs.get("avatar_url");
            AppUser user = users.findByGithubId(githubId)
                    .orElseGet(() -> users.save(new AppUser(githubId, login, name, avatar)));
            user.updateProfile(login, name, avatar);
            return Optional.of(user);
        }
        if (auth != null && auth.getPrincipal() instanceof DevPrincipal dev) {
            return users.findByLogin(dev.login());
        }
        return Optional.empty();
    }

    public AppUser require() {
        return find().orElseThrow(() -> new Errors.Unauthorized("Sign in first."));
    }

    /** The agent whose API key authenticated this request. */
    public Agent requireAgent() {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth instanceof ApiKeyAuthentication key) {
            return agents.findById(key.getAgentId())
                    .orElseThrow(() -> new Errors.Unauthorized("Agent no longer exists."));
        }
        throw new Errors.Unauthorized("This operation needs an agent API key (X-Api-Key header).");
    }
}
