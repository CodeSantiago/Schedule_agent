"""
Capitán Barbería — WhatsApp Bot API v3.0
Linear flow for ManyChat: Service → Day → Time → Barber → Name → Confirm
"""

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo
from groq import Groq
from dotenv import load_dotenv

import asyncio
import uvicorn
import json
import logging
import os
import re
import time
import redis

BUENOS_AIRES = ZoneInfo("America/Argentina/Buenos_Aires")

load_dotenv(override=True)

from sheets import (
    BARBEROS,
    DIAS_VALIDOS,
    HORARIOS,
    _barbero_disponible_en_horario,
    _cache_config,
    _crear_pestaña_config,
    bloquear_slot_cb,
    buscar_turnos_por_contacto,
    cancelar_turno,
    guardar_turno_en_sheets,
    invalidar_cache_disponibilidad,  # FASE 1.4
    obtener_ausentes_dia,
    obtener_bot_activo,
    obtener_disponibilidad,
    obtener_disponibilidad_cached,  # FASE 1.4
    verificar_turno_cb,
)

# ============================================================
# CONFIGURATION
# ============================================================

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
API_SECRET_KEY = os.environ.get("API_SECRET_KEY", "").strip()
ADMIN_PHONES = os.environ.get("ADMIN_PHONES", "").strip()

SERVICIOS = {"C": "Corte", "B": "Barba", "CB": "Corte y Barba"}
PRECIOS = {
    "C": {"efectivo": 19000, "otros": 21000},
    "B": {"efectivo": 17000, "otros": 19000},
    "CB": {"efectivo": 24000, "otros": 26000},
}


SOLO_CORTE = {
    "O": ["11:30", "19:30"],
    "R": ["15:00", "19:00"],
    "A": ["11:30", "19:30"],
}

SESSION_TIMEOUT = 1800
MAX_MESSAGES_PER_MINUTE = 10

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

groq_client = Groq(api_key=GROQ_API_KEY, timeout=5.0) if GROQ_API_KEY else None  # FASE 1.5

# Redis for session persistence (survives Railway restarts).
# REDIS_URL is REQUIRED. If missing, we fail startup instead of
# silently falling back to in-memory (which reproduced the original bug).
try:
    REDIS_URL = os.environ["REDIS_URL"].strip()
    if not REDIS_URL:
        raise KeyError("REDIS_URL is empty")
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
    logger.info("[REDIS] Connected OK")
except KeyError as e:
    logger.critical(f"[REDIS] REDIS_URL not configured ({e}). Aborting startup.")
    raise SystemExit(1)
except redis.RedisError as e:
    logger.critical(f"[REDIS] Could not connect to Redis: {e}. Aborting startup.")
    raise SystemExit(1)
REDIS_KEY_PREFIX = "sesion:"
REDIS_SESSION_TTL = 1800  # 30 min

# Force 1 worker on Railway temporarily to stabilise production.
# Once stable, migrate locks/debounce to distributed Redis and scale to N workers.

rate_limits: Dict[str, List[float]] = {}
blocked_barbers: Dict[str, str] = {}
_DEBOUNCE_SECONDS = 0  # Temporarily disabled to stabilise

# FASE 1.1 — dedup by message_id (idempotency)
_processed_ids: Dict[str, float] = {}
_DEDUP_TTL = 300  # 5 min

# FASE 1.3 — lock per user_id (prevents concurrent double processing)
_user_locks: Dict[str, asyncio.Lock] = {}
_user_locks_guard = asyncio.Lock()  # protects the lock dict create/delete

