"use client";

import { ApolloProvider } from "@apollo/client/react";
import { useState } from "react";
import { makeClient } from "@/lib/apollo";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(makeClient);  // one client per browser tab
  return <ApolloProvider client={client}>{children}</ApolloProvider>;
}
