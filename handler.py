"""Chatwoot-Jeffy bridge: classify incoming messages and route conversations."""

import hashlib
import hmac
import logging
import time

import httpx
import yaml
from fastapi import FastAPI, Header, HTTPException, Request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("chatwoot-jeffy")

app = FastAPI(title="Chatwoot-Jeffy Bridge")

CONFIG = {}
ROUTING_MAP = {}


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def build_routing_map(config: dict) -> dict:
    return {rule["label"]: rule for rule in config.get("routing", [])}


@app.on_event("startup")
async def startup():
    global CONFIG, ROUTING_MAP
    CONFIG = load_config()
    ROUTING_MAP = build_routing_map(CONFIG)
    log.info(
        "Bridge ready: Jeffy task=%s, %d routing rules",
        CONFIG["jeffy"]["task"],
        len(ROUTING_MAP),
    )


def verify_signature(body: bytes, signature: str, timestamp: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode(),
        f"{timestamp}.{body.decode()}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


async def classify(text: str) -> dict:
    jeffy = CONFIG["jeffy"]
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{jeffy['url']}/v1/predict",
            json={"text": text, "task": jeffy["task"]},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()


async def chatwoot_api(method: str, path: str, json: dict = None) -> dict:
    cw = CONFIG["chatwoot"]
    url = f"{cw['url']}/api/v1/accounts/{cw['account_id']}{path}"
    async with httpx.AsyncClient() as client:
        resp = await client.request(
            method,
            url,
            json=json,
            headers={"Authorization": f"Bearer {cw['api_token']}"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}


async def get_existing_labels(conversation_id: int) -> list[str]:
    resp = await chatwoot_api("GET", f"/conversations/{conversation_id}/labels")
    return resp.get("payload", [])


async def apply_labels(conversation_id: int, new_labels: list[str]):
    existing = await get_existing_labels(conversation_id)
    merged = sorted(set(existing + new_labels))
    await chatwoot_api("POST", f"/conversations/{conversation_id}/labels", {"labels": merged})
    log.info("  Labels: %s -> %s", existing, merged)


async def assign_team(conversation_id: int, team_id: int):
    await chatwoot_api("POST", f"/conversations/{conversation_id}/assignments", {"team_id": team_id})
    log.info("  Assigned to team %d", team_id)


async def set_priority(conversation_id: int, priority: str):
    await chatwoot_api("PATCH", f"/conversations/{conversation_id}", {"priority": priority})
    log.info("  Priority: %s", priority)


@app.post("/webhook")
async def webhook(
    request: Request,
    x_chatwoot_signature: str = Header(None),
    x_chatwoot_timestamp: str = Header(None),
):
    body = await request.body()
    secret = CONFIG["chatwoot"].get("webhook_secret")
    if secret and x_chatwoot_signature:
        if not verify_signature(body, x_chatwoot_signature, x_chatwoot_timestamp or "", secret):
            raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    event = payload.get("event")

    if event != "message_created":
        return {"status": "ignored", "event": event}

    message = payload.get("content", "")
    message_type = payload.get("message_type")
    conversation = payload.get("conversation", {})
    conversation_id = conversation.get("id")

    if not message or not conversation_id:
        return {"status": "skipped", "reason": "no content or conversation"}

    # Only classify incoming messages (from contacts, not agents)
    if message_type not in (None, "incoming", 0):
        return {"status": "skipped", "reason": "outgoing message"}

    log.info("Classifying conversation %d: %.80s...", conversation_id, message)

    t0 = time.time()
    result = await classify(message)
    ms = (time.time() - t0) * 1000

    label = result.get("label", "")
    confidence = result.get("confidence", 0)
    margin = result.get("margin", 0)
    threshold = CONFIG["jeffy"].get("confidence_threshold", 0.5)

    log.info("  Result: %s (confidence=%.2f, margin=%.2f, %.0fms)", label, confidence, margin, ms)

    if confidence < threshold:
        fallback = CONFIG.get("fallback", {})
        fallback_labels = fallback.get("chatwoot_labels", ["needs-review"])
        await apply_labels(conversation_id, fallback_labels)
        return {
            "status": "low_confidence",
            "label": label,
            "confidence": confidence,
            "action": "fallback",
        }

    rule = ROUTING_MAP.get(label)
    if not rule:
        fallback = CONFIG.get("fallback", {})
        fallback_labels = fallback.get("chatwoot_labels", ["needs-review"])
        await apply_labels(conversation_id, [label, "auto-classified"] + fallback_labels)
        return {
            "status": "no_rule",
            "label": label,
            "confidence": confidence,
            "action": "labeled_only",
        }

    if "chatwoot_labels" in rule:
        await apply_labels(conversation_id, rule["chatwoot_labels"])

    if "team_id" in rule:
        await assign_team(conversation_id, rule["team_id"])

    if "priority" in rule:
        await set_priority(conversation_id, rule["priority"])

    return {
        "status": "routed",
        "label": label,
        "confidence": confidence,
        "margin": margin,
        "actions": list(rule.keys()),
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