app = FastAPI(title="Capitán Barbería API", version="3.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# MODELS
# ============================================================


class MessageRequest(BaseModel):
    user_id: str
    message: str
    user_name: Optional[str] = None
    message_id: Optional[str] = None  # FASE 1.1 — idempotent dedup


class MessageResponse(BaseModel):
    response: str
    action: Optional[str] = None


class TakeoverRequest(BaseModel):
    user_id: str


class AdminRequest(BaseModel):
    user_id: str
    command: str
    barbero: Optional[str] = None
    motivo: Optional[str] = None


# ============================================================
# SECURITY
# ============================================================


def verify_api_key(authorization: Optional[str] = None) -> bool:
    if not API_SECRET_KEY:
        return True
    return authorization == f"Bearer {API_SECRET_KEY}"


def check_rate_limit(user_id: str) -> Tuple[bool, str]:
    now = time.time()
    if user_id not in rate_limits:
        rate_limits[user_id] = []
    rate_limits[user_id] = [t for t in rate_limits[user_id] if now - t < 60]
    if len(rate_limits[user_id]) >= MAX_MESSAGES_PER_MINUTE:
        return False, "Esperame unos segundos."
    rate_limits[user_id].append(now)
    return True, ""


# FASE 1.1 — idempotent dedup by message_id
def _already_processed(message_id: str) -> bool:
    """True if the message_id was already processed within the TTL window."""
    if not message_id:
        return False
    now = time.time()
    # lazy cleanup
    expired = [k for k, t in _processed_ids.items() if now - t > _DEDUP_TTL]
    for k in expired:
        _processed_ids.pop(k, None)
    return message_id in _processed_ids


def _mark_processed(message_id: str) -> None:
    if message_id:
        _processed_ids[message_id] = time.time()


# FASE 1.3 — lock per user_id
async def _get_user_lock(user_id: str) -> asyncio.Lock:
    """Return (creating if needed) an asyncio.Lock unique per user_id."""
    async with _user_locks_guard:
        lock = _user_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _user_locks[user_id] = lock
        return lock


# ============================================================
# HELPERS
# ============================================================


def today_weekday() -> Optional[str]:
    days = {0: "LUNES", 1: "MARTES", 2: "MIERCOLES", 3: "JUEVES", 4: "VIERNES"}
    return days.get(datetime.now(BUENOS_AIRES).weekday())


def tomorrow_weekday() -> Optional[str]:
    d = (datetime.now(BUENOS_AIRES).weekday() + 1) % 7
    days = {0: "LUNES", 1: "MARTES", 2: "MIERCOLES", 3: "JUEVES", 4: "VIERNES"}
    return days.get(d)


def current_time() -> str:
    return datetime.now(BUENOS_AIRES).strftime("%H:%M")


def _barber_is_absent(cod: str, day: str) -> bool:
    return cod in obtener_ausentes_dia(SPREADSHEET_ID, day)


def _barber_is_active(cod: str, day: str) -> bool:
    return cod not in blocked_barbers and not _barber_is_absent(cod, day)


def code_to_name(cod: Optional[str]) -> str:
    if not cod:
        return ""
    return BARBEROS.get(cod, {}).get("nombre", cod)


def name_to_code(nombre: Optional[str]) -> Optional[str]:
    if not nombre:
        return None
    n = nombre.lower().strip()
    mapa = {
        "omar": "O",
        "rodrigo": "R",
        "rodri": "R",
        "agustín": "A",
        "agustin": "A",
        "agus": "A",
        "agusti": "A",
        "enzo": "E",
    }
    return mapa.get(n)


def resolve_day(dia_raw: Optional[str]) -> Optional[str]:
    if not dia_raw:
        return None
    d = dia_raw.upper().strip()
    if d == "HOY":
        return today_weekday()
    if d in ("MAÑANA", "MANANA"):
        return tomorrow_weekday()
    if d in DIAS_VALIDOS:
        return d
    return None


# ============================================================
# DETERMINISTIC DETECTION (fast, free, reliable)
# ============================================================


def detect_service(text: str) -> Optional[str]:
    """Detect service using simple rules. None if unclear."""
    t = text.lower()
    # CB first (more specific)
    cb_patterns = [
        "corte y barba",
        "corte con barba",
        "barba y corte",
        "pelo y barba",
        "barba y pelo",
        "los dos",
        "ambos",
        "completo",
        "combo",
        "full",
        "las dos cosas",
        "los 2",
        "corte barba",
        "barba corte",
    ]
    if any(p in t for p in cb_patterns):
        return "CB"
    # Barba (only if it does NOT mention corte/pelo)
    barba_patterns = [
        "barba",
        "afeitarme",
        "afeitar",
        "perfilado",
        "perfilar",
        "rasurar",
    ]
    if any(p in t for p in barba_patterns) and not any(
        p in t for p in ["corte", "pelo", "cabello"]
    ):
        return "B"
    # Corte (only if it does NOT mention barba)
    corte_patterns = [
        "corte",
        "cortarme",
        "pelo",
        "cabello",
        "cortame",
        "fade",
        "degradado",
        "rapado",
        "desvanecido",
        "cortecito",
        "recorte",
    ]
    if any(p in t for p in corte_patterns) and "barba" not in t:
        return "C"
    return None


def detect_day(text: str) -> Optional[str]:
    """Detect day. Returns valid day, 'FIN_DE_SEMANA', or None."""
    t = text.lower()
    # Weekend
    if any(d in t for d in ["sabado", "sábado", "domingo", "finde", "fin de semana"]):
        return "FIN_DE_SEMANA"
    # Today / now / already
    if any(d in t for d in ["hoy", "ahora"]) or re.search(r"\bya\b", t):
        return "HOY"
    # Tomorrow (careful: "mañana" also = "morning")
    # Only if "mañana" means day, not time range
    if re.search(r"\bmañana\b|\bmanana\b", t) and not any(
        p in t
        for p in [
            "por la mañana",
            "a la mañana",
            "de mañana",
            "por la manana",
            "a la manana",
        ]
    ):
        weekday = datetime.now(BUENOS_AIRES).weekday()
        tomorrow = (weekday + 1) % 7
        if tomorrow >= 5:  # Saturday (5) or Sunday (6)
            return "FIN_DE_SEMANA"
        return "MAÑANA"
    # Weekdays
    days_map = {
        "lunes": "LUNES",
        "martes": "MARTES",
        "miercoles": "MIERCOLES",
        "miércoles": "MIERCOLES",
        "jueves": "JUEVES",
        "viernes": "VIERNES",
    }
    for word, day in days_map.items():
        if word in t:
            return day
    return None


def detect_time(text: str) -> Optional[str]:
    """Detect time in HH:MM format. Ignores bare digits (1-9) to avoid
    confusing with numeric menu selection."""
    t = text.lower().replace(".", ":").replace(",", ":")
    # If the message is just 1-2 digits (menu selection), it's not a time
    if re.match(r"^\s*\d{1,2}\.?\s*$", t):
        return None
    t = re.sub(r"hs|hrs|horas", "", t)
    # Format HHMM without separator (e.g. "1130", "1430")
    t = re.sub(r"\b(\d{2})(\d{2})\b", r"\1:\2", t)
    t = re.sub(r"(\d{1,2})\s*y\s*media", r"\1:30", t)
    t = re.sub(r"(\d{1,2})\s*y\s*treinta", r"\1:30", t)
    match = re.search(r"(\d{1,2})(?::(\d{2}))?", t)
    if match:
        hour = int(match.group(1))
        mins = match.group(2) or "00"
        if 1 <= hour <= 9:
            hour += 12
        if 10 <= hour <= 19:
            h = f"{hour:02d}:{mins}"
            if h in HORARIOS:
                return h
    return None


def detect_barber(text: str) -> Optional[str]:
    """Detect barber by name. Returns code or None."""
    t = text.lower()
    # Agustín: variants and diminutives
    if re.search(r"\bagust[ií]n\b|\bagustin\b|\bagus\b|\bagusti\b", t):
        return "A"
    # Rodrigo: variants and diminutives
    if re.search(r"\brodrigo\b|\brodri\b", t):
        return "R"
    # Omar: word boundary to avoid false positives ("tomar" → no match)
    if re.search(r"\bomar\b", t):
        return "O"
    # Enzo
    if re.search(r"\benzo\b", t):
        return "E"
    return None


def detect_affirmative(text: str) -> bool:
    """Detect SIMPLE affirmative response."""
    t = text.lower().strip()
    exactos = {
        "si",
        "sí",
        "dale",
        "ok",
        "okay",
        "sip",
        "sep",
        "claro",
        "de una",
        "perfecto",
        "genial",
        "listo",
        "bueno",
        "confirmo",
        "me sirve",
        "obvio",
        "sale",
        "joya",
        "va",
        "sisi",
        "dale dale",
        "okey",
        "si dale",
        "sí dale",
        "si va",
        "sí va",
        "si perfecto",
        "sí perfecto",
        "si claro",
        "sí claro",
        "si gracias",
        "sí gracias",
        "me va",
        "agendalo",
        "reservalo",
        "mandalo",
        "ese",
        "ese va",
        "ese mismo",
        "a esa hora",
        "me queda bien",
        "me viene bien",
    }
    return t in exactos


def detect_negative(text: str) -> bool:
    """Detect SIMPLE negative response."""
    t = text.lower().strip()
    exactos = {
        "no",
        "nop",
        "nope",
        "nah",
        "na",
        "no gracias",
        "para nada",
        "ni ahí",
        "ni ahi",
        "paso",
        "mejor no",
        "no me sirve",
        "no va",
        "no quiero",
        "no puedo",
        "no no",
        "cancelar",
        "ninguno",
        "no me queda",
        "no me viene",
        "no puedo a esa hora",
    }
    return t in exactos


def detect_farewell(text: str, in_flow: bool = False) -> bool:
    """
    Detect farewell. KEY: if in_flow=True, stricter.
    'gracias' is NOT a farewell if we are mid-booking.
    """
    t = text.lower().strip()

    # Unambiguous farewells (always farewell)
    clear_farewells = [
        "chau",
        "adios",
        "adiós",
        "nos vemos",
        "hasta luego",
        "hasta pronto",
        "bye",
        "buenas noches",
        "luego les escribo",
        "después hablamos",
    ]
    if any(d in t for d in clear_farewells):
        return True

    return False


def _is_thanks(text: str) -> bool:
    """Detect 'gracias' with variations: repeated letters, punctuation, caps, etc."""
    t = re.sub(r"[^a-záéíóúñü\s]", "", text.lower().strip())
    return bool(re.search(r"\bg+r+a+c+i+a+s+\b", t))


def detect_thanks(text: str) -> bool:
    """Detect thanks (gracias, etc.)."""
    t = text.lower().strip()
    if _is_thanks(t):
        return True
    return any(
        w in t
        for w in [
            "te agradezco",
            "les agradezco",
            "agradezco",
        ]
    )


def detect_greeting(text: str) -> bool:
    t = text.lower().strip()
    # "gracias" and similar are NOT greetings
    if _is_thanks(t) or "agradezco" in t:
        return False
    greetings = [
        "hola",
        "buenas",
        "buen dia",
        "buen día",
        "buenas tardes",
        "buenos dias",
        "buenos días",
        "qué tal",
        "que tal",
        "hey",
    ]
    return any(s in t for s in greetings)


def detect_cancellation(text: str) -> bool:
    t = text.lower()
    return any(
        p in t
        for p in [
            "cancelar",
            "cancelo",
            "cancelar turno",
            "cancelo turno",
            "anular",
            "anulo",
            "borrar turno",
            "eliminar turno",
        ]
    )


def detect_reschedule(text: str) -> bool:
    t = text.lower()
    return any(
        p in t
        for p in [
            "reagendar",
            "cambiar turno",
            "cambiar el turno",
            "mover turno",
            "mover el turno",
            "cambiar horario",
            "cambiar el horario",
            "reprogramar",
            "modificar turno",
        ]
    )


def detect_prices(text: str) -> bool:
    t = text.lower()
    return any(
        p in t
        for p in [
            "precio",
            "cuánto",
            "cuanto",
            "costo",
            "vale",
            "tarifa",
            "cobran",
            "cuánto sale",
            "cuanto sale",
            "cuánto cuesta",
            "cuanto cuesta",
        ]
    )


def detect_no_preference(text: str) -> bool:
    t = text.lower().strip()
    return any(
        p in t
        for p in [
            "cualquiera",
            "el que haya",
            "el que sea",
            "el que esté",
            "da igual",
            "me da igual",
            "me da lo mismo",
            "sin preferencia",
            "no importa",
            "indistinto",
            "no tengo preferencia",
            "el que pueda",
            "el disponible",
            "con quien sea",
        ]
    )


def detect_ask_human(text: str) -> bool:
    t = text.lower()
    return any(
        p in t
        for p in [
            "hablar con alguien",
            "hablar con una persona",
            "atención humana",
            "atencion humana",
            "quiero hablar con",
            "necesito hablar con",
            "pasar con un barbero",
            "comunicarme con",
            "me comunico con",
        ]
    )


# ============================================================
# LLM — FALLBACK ONLY for ambiguous messages
# ============================================================

SYSTEM_PROMPT_FALLBACK = """Sos el asistente de Capitán Barbería. Analizá este mensaje de un cliente y respondé SOLO con JSON.

CONTEXTO:
- Barberos: Omar, Rodrigo, Agustín. Enzo YA NO trabaja.
- Servicios: Corte (C), Barba (B), Corte y Barba (CB)
- Atendemos LUNES a VIERNES, 10:00 a 19:30
- Hoy es {dia_hoy} ({fecha_hoy})
- Estado de la conversación: {estado}

Respondé SOLO JSON:
{{
  "intencion": "agendar" | "cancelar" | "reagendar" | "consultar_precios" | "saludo" | "despedida" | "afirmativo" | "negativo" | "dar_nombre" | "elegir_barbero" | "sin_preferencia_barbero" | "pedir_humano" | "otro",
  "servicio": "C" | "B" | "CB" | null,
  "dia": "LUNES"|"MARTES"|"MIERCOLES"|"JUEVES"|"VIERNES"|"HOY"|"MAÑANA"|"FIN_DE_SEMANA" | null,
  "horario": "HH:MM" | null,
  "barbero": "Omar"|"Rodrigo"|"Agustín"|"Enzo" | null,
  "nombre": "string" | null
}}

REGLAS:
- "hoy"/"ahora"/"ya" → dia="HOY"
- "mañana" (como día, no rango) → dia="MAÑANA"
- "a las 3" = 15:00 (contexto barbería)
- Si parece un nombre propio → intencion="dar_nombre"
- "gracias" en medio de un turno = "afirmativo", NO "despedida"
- Sin markdown, sin backticks."""


def classify_with_llm(text: str, state: str) -> dict:
    """Fallback: use LLM only when deterministic detection is insufficient."""
    if not groq_client:
        return {"intencion": "otro"}
    t_groq_start = time.time()  # FASE 1.6 — timing Groq
    try:
        hoy = today_weekday()
        prompt = SYSTEM_PROMPT_FALLBACK.format(
            dia_hoy=hoy.lower() if hoy else "fin de semana",
            fecha_hoy=datetime.now(BUENOS_AIRES).strftime("%d/%m/%Y"),
            estado=state,
        )
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
            temperature=0,
            max_tokens=200,
        )
        raw = (response.choices[0].message.content or "").strip()
        raw = re.sub(r"```json?\s*", "", raw)
        raw = re.sub(r"```", "", raw)
        result = json.loads(raw)
        t_groq_end = time.time()
        logger.info(
            f"[GROQ] elapsed={round((t_groq_end - t_groq_start) * 1000)}ms "
            f"intent={result.get('intencion')} "
            f"model=llama-3.3-70b"
        )
        logger.info(f"[LLM FALLBACK] '{text}' → {result}")
        return result
    except Exception as e:
        t_groq_end = time.time()
        # FASE 1.5 — explicit timeout / rate limit log
        logger.error(
            f"[GROQ] elapsed={round((t_groq_end - t_groq_start) * 1000)}ms "
            f"ERROR {type(e).__name__}: {e}"
        )
        return {"intencion": "otro"}


