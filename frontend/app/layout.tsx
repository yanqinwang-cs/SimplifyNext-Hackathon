import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SimplifyNext | Investigator workspace",
  description: "A focused investigator workspace for academic-integrity review.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
