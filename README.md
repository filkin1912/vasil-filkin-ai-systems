# Commerce support assistant

Small support agent for an already-authenticated ecommerce customer. It answers from that customer's orders and records a refund only when the order line supports it.

The diagram and the technology choices are in [architecture.html](architecture.html).

## How to run

From this directory, on Python 3.12:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts/run_scenarios.py
python -m pytest
uvicorn support.api:app --host 127.0.0.1 --port 8000
```

`scripts/run_scenarios.py` runs the four supplied messages, then three follow-up turns that show the confirmation gate. It exits with a non-zero status if an official message submits a refund, if a bare "yes" picks between two open refunds, or if a repeated confirmation creates a second payment.

The HTTP entry is the same service. With the server running:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/support/messages -Method Post -ContentType "application/json" -Body '{"authenticated_customer_id":"CUST-001","message":"Where is order ORD-100?"}'
```

No API key is required. `POST /runtime/clear` drops scratch records and reloads the seed snapshot. `GET /runtime` is a local inspection route for the ledger.

## What I implemented

- A seed-backed commerce mock. The JSON files are read-only. Orders, proposals, refunds, cases, and the review queue live in memory. `clear_runtime()` and a new process both return to the seed.
- A LangGraph turn: interpret the message, call tools, apply the refund policy, draft a reply, then check that the reply only states facts the tools returned.
- Three tools, with the customer id bound by the service: `get_order`, `quote_refund`, `confirm_proposal`. An order that belongs to someone else is returned with the same shape as a missing order.
- A refund ledger. The amount is `unit_price × quantity` from the line, quantized to cents. The customer's figure is never the payable amount.
- A mock model with the same request shape a live tool-calling model would see: a system prompt, a user JSON payload, tool calls or a reply object. `OpenAICompatibleClient.payload()` shows the live request. `complete()` refuses to send it.

## Assumptions

- The caller has already authenticated the customer. This service does not check a password or a token.
- "Submitted" means recorded on the local ledger. A payment provider would sit behind the same policy and the same idempotency key.
- A delivered line is refundable when the customer reports damage. A returned line is refundable when `return_status` is `received`. An in-transit order is not refundable yet; the tracking number is the answer.
- Asking for a refund quotes it. Money moves when the customer asks to process it and any amount they name matches the line, or when they confirm an open proposal by its amount.
- One line per request. "One of" or a singular item name means one unit. A plural line with no quantity is a question, not a guess.
- If two proposals are open, "yes" is not enough. The customer has to name the amount.
- Repeating a confirmation returns the existing refund instead of creating another.
- A draft that mentions an amount, order, item, tracking number, or other customer that the decision does not allow is discarded. The customer receives a template built from the decision, and the draft is stored on the review queue.

## Deliberately incomplete

- No live model call. The mock is a deterministic parser with the production request shape.
- No payment provider, email, or webhook.
- No database. The brief requires memory-only state.
- No chat transcript beyond the open proposals needed to confirm a refund.
- No review UI. Rejected drafts are records.
- Lexical item matching, and an English phrase list for "please process". Unusual wording becomes a confirmation step instead of a payment.
- The execution phrase list is conservative on purpose.

## What I would do next

- Replace `MockModel` with a primary structured-output client and an OpenRouter fallback on timeout. The policy functions stay the call site.
- Persist the ledger in PostgreSQL with a unique constraint on the idempotency key, and give open proposals a TTL in Redis.
- Pass the same idempotency key to the payment provider.
- Add a bounded evaluation set of paraphrases, including attempts to refund another customer's order and to name a higher amount.
- Put the rejected-draft queue in front of a person before any template that the grounding check refused.

## Session transcripts

This project was written in Cursor. I could not retrieve raw Claude Code session files for this directory, because those sessions were not created. I did not reconstruct or summarize stand-in transcripts. The brief says not to fabricate them.

## Layout

```
commerce_mock/     seed JSON and untyped in-memory scratch space
support/           agent, policy, ledger, model boundary, HTTP API
scenarios/         the four supplied requests
scripts/           runnable walkthrough
tests/             mock infrastructure checks and support behaviour
architecture.html  diagram and technology choices
```
