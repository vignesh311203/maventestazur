"""Same-origin frontend hosting for Railway and single-container deployments."""
from pathlib import Path
from fastapi import Request
from fastapi.staticfiles import StaticFiles
from .main import app, settings

WEB = Path(__file__).resolve().parent.parent / "web"

@app.middleware("http")
async def website_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if not path.startswith("/api/") and path not in {"/health", "/ready", "/openapi.json", "/docs"}:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'"
        )
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if path.startswith("/assets/") else "no-cache"
        )
    return response

app.mount("/", StaticFiles(directory=WEB, html=True), name="website")