# ============================================================
# AVAILABILITY
# ============================================================


def get_available_slots(
    day: str,
    barber_code: Optional[str] = None,
    service: str = "C",
    exclude: Optional[List[str]] = None,
) -> List[Tuple[str, str]]:
    """Return list of (time, barber_code) available slots."""
    try:
        # FASE 1.4 — use cached version to reduce Sheets calls
        disp = obtener_disponibilidad_cached(SPREADSHEET_ID, day)
        now = current_time()
        today = today_weekday()
        exclude = exclude or []
        results = []

        for time_slot in HORARIOS:
            if day == today and time_slot <= now:
                continue
            if time_slot in exclude:
                continue

            info = disp.get(time_slot, {})
            disponibles = info.get("disponibles", [])

            # Filter blocked/absent
            active = []
            for nombre in disponibles:
                cod = next(
                    (c for c, inf in BARBEROS.items() if inf["nombre"] == nombre), None
                )
                if cod and _barber_is_active(cod, day):
                    active.append((nombre, cod))

            if not active:
                continue

            if barber_code:
                nombre_b = BARBEROS.get(barber_code, {}).get("nombre")
                if not any(n == nombre_b for n, _ in active):
                    continue
                if time_slot in SOLO_CORTE.get(barber_code, []) and service != "C":
                    continue
                if service == "CB":
                    idx_h = HORARIOS.index(time_slot)
                    if idx_h + 1 >= len(HORARIOS):
                        continue
                    siguiente = HORARIOS[idx_h + 1]
                    if not _barbero_disponible_en_horario(barber_code, siguiente):
                        continue
                    if nombre_b not in disp.get(siguiente, {}).get("disponibles", []):
                        continue
                results.append((time_slot, barber_code))
            else:
                for nombre, cod in active:
                    if time_slot in SOLO_CORTE.get(cod, []) and service != "C":
                        continue
                    if service == "CB":
                        idx_h = HORARIOS.index(time_slot)
                        if idx_h + 1 >= len(HORARIOS):
                            continue
                        siguiente = HORARIOS[idx_h + 1]
                        if not _barbero_disponible_en_horario(cod, siguiente):
                            continue
                        if nombre not in disp.get(siguiente, {}).get("disponibles", []):
                            continue
                    results.append((time_slot, cod))

        return results
    except Exception as e:
        logger.error(f"[AVAILABILITY] Error: {e}")
        return []


def get_barbers_for_day(day: str) -> List[str]:
    slots = get_available_slots(day)
    codes = set(cod for _, cod in slots)
    return [BARBEROS[c]["nombre"] for c in sorted(codes) if c in BARBEROS]


# ============================================================
# SESSIONS
# ============================================================


def _new_session() -> dict:
    return {
        "step": "start",
        "service": None,
        "day": None,
        "barber": None,
        "time": None,
        "name": None,
        "rejected_times": [],
        "shown_times": [],
        "last_active": time.time(),
        "enzo_client": False,
        "transferred_to_human": False,
        "transferred_date": "",
        "appointment_to_cancel": None,
        "found_appointments": None,
        "failed_attempts": 0,
        "no_barber_preference": False,
    }


def get_session(user_id: str) -> dict:
    """
    Read session from Redis. If it does not exist or has expired,
    create a new one (preserving flags that must survive reset).
    Returns a dict the caller can mutate in-place; changes are
    persisted with set_session().
    """
    now = time.time()

    # Read from Redis
    raw = None
    if redis_client:
        try:
            raw = redis_client.get(REDIS_KEY_PREFIX + user_id)
        except Exception as e:
            logger.error(f"[REDIS] get error user={user_id}: {e}")

    if raw:
        try:
            s = json.loads(raw)
            # Validate timeout
            if now - s.get("last_active", 0) > SESSION_TIMEOUT:
                preserved = {
                    "enzo_client": s.get("enzo_client", False),
                    "transferred_to_human": s.get("transferred_to_human", False),
                    "transferred_date": s.get("transferred_date", ""),
                }
                s = _new_session()
                s.update(preserved)
        except json.JSONDecodeError:
            s = _new_session()
    else:
        s = _new_session()

    s["last_active"] = now
    return s


def set_session(user_id: str, sesion: dict) -> None:
    """Persist the session in Redis with TTL."""
    if not redis_client:
        return
    try:
        sesion["last_active"] = time.time()
        redis_client.setex(
            REDIS_KEY_PREFIX + user_id,
            REDIS_SESSION_TTL,
            json.dumps(sesion, ensure_ascii=False, default=str),
        )
    except Exception as e:
        logger.error(f"[REDIS] setex error user={user_id}: {e}")


def reset_session(user_id: str, sesion: dict = None):
    new = _new_session()
    if sesion is not None:
        preserved = {
            "enzo_client": sesion.get("enzo_client", False),
            "transferred_to_human": sesion.get("transferred_to_human", False),
            "transferred_date": sesion.get("transferred_date", ""),
        }
        sesion.clear()
        sesion.update(new)
        sesion.update(preserved)
        set_session(user_id, sesion)
    else:
        preserved = {}
        if redis_client:
            try:
                raw = redis_client.get(REDIS_KEY_PREFIX + user_id)
                if raw:
                    s = json.loads(raw)
                    preserved = {
                        "enzo_client": s.get("enzo_client", False),
                        "transferred_to_human": s.get("transferred_to_human", False),
                        "transferred_date": s.get("transferred_date", ""),
                    }
            except Exception as e:
                logger.error(f"[REDIS] reset error user={user_id}: {e}")
        new.update(preserved)
        set_session(user_id, new)


# ============================================================
# MAIN LOGIC
# ============================================================


