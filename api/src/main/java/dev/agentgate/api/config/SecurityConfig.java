package dev.agentgate.api.config;

import dev.agentgate.api.auth.ApiKeyAuthenticationFilter;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpStatus;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.oauth2.client.registration.ClientRegistrationRepository;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.AnonymousAuthenticationFilter;
import org.springframework.security.web.authentication.HttpStatusEntryPoint;

@Configuration
@EnableConfigurationProperties(AppProperties.class)
public class SecurityConfig {

    @Bean
    SecurityFilterChain filterChain(HttpSecurity http, ApiKeyAuthenticationFilter apiKeyFilter,
                                    ObjectProvider<ClientRegistrationRepository> oauthClients,
                                    AppProperties props) throws Exception {
        http
                // CSRF tokens are off because cross-site requests can't carry our credentials:
                // the session cookie is SameSite=Lax (not sent on cross-site POSTs), /graphql only
                // accepts application/json (which needs a CORS preflight we never approve), and
                // the runner authenticates with an X-Api-Key header that browsers never add.
                .csrf(csrf -> csrf.disable())
                .authorizeHttpRequests(auth -> auth
                        // Resolvers and controllers check identity themselves, so one endpoint can
                        // serve both signed-in users and API-key callers.
                        .requestMatchers("/graphql", "/api/artifacts/**", "/dev/login", "/auth/providers",
                                "/actuator/health").permitAll()
                        .anyRequest().authenticated())
                .addFilterBefore(apiKeyFilter, AnonymousAuthenticationFilter.class)
                .exceptionHandling(e -> e.authenticationEntryPoint(new HttpStatusEntryPoint(HttpStatus.UNAUTHORIZED)))
                .logout(logout -> logout.logoutUrl("/logout").logoutSuccessUrl(props.appUrl() + "/"));

        // GitHub sign-in is enabled only when the "github" profile supplies OAuth credentials.
        if (oauthClients.getIfAvailable() != null) {
            http.oauth2Login(oauth -> oauth.defaultSuccessUrl(props.appUrl() + "/", true));
        }
        return http.build();
    }
}
