package dev.agentgate.api.auth;

import java.util.List;
import org.springframework.security.authentication.AbstractAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;

/** A request authenticated by an agent's API key (the eval runner, not a person). */
public class ApiKeyAuthentication extends AbstractAuthenticationToken {

    private final Long agentId;

    public ApiKeyAuthentication(Long agentId) {
        super(List.of(new SimpleGrantedAuthority("ROLE_AGENT")));
        this.agentId = agentId;
        setAuthenticated(true);
    }

    public Long getAgentId() {
        return agentId;
    }

    @Override
    public Object getCredentials() {
        return "";
    }

    @Override
    public Object getPrincipal() {
        return agentId;
    }
}