def process_message(user_id: str, text: str) -> Tuple[str, Optional[str]]:
    if not text or not text.strip():
        return "", None

    text = text.strip()[:500]

    # Rate limit
    ok, error = check_rate_limit(user_id)
    if not ok:
        return error, None

    # Transferred check
    sesion = get_session(user_id)
    if sesion.get("transferred_to_human"):
        if sesion.get("transferred_date") == datetime.now(BUENOS_AIRES).strftime(
            "%Y-%m-%d"
        ):
            return "BOT_SILENCED", None
        sesion["transferred_to_human"] = False
        sesion["transferred_date"] = ""

    step = sesion["step"]
    in_flow = step != "start"

    logger.info(f"[{user_id}] MSG: '{text}' | STEP: {step}")

    # ── STEP 1: Deterministic detection (fast, no LLM) ──
    service_det = detect_service(text)
    day_det = detect_day(text)
    time_det = detect_time(text)
    barber_det = detect_barber(text)
    is_affirmative = detect_affirmative(text)
    is_negative = detect_negative(text)
    is_farewell = detect_farewell(text, in_flow=in_flow)
    is_greeting = detect_greeting(text)
    is_cancellation = detect_cancellation(text)
    is_reschedule = detect_reschedule(text)
    is_prices = detect_prices(text)
    is_no_pref = detect_no_preference(text)
    is_ask_human = detect_ask_human(text)
    is_thanks = detect_thanks(text)

    # ── STEP 2: If nothing was detected, use LLM as fallback ──
    llm_result = None
    nothing_detected = (
        not service_det
        and not day_det
        and not time_det
        and not barber_det
        and not is_affirmative
        and not is_negative
        and not is_farewell
        and not is_greeting
        and not is_cancellation
        and not is_reschedule
        and not is_prices
        and not is_no_pref
        and not is_ask_human
        and not is_thanks
    )

    if nothing_detected or (step == "awaiting_name"):
        state_desc = f"step={step}, service={sesion.get('service')}, day={sesion.get('day')}, barber={code_to_name(sesion.get('barber'))}"
        llm_result = classify_with_llm(text, state_desc)

        # Incorporate what the LLM detected (only if deterministic did not)
        if not service_det and llm_result.get("servicio"):
            service_det = llm_result["servicio"]
        if not day_det and llm_result.get("dia"):
            day_det = llm_result["dia"]
        if (
            not time_det
            and llm_result.get("horario")
            and llm_result["horario"] in HORARIOS
        ):
            time_det = llm_result["horario"]
        if not barber_det and llm_result.get("barbero"):
            barber_det = name_to_code(llm_result["barbero"])
        if llm_result.get("intencion") == "afirmativo":
            is_affirmative = True
        if llm_result.get("intencion") == "negativo":
            is_negative = True
        if llm_result.get("intencion") == "despedida":
            is_farewell = True
        if llm_result.get("intencion") == "saludo":
            if not is_thanks:
                is_greeting = True
        if llm_result.get("intencion") == "dar_nombre":
            pass  # Handled below
        if llm_result.get("intencion") == "sin_preferencia_barbero":
            is_no_pref = True
        if llm_result.get("intencion") == "pedir_humano":
            is_ask_human = True
        if llm_result.get("intencion") == "cancelar":
            is_cancellation = True
        if llm_result.get("intencion") == "reagendar":
            is_reschedule = True
        if llm_result.get("intencion") == "consultar_precios":
            is_prices = True

    logger.info(
        f"[{user_id}] DET: srv={service_det} day={day_det} tm={time_det} "
        f"bar={barber_det} af={is_affirmative} neg={is_negative} "
        f"bye={is_farewell} grt={is_greeting} llm={llm_result is not None}"
    )

    # ── STEP 3: Immediate actions (before state machine) ──

    if is_ask_human:
        reset_session(user_id, sesion)
        return _msg_transfer(), "transfer_to_human"

    # Enzo
    if barber_det == "E":
        sesion["enzo_client"] = True
        barber_det = None
        return _msg_enzo(), None

    # Thanks outside flow → friendly farewell
    if (
        is_thanks
        and not in_flow
        and not service_det
        and not day_det
        and not barber_det
        and not time_det
    ):
        reset_session(user_id, sesion)
        return "¡Gracias a vos! Cuando necesites, acá estamos 💈", None

    # Prices
    if is_prices:
        return (
            _msg_prices(service_det or sesion.get("service")),
            None,
        )

    # Cancel (only if not already in that flow)
    if is_cancellation and step not in (
        "awaiting_cancellation",
        "selecting_cancel_appointment",
    ):
        return _start_cancellation(user_id, sesion)

    # Reschedule
    if is_reschedule and step not in (
        "awaiting_reschedule",
        "selecting_reschedule_appointment",
    ):
        return _start_reschedule(user_id, sesion)

    # Farewell (ONLY if not in active flow AND no booking data)
    if (
        is_farewell
        and not in_flow
        and not service_det
        and not day_det
        and not barber_det
        and not time_det
    ):
        reset_session(user_id, sesion)
        return (
            "Hasta luego, que tengas un buen día. ¡Te esperamos en tu próxima visita! 💈",
            None,
        )

    # Weekend
    if day_det == "FIN_DE_SEMANA":
        return (
            "Los fines de semana Capitán permanece cerrado! Podemos ayudarte con algo más? 💈",
            None,
        )

    # ── STEP 4: Update session with detected data ──
    if service_det and service_det in SERVICIOS and step != "awaiting_name":
        if service_det != sesion.get("service"):
            sesion["service"] = service_det
            sesion["time"] = None
            sesion["rejected_times"] = []

    resolved_day = resolve_day(day_det)
    if (
        resolved_day
        and resolved_day != sesion.get("day")
        and step != "awaiting_name"
    ):
        sesion["day"] = resolved_day
        sesion["time"] = None
        sesion["rejected_times"] = []

    if barber_det and barber_det in BARBEROS and step != "awaiting_name":
        if barber_det != sesion.get("barber"):
            sesion["barber"] = barber_det
            sesion["time"] = None
            sesion["rejected_times"] = []

    # Only set time if we are in a step where it makes sense
    # In awaiting_time, DO NOT set: list-number selection takes priority
    if time_det and step == "awaiting_confirmation":
        sesion["time"] = time_det

    # ── STEP 5: State machine ──
    result = _flow(
        user_id,
        sesion,
        text,
        is_affirmative,
        is_negative,
        is_no_pref,
        is_farewell,
        is_greeting,
        llm_result,
        time_det,
    )
    # Persist session in Redis after flow mutated state
    set_session(user_id, sesion)
    return result


# ============================================================
# STATE MACHINE
# ============================================================


def _extract_number(text: str) -> Optional[int]:
    """Extract a simple number from text (for menu selection)."""
    t = text.strip()
    match = re.match(r"^(\d{1,2})\.?$", t)
    if match:
        return int(match.group(1))
    return None


def _is_next_dates(text: str) -> bool:
    """Detect if the user wants 'próximas fechas' / another far-off day."""
    t = text.lower().strip()
    return any(
        p in t
        for p in [
            "próximas fechas",
            "proximas fechas",
            "otro dia",
            "otro día",
            "próxima semana",
            "proxima semana",
            "la semana que viene",
            "más adelante",
            "mas adelante",
        ]
    )


def _msg_menu() -> str:
    return (
        "Hola!! Soy el Bot de Capi 💈\n"
        "Te voy a ayudar a agendar con nosotros!! 😊\n\n"
        "¿Qué necesitás?"
    )


def _msg_ask_service() -> str:
    return (
        "¿Qué tipo de servicio querías realizarte?\n\n"
        "Recordá que abonando en efectivo tenés un 10% off!"
    )


def _msg_ask_day(sesion: dict) -> str:
    return (
        "Oka! Perfecto, ¿para qué día buscabas turno?\n\n"
        "(En caso de próximas fechas, te derivamos con un profesional)"
    )


def _msg_ask_barber(sesion: dict) -> str:
    return (
        "Ya casi estamos... Tenés barbero de preferencia?\n\n"
        "Todos son excelentes! 💈\n"
        "Si no tenés preferencia, escribí 'cualquiera'"
    )


def _show_available_times(sesion: dict) -> Tuple[str, Optional[str]]:
    """Show the list of available times as a numbered list."""
    day = sesion["day"]
    barber = sesion.get("barber")
    service = sesion.get("service", "C")

    # If "ANY", search all barbers
    barber_code = None if barber == "ANY" else barber

    slots = get_available_slots(day, barber_code, service)

    if not slots:
        if barber and barber != "ANY":
            name_b = code_to_name(barber)
            sesion["barber"] = None
            sesion["no_barber_preference"] = False
            sesion["step"] = "awaiting_barber"
            sesion["failed_attempts"] = 0
            return (
                f"{name_b} no tiene turnos disponibles el {day.lower()}.\n"
                "Elegí otro barbero.\n\n" + _msg_ask_barber(sesion),
                "ask_barber",
            )
        sesion["day"] = None
        sesion["step"] = "awaiting_day"
        return (
            f"No hay turnos para el {day.lower()}. Probá otro día.",
            "ask_day",
        )

    # Get unique times
    hours = sorted(set(h for h, _ in slots))

    sesion["shown_times"] = hours
    sesion["step"] = "awaiting_time"
    sesion["failed_attempts"] = 0

    # Build message
    service_n = SERVICIOS[service].lower()
    name_b = code_to_name(barber_code) if barber_code else None

    header = f"Oka! Los horarios disponibles para {service_n} el {day.lower()}"
    if name_b:
        header += f" con {name_b}"
    header += " son:\n\n"

    lines = [f"  {i + 1}. {h}" for i, h in enumerate(hours)]

    return (
        header
        + "\n".join(lines)
        + "\n\nRespondé con el número de opción del horario que te sirva.",
        "ask_time",
    )


