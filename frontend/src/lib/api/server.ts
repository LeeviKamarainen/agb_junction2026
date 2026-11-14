import "server-only";

import createClient from "openapi-fetch";

import type { paths } from "./schema";

// Server Components and Server Actions call FastAPI directly over the internal network.
const backendUrl = process.env.BACKEND_URL;
if (!backendUrl) throw new Error("BACKEND_URL must be set, e.g. http://localhost:8000");

export const api = createClient<paths>({ baseUrl: backendUrl });
