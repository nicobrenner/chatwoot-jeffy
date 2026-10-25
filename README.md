# chatwoot-jeffy

Auto-classify and route Chatwoot conversations using [Jeffy](https://github.com/nicobrenner/jeffy).

New message arrives → Jeffy classifies it → labels, team assignment, and priority are set automatically. No LLM, no API key, runs on CPU.

## How it works

A lightweight webhook bridge sits between Chatwoot and Jeffy:

```
Chatwoot (message_created webhook)
    → Bridge (this service)
        → Jeffy /v1/predict
        → Chatwoot API (labels, team, priority)
```

The bridge:
- Verifies webhook signatures (HMAC-SHA256)
- Classifies incoming messages only (ignores agent replies)
- Merges labels with existing ones (never overwrites)
- Falls back to `needs-review` when confidence is below threshold
- Applies routing rules from a YAML config

## Quick start

**1. Start Jeffy**

```bash
pip install jeffy-classify
jeffy-serve
```

**2. Start the bridge**

```bash
cp config.example.yaml config.yaml
# Edit config.yaml with your Chatwoot URL, API token, and routing rules

pip install -r requirements.txt
uvicorn handler:app --port 8401
```

**3. Add the webhook in Chatwoot**

Settings → Integrations → Webhooks → Add webhook:
- URL: `http://your-bridge-host:8401/webhook`
- Events: `message_created`

## Configuration

Copy `config.example.yaml` to `config.yaml`:

```yaml
chatwoot:
  url: "http://localhost:3000"
  api_token: "your-chatwoot-api-token"
  account_id: 1
  webhook_secret: "your-webhook-secret"

jeffy:
  url: "http://localhost:8400"
  task: "support_ticket"        # any Jeffy task
  confidence_threshold: 0.5     # below this → fallback

routing:
  - label: "payment"
    chatwoot_labels: ["billing", "auto-classified"]
    team_id: 2
    priority: "high"

  - label: "refund"
    chatwoot_labels: ["billing", "auto-classified"]
    team_id: 2
    priority: "high"

  - label: "shipping"
    chatwoot_labels: ["shipping", "auto-classified"]
    priority: "medium"

fallback:
  chatwoot_labels: ["needs-review"]
```

The `label` in each rule must match a label the chosen task actually emits (run `curl localhost:8400/v1/capabilities/support_ticket` to list them). Each rule maps that label to Chatwoot actions. All fields are optional:
- `chatwoot_labels`: labels to add (merged with existing)
- `team_id`: team to assign the conversation to
- `priority`: one of `urgent`, `high`, `medium`, `low`

## Docker

```bash
cp config.example.yaml config.yaml
docker compose up
```

This starts both Jeffy and the bridge. Point your Chatwoot webhook at `http://bridge-host:8401/webhook`.

## Jeffy tasks

Jeffy ships with 70+ pretrained classifiers. Pick the one that fits your use case:

| Task | Classes | Labels |
|---|---|---|
| `support_ticket` | 11 | account, cancel, contact, delivery, feedback, invoice, order, payment, refund, shipping, subscription |
| `banking77` | 77 | Banking intents (card issues, transfers, top-ups) |
| `emotion` | 6 | sadness, joy, love, anger, fear, surprise |
| `sms_spam` | 2 | ham, spam |
| `clinc_oos` | 151 | Intent detection with out-of-scope |
| `inbox_router` | 4 | family, notification, promo, work |

`support_ticket` is the best default for customer support. List every task with `curl localhost:8400/v1/capabilities`.

Or train your own classifier on your labeled conversations:

```python
from jeffy.train import train_classifier

clf = train_classifier(texts, labels, task_id="my-chatwoot-router")
clf.save("./model_pack")   # writes ./model_pack/my-chatwoot-router/
```

Serve it with `JEFFY_PACK_DIR=./model_pack jeffy-serve`, then set `task: "my-chatwoot-router"` in the config.

You can also train without Python by POSTing labeled examples to Jeffy's `/v1/train` endpoint, and send corrections back to `/v1/feedback` so the classifier improves from agent behavior (requires jeffy-classify 0.1.0a16+).

## How it compares to Chatwoot Captain

Captain is Chatwoot's built-in AI agent. It uses LLMs to generate responses. This bridge does something different: it classifies and routes, it doesn't respond. They're complementary:

- **Captain**: answers common questions (costs per query, needs LLM)
- **Jeffy bridge**: labels, routes, and prioritizes conversations (free, 15ms, deterministic)

Use both: Jeffy routes the conversation to the right team, Captain handles the easy ones within that team.
