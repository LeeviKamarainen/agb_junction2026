import type { GlassesReply, WatchResult } from "./types";

/**
 * The glasses' WebSocket, /api/glasses/ws. The protocol is in backend/app/glasses/channel.py;
 * these types mirror its pydantic models (a WebSocket is not in the OpenAPI schema, so
 * `make api-types` cannot generate them).
 */
export type ClientMessage =
  | ({ type: "command"; id: number } & (
      { text: string } | { action: string } | { question: string }
    ))
  | { type: "photo"; id: number }
  | { type: "frame"; id: number }
  | { type: "watch"; on: boolean }
  | { type: "ping"; id: number };

export type ServerMessage =
  | { type: "reply"; id: number | null; reply: GlassesReply }
  | {
      type: "watch_result";
      id: number | null;
      result: WatchResult;
      dropped: number;
      ms: number;
    }
  | { type: "error"; id: number | null; status: number; detail: string }
  | { type: "pong"; id: number | null };

export type SocketState =
  | { state: "connecting"; attempt: number }
  | { state: "open" }
  /** `retryInMs` is null when retrying cannot help: the backend refused the token. */
  | { state: "closed"; retryInMs: number | null; reason: string };

type Handlers = {
  onState: (state: SocketState) => void;
  onMessage: (message: ServerMessage) => void;
};

const FIRST_RETRY_MS = 500;
const MAX_RETRY_MS = 10_000;
// A frame or photo is not sent while this much is still waiting to go out: the network is slow.
const MAX_BUFFERED = 2_000_000;

/** Same origin: Caddy passes the upgrade in production, `next dev`'s /api rewrite does locally. */
export function socketUrl(token: string | null): string {
  const url = new URL("/api/glasses/ws", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  if (token) url.searchParams.set("token", token);
  return url.toString();
}

/** One socket that reconnects with backoff until `close()`. */
export class GlassesSocket {
  private socket: WebSocket | null = null;
  private retryMs = FIRST_RETRY_MS;
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private closed = false;

  constructor(
    private readonly url: string,
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

  /** A photo or frame header, then its JPEG as a binary frame. False when not sent. */
  sendJpeg(message: ClientMessage & { type: "photo" | "frame" }, jpeg: Blob) {
    if (!this.socket || !this.open) return false;
    if (message.type === "frame" && this.socket.bufferedAmount > MAX_BUFFERED)
      return false;
    this.socket.send(JSON.stringify(message));
    this.socket.send(jpeg);
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
    const socket = new WebSocket(this.url);
    this.socket = socket;
    socket.onopen = () => {
      this.retryMs = FIRST_RETRY_MS;
      this.attempt = 0;
      this.handlers.onState({ state: "open" });
    };
    socket.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      try {
        this.handlers.onMessage(JSON.parse(event.data) as ServerMessage);
      } catch {
        // Not JSON: nothing the backend sends.
      }
    };
    socket.onclose = (event) => {
      if (this.socket !== socket) return;
      this.socket = null;
      if (this.closed) return;
      if (event.code === 1008) {
        this.handlers.onState({
          state: "closed",
          retryInMs: null,
          reason: "refused the token: add ?token= to the page address",
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
