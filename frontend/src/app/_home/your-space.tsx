"use client";

import { Camera, Images, Sparkles } from "lucide-react";

import { useSession } from "@/lib/session";

const PLACEHOLDERS = [
  {
    icon: Camera,
    title: "Your camera",
    text: "Use your own webcam or phone as the glasses: frames are embedded as your memories while you move around.",
  },
  {
    icon: Images,
    title: "Your memories",
    text: "Everything you have captured, on your own timeline next to the demo day. Only you (and the admin) see it.",
  },
  {
    icon: Sparkles,
    title: "Your triggers",
    text: "Tell the glasses what to watch for in your own frames, and what to say when they see it.",
  },
];

/** The logged-in user's own corner of the demo. Placeholders for now; nothing for visitors. */
export function YourSpace() {
  const { user } = useSession();
  if (!user) return null;

  return (
    <section
      aria-labelledby="your-space-title"
      className="flex flex-col gap-5 border-t border-border py-12"
    >
      <div className="flex flex-col gap-2">
        <p className="eyebrow">04 / YOUR SPACE</p>
        <h2
          id="your-space-title"
          className="text-3xl font-semibold tracking-tight"
        >
          Make it yours, {user.username}.
        </h2>
        <p className="max-w-2xl text-sm text-muted-foreground">
          {user.role === "admin"
            ? "You are the admin: everything here is also in the Memory lab."
            : "Your guest session keeps what you add for 30 minutes, then deletes it."}
        </p>
      </div>
      <ul className="grid gap-4 md:grid-cols-3">
        {PLACEHOLDERS.map(({ icon: Icon, title, text }) => (
          <li
            key={title}
            className="flex min-h-48 flex-col gap-3 rounded-xl border border-dashed border-border bg-card/60 p-5"
          >
            <span className="flex items-center justify-between">
              <Icon size={22} className="text-primary" aria-hidden="true" />
              <span className="rounded-full border border-border px-2 py-0.5 font-mono text-[10px] tracking-wider text-muted-foreground">
                COMING SOON
              </span>
            </span>
            <h3 className="font-semibold">{title}</h3>
            <p className="text-sm text-muted-foreground">{text}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
