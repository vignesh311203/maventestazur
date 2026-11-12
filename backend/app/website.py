"""Same-origin frontend hosting for Railway and single-container deployments."""
from pathlib import Path
from fastapi import Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
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
    if path.rstrip("/") in {"/admin", "/partner", "/customer"} or path.startswith("/api/"):
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Cache-Control"] = "no-store"
    return response

@app.get("/admin", include_in_schema=False)
@app.get("/partner", include_in_schema=False)
@app.get("/customer", include_in_schema=False)
@app.get("/privacy", include_in_schema=False)
def panel():
    return FileResponse(WEB / "index.html")

@app.get("/robots.txt", include_in_schema=False)
def robots():
    return PlainTextResponse("User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /partner\nDisallow: /customer\nDisallow: /api/\nSitemap: https://metroestate-production.up.railway.app/sitemap.xml\n")

@app.get("/sitemap.xml", include_in_schema=False)
def sitemap():
    return Response('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://metroestate-production.up.railway.app/</loc></url><url><loc>https://metroestate-production.up.railway.app/privacy</loc></url></urlset>', media_type="application/xml")

app.mount("/", StaticFiles(directory=WEB, html=True), name="website")
