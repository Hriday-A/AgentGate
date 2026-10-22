package dev.agentgate.api.graphql;

import dev.agentgate.api.auth.Errors;
import graphql.GraphQLError;
import graphql.GraphqlErrorBuilder;
import graphql.schema.DataFetchingEnvironment;
import org.springframework.graphql.execution.DataFetcherExceptionResolverAdapter;
import org.springframework.graphql.execution.ErrorType;
import org.springframework.stereotype.Component;

/** Turns our exceptions into typed GraphQL errors; anything else stays a generic INTERNAL_ERROR. */
@Component
public class GraphQlErrors extends DataFetcherExceptionResolverAdapter {

    @Override
    protected GraphQLError resolveToSingleError(Throwable ex, DataFetchingEnvironment env) {
        ErrorType type;
        if (ex instanceof Errors.Unauthorized) {
            type = ErrorType.UNAUTHORIZED;
        } else if (ex instanceof Errors.NotFound) {
            type = ErrorType.NOT_FOUND;
        } else if (ex instanceof Errors.BadRequest) {
            type = ErrorType.BAD_REQUEST;
        } else {
            return null;  // fall through to the default handler, which hides internal details
        }
        return GraphqlErrorBuilder.newError(env).errorType(type).message(ex.getMessage()).build();
    }
}
