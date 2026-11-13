"""One-way, retry-safe Google Sheets reporting mirror. PostgreSQL is authoritative."""
import base64
import json
import re
from urllib.parse import quote

TABS = {
    "Leads": ["id", "created_at", "name", "phone", "email", "request_type", "source", "interest", "preferred_time", "status", "contact_consent_at"],
    "Messages": ["id", "created_at", "customer_id", "customer_name", "sender", "body"],
    "Plots": ["id", "project", "plot_number", "area_sqft", "rate_per_sqft", "total_price", "status", "map_x", "map_y"],
    "Referrals": ["id", "partner_id", "partner_name", "referred_name", "referred_phone", "status", "reward_amount"],
    "Partners": ["id", "name", "email", "is_active", "referrals_total", "referrals_approved", "rewards_approved", "rewards_paid"],
    "Customers": ["id", "name", "email", "is_active", "created_at"],
    "PropertyDetails": ["id", "project", "plot_number", "description", "amenities", "approvals", "possession", "facing", "road_width_ft", "has_photo"],
    "PlotFinance": ["id", "project", "plot_number", "corner_plot", "corner_premium_percent", "guidance_value", "width_ft", "depth_ft"],
    "Locations": ["id", "project", "plot_number", "location_label", "latitude", "longitude"],
}
API = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

class SheetsError(RuntimeError):
    pass

def load_credentials_info(raw):
    try:
        raw = raw.strip()
        if not raw.startswith("{"):
            raw = base64.b64decode(raw, validate=True).decode()
        info = json.loads(raw)
        if not isinstance(info, dict) or info.get("type") != "service_account":
            raise ValueError()
        return info
    except (ValueError, UnicodeError, TypeError):
        raise SheetsError("Invalid Google service account configuration") from None

def column_letter(n):
    out = ""
    while n:
        n, remainder = divmod(n - 1, 26)
        out = chr(65 + remainder) + out
    return out

def to_row(tab, payload):
    return ["" if payload.get(k) is None else payload[k] for k in TABS[tab]]

class SheetsMirror:
    def __init__(self, sheet_id, credentials_json="", session=None):
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", sheet_id or ""):
            raise SheetsError("Invalid Google spreadsheet ID")
        self.sheet_id = sheet_id
        if session is None:
            from google.oauth2 import service_account
            from google.auth.transport.requests import AuthorizedSession
            credentials = service_account.Credentials.from_service_account_info(load_credentials_info(credentials_json), scopes=SCOPES)
            session = AuthorizedSession(credentials)
        self.session = session

    def close(self):
        self.session.close()

    def _call(self, method, path="", **kwargs):
        response = self.session.request(method, f"{API}/{self.sheet_id}{path}", timeout=(5, 25), **kwargs)
        if response.status_code >= 400:
            # Never include response bodies, which can contain private values.
            raise SheetsError(f"Google Sheets HTTP {response.status_code}")
        return response.json() if response.content else {}

    def _range(self, tab, cells):
        return "/values/" + quote(f"'{tab}'!{cells}", safe="")

    def metadata(self):
        data = self._call("GET", params={"fields": "sheets.properties(sheetId,title,gridProperties)"})
        return {s["properties"]["title"]: s["properties"] for s in data.get("sheets", [])}

    def sync(self, snapshot):
        """Rewrite owned tab ranges in stable ID order, never append. Safe after partial failure."""
        if set(snapshot) != set(TABS):
            raise SheetsError("Incomplete reporting snapshot")
        # Do not silently truncate. Keep records in PostgreSQL if sheet capacity is exceeded.
        if sum((len(snapshot[t]) + 1) * len(TABS[t]) for t in TABS) > 8_000_000:
            raise SheetsError("Reporting dataset exceeds safe spreadsheet capacity")
        meta = self.metadata()
        missing = [t for t in TABS if t not in meta]
        if missing:
            self._call("POST", ":batchUpdate", json={"requests": [
                {"addSheet": {"properties": {"title": t, "gridProperties": {
                    "rowCount": max(100, len(snapshot[t]) + 1), "columnCount": len(TABS[t])
                }}}} for t in missing
            ]})
            meta = self.metadata()
        # Check every existing header before modifying any data. Use a dedicated spreadsheet.
        for tab, headers in TABS.items():
            existing = self._call("GET", self._range(tab, f"A1:{column_letter(len(headers))}1")).get("values", [])
            if existing and existing[0] != headers:
                raise SheetsError(f"Unexpected header in {tab}; use a dedicated MetroEstate sheet")
        resize = []
        for tab, headers in TABS.items():
            prop = meta[tab]
            grid = prop.get("gridProperties", {})
            rows = max(grid.get("rowCount", 0), len(snapshot[tab]) + 1, 100)
            cols = max(grid.get("columnCount", 0), len(headers))
            if rows != grid.get("rowCount") or cols != grid.get("columnCount"):
                resize.append({"updateSheetProperties": {"properties": {"sheetId": prop["sheetId"], "gridProperties": {"rowCount": rows, "columnCount": cols}}, "fields": "gridProperties.rowCount,gridProperties.columnCount"}})
        if resize:
            self._call("POST", ":batchUpdate", json={"requests": resize})
            meta = self.metadata()
        for tab, headers in TABS.items():
            end = column_letter(len(headers))
            rows = [headers] + [to_row(tab, row) for row in snapshot[tab]]
            # Keep request sizes bounded. RAW prevents formula execution from customer text.
            for offset in range(0, len(rows), 200):
                batch = rows[offset:offset + 200]
                self._call("PUT", self._range(tab, f"A{offset + 1}:{end}{offset + len(batch)}"), params={"valueInputOption": "RAW"}, json={"values": batch})
            # Clear only the owned columns, never delete tabs or unrelated columns.
            last_grid = meta[tab].get("gridProperties", {}).get("rowCount", len(rows))
            if last_grid > len(rows):
                self._call("POST", self._range(tab, f"A{len(rows) + 1}:{end}{last_grid}") + ":clear", json={})
