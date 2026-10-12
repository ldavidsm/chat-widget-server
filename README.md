# chat-widget-server

***Español** · [English](https://github.com/ldavidsm/chat-widget-server/blob/main/README.en.md)*

Backend FastAPI para [`@luisdsm/chat-widget`](https://github.com/ldavidsm/chat-widget).
Tu base de datos, tus herramientas, tu prompt, **tu modelo** — esto se encarga
del bucle del agente, del streaming y de los bloques de interfaz.

El widget es un cliente de navegador: no puede hablar con tu base de datos, y
no debe guardar tu API key. Esta es la pieza que va en medio.

```bash
pip install "chat-widget-server[claude]"   # con el agente de Claude
pip install chat-widget-server             # solo el núcleo, trae tu modelo
```

El núcleo **no arrastra ningún SDK de proveedor**. Solo una clase del paquete
está atada a un modelo — ver [Cualquier LLM](#cualquier-llm).

## Todo junto

```python
from chat_widget_server import Agent, blocks, create_app, emit_block, tool

@tool
async def check_availability(service: str, start: str, end: str) -> str:
    """Huecos libres de un servicio entre dos fechas.

    Args:
        service: Nombre del servicio, p. ej. "limpieza facial".
        start: Primer día a mirar, AAAA-MM-DD.
        end: Último día a mirar, AAAA-MM-DD.
    """
    slots = await db.find_slots(service, start, end)

    # Lo que se devuelve es lo que lee el modelo. El bloque es lo que ve
    # la persona.
    emit_block(blocks.calendar(service=service, slots=slots))
    return f"{len(slots)} huecos entre {start} y {end}."

@tool
async def book(service: str, when: str, customer_name: str) -> str:
    """Reserva una cita. `when` es una fecha-hora ISO con offset."""
    appointment = await db.create_appointment(service, when, customer_name)
    return f"Reservado. Referencia {appointment.id}."

agent = Agent(
    tools=[check_availability, book],
    system="Reservas citas para una clínica de estética. Sé breve y cálida.",
)

app = create_app(agent, allowed_origins=["https://mi-clinica.com"])
```

```bash
uvicorn main:app --reload
```

En la página:

```html
<script src="https://cdn.jsdelivr.net/npm/@luisdsm/chat-widget@0.3.2/dist/chat-widget.js"
        data-endpoint="https://api.mi-clinica.com/chat"
        data-stream="true"
        async></script>
```

Esa es la integración. El modelo decide cuándo mirar el calendario, tu
herramienta responde desde tu base de datos y el widget lo pinta.

## Cómo encajan las dos mitades

```
navegador                    tu servidor                  tus datos
┌──────────────┐  POST /chat ┌────────────────┐          ┌──────────┐
│ chat-widget  │────────────>│ create_router  │          │ Postgres │
│              │<────────────│      ↓         │          └──────────┘
│  texto + UI  │  stream SSE │     Agent  ────┼──> tools ────┘
└──────────────┘             │      ↓         │
                             │  API de Claude │  ← tu llave vive aquí, y solo aquí
                             └────────────────┘
```

## Cualquier LLM

De ocho módulos, siete no saben nada de ningún proveedor: los tipos de evento y
`AgentProtocol`, el contrato con el widget, los bloques, la memoria de sesión,
los límites de uso, la ruta SSE y el agente de guion para tests. Solo `agent.py`
es específico de Claude, y se carga en diferido — así que la instalación base
funciona sin ningún SDK de proveedor presente.

Para usar GPT, Gemini, un modelo local o cualquier otra cosa, implementa
`AgentProtocol`:

```python
from chat_widget_server import Completed, TextChunk, create_app

class MyAgent:
    async def stream(self, message, history=None):
        # ... el bucle de herramientas con streaming de tu proveedor ...
        yield TextChunk("hola")
        yield Completed(text="hola", messages=mirrored_history)

    async def reply(self, message, history=None):
        ...

app = create_app(MyAgent(), allowed_origins=["https://mi-clinica.com"])
```

Cuatro tipos de evento, y dos reglas en las que el router se apoya:

| Evento | Qué significa |
| --- | --- |
| `TextChunk(text)` | Un trozo de la respuesta, a medida que se genera |
| `BlockEmitted(block)` | Pinta esto — calendario, tarjetas, botones |
| `Refused(category, explanation)` | El proveedor lo rechazó; nada aprovechable |
| `Completed(text, blocks, messages, truncated, usage)` | Fin del turno |

1. **`stream()` termina con exactamente un `Completed`**, incluso después de un
   `Refused`. Es lo que cierra el stream SSE y guarda la sesión. La excepción es
   una excepción: si la llamada al proveedor falla, déjala propagarse — el router
   la captura y se disculpa con la persona. Un turno que no completó es también
   un turno cuyo historial no se guardó.
2. **`Completed.messages` es la conversación a persistir**, en el formato de
   mensajes de tu proveedor. Nadie más lo lee — vuelve tal cual como `history`
   en el turno siguiente — así que su forma es enteramente tuya.

`emit_block()` funciona desde dentro de tus herramientas con cualquier
proveedor: es una variable de contexto, no una característica de Claude.

## Herramientas

> El decorador de abajo es el del agente de Claude. Con otro proveedor defines
> las herramientas como lo haga tu SDK y llamas a `emit_block()` desde dentro;
> el resto de esta sección sigue aplicando.

Una herramienta es un `async def` con docstring. El docstring **no es un
comentario**: es lo que lee el modelo para decidir si la llama, así que
escríbelo para el modelo — di qué hace y qué significa cada argumento.

```python
@tool
async def price_of(service: str) -> str:
    """Precio actual de un servicio, en euros.

    Args:
        service: Nombre exacto del servicio, tal como aparece en el catálogo.
    """
    return await db.price(service)
```

Los tipos salen de las anotaciones; el esquema se genera por ti.

### Emitir bloques

`emit_block()` encola algo para que el widget lo pinte. Funciona solo dentro de
una herramienta durante una petición: llamarlo en cualquier otro sitio lanza una
excepción, así que una llamada mal puesta falla en tus tests en lugar de perder
el calendario en silencio en producción.

```python
from chat_widget_server import blocks, emit_block

emit_block(blocks.calendar(service="Facial", slots=[...]))
emit_block(blocks.cards([
    {"title": "Limpieza facial", "text": "60 min · 45 €",
     "button": "Reservar", "value": "Reservar una limpieza facial"},
]))
emit_block(blocks.quick_replies(["Mañana", "La semana que viene"]))
```

Construye los huecos con `blocks.slot()`:

```python
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

blocks.slot(
    datetime(2026, 10, 7, 10, tzinfo=ZoneInfo("Europe/Madrid")),
    duration=timedelta(hours=3),
    resources=[{"label": "Ana Pérez", "type": "staff"}, "Cabina 2"],
)
```

**`slot()` rechaza a propósito un instante sin offset** — tanto un `datetime`
sin zona horaria como una cadena del tipo `"2026-10-07T10:00:00"`. El navegador
lee un instante sin offset como hora local de *quien mira*, no de tu negocio, así
que alguien desde otro país ve —y reserva— la hora equivocada, sin un error en
ninguna parte. Pon la zona horaria y el problema desaparece. Una fecha sola
(`"2026-10-07"`) no lleva hora que malinterpretar y pasa.

## El Agent

```python
Agent(
    tools=[...],
    system="...",
    model="claude-opus-5",   # el valor por defecto
    max_tokens=16_000,
    effort="low",            # low | medium | high | xhigh | max
    thinking=True,           # adaptive thinking
    max_iterations=8,        # tope de rondas de herramienta por mensaje
    fallbacks=True,          # fallbacks de rechazo en el servidor
    cache=True,              # caché de prompt
)
```

**`effort="low"` es el valor por defecto porque esto es una ruta de chat.** El
esfuerzo bajo es más barato y más rápido, y la calidad se sostiene para este
tipo de trabajo. Súbelo a `"medium"` o `"high"` si tu superficie de herramientas
es lo bastante grande como para que elegir la correcta requiera razonar de
verdad — y mídelo sobre tus propias conversaciones en lugar de suponerlo.

**`fallbacks=True`** activa los fallbacks de rechazo del servidor: si los
clasificadores de seguridad rechazan una petición, la API la vuelve a ejecutar
en otro modelo dentro de la misma llamada, así que la persona recibe una
respuesta en lugar de silencio. Pasa `fallbacks=False` para desactivarlo.

**`cache=True`** cachea el system prompt y las definiciones de herramientas, que
son la parte más grande y más repetida de cada petición — un bucle de
herramientas las manda varias veces por mensaje. Recorta la factura
sustancialmente y no cuesta nada en calidad.

Para que funcione, **mantén el system prompt congelado.** Interpolar la fecha de
hoy o el nombre de la persona pone texto volátil al principio del prefijo e
invalida todo lo que viene detrás, en cada petición. Pasa ese tipo de contexto
en el mensaje.

Mira `cache_read` en los logs para confirmar que funciona: en una conversación
sana crece con cada turno mientras `cache_write` se queda pequeño. Clavado a
cero significa que algo está reescribiendo el prefijo — o que se cambió `model`
por uno con un mínimo cacheable más alto (512 tokens en Claude Opus 5, 1024 en
Sonnet 5, 4096 en Haiku 4.5; por debajo no se cachea nada y no falla nada).

**Tienes que encender los logs para ver cualquiera de esto.** Al ser una
librería, este paquete nunca configura logging — y uvicorn configura solo sus
propios loggers, así que un `INFO` de `chat_widget_server` sube a un root logger
sin handler y se descarta. Una línea en tu entrypoint, como hacen los dos
ejemplos:

```python
import logging
logging.basicConfig(level=logging.INFO)
```

Fuera de FastAPI, úsalo directamente:

```python
result = await agent.reply("¿tenéis algo el martes?")
print(result.text, result.blocks)

async for event in agent.stream("¿y el miércoles?", history=result.messages):
    ...  # TextChunk | BlockEmitted | Refused | Completed
```

## Rutas

`create_app` te da una app entera con CORS; `create_router` se enchufa a una app
que ya tengas:

```python
from fastapi import FastAPI
from chat_widget_server import create_router

app = FastAPI()
app.include_router(create_router(agent, path="/api/chat"))
```

Una sola ruta sirve los dos modos del widget. El widget manda
`Accept: text/event-stream` cuando está configurado con `stream: true`, así que
la misma URL responde o con SSE o con un único objeto JSON — sin un segundo
endpoint y sin un flag que mantener sincronizado.

## Memoria de la conversación

El widget manda su propio `history`, y el servidor **lo ignora por defecto**. Ese
historial viene de un navegador, así que un turno de asistente falsificado es un
canal de inyección de prompt directo a tu system prompt. El servidor guarda su
propia copia, indexada por `sessionId`.

`MemorySessionStore` es el valor por defecto y es correcto para un solo proceso.
Antes de levantar más de un worker, cámbialo — el protocolo son dos métodos:

```python
class RedisSessionStore:
    async def load(self, session_id: str) -> list[dict]: ...
    async def save(self, session_id: str, messages: list[dict]) -> None: ...

create_router(agent, store=RedisSessionStore(redis))
```

Indexar por `sessionId` es además lo que hace posible un "borra mi
conversación" — ver más abajo.

## Construye el front gratis

Cada mensaje cuesta dinero, lo que convierte "déjame comprobar el maquetado" en
un hábito caro. `ScriptedAgent` responde con reglas escritas a mano, sin API key
y sin factura, por la misma ruta y con los mismos tipos de evento:

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

El streaming, los bloques, el calendario, CORS, las sesiones y los límites de
uso son todos reales — solo las respuestas están escritas. Lo que no te puede
decir es si tu prompt y las descripciones de tus herramientas hacen que el
modelo elija la correcta; eso necesita una petición de verdad.

`python examples/offline.py` es una demo completa montada así.

## Límites de uso

Activados por defecto, porque un endpoint sin protección es el presupuesto de un
desconocido: un bucle en la consola del navegador puede gastarse un mes de
crédito en una tarde.

```python
create_router(
    agent,
    max_per_minute=15,          # por sesión
    max_per_minute_per_ip=40,   # por llamante, pilla al que rota sessionId
    trust_forwarded_for=False,  # ver abajo
    trusted_proxy_hops=1,       # cuántos proxies tienes de verdad
)
```

Pasa `0` a cualquiera de los dos para desactivarlo. Pasarse del límite devuelve
un **429** con cabecera `Retry-After`, y la llamada cara se salta: al agente no
se llega nunca.

Tres cosas que merece la pena saber:

- **Los contadores son por proceso.** Con varios workers cada uno tiene los
  suyos, así que el límite real es tu número multiplicado por los workers. Pon el
  límite duro en tu proxy, o pasa `limiter=` con un objeto respaldado por Redis
  que implemente `check(key) -> float | None`.
- **`trust_forwarded_for` está desactivado por defecto.** Cualquiera puede poner
  `X-Forwarded-For`, así que confiar en ella sin un proxy delante convierte el
  límite por IP en un valor que elige quien llama. Detrás de un proxy sí la
  necesitas, o todas las peticiones parecen ser del proxy y comparten un cubo.
- **Con ella activada, `trusted_proxy_hops` es lo que lo mantiene honesto.** Un
  proxy *añade* la dirección que vio, así que la cabecera queda
  `<lo que mandó quien llama>, <lo que vio tu proxy>`. Solo la última entrada la
  escribió tu propia infraestructura. Leer la primera —el atajo habitual— le
  permite a un llamante rotar de cubo indefinidamente variando una cabecera que
  él controla. Pon el número de proxies que tienes delante de la app.

### Mantener vivo un turno lento

Un turno con herramienta se queda callado veinte o cuarenta segundos mientras el
modelo piensa y las herramientas corren. Sin nada en el cable, nginx, Cloudflare
y casi cualquier PaaS deciden que la respuesta SSE está inactiva y la cierran —
lo que le llega a la persona como una respuesta que se corta a media frase, sin
un error en ningún log.

La ruta escribe un comentario SSE cada 15 segundos para evitarlo. El widget
ignora cualquier línea que empiece por `:`, así que nada llega a la conversación.

```python
create_router(agent, heartbeat_seconds=15.0)   # 0 lo desactiva
```

Súbelo si tu proxy es paciente, bájalo si no lo es; un comentario por visitante
cada 15 segundos no cuesta nada.

## Antes de ponerlo en producción

- **CORS**: `create_app` exige `allowed_origins` y rechaza `"*"`. El widget te
  llama desde la página de alguien; un origen permisivo deja que cualquier sitio
  de internet se gaste tus tokens.
- **Límites de uso**: activados por defecto — comprueba que los números encajan
  con tu tráfico, y lee el aviso de los contadores por proceso antes de levantar
  varios workers.
- **Tu API key** va en el entorno (`ANTHROPIC_API_KEY`), nunca en la
  configuración del widget y nunca en el repo.
- **Datos personales**: un chat que pide un nombre, un teléfono o un síntoma está
  recogiendo datos personales, y en contextos de salud son categoría especial
  según el RGPD. Ten una base legal, di qué haces con los mensajes y sé capaz de
  borrar una conversación — que es la razón por la que las sesiones van indexadas.
- **Coste**: cada mensaje es una llamada a la API, y las rondas de herramienta lo
  multiplican: un mensaje que usa una herramienta cuesta dos. `max_iterations`
  limita las rondas, la caché de prompt recorta la parte repetida y
  `ScriptedAgent` mantiene el desarrollo gratis. Los números `in` / `out` /
  `cache_read` que se loguean son la base para estimar — no una suposición.

## Desarrollo

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                        # sin API key
python examples/offline.py    # demo sin API key y sin factura
python examples/clinic.py     # demo con modelo real (necesita ANTHROPIC_API_KEY)
```

## Licencia

MIT
