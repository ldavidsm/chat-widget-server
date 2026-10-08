"""Runnable demo: a beauty clinic that books appointments.

    export ANTHROPIC_API_KEY=sk-ant-...
    python examples/clinic.py

Then open http://localhost:8000 — the page loads the published widget from the
CDN and points it at this server. The data is fake and lives in memory; the
agent loop, the streaming and the blocks are real.
"""

from __future__ import annotations

import os
from datetime import datetime

from fastapi.responses import HTMLResponse

from chat_widget_server import Agent, blocks, create_app, emit_block, tool

from _demo import MADRID, SERVICES, openings, page, service_cards

BOOKINGS: list[dict] = []


# ── Tools ────────────────────────────────────────────────


@tool
async def list_services(category: str | None = None) -> str:
    """List the treatments on offer, with price and duration.

    Args:
        category: Optional filter — facial, masaje, manos.
    """
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
    key = service.strip().lower()
    if key not in SERVICES:
        return f"No existe el tratamiento {service!r}. Los disponibles son: {', '.join(SERVICES)}."

    slots = openings(key, days_ahead, taken={b["when"] for b in BOOKINGS})
    if not slots:
        return f"No hay huecos para {key} en los próximos {days_ahead} días."

    # Text for the model, calendar for the visitor.
    emit_block(blocks.calendar(service=key.title(), slots=slots, title="Elige tu fecha"))
    return f"{len(slots)} huecos disponibles para {key} en los próximos {days_ahead} días."


@tool
async def book_appointment(service: str, when: str, customer_name: str) -> str:
    """Book an appointment.

    Args:
        service: Treatment name.
        when: Start of the appointment as an ISO 8601 datetime with offset.
        customer_name: Who the appointment is for.
    """
    key = service.strip().lower()
    if key not in SERVICES:
        return f"No existe el tratamiento {service!r}."

    try:
        at = datetime.fromisoformat(when)
    except ValueError:
        return f"No entiendo la fecha {when!r}. Necesito ISO 8601, p.ej. 2026-10-07T10:00:00+02:00."

    if at.tzinfo is None:
        return "Esa fecha no lleva zona horaria. Añade el offset, p.ej. +02:00."

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
        "exacta y el nombre de la persona; si falta alguno, pídelo.\n\n"
        "Cuando una herramienta ya ha mostrado un calendario o unas tarjetas, no "
        "repitas la lista en texto: comenta brevemente y deja que la vea."
    ),
)

app = create_app(agent, allowed_origins=["http://localhost:8000"], title="Clínica demo")

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

    uvicorn.run(app, host="127.0.0.1", port=8000)
