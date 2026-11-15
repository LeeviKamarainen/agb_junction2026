"use client";

import { Mic } from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { type Recognition, recognitionConstructor, transcripts } from "../glasses/speech";

const subscribeToNothing = () => () => {};

/**
 * Hold to talk: the microphone listens while the button is held, and on release the words go to
 * `onText`. Shows what it hears as it hears it. Renders nothing in a browser without speech
 * recognition (Firefox): typing still works.
 */
export function HoldToTalk({
  onText,
  disabled = false,
  className = "",
  label = "Hold to talk",
  onListening,
}: {
  onText: (text: string) => void;
  /** The microphone opened or closed, e.g. for the glasses' recording light. */
  onListening?: (listening: boolean) => void;
  disabled?: boolean;
  className?: string;
  label?: string;
}) {
  const supported = useSyncExternalStore(
    subscribeToNothing,
    () => recognitionConstructor() !== null,
    () => false,
  );
  const recognition = useRef<Recognition | null>(null);
  const final = useRef("");
  const [heard, setHeard] = useState("");
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => () => recognition.current?.abort(), []);

  function start() {
    if (recognition.current || disabled) return;
    const Recognition = recognitionConstructor();
    if (!Recognition) return;
    const listener = new Recognition();
    listener.lang = "en-US";
    listener.continuous = true;
    listener.interimResults = true;
    final.current = "";
    listener.onresult = (event) => {
      const words = transcripts(event);
      final.current = words.final;
      setHeard([words.final, words.interim].filter(Boolean).join(" "));
    };
    listener.onerror = (event) => {
      if (event.error === "no-speech" || event.error === "aborted") return;
      setError(
        event.error === "not-allowed"
          ? "Microphone access was blocked."
          : event.error === "network"
            ? "This browser can’t reach its speech service. Use Chrome, Edge or Safari, or type."
            : `Speech recognition error: ${event.error}.`,
      );
    };
    listener.onend = () => {
      recognition.current = null;
      setListening(false);
      onListening?.(false);
      setHeard("");
      const text = final.current.trim();
      if (text) onText(text);
    };
    recognition.current = listener;
    setError(null);
    setHeard("");
    setListening(true);
    onListening?.(true);
    listener.start();
  }

  // Stopping, not aborting, lets the last words settle; onend sends them.
  const stop = () => recognition.current?.stop();

  if (!supported) return null;
  return (
    <span className="relative inline-flex">
      <button
        type="button"
        disabled={disabled}
        aria-pressed={listening}
        onPointerDown={(event) => {
          event.preventDefault(); // no text selection or context menu on a long press
          start();
        }}
        onPointerUp={stop}
        onPointerLeave={stop}
        onPointerCancel={stop}
        onContextMenu={(event) => event.preventDefault()}
        onKeyDown={(event) => {
          if ((event.key === " " || event.key === "Enter") && !event.repeat) {
            event.preventDefault();
            start();
          }
        }}
        onKeyUp={(event) => {
          if (event.key === " " || event.key === "Enter") stop();
        }}
        className={`inline-flex touch-none select-none items-center justify-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium transition-colors disabled:opacity-50 ${
          listening
            ? "bg-red-600 text-white"
            : "bg-secondary text-secondary-foreground hover:bg-secondary/80"
        } ${className}`}
      >
        <Mic size={16} aria-hidden="true" />
        {listening ? "Listening… release to ask" : label}
      </button>
      {(heard || error) && (
        <span
          role="status"
          className={`absolute bottom-full left-0 mb-2 w-max max-w-[18rem] rounded-md bg-black/80 px-2 py-1 text-xs ${error ? "text-red-300" : "text-white"}`}
        >
          {error ?? `“${heard}”`}
        </span>
      )}
    </span>
  );
}