def _flow(
    user_id: str,
    sesion: dict,
    text: str,
    is_affirmative: bool,
    is_negative: bool,
    is_no_pref: bool,
    is_farewell: bool,
    is_greeting: bool,
    llm_result: Optional[dict],
    time_det: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """
    Linear flow for ManyChat v3.1:
    Menu → Service → Day → Barber → Times (list) → Name → Confirm

    Returns (text, action). ManyChat uses 'action' to decide which flow to show.
    Possible actions:
      - "ask_menu"           → ManyChat shows buttons: Book / Prices / Talk
      - "ask_service"        → ManyChat shows buttons: Corte / CyB / Barba
      - "ask_day"            → ManyChat shows buttons: Today / Tomorrow / Next dates
      - "ask_barber"         → ManyChat shows buttons: Omar / Rodrigo / Agustín
      - "ask_time"           → ManyChat shows list of times or text
      - "ask_name"           → ManyChat shows text input
      - "booking_confirmed"  → ManyChat shows final summary
      - "transfer_to_human"  → ManyChat transfers to human
      - None                 → Informational message, no special flow required
    """

    step = sesion["step"]
    num = _extract_number(text)

    # ── CANCELLATION / RESCHEDULE ──
    if step == "awaiting_cancellation":
        return _process_cancellation(user_id, sesion, is_affirmative, is_negative)

    if step == "awaiting_reschedule":
        return _process_reschedule(user_id, sesion, is_affirmative, is_negative)

    if step in ("selecting_cancel_appointment", "selecting_reschedule_appointment"):
        return _process_appointment_selection(user_id, sesion, text, is_negative, llm_result)

    # ── AWAITING NAME ──
    if step == "awaiting_name":
        nombre = None
        if llm_result and llm_result.get("nombre"):
            nombre = llm_result["nombre"]
        if (
            llm_result
            and llm_result.get("intencion") == "dar_nombre"
            and llm_result.get("nombre")
        ):
            nombre = llm_result["nombre"]

        if nombre:
            nombre_clean = re.sub(r"[^a-záéíóúñüA-ZÁÉÍÓÚÑÜ\s\-\.]", "", nombre).strip()
            if 2 <= len(nombre_clean) <= 50:
                sesion["name"] = nombre_clean.title()
                return _attempt_save(user_id, sesion)

        if is_farewell:
            reset_session(user_id, sesion)
            return (
                "Hasta luego, que tengas un buen día. ¡Te esperamos! 💈",
                None,
            )

        sesion["failed_attempts"] += 1
        if sesion["failed_attempts"] >= 2:
            reset_session(user_id, sesion)
            return _msg_transfer(), "transfer_to_human"
        return (
            "¿Me decís tu nombre y apellido para agendar el turno?",
            "ask_name",
        )

    # ── AWAITING TIME (select from the list shown) ──
    if step == "awaiting_time":
        shown_times = sesion.get("shown_times", [])

        # Clear previous time to avoid carry-over
        sesion["time"] = None

        # Selection by list number (PRIORITY over detect_time)
        if num and shown_times and 1 <= num <= len(shown_times):
            sesion["time"] = shown_times[num - 1]
        elif time_det:
            # Only use detected time if the user wrote an explicit time (not a number)
            sesion["time"] = time_det

        # If a time was selected (by number or by detect_time)
        if sesion.get("time"):
            barber = sesion.get("barber")
            # If "ANY" was chosen, assign the first available barber at that time
            if barber == "ANY":
                sesion["barber"] = _assign_available_barber(sesion)
                if not sesion["barber"]:
                    sesion["time"] = None
                    return (
                        "No hay disponibilidad a esa hora. Elegí otro horario de la lista.",
                        "ask_time",
                    )

            # Verify time
            ok, msg = _verify_time(sesion)
            if ok:
                sesion["step"] = "awaiting_name"
                if sesion.get("no_barber_preference"):
                    barber_text = "con cualquiera"
                else:
                    barber_text = f"con {code_to_name(sesion['barber'])}"
                
                return (
                    f"Último paso!  ‼️ Te pido tu nombre y apellido para confirmar.\n\n"
                    f"📅 *Para agendar:*\n"
                    f"• 📆 Día: {sesion['day'].capitalize()}\n"
                    f"• 🕐 Horario: {sesion['time']}\n"
                    f"• {barber_text}\n\n"
                    f"✍️ ¿Cuál es tu nombre y apellido?",
                    "ask_name",
                )
            else:
                sesion["time"] = None
                return (
                    f"{msg}\nElegí otro horario de la lista.",
                    "ask_time",
                )

        if is_negative:
            reset_session(user_id, sesion)
            return "Dale, sin problema. Cuando necesites, acá estamos 💈", None

        sesion["failed_attempts"] += 1
        if sesion["failed_attempts"] >= 3:
            reset_session(user_id, sesion)
            return _msg_transfer(), "transfer_to_human"
        return (
            "Respondé con el número de opción del horario que te sirva.",
            "ask_time",
        )

    # ── AWAITING BARBER ──
    if step == "awaiting_barber":
        barber_options = ["O", "R", "A"]

        # Number 4 or "cualquiera"
        if num == 4 or is_no_pref:
            sesion["barber"] = "ANY"
            sesion["no_barber_preference"] = True
            return _show_available_times(sesion)

        # Number 1-3: barber selection
        if num and 1 <= num <= 3:
            cod = barber_options[num - 1]
            if _barber_is_active(cod, sesion["day"]):
                sesion["barber"] = cod
                sesion["no_barber_preference"] = False
                return _show_available_times(sesion)
            else:
                name_b = code_to_name(cod)
                return (
                    f"{name_b} no está disponible el {sesion['day'].lower()}.\n"
                    "Elegí otro barbero.\n\n" + _msg_ask_barber(sesion),
                    "ask_barber",
                )

        # Barber detected by name (e.g. "Omar", "Rodrigo")
        if sesion.get("barber") and sesion["barber"] in BARBEROS:
            if _barber_is_active(sesion["barber"], sesion["day"]):
                sesion["no_barber_preference"] = False
                return _show_available_times(sesion)
            else:
                name_b = code_to_name(sesion["barber"])
                sesion["barber"] = None
                return (
                    f"{name_b} no está disponible el {sesion['day'].lower()}.\n"
                    "Elegí otro barbero.\n\n" + _msg_ask_barber(sesion),
                    "ask_barber",
                )

        if is_affirmative or is_negative:
            sesion["barber"] = "ANY"
            sesion["no_barber_preference"] = True
            return _show_available_times(sesion)

        sesion["failed_attempts"] += 1
        if sesion["failed_attempts"] >= 3:
            sesion["barber"] = "ANY"
            sesion["no_barber_preference"] = True
            return _show_available_times(sesion)

        return _msg_ask_barber(sesion), "ask_barber"

    # ── AWAITING DAY ──
    if step == "awaiting_day":
        # Number 3 or "próximas fechas" → transfer to human
        if num == 3 or _is_next_dates(text):
            reset_session(user_id, sesion)
            return _msg_transfer(), "transfer_to_human"

        # Number 1 = Today, 2 = Tomorrow
        if num == 1 and not sesion.get("day"):
            day_h = today_weekday()
            if day_h:
                sesion["day"] = day_h
            else:
                return (
                    "Hoy es fin de semana, no atendemos.\n"
                    "Elegí otra opción.\n\n" + _msg_ask_day(sesion),
                    "ask_day",
                )
        elif num == 2 and not sesion.get("day"):
            day_m = tomorrow_weekday()
            if day_m:
                sesion["day"] = day_m
            else:
                return (
                    "Los fines de semana Capitán permanece cerrado! ¿Qué día de lunes a viernes te queda bien? 💈",
                    "ask_day",
                )

        if sesion.get("day"):
            # Verify day availability
            slots = get_available_slots(
                sesion["day"], None, sesion.get("service", "C")
            )
            if not slots:
                lost_day = sesion["day"]
                sesion["day"] = None
                return (
                    f"No hay turnos para {lost_day.lower()}. Probá otro día.\n\n"
                    + _msg_ask_day(sesion),
                    "ask_day",
                )
            # Advance to barber
            sesion["step"] = "awaiting_barber"
            sesion["failed_attempts"] = 0
            return _msg_ask_barber(sesion), "ask_barber"

        if is_negative or is_farewell:
            reset_session(user_id, sesion)
            return "Hasta luego, ¡te esperamos! 💈", None

        sesion["failed_attempts"] += 1
        if sesion["failed_attempts"] >= 3:
            reset_session(user_id, sesion)
            return _msg_transfer(), "transfer_to_human"

        return _msg_ask_day(sesion), "ask_day"

    # ── AWAITING SERVICE ──
    if step == "awaiting_service":
        # Number: 1=Corte, 2=Corte y Barba, 3=Barba
        if num == 1 and not sesion.get("service"):
            sesion["service"] = "C"
        elif num == 2 and not sesion.get("service"):
            sesion["service"] = "CB"
        elif num == 3 and not sesion.get("service"):
            sesion["service"] = "B"

        if sesion.get("service"):
            sesion["step"] = "awaiting_day"
            sesion["failed_attempts"] = 0
            return _msg_ask_day(sesion), "ask_day"

        if is_farewell:
            reset_session(user_id, sesion)
            return "Hasta luego, ¡te esperamos! 💈", None

        sesion["failed_attempts"] += 1
        if sesion["failed_attempts"] >= 3:
            reset_session(user_id, sesion)
            return _msg_transfer(), "transfer_to_human"

        return _msg_ask_service(), "ask_service"

    # ── AWAITING MENU ──
    if step == "awaiting_menu":
        t = text.lower().strip()

        # Option 1: Book
        is_book = num == 1 or any(
            p in t for p in ["reservar", "turno", "agendar", "reservar un turno"]
        )
        if llm_result and llm_result.get("intencion") == "agendar":
            is_book = True
        # If service was already detected in the text, it's a book
        if sesion.get("service"):
            is_book = True

        if is_book:
            sesion["failed_attempts"] = 0
            if sesion.get("service"):
                # Service already detected → skip to day
                sesion["step"] = "awaiting_day"
                return _msg_ask_day(sesion), "ask_day"
            sesion["step"] = "awaiting_service"
            return _msg_ask_service(), "ask_service"

        # Option 2: Prices
        is_prices_selection = num == 2 or any(
            p in t
            for p in [
                "valores",
                "información sobre valores",
                "informacion sobre valores",
                "precios",
                "nuestros precios",
                "ver precios",
            ]
        )
        if is_prices_selection:
            return (
                _msg_prices(sesion.get("service"))
                + "\n\n¿Querés agendar un turno? Respondé 'Reservar'.",
                "ask_menu",
            )

        # Option 3: Talk to professional
        is_talk = num == 3 or any(
            p in t for p in ["hablar", "profesional", "profesionales", "humano"]
        )
        if is_talk:
            reset_session(user_id, sesion)
            return _msg_transfer(), "transfer_to_human"

        sesion["failed_attempts"] += 1
        if sesion["failed_attempts"] >= 3:
            reset_session(user_id, sesion)
            return _msg_transfer(), "transfer_to_human"
        return _msg_menu(), "ask_menu"

    # ══════════════════════════════════════════════════════════
    # LINEAR FLOW: ask what is missing in order
    # ══════════════════════════════════════════════════════════

    # Farewell
    if is_farewell:
        reset_session(user_id, sesion)
        return "Hasta luego, que tengas un buen día. ¡Te esperamos! 💈", None

    # If the user already brings data (e.g. "quiero un corte hoy"), skip menu
    if sesion.get("service"):
        if not sesion.get("day"):
            sesion["step"] = "awaiting_day"
            sesion["failed_attempts"] = 0
            return _msg_ask_day(sesion), "ask_day"
        if not sesion.get("barber"):
            sesion["step"] = "awaiting_barber"
            sesion["failed_attempts"] = 0
            return _msg_ask_barber(sesion), "ask_barber"
        if not sesion.get("time"):
            return _show_available_times(sesion)
        if not sesion.get("name"):
            sesion["step"] = "awaiting_name"
            sesion["failed_attempts"] = 0
            if sesion.get("no_barber_preference"):
                barber_text = "con cualquiera"
            else:
                barber_text = f"con {code_to_name(sesion['barber'])}"
            return (
                f"📅 {sesion['day'].lower()} a las {sesion['time']} {barber_text}.\n"
                f"¿Nombre y apellido para confirmar?",
                "ask_name",
            )
        return _attempt_save(user_id, sesion)

    # ── DEFAULT: Show menu ──
    sesion["step"] = "awaiting_menu"
    sesion["failed_attempts"] = 0
    return _msg_menu(), "ask_menu"


# ============================================================
# FLOW HELPER FUNCTIONS
# ============================================================


def _available_barbers_at_time(sesion: dict) -> List[str]:
    """Return names of barbers available at the session's time slot."""
    day = sesion.get("day")
    time_slot = sesion.get("time")
    service = sesion.get("service", "C")
    if not day or not time_slot:
        return []
    slots = get_available_slots(day, None, service)
    codes = set()
    for h, cod in slots:
        if h == time_slot:
            codes.add(cod)
    return [BARBEROS[c]["nombre"] for c in sorted(codes) if c in BARBEROS]


def _assign_available_barber(sesion: dict) -> Optional[str]:
    """Assign the first available barber at the session's time slot."""
    day = sesion.get("day")
    time_slot = sesion.get("time")
    service = sesion.get("service", "C")
    if not day or not time_slot:
        return None
    slots = get_available_slots(day, None, service)
    for h, cod in slots:
        if h == time_slot:
            return cod
    return None


def _barber_available_at_time(sesion: dict) -> bool:
    """Check if the session's barber is available at that time."""
    day = sesion.get("day")
    time_slot = sesion.get("time")
    barber = sesion.get("barber")
    service = sesion.get("service", "C")
    if not day or not time_slot or not barber:
        return False
    slots = get_available_slots(day, barber, service)
    return any(h == time_slot for h, _ in slots)


def _validate_and_assign_time(user_id: str, sesion: dict) -> Tuple[str, Optional[str]]:
    """Validate the entered time. If invalid, suggest closest available times."""
    ok, msg = _verify_time(sesion)
    if ok:
        # Valid time → ask barber
        barbers = _available_barbers_at_time(sesion)
        if not barbers:
            sesion["time"] = None
            return (
                f"No hay disponibilidad a las {sesion['time']}. Probá otro horario.",
                "ask_time",
            )
        if len(barbers) == 1:
            sesion["barber"] = _assign_available_barber(sesion)
            sesion["step"] = "awaiting_name"
            name_b = code_to_name(sesion["barber"])
            return (
                f"Te atiende {name_b}.\n"
                f"📅 {sesion['day'].lower()} a las {sesion['time']}.\n"
                f"¿Nombre y apellido?",
                "ask_name",
            )
        sesion["step"] = "awaiting_barber"
        sesion["failed_attempts"] = 0
        return (
            f"A las {sesion['time']} están disponibles:\n\n"
            + "\n".join(f"💈 {b}" for b in barbers)
            + "\n\n¿Con quién preferís? O escribí 'cualquiera'.",
            "ask_barber",
        )

    # Time not available → suggest close ones
    requested_time = sesion["time"]
    sesion["time"] = None
    available_slots = get_available_slots(
        sesion["day"], None, sesion.get("service", "C")
    )
    if not available_slots:
        sesion["day"] = None
        sesion["step"] = "awaiting_day"
        return (
            f"No hay turnos para {sesion.get('day', 'ese día')}. ¿Probamos otro día?",
            "ask_day",
        )

    # Find the 3 closest times
    unique_hours = sorted(set(h for h, _ in available_slots))
    closest = sorted(
        unique_hours, key=lambda h: abs(_time_to_min(h) - _time_to_min(requested_time))
    )[:3]
    closest.sort()

    sesion["step"] = "awaiting_time"
    return (
        f"{msg}\n\nHorarios cercanos disponibles:\n"
        + "\n".join(f"  • {h}" for h in closest)
        + "\n\nEscribí el que te sirva.",
        "ask_time",
    )


def _time_to_min(time_str: str) -> int:
    """Convert HH:MM to minutes for comparison."""
    try:
        parts = time_str.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return 0


def _verify_time(sesion: dict) -> Tuple[bool, str]:
    day = sesion["day"]
    time_slot = sesion["time"]
    barber = sesion.get("barber")
    service = sesion.get("service", "C")

    if day == today_weekday() and time_slot <= current_time():
        return False, f"Ese horario ({time_slot}) ya pasó para hoy."

    if (
        barber
        and barber not in ("ANY", "X")
        and service != "C"
        and time_slot in SOLO_CORTE.get(barber, [])
    ):
        name_b = code_to_name(barber)
        return False, f"A las {time_slot} con {name_b} solo se aceptan cortes."

    slots = get_available_slots(
        day, barber if barber not in ("ANY", "X") else None, service
    )
    if not any(h == time_slot for h, _ in slots):
        return False, f"No hay disponibilidad a las {time_slot}."

    if service == "CB" and barber and barber not in ("ANY", "X"):
        if not verificar_turno_cb(SPREADSHEET_ID, day, time_slot, barber):
            return (
                False,
                "No hay disponibilidad para corte y barba a esa hora (necesita 2 turnos seguidos).",
            )

    return True, ""


def _attempt_save(user_id: str, sesion: dict) -> Tuple[str, Optional[str]]:
    day = sesion["day"]
    time_slot = sesion["time"]
    barber = sesion["barber"]
    service = sesion["service"]
    name = sesion["name"]

    ok, msg = _verify_time(sesion)
    if not ok:
        sesion["time"] = None
        sesion["step"] = "awaiting_time"
        return msg + " Decime otro horario.", "ask_time"

    # If no preference, save "X" in the sheet
    barber_for_sheet = "X" if sesion.get("no_barber_preference") else barber

    try:
        if service == "CB":
            success = bloquear_slot_cb(
                SPREADSHEET_ID, day, time_slot, barber_for_sheet, name, user_id
            )
        else:
            success = guardar_turno_en_sheets(
                SPREADSHEET_ID,
                day,
                {
                    "time": time_slot,
                    "cliente": name,
                    "barbero": barber_for_sheet,
                    "servicio": service,
                    "contacto": user_id,
                },
            )

        if success:
            logger.info(
                f"[{user_id}] APPOINTMENT SAVED: {day} {time_slot} {barber_for_sheet} {name}"
            )
            invalidar_cache_disponibilidad(SPREADSHEET_ID, day)
            resp = _msg_confirm(sesion)
            sesion["transferred_to_human"] = True
            sesion["transferred_date"] = datetime.now(BUENOS_AIRES).strftime(
                "%Y-%m-%d"
            )
            reset_session(user_id, sesion)
            return resp, "booking_confirmed"
        else:
            sesion["time"] = None
            sesion["step"] = "awaiting_time"
            return "Ese horario se acaba de ocupar. Decime otro.", "ask_time"
    except Exception as e:
        logger.error(f"[{user_id}] Error saving: {e}")
        return "Hubo un error. ¿Podés intentar de nuevo?", None


# ============================================================
# CANCELLATION / RESCHEDULE
# ============================================================


def _start_cancellation(user_id: str, sesion: dict) -> Tuple[str, Optional[str]]:
    appointments = buscar_turnos_por_contacto(SPREADSHEET_ID, user_id)
    if not appointments:
        return (
            "No encontré turnos agendados con tu número. Si creés que hay un error, contactanos directamente 📞",
            None,
        )
    sesion["found_appointments"] = appointments
    if len(appointments) == 1:
        sesion["appointment_to_cancel"] = appointments[0]
        sesion["step"] = "awaiting_cancellation"
        t = appointments[0]
        return (
            f"Encontré tu turno:\n📅 {t['dia']} a las {t['horario']} con {t.get('barbero', '')}\n\n¿Querés cancelarlo? Respondé 'sí' o 'no'.",
            None,
        )
    sesion["step"] = "selecting_cancel_appointment"
    lines = ["Encontré estos turnos a tu nombre:\n"]
    for i, t in enumerate(appointments, 1):
        lines.append(
            f"{i}. {t['dia']} a las {t['horario']} con {t.get('barbero', '')}"
        )
    lines.append("\n¿Cuál querés cancelar? Respondé con el número.")
    return "\n".join(lines), None


def _start_reschedule(user_id: str, sesion: dict) -> Tuple[str, Optional[str]]:
    appointments = buscar_turnos_por_contacto(SPREADSHEET_ID, user_id)
    if not appointments:
        return "No encontré turnos para cambiar. ¿Querés agendar uno nuevo? 💈", None
    sesion["found_appointments"] = appointments
    if len(appointments) == 1:
        sesion["appointment_to_cancel"] = appointments[0]
        sesion["step"] = "awaiting_reschedule"
        t = appointments[0]
        return (
            f"Encontré tu turno:\n📅 {t['dia']} a las {t['horario']} con {t.get('barbero', '')}\n\n¿Querés cambiar este turno? Respondé 'sí' o 'no'.",
            None,
        )
    sesion["step"] = "selecting_reschedule_appointment"
    lines = ["Encontré estos turnos a tu nombre:\n"]
    for i, t in enumerate(appointments, 1):
        lines.append(
            f"{i}. {t['dia']} a las {t['horario']} con {t.get('barbero', '')}"
        )
    lines.append("\n¿Cuál querés cambiar? Respondé con el número.")
    return "\n".join(lines), None


def _process_cancellation(
    user_id: str, sesion: dict, is_affirmative: bool, is_negative: bool
) -> Tuple[str, Optional[str]]:
    appointment = sesion.get("appointment_to_cancel")
    if not appointment:
        reset_session(user_id, sesion)
        return "Algo salió mal. ¿Podés intentar de nuevo?", None
    if is_affirmative:
        ok = cancelar_turno(SPREADSHEET_ID, appointment["dia"], appointment["fila"])
        reset_session(user_id, sesion)
        if ok:
            # FASE 1.4 — invalidate cache for the affected day
            invalidar_cache_disponibilidad(SPREADSHEET_ID, appointment["dia"])
            return (
                f"✅ Listo, tu turno del {appointment['dia']} a las {appointment['horario']} con {appointment.get('barbero', '')} fue cancelado.\nSi necesitás agendar otro, acá estamos 💈",
                None,
            )
        return "Hubo un error al cancelar. ¿Podés intentar de nuevo?", None
    if is_negative:
        reset_session(user_id, sesion)
        return "Dale, no cancelo nada. ¿Te ayudo con algo más? 💈", None
    return "¿Confirmás la cancelación? Respondé 'sí' o 'no'.", None


def _process_reschedule(
    user_id: str, sesion: dict, is_affirmative: bool, is_negative: bool
) -> Tuple[str, Optional[str]]:
    appointment = sesion.get("appointment_to_cancel")
    if not appointment:
        reset_session(user_id, sesion)
        return "Algo salió mal. ¿Podés intentar de nuevo?", None
    if is_affirmative:
        ok = cancelar_turno(SPREADSHEET_ID, appointment["dia"], appointment["fila"])
        if ok:
            reset_session(user_id, sesion)
            new_session = get_session(user_id)
            new_session["step"] = "awaiting_service"
            set_session(user_id, new_session)
            return (
                f"Dale, vamos a cambiar tu turno del {appointment['dia']} a las {appointment['horario']} con {appointment.get('barbero', '')}. 📅\n\n"
                f"Decime: ¿qué servicio querés?\n💈 Corte\n🧔 Barba\n✂️ Corte y Barba",
                None,
            )
        return "Hubo un error al modificar el turno. ¿Podés intentar de nuevo?", None
    if is_negative:
        reset_session(user_id, sesion)
        return "Dale, dejo el turno como está. ¿Te ayudo con algo más? 💈", None
    return "¿Confirmás el cambio? Respondé 'sí' o 'no'.", None


def _process_appointment_selection(
    user_id: str, sesion: dict, text: str, is_negative: bool, llm_result: Optional[dict]
) -> Tuple[str, Optional[str]]:
    appointments = sesion.get("found_appointments", [])
    is_cancel = sesion["step"] == "selecting_cancel_appointment"
    action_text = "cancelar" if is_cancel else "cambiar"
    if is_negative:
        reset_session(user_id, sesion)
        return f"Dale, no vamos a {action_text} nada. ¿Te ayudo con algo más? 💈", None
    num = _extract_number(text)
    if num is None and llm_result and llm_result.get("texto_relevante"):
        match = re.search(r"\b(\d+)\b", llm_result["texto_relevante"])
        if match:
            num = int(match.group(1))
    if num and 1 <= num <= len(appointments):
        appointment = appointments[num - 1]
        sesion["appointment_to_cancel"] = appointment
        sesion["step"] = (
            "awaiting_cancellation" if is_cancel else "awaiting_reschedule"
        )
        return (
            f"Vas a {action_text}: {appointment['dia']} a las {appointment['horario']} con {appointment.get('barbero', '')}.\n¿Confirmás? Respondé 'sí' o 'no'.",
            None,
        )
    return (
        f"Respondé con un número del 1 al {len(appointments)} para elegir cuál turno {action_text}, o 'no' para salir.",
        None,
    )


# ============================================================
# MESSAGES
# ============================================================


def _msg_confirm(sesion: dict) -> str:
    name = sesion["name"]
    day = sesion["day"]
    time_slot = sesion["time"]
    service = sesion["service"]
    service_n = SERVICIOS[service]

    if sesion.get("no_barber_preference"):
        name_b = "con cualquiera"
    else:
        name_b = "con " + code_to_name(sesion["barber"])

    p = PRECIOS[service]
    price_text = f"${p['efectivo']:,} efectivo / ${p['otros']:,} otros"
    return (
        f"✅ ¡Listo {name}! Turno confirmado:\n\n"
        f"📅 {day} a las {time_slot}\n"
        f"💈 {service_n} {name_b}\n"
        f"💵 {price_text}\n"
        f"📍 Maipú 893, Retiro\n\n"
        f"Te pedimos puntualidad por tema de agenda. ¡Te esperamos!"
    )


def _msg_transfer() -> str:
    return "¡En breves te va a estar saludando uno de los profesionales! Cualquier consulta, estamos a disposición 💈"


def _msg_enzo() -> str:
    return (
        "Te comento, Enzo ya no forma parte del equipo de Capi 💈. Se dedica a otra profesión ahora\n\n"
        "Queremos que sigas siendo parte de Capitán! Así que te ofrecemos que pruebes con otro de nuestros Barberos (todos son excelentes!\n\n"
        "¿Con quién querés agendar? Tenemos a Omar, Rodrigo y Agustín disponibles 💈"
    )


def _msg_prices(service: Optional[str] = None) -> str:
    if service and service in PRECIOS:
        p = PRECIOS[service]
        name = SERVICIOS[service]
        return f"El {name} sale ${p['efectivo']:,} en efectivo o ${p['otros']:,} con otros medios. ¿Te lo agendo?"
    return (
        "Nuestros precios:\n\n"
        "💈 Corte: $19.000 efectivo / $21.000 otros\n"
        "🧔 Barba: $17.000 efectivo / $19.000 otros\n"
        "✂️ Corte y Barba: $24.000 efectivo / $26.000 otros\n\n"
        "¿Te agendo un turno?"
    )


# ============================================================
# ENDPOINTS
# ============================================================


# ── DYNAMIC ENDPOINTS FOR MANYCHAT ──


class DisponibilidadRequest(BaseModel):
    user_id: str
    dia: Optional[str] = None
    horario: Optional[str] = None
    servicio: Optional[str] = None


@app.post("/disponibilidad/dias")
async def get_available_days(
    authorization: Optional[str] = Header(None),
):
    """Return the days that have at least one available slot."""
    if not verify_api_key(authorization):
        raise HTTPException(status_code=401, detail="Invalid API key")

    hoy = today_weekday()
    manana = tomorrow_weekday()
    days_with_slots = []

    for day in DIAS_VALIDOS:
        slots = get_available_slots(day)
        if slots:
            label = day.lower()
            if day == hoy:
                label = f"Hoy ({day.lower()})"
            elif day == manana:
                label = f"Mañana ({day.lower()})"
            days_with_slots.append({"codigo": day, "label": label})

    return {"dias": days_with_slots}


@app.post("/disponibilidad/horarios")
async def get_available_times(
    req: DisponibilidadRequest,
    authorization: Optional[str] = Header(None),
):
    """Return available times for a day and service."""
    if not verify_api_key(authorization):
        raise HTTPException(status_code=401, detail="Invalid API key")

    day = resolve_day(req.dia) if req.dia else None
    if not day:
        raise HTTPException(status_code=400, detail="Invalid day")

    service = req.servicio or "C"
    slots = get_available_slots(day, None, service)
    unique_hours = sorted(set(h for h, _ in slots))

    return {
        "dia": day,
        "horarios": unique_hours,
        "rango": (
            f"{unique_hours[0]} a {unique_hours[-1]}"
            if unique_hours
            else "sin disponibilidad"
        ),
    }


@app.post("/disponibilidad/barberos")
async def get_available_barbers(
    req: DisponibilidadRequest,
    authorization: Optional[str] = Header(None),
):
    """Return available barbers for a day + time + service."""
    if not verify_api_key(authorization):
        raise HTTPException(status_code=401, detail="Invalid API key")

    day = resolve_day(req.dia) if req.dia else None
    if not day or not req.horario:
        raise HTTPException(status_code=400, detail="Missing day and/or time")

    service = req.servicio or "C"
    slots = get_available_slots(day, None, service)
    codes = set()
    for h, cod in slots:
        if h == req.horario:
            codes.add(cod)

    barbers = [
        {"codigo": c, "nombre": BARBEROS[c]["nombre"]}
        for c in sorted(codes)
        if c in BARBEROS
    ]

    return {"dia": day, "horario": req.horario, "barberos": barbers}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "3.1",
        "groq": bool(groq_client),
        "sheets": bool(SPREADSHEET_ID),
    }


