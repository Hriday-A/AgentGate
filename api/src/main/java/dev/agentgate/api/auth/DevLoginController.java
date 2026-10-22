package dev.agentgate.api.auth;

import dev.agentgate.api.config.AppProperties;
import dev.agentgate.api.domain.AppUser;
import dev.agentgate.api.domain.UserRepository;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.http.HttpStatus;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.client.registration.ClientRegistrationRepository;
import org.springframework.security.web.context.HttpSessionSecurityContextRepository;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
public class DevLoginController {

    private final AppProperties props;
    private final UserRepository users;
    private final ObjectProvider<ClientRegistrationRepository> oauthClients;
    private final HttpSessionSecurityContextRepository sessions = new HttpSessionSecurityContextRepository();

    public DevLoginController(AppProperties props, UserRepository users,
                              ObjectProvider<ClientRegistrationRepository> oauthClients) {
        this.props = props;
        this.users = users;
        this.oauthClients = oauthClients;
    }

    /** Lets the dashboard show the right sign-in buttons. */
    @GetMapping("/auth/providers")
    public Map<String, Boolean> providers() {
        return Map.of("github", oauthClients.getIfAvailable() != null, "dev", props.devLogin());
    }

    /** Signs in as a local user without GitHub. Disabled unless agentgate.dev-login=true. */
    @PostMapping("/dev/login")
    public Map<String, String> devLogin(@RequestParam(defaultValue = "demo") String login,
                                        HttpServletRequest request, HttpServletResponse response) {
        if (!props.devLogin()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND);
        }
        if (!login.matches("[A-Za-z0-9-]{1,39}")) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Invalid login");
        }
        AppUser user = users.findByLogin(login).orElseGet(() -> users.save(new AppUser(null, login, login, null)));
        var auth = UsernamePasswordAuthenticationToken.authenticated(
                new DevPrincipal(user.getLogin()), null, List.of(new SimpleGrantedAuthority("ROLE_USER")));
        if (request.getSession(false) != null) {
            request.changeSessionId();  // new session id on sign-in prevents session fixation
        }
        var context = SecurityContextHolder.createEmptyContext();
        context.setAuthentication(auth);
        SecurityContextHolder.setContext(context);
        sessions.saveContext(context, request, response);  // persist in the HTTP session
        return Map.of("login", user.getLogin());
    }
}
