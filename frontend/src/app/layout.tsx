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
  title: "Recall/Remind — a private visual memory for your Meta glasses",
  description:
    "Give your Meta glasses a private visual memory and real-time notifications triggered from visual cues.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // Extensions such as the Meta Ray-Ban Display Simulator add attributes to <html> before React
    // loads; this silences the hydration warning for this one element only, not its children.
    <html
      lang="en"
      data-scroll-behavior="smooth"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} dark h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