@app.post("/message", response_model=MessageResponse)
async def handle_message(
    req: MessageRequest, authorization: Optional[str] = Header(None)
):
    t_request_start = time.time()  # FASE 1.6 — total timing
    if not verify_api_key(authorization):
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Auto-response on weekends
    weekday = datetime.now(BUENOS_AIRES).weekday()
    if weekday >= 5:
        return MessageResponse(
            response=(
                "Queridos Clientes!\n"
                "La barbería permanece cerrada los Sábados y Domingos!\n"
                "Que tengan un buen fin de semana!"
            ),
            action=None,
        )

    if not obtener_bot_activo(SPREADSHEET_ID):
        return MessageResponse(response="BOT_OFF", action=None)

    # FASE 1.1 — idempotent dedup by message_id
    if req.message_id and _already_processed(req.message_id):
        logger.info(f"[DEDUP] duplicated message_id ignored: user={req.user_id} mid={req.message_id}")
        return MessageResponse(response="BOT_SILENCED", action=None)

    user_id = req.user_id

    # FASE 1.3 — lock per user_id (prevents concurrent double processing)
    user_lock = await _get_user_lock(user_id)
    async with user_lock:

        # Debounce temporarily disabled (DEBOUNCE_SECONDS = 0)
        final_text = req.message

        logger.info(f">>> REQUEST: user={user_id} msg='{final_text}'")

        # FASE 1.5 — process inside try/except to catch APITimeoutError
        try:
            response_text, action = process_message(user_id, final_text)
        except Exception as e:
            t_err = time.time()
            logger.error(
                f"[TIMING] user={user_id} ERROR processing "
                f"total={round((t_err - t_request_start) * 1000)}ms err={type(e).__name__}: {e}"
            )
            return MessageResponse(
                response="Disculpá, tuve un problema. ¿Podés intentar de nuevo?",
                action=None,
            )

        if response_text in ("BOT_SILENCED", ""):
            return MessageResponse(response="BOT_SILENCED", action=None)
        if action == "transfer_to_human":
            sesion = get_session(user_id)
            sesion["transferred_to_human"] = True
            sesion["transferred_date"] = datetime.now(BUENOS_AIRES).strftime("%Y-%m-%d")
            set_session(user_id, sesion)

        # FASE 1.1 — mark as processed only when it finished OK
        if req.message_id:
            _mark_processed(req.message_id)

        t_request_end = time.time()
        logger.info(
            f"[TIMING] user={user_id} total={round((t_request_end - t_request_start) * 1000)}ms"
        )
        logger.info(f"<<< RESPONSE: user={user_id} resp='{response_text[:80]}' action={action}")
        return MessageResponse(response=response_text, action=action)


