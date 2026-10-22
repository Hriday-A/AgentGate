package dev.agentgate.api.auth;

/** Exceptions the GraphQL layer maps to typed errors (see graphql/GraphQlErrors). */
public final class Errors {

    private Errors() {
    }

    public static class Unauthorized extends RuntimeException {
        public Unauthorized(String message) {
            super(message);
        }
    }

    /** Also used for "exists but isn't yours", so other users' resources aren't revealed. */
    public static class NotFound extends RuntimeException {
        public NotFound(String message) {
            super(message);
        }
    }

    public static class BadRequest extends RuntimeException {
        public BadRequest(String message) {
            super(message);
        }
    }
}
