/**
 * Thin client for the FastAPI JSON API.
 *
 * In dev: requests go through the Vite proxy at `/api` so the browser
 * sees same-origin and the cookie flows naturally.
 *
 * In prod: the Astro Node server is deployed behind the same origin
 * as the API (or a reverse proxy routes `/api` to the FastAPI
 * process). `API_BASE_URL` lets a deployment override the upstream.
 */

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, body: unknown) {
    super(`API ${status}`);
    this.status = status;
    this.body = body;
  }
}

export interface ApiFetchOptions {
  method?: string;
  headers?: Record<string, string>;
  body?: BodyInit | null;
  token?: string;
  /** Incoming Astro request — used to forward the cookie. */
  request?: Request;
}

function resolveBaseUrl(): string {
  // `/api` is proxied to FastAPI in dev; in prod, the deployment
  // exposes the API under the same path. Override with API_BASE_URL
  // when running the Astro server and FastAPI on different origins.
  return process.env.API_BASE_URL ?? "http://localhost:8000";
}

export async function apiFetch(
  path: string,
  opts: ApiFetchOptions = {},
): Promise<Response> {
  const base = resolveBaseUrl();
  const headers: Record<string, string> = { ...(opts.headers ?? {}) };
  // The FastAPI JSON API authenticates via the `Authorization:
  // Bearer` header — it does NOT read the `barber_session` cookie.
  // If the caller didn't pass an explicit token, fall back to the
  // bearer in the incoming request's cookie so server-rendered
  // pages (no JS context) can still call the API.
  let token = opts.token;
  if (!token && opts.request) {
    const cookieHeader = opts.request.headers.get("cookie") ?? "";
    // Try tenant_session first, then fall back to barber_session.
    const match =
      cookieHeader.match(/(?:^|;\s*)tenant_session=([^;]+)/) ??
      cookieHeader.match(/(?:^|;\s*)barber_session=([^;]+)/);
    if (match) {
      token = decodeURIComponent(match[1]);
    }
  }
  if (token) {
    headers["authorization"] = `Bearer ${token}`;
  }
  const res = await fetch(`${base}${path}`, {
    method: opts.method ?? "GET",
    headers,
    body: opts.body,
  });
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      // ignore: non-JSON body
    }
    throw new ApiError(res.status, body);
  }
  return res;
}

export async function apiJson<T>(
  path: string,
  opts: ApiFetchOptions = {},
): Promise<T> {
  const res = await apiFetch(path, opts);
  return (await res.json()) as T;
}