@app.post("/reset")
async def handle_reset(
    req: MessageRequest, authorization: Optional[str] = Header(None)
):
    if not verify_api_key(authorization):
        raise HTTPException(status_code=401, detail="Invalid API key")
    reset_session(req.user_id)
    sesion = get_session(req.user_id)
    sesion["transferred_to_human"] = False
    sesion["transferred_date"] = ""
    set_session(req.user_id, sesion)
    return {"response": "Dale, empezamos de nuevo. ¿Qué necesitás?"}


@app.post("/admin")
async def handle_admin(req: AdminRequest, authorization: Optional[str] = Header(None)):
    if not verify_api_key(authorization):
        raise HTTPException(status_code=401, detail="Invalid API key")
    command = req.command.lower()
    if command in ("bloquear", "desbloquear"):
        if not req.barbero:
            raise HTTPException(status_code=400, detail="Missing 'barbero' parameter")
        b = name_to_code(req.barbero)
        if not b or b not in BARBEROS:
            raise HTTPException(status_code=400, detail="Barber not recognized")
        name = BARBEROS[b]["nombre"]
        if command == "bloquear":
            blocked_barbers[b] = req.motivo or "blocked"
            return {"response": f"✅ {name} blocked: {req.motivo}"}
        blocked_barbers.pop(b, None)
        return {"response": f"✅ {name} unblocked."}
    raise HTTPException(status_code=400, detail="Invalid command.")


