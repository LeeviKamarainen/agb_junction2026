"use client";

import { api } from "@/lib/api/browser";
import { AskChat } from "../memory/ask-chat";

const EXAMPLES = [
  "Where did I leave my keys?",
  "Who did I have coffee with?",
  "Where is my laptop charger?",
];

/** The search stays visible to visitors; their first question starts a temporary guest. */
export function DemoAsk() {
  async function ensureSession() {
    const session = await api.GET("/api/auth/session");
    if (!session.response.ok)
      throw new Error("Could not check your login. Please try again.");
    if (!session.data) {
      const created = await api.POST("/api/auth/guest");
      if (!created.data)
        throw new Error(
          "Could not start a guest session. Please log in and try again.",
        );
      window.dispatchEvent(new Event("session-changed"));
    }
  }
  return (
    <div className="mt-6">
      <AskChat
        compact
        beforeAsk={ensureSession}
        pinned={[]}
        examples={EXAMPLES}
        intro=""
        className=""
      />
      <p className="mt-3 text-xs text-muted-foreground">
        No account needed. Your first question starts a 60-minute guest session.
        Your private memories stay scoped to your login.
      </p>
    </div>
  );
}
