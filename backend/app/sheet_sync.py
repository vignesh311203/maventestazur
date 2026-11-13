"""Durable change queue, private reporting snapshots, and optional background sync."""
import csv
import io
import threading
import zipfile
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, delete, text
from .sheets_mirror import SheetsMirror, SheetsError, TABS, to_row

def now():
    return datetime.now(timezone.utc)

def iso(value):
    return value.isoformat() if value else ""

def snapshot(db):
    from . import main as m
    users = {u.id: u for u in db.scalars(select(m.User).order_by(m.User.id))}
    referrals = list(db.scalars(select(m.Referral).order_by(m.Referral.id)))
    partner_stats = {}
    for referral in referrals:
        stats = partner_stats.setdefault(referral.referrer_id, [0, 0, 0.0, 0.0])
        stats[0] += 1
        if referral.status in {"approved", "paid"}:
            stats[1] += 1
            stats[2] += referral.reward_amount
        if referral.status == "paid":
            stats[3] += referral.reward_amount
    def name(uid):
        return users[uid].full_name if uid in users else ""
    return {
        "Leads": [{"id": x.id, "created_at": iso(x.created_at), "name": x.name, "phone": x.phone, "email": x.email, "request_type": x.request_type, "source": x.source, "interest": x.interest, "preferred_time": x.preferred_time, "status": x.status, "contact_consent_at": iso(x.contact_consent_at)} for x in db.scalars(select(m.Lead).order_by(m.Lead.id))],
        "Messages": [{"id": x.id, "created_at": iso(x.created_at), "customer_id": x.customer_id, "customer_name": name(x.customer_id), "sender": x.sender, "body": x.body} for x in db.scalars(select(m.SupportMessage).order_by(m.SupportMessage.id))],
        "Plots": [{"id": x.id, "project": x.project, "plot_number": x.plot_number, "area_sqft": x.area_sqft, "rate_per_sqft": x.rate_per_sqft, "total_price": round(x.area_sqft * x.rate_per_sqft, 2), "status": x.status, "map_x": x.x, "map_y": x.y, "location_label": x.location_label, "latitude": x.latitude, "longitude": x.longitude} for x in db.scalars(select(m.Plot).order_by(m.Plot.id))],
        "Referrals": [{"id": x.id, "partner_id": x.referrer_id, "partner_name": name(x.referrer_id), "referred_name": x.referred_name, "referred_phone": x.referred_phone, "status": x.status, "reward_amount": x.reward_amount} for x in referrals],
        "Partners": [{"id": u.id, "name": u.full_name, "email": u.email, "is_active": u.is_active, "referrals_total": partner_stats.get(u.id, [0, 0, 0, 0])[0], "referrals_approved": partner_stats.get(u.id, [0, 0, 0, 0])[1], "rewards_approved": partner_stats.get(u.id, [0, 0, 0, 0])[2], "rewards_paid": partner_stats.get(u.id, [0, 0, 0, 0])[3]} for u in users.values() if u.role == "partner"],
        "Locations": [{"id": p.id, "project": p.project, "plot_number": p.plot_number, "location_label": p.location_label, "latitude": p.latitude, "longitude": p.longitude} for p in db.scalars(select(m.Plot).order_by(m.Plot.id))],
        "Customers": [{"id": u.id, "name": u.full_name, "email": u.email, "is_active": u.is_active, "created_at": iso(u.created_at)} for u in users.values() if u.role == "customer"],
    }

def csv_safe(value):
    if isinstance(value, str) and (value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))):
        return "'" + value
    return value

def export_zip(data):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for tab, headers in TABS.items():
            buf = io.StringIO(newline="")
            writer = csv.writer(buf)
            writer.writerow(headers)
            writer.writerows([csv_safe(v) for v in to_row(tab, row)] for row in data[tab])
            archive.writestr(tab + ".csv", buf.getvalue().encode("utf-8-sig"))
    return out.getvalue()

def configured(settings, sheet_id=None):
    target = settings.google_sheet_id if sheet_id is None else sheet_id
    return bool(settings.google_sheets_enabled and target and settings.google_service_account_json.get_secret_value())

def process_once(m, mirror_factory=SheetsMirror):
    if not (m.settings.google_sheets_enabled and m.settings.google_service_account_json.get_secret_value()):
        return "disabled"
    # Session-level advisory lock keeps concurrent workers from overwriting one another.
    with m.engine.connect() as conn:
        is_pg = conn.dialect.name == "postgresql"
        if is_pg:
            locked = conn.execute(text("SELECT pg_try_advisory_lock(52403181)")).scalar()
            conn.commit()
            if not locked:
                return "busy"
        sheet_id = m.settings.google_sheet_id
        try:
            with m.SessionLocal() as db:
                sheet_id = m.reporting_config(db)["sheet_id"]
                if not configured(m.settings, sheet_id):
                    return "disabled"
                ids = list(db.scalars(select(m.SheetOutbox.id).order_by(m.SheetOutbox.id).limit(1000)))
                state = db.get(m.SheetSyncStatus, 1)
                last = state.last_success if state else None
                if last and last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if not ids and state and state.sheet_id == sheet_id and last and now() - last < timedelta(hours=1):
                    return "idle"
                data = snapshot(db)
            mirror = mirror_factory(sheet_id, m.settings.google_service_account_json.get_secret_value())
            try:
                mirror.sync(data)
            finally:
                mirror.close()
            with m.SessionLocal() as db:
                state = db.get(m.SheetSyncStatus, 1)
                if not state:
                    state = m.SheetSyncStatus(id=1)
                    db.add(state)
                state.sheet_id = sheet_id
                state.last_success = now()
                state.last_error = ""
                if ids:
                    # Delete only captured IDs; later committed events remain queued.
                    db.execute(delete(m.SheetOutbox).where(m.SheetOutbox.id.in_(ids)))
                db.commit()
            return "synced"
        except Exception as exc:
            # A failed Google request never consumes queue entries.
            error = str(exc)[:160] if isinstance(exc, SheetsError) else "Google sync failed (" + type(exc).__name__ + ")"
            with m.SessionLocal() as db:
                state = db.get(m.SheetSyncStatus, 1)
                if not state:
                    state = m.SheetSyncStatus(id=1, sheet_id=sheet_id)
                    db.add(state)
                state.last_error = error
                db.commit()
            return "failed"
        finally:
            if is_pg:
                conn.execute(text("SELECT pg_advisory_unlock(52403181)"))
                conn.commit()

def start_worker(m):
    if not (m.settings.google_sheets_enabled and m.settings.google_service_account_json.get_secret_value()):
        return None, None
    stop = threading.Event()
    def run():
        delay = 0
        while not stop.wait(delay):
            try:
                result = process_once(m)
            except Exception:
                result = "failed"
            delay = min(600, max(60, delay * 2)) if result == "failed" else 60
    worker = threading.Thread(target=run, daemon=True, name="sheets-mirror")
    worker.start()
    return stop, worker
