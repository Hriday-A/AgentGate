"use client";

import Link from "next/link";
import { useQuery } from "@apollo/client/react";
import { ME } from "@/lib/queries";
import type { User } from "@/lib/types";

export function Header() {
  const { data } = useQuery<{ me: User | null }>(ME);
  return (
    <header className="topbar">
      <Link href="/" className="brand">AgentGate</Link>
      {data?.me && (
        <nav>
          <span className="muted">{data.me.login}</span>
          <form action="/logout" method="post"><button className="link">Sign out</button></form>
        </nav>
      )}
    </header>
  );
}
