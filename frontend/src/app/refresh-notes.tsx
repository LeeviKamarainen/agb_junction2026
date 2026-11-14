"use client";

import { RefreshCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useTransition } from "react";

export function RefreshNotes() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  return (
    <button
      className="post-button"
      type="button"
      disabled={pending}
      onClick={() => startTransition(() => router.refresh())}
    >
      {pending ? "Trying again…" : "Try again"}
      <RefreshCw
        size={16}
        className={pending ? "spinner" : undefined}
        aria-hidden="true"
      />
    </button>
  );
}
