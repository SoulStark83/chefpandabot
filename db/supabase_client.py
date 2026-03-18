# -*- coding: utf-8 -*-
import json
import logging
import urllib.request as ur
import urllib.parse as up
import urllib.error as ue
from typing import Optional, Any, List

from config.settings import SUPABASE_URL, SUPABASE_KEY, CAMPOS_RESTAURANTE

logger = logging.getLogger("chefpanda-admin")


# ── BASE ──────────────────────────────────────────────────

def sb_headers(extra=None):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    if extra:
        headers.update(extra)
    return headers


def sb_request(
    method: str,
    path: str,
    query: Optional[str] = None,
    body: Any = None,
    prefer: Optional[str] = None,
    storage: bool = False,
    extra_headers: Optional[dict] = None,
):
    if storage:
        url = f"{SUPABASE_URL}/storage/v1/{path}"
    else:
        url = f"{SUPABASE_URL}/rest/v1/{path}"

    if query:
        url += f"?{query}"

    headers = sb_headers(extra_headers or {})

    payload = None
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            payload = body
        else:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

    if prefer:
        headers["Prefer"] = prefer

    req = ur.Request(url, data=payload, headers=headers, method=method)

    try:
        with ur.urlopen(req, timeout=60) as r:
            raw = r.read()
            if not raw:
                return None
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                return raw
    except ue.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        logger.error("Supabase error %s %s -> %s", method, path, detail[:500])
        raise Exception(f"Supabase {method} {path} -> HTTP {e.code}: {detail[:500]}")
    except Exception as e:
        logger.exception("Supabase request failed")
        raise Exception(f"Error Supabase: {e}")


def sb_get(tabla: str, filtro: Optional[str] = None, campos: str = "*"):
    query = f"select={up.quote(campos)}"
    if filtro:
        query += f"&{filtro}"
    return sb_request("GET", tabla, query=query) or []


def sb_insert(tabla: str, datos: Any):
    return sb_request("POST", tabla, body=datos, prefer="return=representation") or []


def sb_upsert(tabla: str, datos: Any, on_conflict: str):
    query = f"on_conflict={up.quote(on_conflict)}"
    return sb_request(
        "POST", tabla, query=query, body=datos,
        prefer="resolution=merge-duplicates,return=representation"
    ) or []


def sb_update_by_id(tabla: str, id_val: int, datos: dict):
    return sb_request("PATCH", tabla, query=f"id=eq.{id_val}", body=datos)


def sb_update_where(tabla: str, filtro: str, datos: dict):
    return sb_request("PATCH", tabla, query=filtro, body=datos)


def sb_delete_where(tabla: str, filtro: str):
    return sb_request("DELETE", tabla, query=filtro)


# ── RESTAURANTES ───────────────────────────────────────────

def get_restaurante(rid: int) -> Optional[dict]:
    rows = sb_get("restaurantes", f"id=eq.{rid}&limit=1", CAMPOS_RESTAURANTE)
    return rows[0] if rows else None


def get_restaurantes_activos() -> List[dict]:
    return sb_get("restaurantes", "activo=eq.true&order=id.asc", CAMPOS_RESTAURANTE)


# ── FUENTES ────────────────────────────────────────────────

def get_fuentes_restaurante(rid: int, solo_activas: bool = False) -> List[dict]:
    filtro = f"restaurante_id=eq.{rid}&order=prioridad.asc,id.asc"
    if solo_activas:
        filtro = f"restaurante_id=eq.{rid}&activa=eq.true&order=prioridad.asc,id.asc"
    return sb_get(
        "fuentes_restaurante", filtro,
        "id,restaurante_id,plataforma,url,activa,modo_acceso,prioridad,"
        "ultima_actualizacion,ultimo_estado,ultimo_error,metadata"
    )


def get_fuente(rid: int, plataforma: str) -> Optional[dict]:
    rows = sb_get(
        "fuentes_restaurante",
        f"restaurante_id=eq.{rid}&plataforma=eq.{plataforma}&limit=1",
        "id,restaurante_id,plataforma,url,activa,modo_acceso,prioridad,"
        "ultima_actualizacion,ultimo_estado,ultimo_error,metadata"
    )
    return rows[0] if rows else None


def get_ultima_ejecucion_fuente(rid: int) -> List[dict]:
    try:
        return sb_get("v_ultima_ejecucion_fuente", f"restaurante_id=eq.{rid}", "*")
    except Exception:
        return []


# ── RESEÑAS ────────────────────────────────────────────────

def contar_resenas_restaurante(rid: int) -> int:
    return len(sb_get("resenas", f"restaurante_id=eq.{rid}", "id"))


# ── ANÁLISIS ───────────────────────────────────────────────

def get_ultimos_analisis(rid: int, limit: int = 3) -> List[dict]:
    return sb_get(
        "analisis",
        f"restaurante_id=eq.{rid}&order=fecha.desc&limit={limit}",
        "id,semana,anio,pandascore,titular,fecha,score_reputacion,score_experiencia"
    )


# ── COMPETIDORES ───────────────────────────────────────────

def get_competidores(rid: int) -> List[dict]:
    return sb_get(
        "competidores",
        f"restaurante_id=eq.{rid}&activo=eq.true&order=id.asc",
        "id,nombre,tipo_cocina,plataforma_principal,url,distancia_metros"
    )
