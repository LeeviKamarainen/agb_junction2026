import { ArrowLeft } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { AuthForm } from "./auth-form";

export const metadata: Metadata = {
  title: "Log in — Junction / 26",
  description:
    "Start a 60-minute guest session or return to your temporary account with its username.",
};

type LoginParams = Promise<{ admin?: string }>;

async function LoginForm({ searchParams }: { searchParams: LoginParams }) {
  const { admin } = await searchParams;
  return <AuthForm admin={admin === "1"} />;
}

export default function LoginPage({
  searchParams,
}: {
  searchParams: LoginParams;
}) {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-6 px-4 py-10">
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
        <LoginForm searchParams={searchParams} />
      </Suspense>
      <Link
        href="/"
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft size={15} aria-hidden="true" /> Home
      </Link>
    </main>
  );
}
