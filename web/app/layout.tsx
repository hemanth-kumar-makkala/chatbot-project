import type { ReactNode } from "react";
import "../styles/globals.css";

export const metadata = {
  title: "Staffingly Knowledge Assistant",
  description: "Internal staff tool for managing client document knowledge bases",
};

export default function RootLayout({
  children,
}: {
  children: ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body suppressHydrationWarning>{children}</body>
    </html>
  );
}
