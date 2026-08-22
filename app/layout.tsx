import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Archaeologist — Python repository explorer",
  description: "Explore the files and structure of an analyzed Python repository.",
  icons: { icon: "/favicon.svg", shortcut: "/favicon.svg" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
