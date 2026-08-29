import { API_CONTRACT_VERSION } from "../generated/api";

/** Build a fetch Response that behaves like the current API release. */
export function apiJsonResponse(body: unknown, init: ResponseInit = {}): Response {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  headers.set("X-API-Contract-Version", API_CONTRACT_VERSION);
  return new Response(JSON.stringify(body), { ...init, headers });
}
