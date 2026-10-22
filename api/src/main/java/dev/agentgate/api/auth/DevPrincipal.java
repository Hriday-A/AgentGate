package dev.agentgate.api.auth;

import java.io.Serializable;

/** Principal for local development sign-in (see DevLoginController). */
public record DevPrincipal(String login) implements Serializable {
}
