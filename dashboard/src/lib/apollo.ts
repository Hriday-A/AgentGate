import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";

export function makeClient() {
  return new ApolloClient({
    // Relative URL: Next.js rewrites /graphql to the API, and the session cookie rides along.
    link: new HttpLink({ uri: "/graphql", credentials: "same-origin" }),
    cache: new InMemoryCache(),
  });
}
