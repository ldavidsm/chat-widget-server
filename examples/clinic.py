"""Runnable demo: a beauty clinic that books appointments.

    export ANTHROPIC_API_KEY=sk-ant-...
    python examples/clinic.py

Then open http://localhost:8000 — the page loads the published widget from the
CDN and points it at this server. The data is fake and lives in memory; the
agent loop, the streaming and the blocks are real.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from fastapi.responses import HTMLResponse

from chat_widget_server import Agent, blocks, create_app, emit_block, tool

from _demo import MADRID, SERVICES, openings, page, service_cards

DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

BOOKINGS: list[dict] = []

# Every tool logs the arguments it was handed. The point of running this example
# is to find out whether the model picks the right tool and fills it in
# correctly, and that is the only place it shows. The router logs the token
# usage of the turn; this logs the decision inside it.
log = logging.getLogger("clinica")


# ── Tools ────────────────────────────────────────────────


@tool
async def list_services(category: str | None = None) -> str:
    """List the treatments on offer, with price and duration.

    Args:
        category: Optional filter — facial, masaje, manos.
    """
    log.info("🔧 list_services(category=%r)", category)
    items = service_cards(category)
    if not items:
        return f"No hay tratamientos en la categoría {category!r}."

    emit_block(blocks.cards(items))
    return "\n".join(f"- {i['title']}: {i['text']}" for i in items)


@tool
async def check_availability(service: str, days_ahead: int = 14) -> str:
    """Free openings for a treatment, starting today.

    Args:
        service: Treatment name, exactly as listed by list_services.
        days_ahead: How many days forward to look. Defaults to 14.
    """
    log.info("🔧 check_availability(service=%r, days_ahead=%r)", service, days_ahead)
    key = service.strip().lower()
    if key not in SERVICES:
        return f"No existe el tratamiento {service!r}. Los disponibles son: {', '.join(SERVICES)}."

    slots = openings(key, days_ahead, taken={b["when"] for b in BOOKINGS})
    if not slots:
        return f"No hay huecos para {key} en los próximos {days_ahead} días."

    # Text for the model, calendar for the visitor.
    emit_block(blocks.calendar(service=key.title(), slots=slots, title="Elige tu fecha"))

    # What this returns is what the model reads, and a bare count leaves it
    # unable to answer "do you have anything on Sunday?" — in the first real
    # run it invited the visitor to pick a Sunday that does not exist, because
    # it never learned which days were on offer. Give it the days.
    per_day: dict[str, int] = {}
    for opening in slots:
        per_day[opening["start"][:10]] = per_day.get(opening["start"][:10], 0) + 1

    listing = ", ".join(
        f"{DIAS[datetime.fromisoformat(day).weekday()]} {day} ({count})"
        for day, count in per_day.items()
    )
    return (
        f"{len(slots)} huecos para {key} en los próximos {days_ahead} días. "
        f"Días con hueco: {listing}. La clínica cierra los domingos."
    )


@tool
async def book_appointment(service: str, when: str, customer_name: str) -> str:
    """Book an appointment.

    Args:
        service: Treatment name.
        when: Start of the appointment as an ISO 8601 datetime with offset,
            including the year — read it off the slot the visitor picked, never
            from memory.
        customer_name: The visitor's own name. Never the professional attending
            them: a picked slot reads "with Marta Ruiz", and Marta is staff.
            Ask the visitor for their name if they have not given it.
    """
    log.info(
        "🔧 book_appointment(service=%r, when=%r, customer_name=%r)",
        service, when, customer_name,
    )
    key = service.strip().lower()
    if key not in SERVICES:
        return f"No existe el tratamiento {service!r}."

    try:
        at = datetime.fromisoformat(when)
    except ValueError:
        return f"No entiendo la fecha {when!r}. Necesito ISO 8601, p.ej. 2026-10-07T10:00:00+02:00."

    if at.tzinfo is None:
        return "Esa fecha no lleva zona horaria. Añade el offset, p.ej. +02:00."

    # A wrong year parses perfectly and books an appointment in the past with
    # every other field plausible. It happened on the very first real run: the
    # slot was 2026 and the model wrote 2025. Cheap to catch, invisible if not.
    now = datetime.now(MADRID)
    if at < now:
        return (
            f"Esa fecha ya pasó: {at.astimezone(MADRID):%d/%m/%Y a las %H:%M}. "
            f"Hoy es {now:%d/%m/%Y}. Revisa el año y vuelve a intentarlo."
        )

    BOOKINGS.append({"service": key, "when": at.isoformat(), "name": customer_name})
    pretty = at.astimezone(MADRID).strftime("%d/%m/%Y a las %H:%M")
    return f"Cita confirmada: {key} el {pretty} para {customer_name}. Referencia #{len(BOOKINGS)}."


# ── App ──────────────────────────────────────────────────

agent = Agent(
    tools=[list_services, check_availability, book_appointment],
    system=(
        "Eres la asistente de una clínica de estética en Madrid. Hablas español, "
        "en tono cálido y breve: dos o tres frases como máximo.\n\n"
        "Usa las herramientas para responder con datos reales — nunca te inventes "
        "precios ni disponibilidad. Para reservar necesitas el tratamiento, la fecha "
        "exacta y el nombre de quien viene; si falta alguno, pídelo.\n\n"
        "Dos cosas que se confunden con facilidad. El profesional que aparece en un "
        "hueco («con Marta Ruiz») es de la clínica, no es el cliente: nunca reserves "
        "a su nombre. Y la fecha de un hueco incluye el año — cópialo tal cual, no lo "
        "deduzcas.\n\n"
        "Cuando una herramienta ya ha mostrado un calendario o unas tarjetas, no "
        "repitas la lista en texto: comenta brevemente y deja que la vea."
    ),
)

# localhost y 127.0.0.1 son orígenes distintos para el navegador, y uvicorn
# escucha en la segunda — así que es la que uno acaba escribiendo. Con solo una
# de las dos permitida, el preflight de CORS devuelve un 400 antes de llegar al
# agente, y el síntoma (OPTIONS /chat 400) no se parece en nada a su causa.
LOCAL_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000"]

app = create_app(agent, allowed_origins=LOCAL_ORIGINS, title="Clínica demo")

NOTE = (
    "El widget de abajo a la derecha habla con <code>POST /chat</code> de este mismo "
    "servidor, y detrás hay un modelo de verdad llamando a tus herramientas. "
    "Prueba <em>¿qué tratamientos tenéis?</em> o <em>quiero reservar una limpieza facial</em>. "
    "Los datos son inventados y viven en memoria."
)


@app.get("/", response_class=HTMLResponse)
async def home() -> str:
    return page(
        endpoint="http://localhost:8000/chat",
        note=NOTE,
        greeting="¡Hola! 👋 ¿Buscas un tratamiento o quieres reservar?",
    )


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "Falta ANTHROPIC_API_KEY.\n\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...\n"
            "  python examples/clinic.py\n"
        )

    import uvicorn

    # Without this the usage line the router logs after every turn is invisible:
    # uvicorn configures only its own loggers, so an INFO record from
    # `chat_widget_server` propagates to a root logger with no handler and is
    # dropped. Those are the numbers you want while testing — `cache_read`
    # climbing means the prompt cache is working, flat zero means something is
    # rewriting the prefix on every request.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:   %(message)s")

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
