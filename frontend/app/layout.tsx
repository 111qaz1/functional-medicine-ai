import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Functional Medicine Review Console",
  description: "Grounded nutrition recommendation workspace for internal reviewers."
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
