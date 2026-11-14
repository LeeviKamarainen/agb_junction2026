"use client";

import {
  AlertTriangle,
  Bot,
  CornerDownRight,
  LoaderCircle,
  MessageSquareText,
  Search,
  SendHorizontal,
  Sparkles,
} from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";

import { errorText, formatTime, type SearchResult, type Step } from "./types";

type Exchange = {
  id: number;
  question: string;
  steps: Step[];
  done: boolean;
};

const EXAMPLES = [
  "Where did I leave my keys?",
  "What did I see this morning?",
  "Who did I have coffee with?",
];

export function AskChat() {
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [exchanges]);

  function addStep(id: number, step: Step, done = false) {
    setExchanges((current) =>
      current.map((exchange) =>
        exchange.id === id
          ? {
              ...exchange,
              steps: [...exchange.steps, step],
              done: done || exchange.done,
            }
          : exchange,
      ),
    );
  }

  async function ask(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    const id = Date.now();
    setExchanges((current) => [
      ...current,
      { id, question: trimmed, steps: [], done: false },
    ]);
    setQuestion("");
    setBusy(true);
    const fail = (content: string) =>
      addStep(id, { type: "error", round: 0, elapsed_ms: 0, content }, true);

    try {
      const response = await fetch("/api/ask/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: trimmed }),
      });
      if (!response.ok || !response.body) {
        const body = await response.json().catch(() => null);
        fail(errorText(body, `The backend answered ${response.status}`));
        return;
      }
      // Newline-delimited JSON: one Step per line, as the backend produces them.
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = "";
      let finished = false;
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += value;
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.trim()) continue;
          const step = JSON.parse(line) as Step;
          finished ||= step.type === "answer" || step.type === "error";
          addStep(id, step, finished);
        }
      }
      if (!finished) fail("The stream ended without an answer.");
    } catch (error) {
      fail(error instanceof Error ? error.message : "Could not reach the backend");
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void ask(question);
  }

  return (
    <section
      aria-labelledby="chat-title"
      className="flex min-h-[70vh] flex-col rounded-xl border border-border bg-card"
    >
      <div className="flex items-center justify-between gap-2 border-b border-border p-4">
        <h2 id="chat-title" className="flex items-center gap-2 font-semibold">
          <Bot size={18} aria-hidden="true" /> Ask Gemma
        </h2>
        {exchanges.length > 0 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setExchanges([])}
            disabled={busy}
          >
            Clear
          </Button>
        )}
      </div>

      <div
        className="flex flex-1 flex-col gap-6 overflow-y-auto p-4"
        aria-live="polite"
      >
        {exchanges.length === 0 && (
          <div className="m-auto flex max-w-sm flex-col items-center gap-3 text-center text-sm text-muted-foreground">
            <MessageSquareText size={28} aria-hidden="true" />
            <p>
              Ask about your memories. Gemma decides what to search for; each
              tool call, its arguments and the results appear here as they
              happen.
            </p>
            <div className="flex flex-wrap justify-center gap-2">
              {EXAMPLES.map((example) => (
                <Button
                  key={example}
                  variant="outline"
                  size="sm"
                  onClick={() => void ask(example)}
                  disabled={busy}
                >
                  {example}
                </Button>
              ))}
            </div>
          </div>
        )}
        {exchanges.map((exchange) => (
          <ExchangeView key={exchange.id} exchange={exchange} />
        ))}
        <div ref={bottom} />
      </div>

      <form
        onSubmit={submit}
        className="flex items-end gap-2 border-t border-border p-3"
      >
        <label htmlFor="question" className="sr-only">
          Your question
        </label>
        <textarea
          id="question"
          rows={2}
          maxLength={2000}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void ask(question);
            }
          }}
          placeholder="Where did I leave my keys?"
          className="flex-1 resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus-visible:border-primary"
          disabled={busy}
        />
        <Button type="submit" size="lg" disabled={busy || !question.trim()}>
          {busy ? (
            <LoaderCircle className="animate-spin" aria-hidden="true" />
          ) : (
            <SendHorizontal aria-hidden="true" />
          )}
          Ask
        </Button>
      </form>
    </section>
  );
}

function ExchangeView({ exchange }: { exchange: Exchange }) {
  const last = exchange.steps.at(-1);
  const final = last?.type === "answer" ? last : undefined;
  const process = final ? exchange.steps.slice(0, -1) : exchange.steps;

  return (
    <article className="flex flex-col gap-3">
      <p className="self-end rounded-2xl rounded-br-sm bg-primary px-4 py-2 text-sm text-primary-foreground">
        {exchange.question}
      </p>

      <ol className="flex flex-col gap-2 border-l border-border pl-4">
        {process.map((step, index) => (
          <li key={index}>
            <StepView step={step} />
          </li>
        ))}
        {!exchange.done && (
          <li className="flex items-center gap-2 text-xs text-muted-foreground">
            <LoaderCircle size={14} className="animate-spin" aria-hidden="true" />
            Working…
          </li>
        )}
      </ol>

      {final && (
        <div className="flex flex-col gap-1 self-start rounded-2xl rounded-bl-sm bg-secondary px-4 py-3 text-sm">
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Sparkles size={13} aria-hidden="true" /> Answer ·{" "}
            {(final.elapsed_ms / 1000).toFixed(1)} s
          </span>
          <p className="whitespace-pre-wrap">
            {final.content || "(Gemma returned an empty answer)"}
          </p>
        </div>
      )}
    </article>
  );
}

