import createClient from "openapi-fetch";

import type { paths } from "./schema";

// Client Components call relative /api URLs; Caddy (production) or the dev rewrite forwards them.
// Only call this from browser code such as event handlers: relative URLs don't work on the server.
export const api = createClient<paths>({ baseUrl: "" });
