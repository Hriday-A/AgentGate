package dev.agentgate.api.auth;

import dev.agentgate.api.domain.AgentRepository;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Authenticates requests carrying an X-Api-Key header. The authentication lives only for this
 * request; nothing is written to a session, so API-key callers stay stateless.
 */
@Component
public class ApiKeyAuthenticationFilter extends OncePerRequestFilter {

    public static final String HEADER = "X-Api-Key";
    private final AgentRepository agents;

    public ApiKeyAuthenticationFilter(AgentRepository agents) {
        this.agents = agents;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String key = request.getHeader(HEADER);
        if (key == null || key.isBlank()) {
            chain.doFilter(request, response);
            return;
        }
        var agent = agents.findByApiKeyHash(ApiKeys.hash(key.strip()));
        if (agent.isEmpty()) {
            // A wrong key is an error, not "anonymous": fail fast so typos are obvious.
            response.sendError(HttpServletResponse.SC_UNAUTHORIZED, "Invalid API key");
            return;
        }
        var context = SecurityContextHolder.createEmptyContext();
        context.setAuthentication(new ApiKeyAuthentication(agent.get().getId()));
        SecurityContextHolder.setContext(context);
        try {
            chain.doFilter(request, response);
        } finally {
            SecurityContextHolder.clearContext();
        }
    }
}
