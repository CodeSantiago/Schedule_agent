/**
 * Session + auth helpers shared by every admin page.
 *
 * The Astro app uses server-rendered pages. It authenticates by
 * sending the same bearer token the FastAPI `/superadmin/auth/login`
 * endpoint issues, and forwards the call's cookie on every request
 * to the API.
 *
 * Why a bearer token stored in an httpOnly cookie?
 * - The JSON API stays the single source of truth for auth; the
 *   Astro frontend never hashes passwords or issues tokens.
 * - The cookie keeps the admin pages stateless from Astro's POV:
 *   a single cookie read per request, no server-side session store.
 */

import type { AstroCookies } from "astro";
import { apiFetch, ApiError } from "./api";

export const SESSION_COOKIE = "barber_session";
export const EMAIL_COOKIE = "barber_email";
export const TENANT_SESSION_COOKIE = "tenant_session";
export const TENANT_EMAIL_COOKIE = "tenant_email";
export const TENANT_ID_COOKIE = "tenant_id";
export const SESSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 30; // 30 days

export interface Principal {
  principal_id: string;
  email: string;
  scope: string;
  tenant_id?: string;
  barber_id?: string;
  barber_name?: string;
  name?: string;
  photo_url?: string;
}

/**
 * Read the raw bearer token from the Astro cookie jar.
 * Returns `null` when the cookie is missing or empty.
 */
export function readToken(cookies: AstroCookies): string | null {
  const raw = cookies.get(SESSION_COOKIE)?.value;
  if (!raw) {
    return null;
  }
  const trimmed = raw.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * Read the tenant bearer token from the Astro cookie jar.
 */
export function readTenantToken(cookies: AstroCookies): string | null {
  const raw = cookies.get(TENANT_SESSION_COOKIE)?.value;
  if (!raw) {
    return null;
  }
  const trimmed = raw.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * Set the session cookie (called after a successful login).
 */
export function setSessionCookie(
  cookies: AstroCookies,
  token: string,
  email: string,
): void {
  cookies.set(SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.ENV === "production",
    path: "/",
    maxAge: SESSION_COOKIE_MAX_AGE,
  });
  // Email is non-sensitive; kept in a non-httpOnly cookie so the
  // topbar can show "Signed in as ..." without an extra round-trip.
  cookies.set(EMAIL_COOKIE, email, {
    httpOnly: false,
    sameSite: "lax",
    secure: process.env.ENV === "production",
    path: "/",
    maxAge: SESSION_COOKIE_MAX_AGE,
  });
}

/**
 * Set the tenant session cookie (called after a successful tenant login).
 */
export function setTenantSessionCookie(
  cookies: AstroCookies,
  token: string,
  email: string,
  tenantId: string,
): void {
  cookies.set(TENANT_SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.ENV === "production",
    path: "/",
    maxAge: SESSION_COOKIE_MAX_AGE,
  });
  cookies.set(TENANT_EMAIL_COOKIE, email, {
    httpOnly: false,
    sameSite: "lax",
    secure: process.env.ENV === "production",
    path: "/",
    maxAge: SESSION_COOKIE_MAX_AGE,
  });
  cookies.set(TENANT_ID_COOKIE, tenantId, {
    httpOnly: false,
    sameSite: "lax",
    secure: process.env.ENV === "production",
    path: "/",
    maxAge: SESSION_COOKIE_MAX_AGE,
  });
}

export function clearSessionCookie(cookies: AstroCookies): void {
  cookies.delete(SESSION_COOKIE, { path: "/" });
  cookies.delete(EMAIL_COOKIE, { path: "/" });
}

export function clearTenantSessionCookie(cookies: AstroCookies): void {
  cookies.delete(TENANT_SESSION_COOKIE, { path: "/" });
  cookies.delete(TENANT_EMAIL_COOKIE, { path: "/" });
  cookies.delete(TENANT_ID_COOKIE, { path: "/" });
}

