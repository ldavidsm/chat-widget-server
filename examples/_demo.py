"""Shared pieces for the two demos: fake data and the host page."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from chat_widget_server import blocks

MADRID = ZoneInfo("Europe/Madrid")
WIDGET = "https://cdn.jsdelivr.net/npm/@luisdsm/chat-widget@0.3.0/dist/chat-widget.js"

SERVICES = {
    "limpieza facial": {"price": 45, "minutes": 60, "category": "facial"},
    "masaje descontracturante": {"price": 55, "minutes": 50, "category": "masaje"},
    "peeling químico": {"price": 80, "minutes": 45, "category": "facial"},
    "manicura": {"price": 25, "minutes": 40, "category": "manos"},
}

STAFF = ["Ana Pérez", "Lucía Gómez", "Marta Ruiz"]


def openings(service: str, days_ahead: int = 14, taken: set[str] | None = None) -> list[dict]:
    """Invent plausible openings: Mon-Sat, mornings and late afternoon."""
    minutes = SERVICES.get(service, {}).get("minutes", 60)
    taken = taken or set()
    now = datetime.now(MADRID)
    found = []

    day = now
    limit = now + timedelta(days=days_ahead)
    while day.date() <= limit.date():
        if day.weekday() != 6:  # closed on Sunday
            for hour in (10, 11, 12, 16, 17):
                at = day.replace(hour=hour, minute=0, second=0, microsecond=0)
                if at < now or (at.day + hour) % 3 == 0 or at.isoformat() in taken:
                    continue
                found.append(
                    blocks.slot(
                        at,
                        end=at + timedelta(minutes=minutes),
                        resources=[{"label": STAFF[hour % len(STAFF)], "type": "staff"}],
                    )
                )
        day += timedelta(days=1)

    return found


def service_cards(category: str | None = None) -> list[dict]:
    return [
        {
            "title": name.title(),
            "text": f"{data['minutes']} min · {data['price']} €",
            "button": "Reservar",
            "value": f"Quiero reservar {name}",
        }
        for name, data in SERVICES.items()
        if category is None or data["category"] == category
    ]


def page(*, endpoint: str, note: str, greeting: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clínica demo · chat-widget-server</title>
<style>
  body {{ margin:0; font-family: system-ui, sans-serif; background:#f9f6f1; color:#2a2118; }}
  .page {{ max-width:720px; margin:0 auto; padding:4rem 2rem; }}
  .hero {{ background:linear-gradient(135deg,#2a2118,#4a3728); color:#f9f6f1;
           border-radius:24px; padding:3.5rem 2rem; text-align:center; }}
  .hero h1 {{ margin:0; font-size:2.4rem; font-weight:400; letter-spacing:2px; }}
  .hero em {{ color:#C9A96E; font-style:italic; }}
  .note {{ background:#fff8e7; border:1px solid #e8d5a0; border-radius:12px;
           padding:1rem 1.4rem; margin-top:2rem; font-size:.9rem; color:#7a6040; line-height:1.6; }}
  code {{ background:rgba(74,55,40,.08); padding:.1rem .4rem; border-radius:4px; font-size:.85em; }}
</style>
</head>
<body>
  <div class="page">
    <div class="hero"><h1>Clínica <em>demo</em></h1></div>
    <div class="note">{note}</div>
  </div>

  <script src="{WIDGET}"
    data-endpoint="{endpoint}"
    data-stream="true"
    data-title="Asistente"
    data-status="En línea"
    data-greeting="{greeting}"
    data-placeholder="Escríbenos…"
    data-locale="es-ES"
    data-quick-replies="✨ Ver tratamientos|📅 Reservar una cita"
    data-primary="#C9A96E"
    async></script>
</body>
</html>
"""
