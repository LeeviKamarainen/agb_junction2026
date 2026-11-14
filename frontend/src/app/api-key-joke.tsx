"use client";

import { Check, Copy, Eye, EyeOff, KeyRound } from "lucide-react";
import { useState } from "react";

// Deliberately fictional. Never replace this joke with a real credential.
const FAKE_KEY = "NOT_A_KEY_ilmari_owes_us_coffee";

export function ApiKeyJoke() {
  const [revealed, setRevealed] = useState(false);
  const [copied, setCopied] = useState(false);
  const [message, setMessage] = useState("");

  async function copyJoke() {
    try {
      await navigator.clipboard.writeText(FAKE_KEY);
      setCopied(true);
      setMessage("Fake key copied. Redeem with Ilmari for absolutely nothing.");
    } catch {
      setMessage(
        "Clipboard unavailable. You can select and copy the fake key above.",
      );
    }
  }

  return (
    <aside className="key-panel" aria-labelledby="key-title">
      <div className="key-panel-top">
        <span className="eyebrow">VERY CLASSIFIED™</span>
        <KeyRound size={18} aria-hidden="true" />
      </div>
      <div className="key-heading">
        <span className="key-kicker">A GIFT TO THE INTERNET</span>
        <h3 id="key-title">
          Ilmari’s
          <br />
          API key<span>.</span>
        </h3>
        <p>
          Because every great project needs
          <br />
          one questionable decision.
        </p>
      </div>
      <div className="fake-terminal">
        <div className="terminal-heading">
          <span>
            <i />
            <i />
            <i />
          </span>
          <span>definitely-not-a-secret.env</span>
        </div>
        <div className="terminal-content">
          <span className="terminal-comment"># 100% fake. 0% billable.</span>
          <span className="terminal-variable">ILMARI_API_KEY=</span>
          <code id="fake-key-value">
            {revealed ? FAKE_KEY : "••••••••••••••••••••"}
          </code>
        </div>
        <div className="key-controls">
          <button
            type="button"
            onClick={() => {
              setRevealed(!revealed);
              setMessage("");
              setCopied(false);
            }}
            aria-expanded={revealed}
            aria-controls="fake-key-value"
          >
            {revealed ? (
              <EyeOff size={15} aria-hidden="true" />
            ) : (
              <Eye size={15} aria-hidden="true" />
            )}
            {revealed ? "Hide the evidence" : "Reveal the secret"}
          </button>
          {revealed && (
            <button
              className="copy-key"
              type="button"
              onClick={copyJoke}
              aria-label="Copy the fake API key"
            >
              {copied ? (
                <Check size={15} aria-hidden="true" />
              ) : (
                <Copy size={15} aria-hidden="true" />
              )}
            </button>
          )}
        </div>
      </div>
      <p className="key-disclaimer">
        Relax, it’s a joke. Real keys stay secret.
        <br />
        <span>Ilmari’s coffee budget, however, is public.</span>
      </p>
      <p className="key-feedback" role="status">
        {message}
      </p>
    </aside>
  );
}
