"""
Módulo para Google Sheets - Capitan Barberia (robusto)
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional, Tuple

import gspread
from google.oauth2.service_account import Credentials
import logging

logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURACION
# ============================================================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

BARBEROS: Dict[str, Dict[str, str]] = {
    "O": {
        "nombre": "Omar",
        "inicio": "10:00",
        "fin": "20:00",
        "descanso_inicio": "12:00",
        "descanso_fin": "13:00",
    },
    "R": {
        "nombre": "Rodrigo",
        "inicio": "10:00",
        "fin": "19:30",
        "descanso_inicio": "15:00",
        "descanso_fin": "16:00",
    },
    "A": {
        "nombre": "Agustín",
        "inicio": "10:00",
        "fin": "20:00",
        "descanso_inicio": "13:00",
        "descanso_fin": "14:00",
    },
    "X": {
        "nombre": "A confirmar",
        "inicio": "10:00",
        "fin": "20:00",
        "descanso_inicio": "00:00",
        "descanso_fin": "00:00",
    },
}

HORARIOS: List[str] = [
    "10:00",
    "10:30",
    "11:00",
    "11:30",
    "12:00",
    "12:30",
    "13:00",
    "13:30",
    "14:00",
    "14:30",
    "15:00",
    "15:30",
    "16:00",
    "16:30",
    "17:00",
    "17:30",
    "18:00",
    "18:30",
    "19:00",
    "19:30",
]

DIAS_VALIDOS = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES"]

_sheets_client = None


def get_sheets_client():
    """Client singleton (gspread)."""
    global _sheets_client
    if _sheets_client is not None:
        return _sheets_client

    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)

    _sheets_client = gspread.authorize(creds)
    return _sheets_client


# ============================================================
# AUXILIARES
# ============================================================


def _norm(s: str) -> str:
    return (s or "").strip()


def _hora_a_minutos(hora_str: str) -> int:
    h, m = hora_str.split(":")
    return int(h) * 60 + int(m)


def _barbero_disponible_en_horario(barbero: str, horario: str) -> bool:
    info = BARBEROS.get(barbero)
    if not info:
        return False

    minutos = _hora_a_minutos(horario)
    inicio = _hora_a_minutos(info["inicio"])
    fin = _hora_a_minutos(info["fin"])
    desc_inicio = _hora_a_minutos(info["descanso_inicio"])
    desc_fin = _hora_a_minutos(info["descanso_fin"])

    if minutos < inicio or minutos >= fin:
        return False
    if desc_inicio <= minutos < desc_fin:
        return False
    return True


def _encontrar_bloque_filas(
    all_values: List[List[str]], horario: str
) -> Tuple[int, List[Dict[str, str]]]:
    """
    Busca el índice (0-based) de la fila donde aparece el 'horario' en col A
    y devuelve (idx, filas) donde 'filas' son las 3 filas consecutivas (max 3)
    con campos cliente/barbero/servicio/contacto.

    Levanta ValueError si no encuentra el horario.
    """
    horario_buscado = _norm(horario).replace(" ", "")
    for idx, row in enumerate(all_values):
        horario_celda = _norm(row[0] if len(row) > 0 else "").replace(" ", "")
        if horario_celda == horario_buscado:
            filas = []
            for i in range(3):
                if idx + i < len(all_values):
                    r = all_values[idx + i]
                    filas.append(
                        {
                            "cliente": _norm(r[2] if len(r) > 2 else ""),
                            "barbero": _norm(r[3] if len(r) > 3 else ""),
                            "servicio": _norm(r[4] if len(r) > 4 else ""),
                            "contacto": _norm(r[9] if len(r) > 9 else ""),
                            "fila_num": idx + i + 1,  # 1-based para gspread
                        }
                    )
            logger.info(f"[BLOQUE] Horario={horario}, idx={idx}, filas={filas}")
            return idx, filas
    raise ValueError(f"Horario {horario} no encontrado en la hoja")


def _limpiar_fila(worksheet, fila_num: int) -> None:
    # C..E vacías + J vacío
    worksheet.update(f"C{fila_num}:E{fila_num}", [["", "", ""]])
    worksheet.update(f"J{fila_num}", [[""]])


# ============================================================
# CONFIG DESDE SHEET (pestaña CONFIG)
# ============================================================
#
# La pestaña CONFIG tiene esta estructura:
#
#   Fila 1: CONFIGURACIÓN CAPITÁN BARBERÍA
#   Fila 2: (vacía)
#   Fila 3: BOT | activo
#   Fila 4: (vacía)
#   Fila 5: BARBERO | LUNES | MARTES | MIERCOLES | JUEVES | VIERNES
#   Fila 6: O       | activo| activo | activo    | activo | activo
#   Fila 7: R       | activo| activo | activo    | activo | activo
#   Fila 8: A       | activo| activo | activo    | activo | activo
#   Fila 9: E       | ausente| ausente| ausente  | ausente| ausente
#
# El cliente solo tiene que cambiar "activo" por "ausente" y viceversa.
# ============================================================

import time

_cache_config: Dict[str, any] = {"data": None, "ts": 0}
_CACHE_TTL_CONFIG = 30  # segundos

# FASE 1.4 — cache de disponibilidad por (spreadsheet_id, dia)
_cache_disp: Dict[str, Dict[str, any]] = {}
_CACHE_TTL_DISP = 12  # segundos (entre 10 y 15 según spec)


def _crear_pestaña_config(spreadsheet_id: str) -> None:
    """Crea la pestaña CONFIG con valores por defecto si no existe."""
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(spreadsheet_id)

        # Verificar si ya existe
        try:
            spreadsheet.worksheet("CONFIG")
            return  # Ya existe, no hacer nada
        except gspread.exceptions.WorksheetNotFound:
            pass

        # Crear la pestaña
        worksheet = spreadsheet.add_worksheet(title="CONFIG", rows=20, cols=7)

        # Escribir estructura
        encabezado = [["CONFIGURACIÓN CAPITÁN BARBERÍA", "", "", "", "", "", ""]]
        worksheet.update("A1:G1", encabezado)

        # Estado del bot
        worksheet.update("A3:B3", [["BOT", "activo"]])

        # Tabla de barberos por día
        header = [["BARBERO", "LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES"]]
        worksheet.update("A5:F5", header)

        filas_barberos = []
        for cod in ["O", "R", "A"]:
            nombre = BARBEROS[cod]["nombre"]
            filas_barberos.append(
                [f"{cod} ({nombre})", "activo", "activo", "activo", "activo", "activo"]
            )

        worksheet.update("A6:F8", filas_barberos)

        logger.info("[CONFIG] Pestaña CONFIG creada exitosamente")
    except Exception as e:
        logger.error(f"[CONFIG] Error al crear pestaña CONFIG: {e}", exc_info=True)


def _leer_config(spreadsheet_id: str) -> Dict:
    """
    Lee la pestaña CONFIG y retorna:
    {
        "bot_activo": True/False,
        "ausentes": {
            "LUNES": {"R": "ausente", ...},
            "MARTES": {...},
            ...
        }
    }
    Con cache de 30 segundos.
    """
    ahora = time.time()

    # Cache
    if (
        _cache_config["data"] is not None
        and ahora - _cache_config["ts"] < _CACHE_TTL_CONFIG
    ):
        return _cache_config["data"]

    resultado = {"bot_activo": True, "ausentes": {dia: {} for dia in DIAS_VALIDOS}}

    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(spreadsheet_id)

        try:
            worksheet = spreadsheet.worksheet("CONFIG")
        except gspread.exceptions.WorksheetNotFound:
            logger.info(f"[_leer_config] Pestaña CONFIG NO existe. Creando...")
            _crear_pestaña_config(spreadsheet_id)
            # INVALIDAR CACHE para forzar re-lectura
            _cache_config["data"] = None
            _cache_config["ts"] = 0
            # Intentar leer de nuevo inmediatamente
            try:
                worksheet = spreadsheet.worksheet("CONFIG")
                logger.info(f"[_leer_config] Pestaña creada y leída exitosamente")
            except gspread.exceptions.WorksheetNotFound:
                logger.error(
                    f"[_leer_config] ERROR: No se pudo leer CONFIG después de crear"
                )
                return resultado

        all_values = worksheet.get_all_values()
        logger.info(
            f"[CONFIG] Filas leídas: {len(all_values)}, primeras 10: {all_values[:10]}"
        )

        # Leer estado del bot (fila 3: BOT | activo/apagado)
        if len(all_values) >= 3:
            row3 = all_values[2]
            if len(row3) >= 2 and _norm(row3[0]).upper() == "BOT":
                estado_bot = _norm(row3[1]).lower()
                logger.info(
                    f"[CONFIG] Celda B3 raw='{row3[1]}', normalizada='{estado_bot}'"
                )
                resultado["bot_activo"] = estado_bot not in (
                    "apagado",
                    "off",
                    "inactivo",
                    "ausente",
                )

        # Leer tabla de barberos (fila 5 = header, filas 6+ = datos)
        # Columnas: BARBERO | LUNES | MARTES | MIERCOLES | JUEVES | VIERNES
        if len(all_values) >= 6:
            for row_idx in range(5, len(all_values)):  # desde fila 6 (0-based: 5)
                row = all_values[row_idx]
                if not row or not _norm(row[0]):
                    continue

                # Extraer código de barbero (primer carácter o antes del paréntesis)
                celda_barbero = _norm(row[0]).upper()
                cod = None
                for c in BARBEROS:
                    if (
                        celda_barbero.startswith(c)
                        or BARBEROS[c]["nombre"].upper() in celda_barbero
                    ):
                        cod = c
                        break

                if not cod:
                    continue

                # Leer estado por día (columnas 1-5)
                for dia_idx, dia in enumerate(DIAS_VALIDOS):
                    if dia_idx + 1 < len(row):
                        estado = _norm(row[dia_idx + 1]).lower()
                        if estado in ("ausente", "no", "off", "inactivo"):
                            resultado["ausentes"][dia][cod] = estado

        _cache_config["data"] = resultado
        _cache_config["ts"] = ahora
        logger.info(
            f"[CONFIG] Leída: bot={'activo' if resultado['bot_activo'] else 'apagado'}, ausentes={resultado['ausentes']}"
        )

    except Exception as e:
        logger.error(f"[CONFIG] Error al leer CONFIG: {e}", exc_info=True)

    return resultado


def obtener_bot_activo(spreadsheet_id: str) -> bool:
    """Retorna True si el bot está activo, False si está apagado."""
    config = _leer_config(spreadsheet_id)
    return config["bot_activo"]


def obtener_ausentes_dia(spreadsheet_id: str, dia: str) -> Dict[str, str]:
    """
    Retorna dict de barberos ausentes para un día específico.
    Ejemplo: {"R": "ausente", "E": "ausente"}
    """
    config = _leer_config(spreadsheet_id)
    return config["ausentes"].get(dia.upper().strip(), {})


def obtener_disponibilidad(
    spreadsheet_id: str, hoja: str
) -> Dict[str, Dict[str, List[str]]]:
    """
    Retorna dict por horario:
      {"10:00": {"disponibles":[...], "ocupados":[...], "bloqueados":[...]}, ...}
    """
    hoja = hoja.upper().strip()
    client = get_sheets_client()
    spreadsheet = client.open_by_key(spreadsheet_id)
    worksheet = spreadsheet.worksheet(hoja)
    all_values = worksheet.get_all_values()

    disponibilidad: Dict[str, Dict[str, List[str]]] = {}

    for horario in HORARIOS:
        info_horario = {"disponibles": [], "ocupados": [], "bloqueados": []}

        # filas del bloque del horario
        filas = []
        try:
            _, filas = _encontrar_bloque_filas(all_values, horario)
        except ValueError:
            # Si el horario no existe en la hoja, lo tratamos como sin datos (nadie disponible)
            disponibilidad[horario] = info_horario
            continue

        # Contar filas bloqueadas genéricamente ("-", "X", etc.)
        filas_bloqueadas_genericas = 0
        for fila in filas:
            barbero_val = fila["barbero"].strip()
            if barbero_val:
                upper_val = barbero_val.upper()
                # Si no es un código de barbero real (O, R, A), es un bloqueo genérico
                if upper_val not in [c for c in BARBEROS if c != "X"]:
                    filas_bloqueadas_genericas += 1

        for cod, info in BARBEROS.items():
            # X es placeholder para "cualquiera", no es un barbero real
            if cod == "X":
                continue
            nombre = info["nombre"]

            if not _barbero_disponible_en_horario(cod, horario):
                info_horario["bloqueados"].append(nombre)
                continue

            ocupado = False
            for fila in filas:
                barbero_val = fila["barbero"].strip().upper()
                if barbero_val == cod:
                    ocupado = True
                    info_horario["ocupados"].append(nombre)
                    break

            if ocupado:
                continue

            # Verificar si quedan filas libres (sin cliente NI barbero)
            filas_libres = sum(
                1
                for f in filas
                if not f["barbero"].strip() and not f["cliente"].strip()
            )
            if filas_libres > 0:
                info_horario["disponibles"].append(nombre)
            else:
                # Todas las filas están ocupadas (por barberos específicos o bloqueos genéricos)
                info_horario["ocupados"].append(nombre)

        disponibilidad[horario] = info_horario

    return disponibilidad


def obtener_disponibilidad_texto(spreadsheet_id: str, hoja: str) -> str:
    try:
        disp = obtener_disponibilidad(spreadsheet_id, hoja)
        lineas = [f"DISPONIBILIDAD {hoja.upper()}:"]
        for horario in HORARIOS:
            info = disp.get(horario, {})
            disponibles = info.get("disponibles", [])
            ocupados = info.get("ocupados", [])
            bloqueados = info.get("bloqueados", [])

            partes = []
            if disponibles:
                partes.append(f"LIBRES: {', '.join(disponibles)}")
            if ocupados:
                partes.append(f"OCUPADOS: {', '.join(ocupados)}")
            if bloqueados:
                partes.append(f"DESCANSO: {', '.join(bloqueados)}")

            if partes:
                lineas.append(f"{horario} - {' | '.join(partes)}")
        return "\n".join(lineas)
    except Exception:
        return "No se pudo obtener disponibilidad."


# FASE 1.4 — wrapper con cache para reducir llamadas a Sheets
def obtener_disponibilidad_cached(
    spreadsheet_id: str, hoja: str
) -> Dict[str, Dict[str, List[str]]]:
    """
    Wrapper cacheado de obtener_disponibilidad. TTL 12s por (spreadsheet, hoja).
    NO modifica la lógica de negocio: si la cache expiró, llama al original.
    """
    key = f"{spreadsheet_id}|{hoja.upper().strip()}"
    ahora = time.time()
    entry = _cache_disp.get(key)
    if entry and ahora - entry["ts"] < _CACHE_TTL_DISP:
        return entry["data"]
    t0 = time.time()
    data = obtener_disponibilidad(spreadsheet_id, hoja)
    elapsed = round((time.time() - t0) * 1000)
    _cache_disp[key] = {"data": data, "ts": ahora}
    try:
        import logging
        logging.getLogger(__name__).info(
            f"[SHEETS] disponibilidad dia={hoja} elapsed={elapsed}ms cache=MISS"
        )
    except Exception:
        pass
    return data


def invalidar_cache_disponibilidad(spreadsheet_id: Optional[str] = None, hoja: Optional[str] = None) -> None:
    """FASE 1.4 — invalidar cache tras un guardado/cancelación exitoso."""
    if spreadsheet_id is None and hoja is None:
        _cache_disp.clear()
        return
    if spreadsheet_id is not None and hoja is not None:
        _cache_disp.pop(f"{spreadsheet_id}|{hoja.upper().strip()}", None)
        return
    if hoja is not None:
        # invalidar todas las entries de esa hoja
        suf = f"|{hoja.upper().strip()}"
        for k in list(_cache_disp.keys()):
            if k.endswith(suf):
                _cache_disp.pop(k, None)


# ============================================================
# GUARDAR TURNOS
# ============================================================


def _guardar_turno_en_sheets(
    spreadsheet_id: str, hoja: str, datos: dict
) -> Tuple[bool, Optional[int], str]:
    """
    Variante con detalle. Retorna: (ok, fila_num, error_msg)
    """
    hoja = hoja.upper().strip()
    horario = _norm(datos.get("horario"))
    barbero = _norm(datos.get("barbero")).upper()
    cliente = _norm(datos.get("cliente"))
    servicio = _norm(datos.get("servicio")).upper()
    contacto = _norm(datos.get("contacto", ""))

    if not horario or horario not in HORARIOS:
        return False, None, "Horario inválido"
    if barbero not in BARBEROS:
        return False, None, "Barbero inválido"
    if not cliente:
        return False, None, "Cliente vacío"

    # validar descanso/turno del barbero en esa franja
    # X es placeholder para "cualquiera", no validar horarios
    if barbero != "X" and not _barbero_disponible_en_horario(barbero, horario):
        return False, None, "Ese barbero no trabaja en ese horario"

    client = get_sheets_client()
    spreadsheet = client.open_by_key(spreadsheet_id)
    worksheet = spreadsheet.worksheet(hoja)
    all_values = worksheet.get_all_values()

    try:
        _, filas = _encontrar_bloque_filas(all_values, horario)
    except ValueError:
        return False, None, "No se encontró el horario en la hoja"

    # Si el barbero ya está ocupado en esa franja -> no
    # X es placeholder, múltiples X en el mismo horario está OK
    if barbero != "X":
        for fila in filas:
            if fila["barbero"].upper() == barbero and fila["cliente"]:
                return False, None, "Ese barbero ya está ocupado en ese horario"

    # Buscar una fila libre (cliente y barbero vacíos) dentro de las 3
    for fila in filas:
        if not fila["cliente"] and not fila["barbero"]:
            fila_num = int(fila["fila_num"])
            logger.info(
                f"[GUARDAR] Horario={horario}, barbero={barbero}, cliente={cliente}, "
                f"fila_num={fila_num}, contacto={contacto}"
            )
            worksheet.update(
                f"C{fila_num}:E{fila_num}",
                [[cliente, barbero, servicio]],
            )
            if contacto:
                worksheet.update(f"J{fila_num}", [[contacto]])
            return True, fila_num, ""

    logger.warning(
        f"[GUARDAR] No hay filas libres para horario={horario}, filas={filas}"
    )
    return False, None, "No hay filas libres en ese horario"


def guardar_turno_en_sheets(spreadsheet_id: str, hoja: str, datos: dict) -> bool:
    ok, _, _ = _guardar_turno_en_sheets(spreadsheet_id, hoja, datos)
    return ok


def verificar_turno_cb(
    spreadsheet_id: str, hoja: str, horario: str, barbero: str
) -> bool:
    """
    Para servicio CB (2 slots seguidos): verifica que el 'siguiente' slot exista y
    que el barbero esté disponible en el siguiente horario.
    """
    try:
        idx = HORARIOS.index(horario)
        if idx + 1 >= len(HORARIOS):
            return False
        siguiente = HORARIOS[idx + 1]

        # Debe trabajar en ambos horarios (incluye descanso)
        if not _barbero_disponible_en_horario(barbero, horario):
            return False
        if not _barbero_disponible_en_horario(barbero, siguiente):
            return False

        disp = obtener_disponibilidad(spreadsheet_id, hoja)
        nombre = BARBEROS.get(barbero, {}).get("nombre", "")
        return nombre in disp.get(siguiente, {}).get("disponibles", [])
    except Exception:
        return False


def bloquear_slot_cb(
    spreadsheet_id: str,
    hoja: str,
    horario: str,
    barbero: str,
    cliente: str,
    contacto: str = "",
) -> bool:
    """
    Reserva dos slots seguidos:
    - slot1: cliente
    - slot2: "{cliente} (CB cont.)"

    Si el segundo falla, hace rollback del primero.
    """
    try:
        idx = HORARIOS.index(horario)
        if idx + 1 >= len(HORARIOS):
            return False
        siguiente = HORARIOS[idx + 1]

        ok1, fila1, err1 = _guardar_turno_en_sheets(
            spreadsheet_id,
            hoja,
            {
                "horario": horario,
                "cliente": cliente,
                "barbero": barbero,
                "servicio": "CB",
                "contacto": contacto,
            },
        )
        if not ok1 or not fila1:
            return False

        ok2, fila2, err2 = _guardar_turno_en_sheets(
            spreadsheet_id,
            hoja,
            {
                "horario": siguiente,
                "cliente": f"{cliente} (CB cont.)",
                "barbero": barbero,
                "servicio": "CB",
                "contacto": "",
            },
        )
        if ok2:
            return True

        # rollback del primero
        client = get_sheets_client()
        spreadsheet = client.open_by_key(spreadsheet_id)
        worksheet = spreadsheet.worksheet(hoja.upper().strip())
        _limpiar_fila(worksheet, fila1)
        return False
    except Exception:
        raise


# ============================================================
# BUSCAR / CANCELAR
# ============================================================


def buscar_turnos_por_contacto(spreadsheet_id: str, telefono: str) -> List[Dict]:
    """
    Devuelve lista de turnos (no incluye filas "(CB cont.)") para un contacto.
    Compara por últimos 8 dígitos.
    """
    try:
        if not telefono:
            return []
        telefono_norm = "".join(c for c in telefono if c.isdigit())
        if len(telefono_norm) < 8:
            return []

        client = get_sheets_client()
        spreadsheet = client.open_by_key(spreadsheet_id)

        turnos: List[Dict] = []
        for dia in DIAS_VALIDOS:
            try:
                worksheet = spreadsheet.worksheet(dia)
                all_values = worksheet.get_all_values()

                horario_actual = ""
                for idx, row in enumerate(all_values):
                    if len(row) > 0 and _norm(row[0]):
                        horario_actual = _norm(row[0])

                    contacto_celda = _norm(row[9] if len(row) > 9 else "")
                    if not contacto_celda:
                        continue

                    contacto_norm = "".join(c for c in contacto_celda if c.isdigit())
                    if len(contacto_norm) < 8:
                        continue

                    if contacto_norm[-8:] == telefono_norm[-8:]:
                        cliente = _norm(row[2] if len(row) > 2 else "")
                        if not cliente or "(CB cont.)" in cliente:
                            continue
                        barbero_cod = _norm(row[3] if len(row) > 3 else "")
                        turnos.append(
                            {
                                "dia": dia,
                                "horario": horario_actual,
                                "cliente": cliente,
                                "barbero": BARBEROS.get(barbero_cod, {}).get(
                                    "nombre", barbero_cod
                                ),
                                "fila": idx + 1,
                            }
                        )
            except Exception:
                continue

        return turnos
    except Exception:
        return []


def cancelar_turno(spreadsheet_id: str, dia: str, fila: int) -> bool:
    """
    Cancela el turno en 'fila' (1-based). Si el servicio era CB, busca y cancela
    la fila de continuación '(CB cont.)' en el bloque del siguiente horario.
    """
    try:
        logger.info(f"[CANCELAR] dia={dia} fila={fila}")
        client = get_sheets_client()
        spreadsheet = client.open_by_key(spreadsheet_id)
        worksheet = spreadsheet.worksheet(dia.upper().strip())

        valores = worksheet.row_values(fila)
        logger.info(f"[CANCELAR] Valores en fila {fila}: {valores}")
        servicio = _norm(valores[4] if len(valores) > 4 else "").upper()
        cliente_original = _norm(valores[2] if len(valores) > 2 else "")
        barbero_original = _norm(valores[3] if len(valores) > 3 else "").upper()

        _limpiar_fila(worksheet, fila)
        logger.info(f"[CANCELAR] Fila {fila} limpiada")

        if servicio == "CB" and cliente_original:
            # Buscar la fila de continuación "(CB cont.)" del mismo barbero
            # en las filas siguientes (hasta 6 filas más = siguiente bloque horario)
            all_values = worksheet.get_all_values()
            cb_cont_nombre = f"{cliente_original} (CB cont.)"
            encontrado = False
            for offset in range(1, 7):
                fila_cont = fila + offset
                if fila_cont - 1 >= len(all_values):
                    break
                row = all_values[fila_cont - 1]  # all_values es 0-based
                cliente_celda = _norm(row[2] if len(row) > 2 else "")
                barbero_celda = _norm(row[3] if len(row) > 3 else "").upper()
                if (
                    cliente_celda == cb_cont_nombre
                    and barbero_celda == barbero_original
                ):
                    _limpiar_fila(worksheet, fila_cont)
                    logger.info(f"[CANCELAR] Fila {fila_cont} limpiada (CB cont.)")
                    encontrado = True
                    break
            if not encontrado:
                logger.warning(
                    f"[CANCELAR] No se encontró fila CB cont. para '{cb_cont_nombre}' "
                    f"barbero={barbero_original} después de fila {fila}"
                )

        return True
    except Exception as e:
        logger.error(f"[CANCELAR] Error: {e}")
        return False
