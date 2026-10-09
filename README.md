# chat-widget-server

FastAPI backend for [`@luisdsm/chat-widget`](https://github.com/ldavidsm/chat-widget).
Your database, your tools, your prompt, **your model** — this handles the agent
loop, the streaming and the UI blocks.

The widget is a browser client: it cannot talk to your database, and it must
not hold your API key. This is the piece that sits in between.

```bash
pip install "chat-widget-server[claude]"   # with the Claude agent
pip install chat-widget-server             # core only, bring your own model
```

The core pulls **no provider SDK**. Only one class in the package is tied to a
model — see [Any LLM](#any-llm).

## The whole thing

```python
from chat_widget_server import Agent, blocks, create_app, emit_block, tool

@tool
async def check_availability(service: str, start: str, end: str) -> str:
    """Free openings for a service between two dates.

    Args:
        service: Name of the service, e.g. "facial cleanse".
        start: First day to look at, YYYY-MM-DD.
        end: Last day to look at, YYYY-MM-DD.
    """
    slots = await db.find_slots(service, start, end)

    # The return value is what the model reads. The block is what the
    # visitor sees.
    emit_block(blocks.calendar(service=service, slots=slots))
    return f"{len(slots)} openings between {start} and {end}."

@tool
async def book(service: str, when: str, customer_name: str) -> str:
    """Book an appointment. `when` is an ISO datetime with offset."""
    appointment = await db.create_appointment(service, when, customer_name)
    return f"Booked. Reference {appointment.id}."

agent = Agent(
    tools=[check_availability, book],
    system="You book appointments for a beauty clinic. Be brief and warm.",
)

app = create_app(agent, allowed_origins=["https://my-clinic.com"])
```

```bash
uvicorn main:app --reload
```

On the page:

```html
<script src="https://cdn.jsdelivr.net/npm/@luisdsm/chat-widget@0.3.0/dist/chat-widget.js"
        data-endpoint="https://api.my-clinic.com/chat"
        data-stream="true"
        async></script>
```

That is the integration. The model decides when to look at the calendar, your
tool answers from your database, and the widget draws it.

## How the two halves fit

```
browser                     your server                  your data
┌──────────────┐  POST /chat ┌────────────────┐          ┌──────────┐
│ chat-widget  │────────────>│ create_router  │          │ Postgres │
│              │<────────────│      ↓         │          └──────────┘
│  text + UI   │  SSE stream │     Agent  ────┼──> tools ────┘
└──────────────┘             │      ↓         │
                             │  Claude API    │  ← your key lives here, only here
                             └────────────────┘
```

## Any LLM

Of seven modules, six know nothing about any provider: the widget contract, the
blocks, the session memory, the rate limits, the SSE route and the scripted
test agent. Only `agent.py` is Claude-specific, and it is loaded lazily — so
the base install works with no provider SDK present at all.

To run GPT, Gemini, a local model or anything else, implement `AgentProtocol`:

```python
from chat_widget_server import Completed, TextChunk, create_app

class MyAgent:
    async def stream(self, message, history=None):
        # ... your provider's streaming tool loop ...
        yield TextChunk("hola")
        yield Completed(text="hola", messages=mirrored_history)

    async def reply(self, message, history=None):
        ...

app = create_app(MyAgent(), allowed_origins=["https://my-clinic.com"])
```

Four event types, and two rules the router relies on:

| Event | Meaning |
| --- | --- |
| `TextChunk(text)` | A piece of the answer, as it is generated |
| `BlockEmitted(block)` | Draw this — calendar, cards, buttons |
| `Refused(category, explanation)` | The provider declined; nothing usable |
| `Completed(text, blocks, messages, truncated, usage)` | End of turn |

1. **`stream()` always ends with exactly one `Completed`**, even after a
   `Refused`. That is what closes the SSE stream and saves the session.
2. **`Completed.messages` is the conversation to persist**, in your provider's
   own message format. Nothing else reads it — it comes straight back as
   `history` on the next turn — so its shape is entirely yours.

`emit_block()` works from inside your tools regardless of provider; it is a
context variable, not a Claude feature.

## Tools

> The tool decorator below is the Claude agent's. With another provider you
> define tools your SDK's way and call `emit_block()` from inside them; the
> rest of this section still applies.

A tool is an `async def` with a docstring. The docstring is not a comment —
it is what the model reads to decide whether to call it, so write it for the
model: say what it does, and what each argument means.

```python
@tool
async def price_of(service: str) -> str:
    """Current price of one service, in euros.

    Args:
        service: Exact service name as listed in the catalogue.
    """
    return await db.price(service)
```

Types come from the annotations; the schema is generated for you.

### Emitting blocks

`emit_block()` queues something for the widget to render. It works only inside
a tool during a request — call it anywhere else and it raises, so a misplaced
call fails in your tests instead of silently dropping the calendar.

```python
from chat_widget_server import blocks, emit_block

emit_block(blocks.calendar(service="Facial", slots=[...]))
emit_block(blocks.cards([
    {"title": "Facial cleanse", "text": "60 min · €45",
     "button": "Book", "value": "Book a facial cleanse"},
]))
emit_block(blocks.quick_replies(["Tomorrow", "Next week"]))
```

Build slots with `blocks.slot()`:

```python
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

blocks.slot(
    datetime(2026, 10, 7, 10, tzinfo=ZoneInfo("Europe/Madrid")),
    duration=timedelta(hours=3),
    resources=[{"label": "Ana Pérez", "type": "staff"}, "Bay 2"],
)
```

**`slot()` refuses an instant with no offset, on purpose** — a `datetime`
without a timezone and a string like `"2026-10-07T10:00:00"` alike. The browser
reads an offset-less instant as the *viewer's* local time, not your business's,
so a customer in another country is shown, and books, the wrong hour, with no
error anywhere. Attach a timezone and the problem disappears. A date on its own
(`"2026-10-07"`) carries no hour to misread and passes.

## The Agent

```python
Agent(
    tools=[...],
    system="...",
    model="claude-opus-5",   # the default
    max_tokens=16_000,
    effort="low",            # low | medium | high | xhigh | max
    thinking=True,           # adaptive thinking
    max_iterations=8,        # cap on tool round-trips per message
    fallbacks=True,          # server-side refusal fallbacks
    cache=True,              # prompt caching
)
```

**`effort="low"` is the default because this is a chat route.** Low effort is
cheaper and faster and holds quality for this kind of work. Raise it to
`"medium"` or `"high"` if your tool surface is large enough that choosing the
right one takes real reasoning — and measure on your own conversations rather
than assuming.

**`fallbacks=True`** turns on server-side refusal fallbacks: if safety
classifiers decline a request, the API re-runs it on another model inside the
same call, so the visitor gets an answer instead of silence. Pass
`fallbacks=False` to opt out.

**`cache=True`** caches the system prompt and the tool definitions, which are
the biggest and most repeated part of every request — a tool loop sends them
several times per message. It cuts the bill substantially and costs nothing in
quality.

For it to work, **keep the system prompt frozen.** Interpolating today's date
or the visitor's name into it puts volatile text at the front of the prefix and
invalidates everything after it, on every request. Pass that kind of context in
the message instead.

Watch `cache_read` in the logs to confirm it is working: in a healthy
conversation it grows with each turn while `cache_write` stays small. Stuck at
zero means something is rewriting the prefix.

Outside FastAPI, drive it directly:

```python
result = await agent.reply("do you have anything on Tuesday?")
print(result.text, result.blocks)

async for event in agent.stream("and on Wednesday?", history=result.messages):
    ...  # TextChunk | BlockEmitted | Refused | Completed
```

## Routes

`create_app` gives you a whole app with CORS; `create_router` plugs into an app
you already have:

```python
from fastapi import FastAPI
from chat_widget_server import create_router

app = FastAPI()
app.include_router(create_router(agent, path="/api/chat"))
```

One route serves both widget modes. The widget sends
`Accept: text/event-stream` when configured with `stream: true`, so the same
URL answers either SSE or a single JSON object — no second endpoint, no flag to
keep in sync.

## Conversation memory

The widget sends its own `history`, and the server **ignores it by default**.
That history comes from a browser, so a forged assistant turn is a
prompt-injection channel straight into your system prompt. The server keeps its
own copy, keyed by `sessionId`.

`MemorySessionStore` is the default and is correct for a single process. Before
you run more than one worker, swap it — the protocol is two methods:

```python
class RedisSessionStore:
    async def load(self, session_id: str) -> list[dict]: ...
    async def save(self, session_id: str, messages: list[dict]) -> None: ...

create_router(agent, store=RedisSessionStore(redis))
```

Keying by `sessionId` is also what makes "delete my conversation" possible —
see below.

## Build the front end for free

Every message costs money, which makes "let me just check the layout" an
expensive habit. `ScriptedAgent` answers from canned rules with no API key and
no bill, through the same route and the same event types:

```python
from chat_widget_server import ScriptedAgent, blocks, create_app

agent = ScriptedAgent(
    rules=[
        (["cita", "reservar"], "Elige el día 👇", [blocks.calendar(slots=[...])]),
        (["precio"], "De 30 a 120 € según el tratamiento.", []),
    ],
    fallback="No te sigo.",
)

app = create_app(agent, allowed_origins=["http://localhost:8000"])
```

The streaming, the blocks, the calendar, CORS, the sessions and the rate limits
are all real — only the answers are scripted. What it cannot tell you is
whether your prompt and tool descriptions make the model pick the right tool;
that needs a real request.

`python examples/offline.py` is a full demo built this way.

## Rate limiting

On by default, because an unprotected endpoint is a stranger's budget: one loop
in a browser console can spend a month of credit in an afternoon.

```python
create_router(
    agent,
    max_per_minute=15,          # per session
    max_per_minute_per_ip=40,   # per caller, catches rotating session ids
    trust_forwarded_for=False,  # see below
    trusted_proxy_hops=1,       # how many proxies you actually run
)
```

Pass `0` to either to disable it. Over the limit returns **429** with a
`Retry-After` header, and the expensive call is skipped — the agent is never
reached.

Two things worth knowing:

- **The counters are per process.** With several workers each keeps its own, so
  the real limit is your number times the workers. Put the hard limit in your
  proxy, or pass `limiter=` with a Redis-backed object implementing
  `check(key) -> float | None`.
- **`trust_forwarded_for` is off by default.** Anyone can set
  `X-Forwarded-For`, so trusting it without a proxy in front turns the IP limit
  into a value the caller chooses. Behind a proxy you do need it on, or every
  request looks like the proxy and shares one bucket.
- **With it on, `trusted_proxy_hops` is what keeps it honest.** A proxy
  *appends* the address it saw, so the header reads
  `<whatever the caller sent>, <what your proxy saw>`. Only the last entry was
  written by your own infrastructure. Reading the first one — the usual
  shortcut — lets one caller rotate buckets forever by varying a header they
  control. Set the count to the number of proxies in front of the app.

### Keeping a slow turn alive

A tool-calling turn goes quiet for twenty to forty seconds while the model
thinks and the tools run. With nothing on the wire, nginx, Cloudflare and most
PaaS proxies decide the SSE response is idle and close it — which reaches the
visitor as an answer that stops mid-sentence, with no error logged anywhere.

The route writes an SSE comment every 15 seconds to prevent that. The widget
ignores any line starting with `:`, so nothing reaches the thread.

```python
create_router(agent, heartbeat_seconds=15.0)   # 0 disables it
```

Raise it if your proxy is patient, lower it if it is not; one comment per
visitor per 15 seconds costs nothing.

## Before you go live

- **CORS**: `create_app` requires `allowed_origins` and rejects `"*"`. The
  widget calls you from someone's page; a permissive origin lets any site on
  the internet spend your tokens.
- **Rate limiting**: on by default — check the numbers suit your traffic, and
  read the per-process caveat above before you run several workers.
- **Your API key** goes in the environment (`ANTHROPIC_API_KEY`), never in the
  widget config and never in the repo.
- **Personal data**: a chat that asks for a name, a phone number or a symptom
  is collecting personal data, and in health contexts it is a special category
  under the GDPR. Have a lawful basis, say what you do with the messages, and
  be able to delete a conversation — which is why sessions are keyed.
- **Cost**: every message is an API call, and tool round-trips multiply it —
  a message that uses a tool costs two. `max_iterations` caps the round-trips;
  prompt caching cuts the repeated part; `ScriptedAgent` keeps development
  free. The logged `in`/`out`/`cache_read` numbers are what to base an estimate
  on — not a guess.

## Develop

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                        # no API key needed
python examples/offline.py    # demo with no API key and no bill
python examples/clinic.py     # demo with a real model (needs ANTHROPIC_API_KEY)
```

## License

MIT
