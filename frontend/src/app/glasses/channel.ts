import type { GlassesReply } from "./types";

/**
 * The glasses' WebSocket, /api/glasses/ws. The protocol is in backend/app/glasses/channel.py;
 * these types mirror its pydantic models (a WebSocket is not in the OpenAPI schema, so
 * `make api-types` cannot generate them).
 */
export type ClientMessage =
  | ({ type: "command"; id: number } & (
      { action: string } | { question: string }
    ))
  | { type: "ping"; id: number }
  | { type: "auth"; token: string };

export type ServerMessage =
  | { type: "reply"; id: number | null; reply: GlassesReply }
  | { type: "error"; id: number | null; status: number; detail: string }
  | { type: "pong"; id: number | null };

export type SocketState =
  | { state: "connecting"; attempt: number }
  | { state: "open" }
  /** `retryInMs` is null when retrying cannot help: the backend refused the login. */
  | { state: "closed"; retryInMs: number | null; reason: string };

type Handlers = {
  onState: (state: SocketState) => void;
  onMessage: (message: ServerMessage) => void;
};

const FIRST_RETRY_MS = 500;
const MAX_RETRY_MS = 10_000;
// The backoff starts over only after a socket stayed open this long, so a backend that accepts
// and then fails at once is not hammered every half second.
const STABLE_MS = 5_000;

/** Same origin: Caddy passes the upgrade in production, `next dev`'s /api rewrite does locally. */
function socketUrl(): string {
  const url = new URL("/api/glasses/ws", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

/**
 * One socket that reconnects with backoff until `close()`. A token goes in the first message,
 * not the address, so it stays out of the server's logs.
 */
export class GlassesSocket {
  private socket: WebSocket | null = null;
  private retryMs = FIRST_RETRY_MS;
  private attempt = 0;
  private openedAt: number | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private closed = false;

  constructor(
    private readonly token: string | null,
    private readonly handlers: Handlers,
  ) {
    this.connect();
  }

  get open() {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  /** False when not connected. */
  send(message: ClientMessage): boolean {
    if (!this.socket || !this.open) return false;
    this.socket.send(JSON.stringify(message));
    return true;
  }

  close() {
    this.closed = true;
    clearTimeout(this.timer);
    this.socket?.close();
  }

  private connect() {
    this.attempt += 1;
    this.handlers.onState({ state: "connecting", attempt: this.attempt });
    const socket = new WebSocket(socketUrl());
    this.socket = socket;
    socket.onopen = () => {
      if (this.token) this.send({ type: "auth", token: this.token });
      this.openedAt = performance.now();
      this.attempt = 0;
      this.handlers.onState({ state: "open" });
    };
    socket.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      let message: ServerMessage;
      try {
        message = JSON.parse(event.data) as ServerMessage;
      } catch {
        return; // Not JSON: nothing the backend sends.
      }
      this.handlers.onMessage(message);
    };
    socket.onclose = (event) => {
      if (this.socket !== socket) return;
      this.socket = null;
      if (this.closed) return;
      if (
        this.openedAt !== null &&
        performance.now() - this.openedAt >= STABLE_MS
      )
        this.retryMs = FIRST_RETRY_MS;
      this.openedAt = null;
      if (event.code === 1008) {
        this.handlers.onState({
          state: "closed",
          retryInMs: null,
          reason: "refused: log in again",
        });
        return;
      }
      this.handlers.onState({
        state: "closed",
        retryInMs: this.retryMs,
        reason: `unreachable (socket closed, ${event.code})`,
      });
      this.timer = setTimeout(() => this.connect(), this.retryMs);
      this.retryMs = Math.min(this.retryMs * 2, MAX_RETRY_MS);
    };
  }
}
