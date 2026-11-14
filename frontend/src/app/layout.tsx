import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Glass memory — your glasses remember the day",
  description:
    "A visual memory for smart glasses: watch a recorded day play back as a timeline and ask Gemma about what the glasses saw.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // Extensions such as the Meta Ray-Ban Display Simulator add attributes to <html> before React
    // loads; this silences the hydration warning for this one element only, not its children.
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} dark h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
