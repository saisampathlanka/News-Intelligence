// NewsIntel Frontend Configuration
// ─────────────────────────────────────────────────────────────────
// Change API_BASE_URL to match your backend deployment.
//
// Options:
//   ''                                    → served from same origin (backend serves frontend at /dashboard)
//   'http://localhost:8000'               → local backend dev
//   'https://news-intel-api.onrender.com' → Render backend
//   'https://news-intel.fly.dev'          → Fly.io backend
//   'https://your-api.up.railway.app'     → Railway backend API service
//
// For Railway: set the NEWSINTEL_API_URL environment variable on the
// news-intel-frontend service. The Dockerfile.frontend entrypoint script
// will inject it here automatically at container start.
// ─────────────────────────────────────────────────────────────────

window.NEWSINTEL_API_URL = '';  // ← injected by Dockerfile.frontend entrypoint, or set manually
