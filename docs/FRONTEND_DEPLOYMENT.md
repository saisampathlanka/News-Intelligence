# Frontend Deployment — NewsIntel v5

The frontend is a single self-contained file: `frontend/index.html`.
No build step, no npm, no bundler required.

---

## Deployment options

### Option A: Served by the FastAPI backend (default)

`GET /dashboard` serves `frontend/index.html` directly.

This is the default setup — nothing extra needed. The frontend and API share the same domain so there are no CORS issues and `API_BASE = ''` works as-is.

**Access:** `https://your-app.onrender.com/dashboard`

---

### Option B: Render static site (separate subdomain)

This is what `render.yaml` configures. The static site is hosted at a different domain from the API.

**Required changes to `frontend/index.html`:**

Find line ~10 in the `<script>` block:
```js
var API_BASE = '';
```

Change it to your API URL:
```js
var API_BASE = 'https://news-intel-api.onrender.com';
```

Also update `render.yaml` `ALLOWED_ORIGINS`:
```yaml
- key: ALLOWED_ORIGINS
  value: "https://news-intel-frontend.onrender.com"
```

---

### Option C: Netlify / Vercel

1. Set `API_BASE` in `index.html` to your backend URL
2. Upload `frontend/` folder to Netlify or Vercel
3. No build settings needed — it's a static file

**Netlify `_redirects` file** (already included in `frontend/`):
```
/*    /index.html    200
```

---

### Option D: Local dev (nginx)

The `docker-compose.yml` includes an nginx container that serves the frontend on port 3000:

```bash
docker-compose up -d frontend
open http://localhost:3000
```

---

## Authentication integration

The frontend handles auth automatically. Here's how it works:

### Token storage

Tokens are stored in `sessionStorage` (cleared on tab close, not persisted to disk):
```js
sessionStorage.setItem('access_token', data.access_token);
sessionStorage.setItem('refresh_token', data.refresh_token);
```

> **Note:** `localStorage` would persist across sessions but is more vulnerable to XSS. `sessionStorage` is the recommended default. For production, `httpOnly` cookies (set by the backend) would be the most secure option.

### Login flow

```js
async function login(email, password) {
  const res = await fetch(API_BASE + '/auth/login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({email, password}),
  });
  if (!res.ok) throw new Error('Invalid credentials');
  const data = await res.json();
  sessionStorage.setItem('access_token', data.access_token);
  sessionStorage.setItem('refresh_token', data.refresh_token);
}
```

### Authenticated API calls

```js
async function apiFetch(path) {
  const token = sessionStorage.getItem('access_token');
  const headers = token ? {'Authorization': 'Bearer ' + token} : {};
  const res = await fetch(API_BASE + path, {headers});
  if (res.status === 401) {
    await refreshTokens();
    return apiFetch(path);  // retry once
  }
  return res.json();
}
```

### Token refresh

```js
async function refreshTokens() {
  const refresh = sessionStorage.getItem('refresh_token');
  if (!refresh) { redirectToLogin(); return; }
  const res = await fetch(API_BASE + '/auth/refresh', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({refresh_token: refresh}),
  });
  if (!res.ok) { redirectToLogin(); return; }
  const data = await res.json();
  sessionStorage.setItem('access_token', data.access_token);
  sessionStorage.setItem('refresh_token', data.refresh_token);
}
```

### API key alternative

For server-to-server or simple scripts:

```js
// Via header
fetch(API_BASE + '/insights/summary', {
  headers: {'X-API-Key': 'nip_your_key_here'},
});

// Via query param
fetch(API_BASE + '/insights/summary?api_key=nip_your_key_here');
```

Generate an API key: `POST /auth/api-key` (requires login first).

---

## CORS configuration

If the frontend is on a different domain, set `ALLOWED_ORIGINS` in the API environment:

```bash
# Single origin
ALLOWED_ORIGINS=https://news-intel-frontend.onrender.com

# Multiple origins (comma-separated, no spaces)
ALLOWED_ORIGINS=https://news-intel-frontend.onrender.com,https://yourdomain.com
```

**Never use `*` in production** — it allows any website to call your API with credentials.

---

## Frontend `API_BASE` quick reference

| Deployment scenario | `API_BASE` value |
|--------------------|-----------------|
| API and frontend same domain | `''` (empty string) |
| Render separate static site | `'https://news-intel-api.onrender.com'` |
| Fly.io | `'https://news-intel.fly.dev'` |
| Local Docker (from host) | `'http://localhost:8000'` |
| Production custom domain | `'https://api.yourdomain.com'` |

---

## Static asset caching

The `render.yaml` and nginx config set:

```
Cache-Control: public, max-age=3600    # 1 hour for all assets
Cache-Control: no-store               # for index.html specifically
```

`index.html` is never cached so users always get the latest version. Since the dashboard, CSS, and JS are all in the one file, this also ensures script updates are always picked up.
