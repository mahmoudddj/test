import "./globals.css";
import type { ReactNode } from "react";
import Nav from "@/components/Nav";

export const metadata = { title: "IRT-CRI Research Platform", description: "IRT-in-CRI Study research data platform" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Nav />
        <main>{children}</main>
      </body>
    </html>
  );
}
