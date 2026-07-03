# Barber Agent — admin dashboard (Astro)

Server-rendered admin dashboard. Replaces the old Jinja/FastAPI web UI.
All real work happens in the FastAPI JSON API; this app is a thin
server-side renderer that authenticates against `/superadmin/auth/login`
and proxies the same bearer token back on every request.

## What's here

- `src/pages/login.astro` — sign-in form
- `src/pages/logout.astro` — POST to clear the cookie
- `src/pages/admin/index.astro` — tenant list
- `src/pages/admin/tenants/new.astro` — create a tenant
- `src/pages/admin/tenants/[tenant_id]/settings.astro` — tenant settings
  + provider-config CRUD (status / activate / delete)
- `src/pages/admin/tenants/[tenant_id]/provider-configs/new.astro` — new
  provider config (typed fields for `whatsapp` / `llm`, raw JSON for the
  rest)
- `src/pages/admin/tenants/[tenant_id]/provider-configs/[config_id]/edit.astro` — edit
- `src/lib/api.ts` — fetch wrapper around the JSON API
- `src/lib/session.ts` — cookie + bearer plumbing

## Removed surface

The old `apps/web/` Jinja UI is gone. Booking, agenda, manual booking,
and the public client page are NOT recreated here — turns are
requested by WhatsApp.

## Run

```bash
# From the repo root
cd apps/admin-astro
npm install
npm run dev
```

The dev server proxies `/api` to the FastAPI backend on
`http://localhost:8000` by default. Override with `API_PROXY_TARGET`.

In production, the Astro Node server runs as a standalone process
(`npm run build && npm start`) and talks to the FastAPI process via
`API_BASE_URL`.

## Auth

`POST /superadmin/auth/login` returns a bearer token. We store it in
an httpOnly cookie (`barber_session`) and a sibling non-httpOnly
cookie (`barber_email`) for the topbar label. The Astro server
forwards the `Cookie` header on every server-side fetch to the API.

## Booking / agenda / public booking

Not in this app. Turns come in through the WhatsApp webhook
(`POST /webhooks/whatsapp/{tenant_id}`).
