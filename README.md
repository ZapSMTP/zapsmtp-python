# ZapSMTP Python SDK

The official Python library for the [ZapSMTP](https://zapsmtp.com) email API: send, receive and track
email, manage templates, domains, webhooks and everything else the API does.

- Every API operation, with types for every parameter and response (`mypy --strict` clean, `py.typed`)
- A synchronous client, `ZapSMTP`, and an asyncio client, `AsyncZapSMTP`
- Safe automatic retries: sends carry an `Idempotency-Key`, so a retry never sends twice
- `for` (or `async for`) over every page of any list
- Webhook signature verification
- Python 3.9+; one dependency, [httpx](https://www.python-httpx.org)

## Install

```bash
pip install zapsmtp
```

## Send an email

```python
from zapsmtp import ZapSMTP

client = ZapSMTP("zap_...")  # or set ZAP_API_KEY and call ZapSMTP()

email = client.emails.send(
    from_="Acme <hello@acme.com>",
    to="jane@example.org",
    subject="Your receipt",
    html="<p>Thanks for your order.</p>",
    tags=[{"name": "category", "value": "receipt"}],
    metadata={"order_id": "A-1042"},
)
print(email.id)
```

`from` is a Python keyword, so the parameter is `from_`; it is sent as `"from"`. The same goes for any
other parameter or response field named after a keyword (`template.from_`).

With a template:

```python
client.emails.send(
    from_="Acme <hello@acme.com>",
    to="jane@example.org",
    template={"id": "receipt", "variables": {"name": "Jane", "total": 42}},
)
```

Attachments:

```python
from zapsmtp import attachment, attachment_from_file

client.emails.send(
    from_="Acme <hello@acme.com>",
    to="jane@example.org",
    subject="Your invoice",
    text="Attached.",
    attachments=[
        attachment_from_file("invoice.pdf"),                 # content type from the extension
        attachment("notes.txt", "Thank you!"),               # bytes or text
        attachment("logo.png", png_bytes, content_id="logo"),  # inline: <img src="cid:logo">
    ],
)
```

Up to 100 emails at once. Batch items are plain dicts (`zapsmtp.types.SendEmailParams`) and use the
API's own key `"from"`:

```python
result = client.emails.send_batch(
    [
        {"from": "Acme <hello@acme.com>", "to": "jane@example.org", "subject": "Hi Jane", "text": "..."},
        {"from": "Acme <hello@acme.com>", "to": "joe@example.org", "subject": "Hi Joe", "text": "..."},
    ],
    validation="permissive",  # send the valid ones, report the others in result.errors
)
```

Images for templates:

```python
from pathlib import Path

asset = client.template_assets.upload_file(Path("logo.png"), name="Logo")
print(asset.url)
```

## Async

`AsyncZapSMTP` has the same resources and methods; await them.

```python
import asyncio
from zapsmtp import AsyncZapSMTP

async def main() -> None:
    async with AsyncZapSMTP() as client:
        email = await client.emails.send(from_="hello@acme.com", to="jane@example.org", subject="Hi", text="Hello")
        print(email.id)

asyncio.run(main())
```

## Responses

Results are typed models: `email.subject`, `domain.dkim_verified`. A field the API left out is `None`, and a
field this version of the SDK does not know yet is kept: read it with `email["new_field"]`.
`to_dict()` gives the JSON exactly as the API sent it (a number JSON cannot hold, such as `1e999`,
reads as `None`). Models are read-only.

## Lists

A list call returns its first page: `page.data`, `page.has_more`, `page.next_cursor`,
`page.has_next_page()` and `page.get_next_page()`. Iterating the page walks every item of every page,
asking the API as it goes, and keeps your filters.

```python
page = client.emails.list(limit=50, status="bounced")
print(len(page.data), page.has_more)

for email in client.emails.list(status="bounced"):
    print(email.id, email.to)

for page in client.emails.list(limit=100).iter_pages():
    ...
```

With the async client, `await` gives the first page and `async for` walks them all:

```python
first = await client.emails.list(limit=50)
async for email in client.emails.list(status="bounced"):
    print(email.id)
```

## Errors

Every API error raises a `ZapSMTPError` subclass with `status`, `code`, `message`, `type`,
`request_id`, `headers` and `body`.

```python
import zapsmtp

try:
    client.emails.send(from_="hello@acme.com", to="jane@example.org", subject="Hi", text="Hello")
except zapsmtp.ValidationError as err:
    print(err.code, err.message)  # e.g. invalid_parameter
except zapsmtp.RateLimitError as err:
    print("retry in", err.retry_after, "s")
except zapsmtp.ZapSMTPError as err:
    print(err.status, err.code, err.request_id)
```

| Class | When |
|---|---|
| `AuthenticationError` | 401: missing or invalid key |
| `PermissionError` | 403: the key may not do this, or the domain is not verified |
| `NotFoundError` | 404 |
| `ConflictError` | 409 |
| `ValidationError` | 400, 413, 422: the request is wrong |
| `RateLimitError` | 429: `retry_after` says how many seconds to wait |
| `ServerError` | 5xx |
| `ConnectionError` | no answer: network failure or timeout (`status` is 0) |

`zapsmtp.PermissionError` and `zapsmtp.ConnectionError` are the SDK's own classes, not Python's
built-ins of the same name: refer to them through the module.

## Retries and timeouts

A request that failed on the way (408, 429, 5xx, a lost connection, a timeout) is tried again twice by
default, waiting as the API's `Retry-After` says (up to 60 s) or backing off exponentially (0.5 s, 1 s,
2 s, ... up to 8 s, with jitter). Only requests that are safe to repeat are retried: reads, `PUT`s,
deletes, and sends, which always carry an `Idempotency-Key`. Other `POST` and `PATCH` requests are
retried only on 429. Quota errors (`monthly_quota_exceeded`, `daily_quota_exceeded`) are not retried.

Pass your own key to make retries of your own safe too:

```python
client.emails.send(..., idempotency_key=f"receipt-{order.id}")
```

An empty or blank key (as `idempotency_key=` or an `Idempotency-Key` header) raises `ValidationError`
before anything is sent: the API does not deduplicate on an empty key.

The timeout covers one attempt, reading the response included: 60 s by default.

```python
client = ZapSMTP(max_retries=4, timeout=20)
client.emails.get(email_id, timeout=5, max_retries=0, extra_headers={"X-Trace": "1"})
fast = client.with_options(timeout=5)  # a copy sharing the connection pool
```

## Response headers

Every resource has a `with_response` view whose methods return an `APIResponse` with `data`, `status`,
`headers` and `request_id`:

```python
response = client.emails.with_response.send(from_="hello@acme.com", to="jane@example.org", subject="Hi", text="Hello")
print(response.data.id, response.request_id, response.headers.get("ratelimit-remaining"))
```

## Webhooks

Check the signature on the raw body, before parsing it. `verify_webhook` returns the event, or raises
`WebhookVerificationError` when the signature is missing, wrong, or older than five minutes.

Flask:

```python
import os
from flask import Flask, request
from zapsmtp import WebhookVerificationError, verify_webhook

app = Flask(__name__)

@app.post("/webhooks/zapsmtp")
def zapsmtp_webhook():
    try:
        event = verify_webhook(request.get_data(), request.headers, os.environ["ZAP_WEBHOOK_SECRET"])
    except WebhookVerificationError:
        return "", 400
    if event.type == "email.bounced":
        ...
    return "", 200
```

FastAPI:

```python
from fastapi import FastAPI, HTTPException, Request

app = FastAPI()

@app.post("/webhooks/zapsmtp")
async def zapsmtp_webhook(request: Request) -> None:
    try:
        event = verify_webhook(await request.body(), request.headers, os.environ["ZAP_WEBHOOK_SECRET"])
    except WebhookVerificationError:
        raise HTTPException(status_code=400)
    print(event.type, event.data)
```

Django:

```python
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

@csrf_exempt
@require_POST
def zapsmtp_webhook(request):
    try:
        event = verify_webhook(request.body, request.headers, os.environ["ZAP_WEBHOOK_SECRET"])
    except WebhookVerificationError:
        return HttpResponseBadRequest()
    ...
    return HttpResponse()
```

A webhook with batch delivery (`batch: true`) signs a JSON array of events. `verify_webhook` refuses
it; use `verify_webhook_events`, which returns a list: every event of a batch, or the one event of any
other request.

```python
from zapsmtp import verify_webhook_events

for event in verify_webhook_events(request.get_data(), request.headers, os.environ["ZAP_WEBHOOK_SECRET"]):
    handle(event)
```

`headers` may be any mapping of request headers, a list of `(name, value)` pairs, or the
`Zap-Signature` value itself. `client.webhooks.verify(...)` and `client.webhooks.verify_events(...)` do the same. Pass `tolerance=` (seconds)
to change the five minutes.

## Configuration

| Argument | Environment | Default |
|---|---|---|
| `api_key` | `ZAP_API_KEY` | required |
| `base_url` | `ZAP_BASE_URL` | `https://api.zapsmtp.com/v1` |
| `timeout` | | 60 s per attempt (`None`: no limit) |
| `max_retries` | | 2 |
| `default_headers` | | |
| `http_client` | | an `httpx.Client` (`httpx.AsyncClient`) of its own |

Pass your own `http_client` for proxies, custom TLS or transports; the SDK does not close a client it
was given. Close the SDK's own with `client.close()` (`await client.close()`), or use the client as a
context manager.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest        # conformance (starts sdks/conformance/server.mjs with node), core, webhooks; 100% coverage
.venv/bin/mypy          # --strict
node ../codegen/generate.mjs --only python   # regenerate src/zapsmtp/_generated after an API change
```
