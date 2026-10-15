import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Bitter — Engineering Intelligence",
  description: "Your AI engineering advisor. Only what needs your attention.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
