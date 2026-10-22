import type { Metadata } from "next";
import { Header } from "@/components/Header";
import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "AgentGate",
  description: "Evaluation runs, traces and trends for browser agents",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <Header />
          <main>{children}</main>
        </Providers>
      </body>
    </html>
  );
}
