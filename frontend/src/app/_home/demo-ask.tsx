"use client";

import { LogIn, LoaderCircle, MessageSquareText } from "lucide-react";
import Link from "next/link";

import { GuestButton } from "@/components/guest-button";
import { useSession } from "@/lib/session";

import { AskChat } from "../memory/ask-chat";

const EXAMPLES = [
  "Where did I leave my keys?",
  "Who did I have coffee with?",
  "Where is my laptop charger?",
  "What did I eat for lunch?",
];

/**
 * Ask Gemma about the demo day. Asking needs a login (a one-click guest is enough), so the
 * backend can keep each user's own memories apart; the search is pinned to the demo collection.
 */
export function DemoAsk({ collection = "demo" }: { collection?: string }) {
  const { user } = useSession();

  if (user === undefined)
    return (
      <div
        className="flex min-h-[28rem] items-center justify-center gap-2 rounded-xl border border-border bg-card text-sm text-muted-foreground"
        role="status"
      >
        <LoaderCircle size={16} className="animate-spin" aria-hidden="true" />
      </div>
    );

  if (user === null)
    return (
      <div className="flex min-h-[28rem] flex-col items-center justify-center gap-5 rounded-xl border border-dashed border-border bg-card p-8 text-center">
        <MessageSquareText
          size={30}
          className="text-primary"
          aria-hidden="true"
        />
        <div className="flex max-w-md flex-col gap-2">
          <h3 className="text-lg font-semibold">
            Ask the glasses what they saw
          </h3>
          <p className="text-sm text-muted-foreground">
            Start a 30-minute guest session — no sign-up — and ask Gemma about
            the day above: where the keys went, who was at coffee, what was for
            lunch.
          </p>
        </div>
        <GuestButton />
        <Link
          href="/login?next=/"
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <LogIn size={15} aria-hidden="true" /> Log in instead
        </Link>
      </div>
    );

  return (
    <AskChat
      pinned={[collection]}
      examples={EXAMPLES}
      intro="Ask about the demo day. Gemma decides what to search for; every search it runs, and what it found, shows up here as it happens."
      className="h-[36rem]"
    />
  );
}
