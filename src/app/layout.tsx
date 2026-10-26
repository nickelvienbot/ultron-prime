import type { Metadata } from "next";
import { IDENTITY } from "@/core/identity";
import "./globals.css";

export const metadata: Metadata = {
  title: IDENTITY.name,
  description: `${IDENTITY.name} — ${IDENTITY.tagline}`,
  // No engine name appears in any metadata field. See src/core/identity/index.ts.
};

export const viewport = {
  themeColor: "#0b0d0f",
  colorScheme: "dark" as const,
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}