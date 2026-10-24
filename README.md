# chatwoot-jeffy

Auto-classify and route Chatwoot conversations using [Jeffy](https://github.com/nicobrenner/jeffy).

New message arrives → Jeffy classifies it → labels, team assignment, and priority are set automatically. No LLM, no API key, runs on CPU.

## How it works

A lightweight webhook bridge sits between Chatwoot and Jeffy:

```
Chatwoot (message_created webhook)
    → Bridge (this service)
        → Jeffy /v1/classify
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
jeffy serve
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
  task: "inbox_router"          # any Jeffy task
  confidence_threshold: 0.5     # below this → fallback

routing:
  - label: "billing"
    chatwoot_labels: ["billing", "auto-classified"]
    team_id: 2
    priority: "medium"

  - label: "technical_support"
    chatwoot_labels: ["technical", "auto-classified"]
    team_id: 3
    priority: "medium"

  - label: "spam"
    chatwoot_labels: ["spam", "auto-classified"]

fallback:
  chatwoot_labels: ["needs-review"]
```

Each routing rule maps a Jeffy label to Chatwoot actions. All fields are optional:
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

Jeffy ships with 15+ pretrained classifiers. Pick the one that fits your use case:

| Task | Classes | Good for |
|---|---|---|
| `inbox_router` | 4 | Email routing (support, sales, spam, personal) |
| `banking77` | 77 | Banking intents (billing, transfers, cards) |
| `emotion` | 6 | Sentiment detection |
| `sms_spam` | 2 | Spam filtering |
| `clinc_oos` | 150 | Intent detection with out-of-scope |

Or train your own classifier on your labeled conversations:

```python
from jeffy.train import train_classifier

clf = train_classifier(texts, labels, task_id="my-chatwoot-router")
clf.save("my-chatwoot-router")
```

Then set `task: "my-chatwoot-router"` in the config.

## How it compares to Chatwoot Captain

Captain is Chatwoot's built-in AI agent. It uses LLMs to generate responses. This bridge does something different: it classifies and routes, it doesn't respond. They're complementary:

- **Captain**: answers common questions (costs per query, needs LLM)
- **Jeffy bridge**: labels, routes, and prioritizes conversations (free, 15ms, deterministic)

Use both: Jeffy routes the conversation to the right team, Captain handles the easy ones within that team.