function StepView({ step }: { step: Step }) {
  const time = (
    <span className="text-muted-foreground">
      {(step.elapsed_ms / 1000).toFixed(1)} s
    </span>
  );

  switch (step.type) {
    case "model_call":
      return (
        <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <Bot size={14} aria-hidden="true" />
          Gemma call {step.round}
          {step.tools_offered
            ? " · vector_search offered"
            : " · last round, tool withheld so it must answer"}
          {step.images_sent ? ` · ${step.images_sent} images attached` : ""}
          {" · "}
          {time}
        </p>
      );
    case "model_text":
      return (
        <p className="text-xs whitespace-pre-wrap text-muted-foreground italic">
          Gemma: {step.content}
        </p>
      );
    case "tool_call":
      return <ToolCallView step={step} />;
    case "tool_result":
      return <ResultsView results={step.results ?? []} />;
    case "tool_error":
    case "error":
      return (
        <div
          className="flex gap-2 rounded-md bg-destructive/10 p-2 text-xs text-destructive"
          role="alert"
        >
          <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          <div className="min-w-0">
            <p className="font-medium">
              {step.type === "error"
                ? "Failed"
                : "The tool call was rejected and sent back to Gemma"}
            </p>
            <pre className="mt-1 whitespace-pre-wrap break-words font-mono">
              {step.content}
            </pre>
          </div>
        </div>
      );
    default:
      return null;
  }
}

type SearchArguments = {
  user_query?: string;
  alternative_queries?: string[];
  keywords?: string[];
  timespan?: { start?: string; end?: string } | null;
  kinds?: string[];
  limit?: number;
};

function ToolCallView({ step }: { step: Step }) {
  const args = (
    step.arguments && typeof step.arguments === "object" ? step.arguments : {}
  ) as SearchArguments;

  return (
    <div className="flex flex-col gap-1.5 rounded-md border border-border bg-background/60 p-2 text-xs">
      <p className="flex items-center gap-1.5 font-mono text-primary">
        <Search size={13} aria-hidden="true" />
        {step.name}()
        {step.call_id && (
          <span className="text-muted-foreground">· {step.call_id}</span>
        )}
      </p>
      {args.user_query && (
        <p>
          <span className="text-muted-foreground">query </span>
          {args.user_query}
        </p>
      )}
      <Chips label="also" items={args.alternative_queries} />
      <Chips label="keywords" items={args.keywords} />
      {args.timespan && (args.timespan.start || args.timespan.end) && (
        <p>
          <span className="text-muted-foreground">time </span>
          {args.timespan.start ? formatTime(args.timespan.start) : "…"} →{" "}
          {args.timespan.end ? formatTime(args.timespan.end) : "now"}
        </p>
      )}
      <Chips label="kinds" items={args.kinds} />
      {args.limit && (
        <p>
          <span className="text-muted-foreground">limit </span>
          {args.limit}
        </p>
      )}
      <details>
        <summary className="cursor-pointer text-muted-foreground">
          Raw arguments
        </summary>
        <pre className="mt-1 overflow-x-auto font-mono text-[11px]">
          {JSON.stringify(step.arguments, null, 2)}
        </pre>
      </details>
    </div>
  );
}

function Chips({ label, items }: { label: string; items?: string[] }) {
  if (!items?.length) return null;
  return (
    <p className="flex flex-wrap items-center gap-1">
      <span className="text-muted-foreground">{label} </span>
      {items.map((item) => (
        <span key={item} className="rounded bg-secondary px-1.5 py-0.5">
          {item}
        </span>
      ))}
    </p>
  );
}

function ResultsView({ results }: { results: SearchResult[] }) {
  if (results.length === 0) {
    return (
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <CornerDownRight size={13} aria-hidden="true" /> No memories matched.
        Gemma gets an empty list.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <CornerDownRight size={13} aria-hidden="true" /> {results.length} result
        {results.length === 1 ? "" : "s"}, best first
      </p>
      <ul className="flex gap-2 overflow-x-auto pb-1">
        {results.map((result, index) => (
          <li
            key={result.id}
            className="flex w-32 shrink-0 flex-col overflow-hidden rounded-md border border-border bg-background/60 text-[11px]"
            title={result.text || undefined}
          >
            {result.image_url ? (
              /* eslint-disable-next-line @next/next/no-img-element -- served by the API */
              <img
                src={result.image_url}
                alt={result.text || `Memory ${result.id}`}
                className="aspect-[4/3] w-full object-cover"
              />
            ) : (
              <p className="line-clamp-4 aspect-[4/3] p-1.5">{result.text}</p>
            )}
            <div className="flex flex-col p-1.5 text-muted-foreground">
              <span className="text-foreground">
                {index + 1}. #{result.id} · {result.kind}
              </span>
              <span>{formatTime(result.created_at)}</span>
              <span>
                {result.distance === null
                  ? "keyword only"
                  : `distance ${result.distance.toFixed(3)}`}
              </span>
              {result.matched_keywords.length > 0 && (
                <span className="truncate">
                  matched {result.matched_keywords.join(", ")}
                </span>
              )}
              <span>score {result.score.toFixed(4)}</span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