@app.get("/bot-status")
async def bot_status(authorization: Optional[str] = Header(None)):
    if not verify_api_key(authorization):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return {"bot_activo": obtener_bot_activo(SPREADSHEET_ID)}


@app.post("/human-takeover")
async def human_takeover(
    req: TakeoverRequest, authorization: Optional[str] = Header(None)
):
    if not verify_api_key(authorization):
        raise HTTPException(status_code=401, detail="Invalid API key")
    sesion = get_session(req.user_id)
    sesion["transferred_to_human"] = True
    sesion["transferred_date"] = datetime.now(BUENOS_AIRES).strftime("%Y-%m-%d")
    set_session(req.user_id, sesion)
    return {
        "response": f"Bot silenced for {req.user_id}.",
        "user_id": req.user_id,
        "silenced": True,
    }


@app.post("/human-release")
async def human_release(
    req: TakeoverRequest, authorization: Optional[str] = Header(None)
):
    if not verify_api_key(authorization):
        raise HTTPException(status_code=401, detail="Invalid API key")
    reset_session(req.user_id)
    sesion = get_session(req.user_id)
    sesion["transferred_to_human"] = False
    sesion["transferred_date"] = ""
    set_session(req.user_id, sesion)
    return {
        "response": f"Bot reactivated for {req.user_id}.",
        "user_id": req.user_id,
        "silenced": False,
    }


@app.on_event("startup")
async def startup_event():
    if SPREADSHEET_ID:
        try:
            _crear_pestaña_config(SPREADSHEET_ID)
            logger.info("[STARTUP] CONFIG sheet OK")
        except Exception as e:
            logger.error(f"[STARTUP] Error creating CONFIG: {e}")


@app.get("/debug/sesiones")
async def debug_sessions(authorization: Optional[str] = Header(None)):
    if not verify_api_key(authorization):
        raise HTTPException(status_code=401, detail="Invalid API key")
    summary = {}
    if redis_client:
        try:
            for key in redis_client.scan_iter(match=REDIS_KEY_PREFIX + "*", count=200):
                uid = key[len(REDIS_KEY_PREFIX):]
                raw = redis_client.get(key)
                if not raw:
                    continue
                try:
                    s = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                summary[uid] = {
                    "step": s.get("step"),
                    "service": s.get("service"),
                    "day": s.get("day"),
                    "barber": code_to_name(s.get("barber")),
                    "time": s.get("time"),
                    "name": s.get("name"),
                }
        except Exception as e:
            logger.error(f"[REDIS] scan error: {e}")
    return {"total": len(summary), "sesiones": summary}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print("=" * 60)
    print("CAPITAN BARBERIA - API v3.1 (ManyChat Flow)")
    print(f"IA: {'Groq active' if groq_client else 'Deterministic only'}")
    print(f"Port: {port}")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=port)
