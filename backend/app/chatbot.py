"""Deterministic, persistent conversational guidance; no external AI or booking."""
from dataclasses import dataclass
import re

MENU = ["Book Site Visit", "Request Brochure", "Call Me Back"]
LABELS = dict(zip((x.casefold() for x in MENU), ("site_visit", "brochure", "callback")))
FLOWS = {"site_visit": ("project", "when", "phone", "consent"), "brochure": ("project",), "callback": ("when", "phone", "consent")}
WHEN = re.compile(r"\b(today|tomorrow|tonight|weekend|next week|this week|monday|tuesday|wednesday|thursday|friday|saturday|sunday|morning|afternoon|evening|noon)\b|\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b|\b\d{1,2}[/-]\d{1,2}\b|\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I)
PHONE = re.compile(r"^\+?[\d\s().-]{7,30}$")
CANCEL = {"cancel", "never mind", "nevermind", "stop", "not now", "no thanks"}
GREETING = {"hi", "hello", "hey", "hii", "namaste", "good morning", "good evening"}
SKIP = {"not sure", "not sure yet", "don't know", "any project", "undecided", "skip", "no idea"}
YES = {"yes", "yes, contact me", "i agree", "yes please", "okay", "ok"}
NO = {"no", "no, chat only", "chat only", "do not call", "no thanks"}

@dataclass
class Reply:
    text: str
    state: dict
    quick: list[str]
    lead: dict | None = None

def has(text, words):
    return any(re.search(r"\b" + re.escape(word) + r"\b", text) for word in words)

def phone_from(text):
    value = text.strip()
    if PHONE.fullmatch(value) and value.isascii() and not re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", value) and 7 <= sum(c.isdigit() for c in value) <= 15:
        return value
    return None

def question(flow, slot, projects):
    if slot == "project":
        return ("Which project would you like to " + ("see?" if flow == "site_visit" else "get the brochure for?"), projects[:4] + ["Not sure yet"])
    if slot == "when":
        return ("When would suit you for the " + ("visit" if flow == "site_visit" else "call") + "? For example, Saturday 11 am (India time).", ["Tomorrow", "This weekend", "Next week"])
    if slot == "phone":
        return ("What's the best phone number for our team to reach you on? You can choose chat only instead.", ["Chat only"])
    return ("May our property team contact you on that number about this request?", ["Yes, contact me", "No, chat only"])

def finish(flow, slots, name):
    project = slots.get("project", "Not decided")
    when = slots.get("when", "")
    phone = slots.get("phone", "") if slots.get("consent") is True else ""
    lead = {"request_type": flow, "interest": {"site_visit": "Site visit: " + project, "brochure": "Brochure: " + project, "callback": "Callback request"}[flow], "preferred_time": when, "phone": phone, "consent": bool(phone)}
    if flow == "site_visit":
        message = f"Thanks, {name}. Your site-visit request has been saved for {project}, {when}. A visit is confirmed only after our property team replies."
    elif flow == "callback":
        message = f"Thanks, {name}. Your callback request has been saved for {when}. This is not an automated call or a confirmed appointment."
    else:
        message = f"Thanks, {name}. Your brochure request has been saved for {project}. The team can share verified project information here."
    if not phone and flow != "brochure":
        message += " The team will follow up in this chat only."
    return Reply(message, {}, MENU, lead)

def info(text, projects, available):
    if has(text, ("price", "cost", "emi", "budget", "loan", "rate", "how much", "pricing")):
        return "You can view available plot prices and use our EMI and plot cost calculators. Final prices, charges and loan eligibility need confirmation from our team."
    if has(text, ("available", "availability", "plots", "plot", "project", "location", "where", "size", "sqft")):
        if available:
            return f"There are {available} plots listed as available across {', '.join(projects[:4]) or 'our listed projects'}. Availability may change; our team will confirm."
        return "I don't see any plots listed as available at the moment. Our team can tell you what's coming up."
    return None

def respond(body, full_name, state=None, projects=None, available=0):
    projects = projects or []
    state = state or {}
    name = ((full_name or "").split() or ["there"])[0]
    text = body.strip().casefold().rstrip("!.")
    flow = state.get("flow")
    slots = dict(state.get("slots") or {})
    if flow not in FLOWS:
        flow, slots = None, {}
    explicit = LABELS.get(text)
    if text in CANCEL:
        return Reply(f"No problem, {name}. I've cancelled the unfinished request. You can start another any time.", {}, MENU)
    if has(text, ("human", "real person", "talk to someone", "speak to", "property advisor", "representative")):
        return Reply("Of course. Your message is waiting in the property team's inbox; a team member can reply here. Your unfinished request is kept if you want to continue.", state if flow else {}, ["Continue request", "Cancel"] if flow else MENU)
    # Explicit menu selections switch flows; ordinary mentions during a flow do not.
    if explicit and explicit != flow:
        flow, slots = explicit, {}
        q, chips = question(flow, FLOWS[flow][0], projects)
        return Reply(f"Happy to help, {name}. {q}", {"flow": flow, "slots": slots}, chips)
    if flow:
        pending = next((key for key in FLOWS[flow] if key not in slots), None)
        if pending is None:
            return finish(flow, slots, name)
        q, chips = question(flow, pending, projects)
        if text == "continue request" or text in GREETING or explicit == flow:
            return Reply(q, state, chips)
        value = None
        if pending == "project":
            match = next((p for p in projects if p.casefold() == text), None)
            if match:
                value = match
            elif text in SKIP:
                value = "Not decided"
            elif len(body.strip()) <= 80 and "?" not in body and not info(text, projects, available) and not phone_from(body) and not WHEN.search(body) and text not in YES | NO and not has(text, ("thank", "thanks", "brochure", "callback", "visit")):
                value = body.strip()
        elif pending == "when":
            if len(body.strip()) <= 80 and WHEN.search(body) and not has(text, ("price", "cost", "loan", "emi", "budget")):
                value = body.strip()
        elif pending == "phone":
            if text in NO:
                slots["phone"] = ""
                slots["consent"] = False
                return finish(flow, slots, name)
            value = phone_from(body)
        elif pending == "consent":
            if text in YES:
                value = True
            elif text in NO:
                slots["phone"] = ""
                value = False
        if value is not None:
            slots[pending] = value
            nxt = next((key for key in FLOWS[flow] if key not in slots), None)
            if nxt is None:
                return finish(flow, slots, name)
            q, chips = question(flow, nxt, projects)
            return Reply(f"Got it. {q}", {"flow": flow, "slots": slots}, chips)
        answer = info(text, projects, available)
        prefix = (answer + "\n\nBack to your request: ") if answer else "Let's continue. "
        return Reply(prefix + q, state, chips)
    if not explicit:
        if has(text, ("callback", "call me", "call back", "phone me")):
            explicit = "callback"
        elif has(text, ("brochure", "pdf", "floor plan")):
            explicit = "brochure"
        elif has(text, ("site visit", "book a visit", "visit", "appointment", "tour")):
            explicit = "site_visit"
    if explicit:
        q, chips = question(explicit, FLOWS[explicit][0], projects)
        return Reply(f"Happy to help, {name}. {q} The request is confirmed only by our property team.", {"flow": explicit, "slots": {}}, chips)
    if text in GREETING:
        return Reply(f"Hi {name}! I'm MetroEstate's automated assistant. I can help with plot prices, site visits, brochures and callbacks. What would you like to do?", {}, MENU)
    if has(text, ("thanks", "thank you", "thx")) and len(text) < 40:
        return Reply(f"You're welcome, {name}! Anything else I can help with?", {}, MENU)
    return Reply(info(text, projects, available) or f"Thanks, {name}. I've saved your message for the property team. I can also help with a site visit, brochure or callback.", {}, MENU)
