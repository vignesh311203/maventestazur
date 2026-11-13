"""Bounded ingress trust and pseudonymous account throttle keys."""
import hashlib
import hmac
from ipaddress import ip_address

def client_ip(request, mode="socket", trusted_hosts=""):
    peer = request.client.host if request.client else "unknown"
    # Railway overwrites X-Real-IP on PUBLIC HTTP ingress. Enable only
    # when private services are trusted and no untrusted TCP path exists.
    if mode == "railway":
        host = request.headers.get("host", "").lower().split(":",1)[0]
        allowed = {h.strip().lower() for h in trusted_hosts.split(",")}
        values = request.headers.getlist("x-real-ip")
        if host in allowed and len(values) == 1:
            try:
                return str(ip_address(values[0].strip()))
            except ValueError:
                pass
    return peer

def account_key(secret, email):
    return hmac.new(secret.encode(), email.strip().casefold().encode(), hashlib.sha256).hexdigest()
