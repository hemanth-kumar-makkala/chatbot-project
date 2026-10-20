import type { ReactNode } from "react";
import "../styles/globals.css";
import { APP_NAME } from "@/lib/appName";

export const metadata = {
  title: APP_NAME,
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
