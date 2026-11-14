import { ArrowLeft } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { AuthForm } from "../login/auth-form";

export const metadata: Metadata = {
  title: "Register — Junction / 26",
  description:
    "Create a guest account: see the demo pictures and add your own memories.",
};

export default function RegisterPage() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-6 px-4 py-10">
      <AuthForm mode="register" />
      <Link
        href="/"
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft size={15} aria-hidden="true" /> Home
      </Link>
    </main>
  );
}
