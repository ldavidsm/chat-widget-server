"""The demo with no API key and no bill — for building the front end.

    python examples/offline.py        # http://localhost:8000

Answers are canned, but everything else is real: the SSE streaming, the
blocks, the calendar with its slots, CORS, the session store, the rate limits.
Use this to get the page right, then switch to `clinic.py` for the real model.
"""

from __future__ import annotations

from fastapi.responses import HTMLResponse

from chat_widget_server import ScriptedAgent, blocks, create_app

from _demo import openings, page, service_cards

agent = ScriptedAgent(
    rules=[
        (
            ["cita", "reservar", "hueco", "disponib"],
            "Perfecto, elige el día que te venga bien 👇",
            [
                blocks.calendar(
                    service="Limpieza facial",
                    title="Elige tu fecha",
                    slots=openings("limpieza facial"),
                )
            ],
        ),
        (
            ["tratamiento", "precio", "servicio", "cuánto"],
            "Estos son los más pedidos:",
            [blocks.cards(service_cards())],
        ),
        (
            ["a las", "quiero el"],
            "¡Listo! Te he apuntado. Te mandamos la confirmación por email ✨",
            [],
        ),
        (["gracias", "adiós"], "¡Un placer! Hasta pronto 👋", []),
    ],
    fallback=(
        "Soy la versión sin modelo: respondo con guion. Prueba a pedir una **cita** "
        "o preguntar por los **tratamientos**."
    ),
)

app = create_app(agent, allowed_origins=["http://localhost:8000"], title="Clínica demo (offline)")

NOTE = (
    "<strong>Modo sin API.</strong> No hay modelo detrás y no se gasta nada: las respuestas "
    "están escritas a mano. Lo que sí es real es el streaming, los bloques, el calendario, "
    "las sesiones y los límites de uso. Prueba <em>quiero una cita</em> o "
    "<em>¿qué tratamientos tenéis?</em>"
)


@app.get("/", response_class=HTMLResponse)
async def home() -> str:
    return page(
        endpoint="http://localhost:8000/chat",
        note=NOTE,
        greeting="¡Hola! 👋 Soy la demo sin modelo. ¿Una cita o ver tratamientos?",
    )


if __name__ == "__main__":
    import uvicorn

    print("\n  Demo sin API key → http://localhost:8000\n")
    uvicorn.run(app, host="127.0.0.1", port=8000)