export function readEmail(cookies: AstroCookies): string | null {
  const v = cookies.get(EMAIL_COOKIE)?.value;
  return v && v.trim().length > 0 ? v : null;
}

export function readTenantEmail(cookies: AstroCookies): string | null {
  const v = cookies.get(TENANT_EMAIL_COOKIE)?.value;
  return v && v.trim().length > 0 ? v : null;
}

export function readTenantId(cookies: AstroCookies): string | null {
  const v = cookies.get(TENANT_ID_COOKIE)?.value;
  return v && v.trim().length > 0 ? v : null;
}

/**
 * Resolve the current principal from the cookie. Returns `null` when
 * the cookie is missing OR the backend rejects the token. The
 * "rejected token" path silently clears the cookie so the user is
 * sent back to /login cleanly.
 */
export async function getPrincipal(
  cookies: AstroCookies,
  request: Request,
): Promise<Principal | null> {
  const token = readToken(cookies);
  if (!token) {
    return null;
  }
  try {
    // The API does not expose a "verify token" endpoint, but it does
    // require the bearer on every superadmin route. The cheapest
    // server-side check is `GET /superadmin/tenants?limit=1`, which
    // always returns 200 for a valid token and 401 otherwise.
    await apiFetch("/superadmin/tenants?limit=1", { request, token });
    return {
      principal_id: "",
      email: readEmail(cookies) ?? "",
      scope: "superadmin",
    };
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      clearSessionCookie(cookies);
      return null;
    }
    return null;
  }
}

/**
 * Resolve the current tenant principal from the cookie. Returns `null`
 * when the cookie is missing OR the backend rejects the token.
 */
export async function getTenantPrincipal(
  cookies: AstroCookies,
  request: Request,
): Promise<Principal | null> {
  const token = readTenantToken(cookies);
  if (!token) {
    return null;
  }
  try {
    const data = await apiFetch("/tenants/auth/me", { request, token });
    const body = (await data.json()) as {
      principal_id: string;
      email: string;
      scope: string;
      barber_id?: string;
      barber_name?: string;
      name?: string;
      photo_url?: string;
    };
    return {
      principal_id: body.principal_id,
      email: body.email,
      scope: body.scope,
      tenant_id: readTenantId(cookies) ?? undefined,
      barber_id: body.barber_id,
      barber_name: body.barber_name,
      name: body.name,
      photo_url: body.photo_url,
    };
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      clearTenantSessionCookie(cookies);
      return null;
    }
    return null;
  }
}

export interface LoginResult {
  ok: boolean;
  error?: string;
  token?: string;
  email?: string;
}

export async function login(
  email: string,
  password: string,
): Promise<LoginResult> {
  try {
    const res = await apiFetch("/superadmin/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, password, label: "dashboard" }),
    });
    const data = (await res.json()) as {
      token: string;
      email: string;
      principal_id: string;
      scope: string;
    };
    return {
      ok: true,
      token: data.token,
      email: data.email,
    };
  } catch (err) {
    if (err instanceof ApiError) {
      return { ok: false, error: "Invalid credentials." };
    }
    return { ok: false, error: "Could not reach the API." };
  }
}

export interface TenantLoginResult extends LoginResult {
  tenant_id?: string;
}

export async function tenantLogin(
  email: string,
  password: string,
): Promise<TenantLoginResult> {
  try {
    const res = await apiFetch("/tenants/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, password, label: "dashboard" }),
    });
    const data = (await res.json()) as {
      token: string;
      email: string;
      principal_id: string;
      scope: string;
      tenant_id?: string;
    };
    return {
      ok: true,
      token: data.token,
      email: data.email,
      tenant_id: data.tenant_id,
    };
  } catch (err) {
    if (err instanceof ApiError) {
      return { ok: false, error: "Invalid credentials." };
    }
    return { ok: false, error: "Could not reach the API." };
  }
}
