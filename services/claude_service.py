# -*- coding: utf-8 -*-
import json
import logging
import re
import urllib.request as ur
import urllib.error as ue

from config.settings import ANTHROPIC_KEY, VERSION_MODELO
from db.supabase_client import sb_get
from handlers.helpers import safe_text, truncate

logger = logging.getLogger("chefpanda-admin")


# ── API CLAUDE ────────────────────────────────────────────

def claude_call(messages, max_tokens=4000, tools=None):
    body = {
        "model": VERSION_MODELO,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if tools:
        body["tools"] = tools

    payload = json.dumps(body).encode("utf-8")
    req = ur.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": ANTHROPIC_KEY,
        }
    )
    try:
        with ur.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode())
    except ue.HTTPError as e:
        err = e.read().decode()
        logger.error("Claude error %s: %s", e.code, err[:500])
        raise Exception(f"Error API {e.code}: {err[:300]}")
    except Exception as e:
        logger.exception("Claude call failed")
        raise Exception(f"Error llamando a Claude: {e}")


def extraer_texto(data):
    return "".join(
        b.get("text", "")
        for b in data.get("content", [])
        if b.get("type") == "text"
    )


def parse_claude_json(texto: str) -> dict:
    raw = safe_text(texto).replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(raw)
    except Exception:
        pass

    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except Exception:
            raw = candidate

    try:
        r = raw
        if r.count('"') % 2 != 0:
            r += '"'
        r += "]" * max(0, r.count("[") - r.count("]"))
        r += "}" * max(0, r.count("{") - r.count("}"))
        return json.loads(r)
    except Exception as e:
        raise Exception(f"No pude parsear JSON de Claude: {e}")


# ── CONTEXTO Y ANÁLISIS ───────────────────────────────────

def construir_contexto_resenas(rid: int, nombre: str, ciudad: str):
    try:
        resenas = sb_get(
            "resenas",
            f"restaurante_id=eq.{rid}&order=fecha_scrape.desc,created_at.desc&limit=80",
            "plataforma,autor,fecha_resena_raw,fecha_resena,nota,titulo,texto,"
            "tiene_respuesta,respuesta_propietario,fecha_scrape"
        )
    except Exception:
        return "", False, {"total": 0, "media": None}

    if not resenas:
        return "", False, {"total": 0, "media": None}

    resenas = sorted(
        resenas,
        key=lambda r: (0 if safe_text(r.get("texto")) else 1, safe_text(r.get("plataforma"))),
    )

    notas = [float(r["nota"]) for r in resenas if r.get("nota") is not None]
    nota_media = round(sum(notas) / len(notas), 2) if notas else None
    total = len(resenas)

    por_plataforma = {}
    for r in resenas:
        plat = r.get("plataforma", "desconocida")
        por_plataforma.setdefault(plat, []).append(r)

    contexto = f"RESEÑAS REALES DE {nombre.upper()} ({safe_text(ciudad).upper()})\n"
    contexto += f"Total: {total} | Nota media calculada: {nota_media}/5\n"
    contexto += f"Fecha scrape más reciente: {resenas[0].get('fecha_scrape','?')}\n\n"

    for plat, rs in por_plataforma.items():
        notas_plat = [float(r["nota"]) for r in rs if r.get("nota") is not None]
        media_plat = round(sum(notas_plat) / len(notas_plat), 2) if notas_plat else None
        respondidas = sum(1 for r in rs if r.get("tiene_respuesta"))

        contexto += (
            f"--- {plat.upper()} "
            f"({len(rs)} reseñas, nota media: {media_plat}, respondidas: {respondidas}/{len(rs)}) ---\n"
        )

        usadas = 0
        for r in rs:
            texto = safe_text(r.get("texto"))
            if len(texto) < 5:
                continue

            fecha_txt = r.get("fecha_resena") or r.get("fecha_resena_raw") or "?"
            nota_str = f"{r['nota']}* " if r.get("nota") is not None else ""
            resp_str = " [RESPONDIDA]" if r.get("tiene_respuesta") else " [SIN RESPUESTA]"
            contexto += f"[{nota_str}{r.get('autor','?')} - {fecha_txt}]{resp_str}\n"
            if r.get("titulo"):
                contexto += f"Título: {truncate(r['titulo'], 120)}\n"
            contexto += f"{truncate(texto, 600)}\n"
            if r.get("respuesta_propietario"):
                contexto += f"Respuesta: {truncate(r['respuesta_propietario'], 250)}\n"
            contexto += "\n"
            usadas += 1
            if usadas >= 12:
                break

    return contexto, True, {"total": total, "media": nota_media}


def analizar_resenas_lote(resenas: list) -> list:
    """
    Analiza un lote de reseñas y devuelve una lista de dicts con el análisis
    estructurado de cada una, en el mismo orden.
    """
    import json as _json

    entradas = []
    for r in resenas:
        entradas.append({
            "id":    r["id"],
            "nota":  r.get("nota"),
            "texto": (r.get("texto") or "")[:500],
        })

    n = len(resenas)
    prompt = f"""Analiza las siguientes {n} reseñas de un restaurante.
Para cada reseña devuelve un objeto JSON con exactamente estos campos:

- "id": el mismo id de entrada (no lo modifiques)
- "sentimiento": "positivo", "neutro" o "negativo"
- "temas_detectados": array con las dimensiones mencionadas explícitamente en la reseña:
    {{"dimension": "food_quality|service|waiting_time|ambience|price_perception|cleanliness",
      "score": número del 1.0 al 5.0 según lo que expresa la reseña,
      "menciones": ["frase literal o paráfrasis corta que lo evidencia"]}}
  Dimensiones:
    food_quality      → calidad de la comida
    service           → atención del personal
    waiting_time      → tiempos de espera (score bajo = espera larga)
    ambience          → ambiente, decoración, ruido
    price_perception  → si el cliente siente que el precio es justo
    cleanliness       → limpieza del local
  IMPORTANTE: solo incluye las dimensiones que la reseña menciona explícita o implícitamente.
  Si no hay ninguna, devuelve [].
- "platos_mencionados": array de {{"plato": "nombre exacto", "percepcion": "positiva|negativa|neutra"}}
  Solo platos concretos. Si no hay ninguno, devuelve [].
- "es_destacable": true si la reseña es especialmente útil, representativa o contiene información accionable
- "es_critica": true si nota <= 2 o expresa insatisfacción grave
- "requiere_respuesta": true si el propietario debería responder (queja, crítica grave, o pregunta directa)

Responde SOLO con un JSON array de exactamente {n} objetos, en el mismo orden que la entrada.
Sin texto adicional, sin markdown.

RESEÑAS:
{_json.dumps(entradas, ensure_ascii=False)}"""

    data = claude_call([{"role": "user", "content": prompt}], max_tokens=5000)
    texto = extraer_texto(data)

    try:
        resultado = parse_claude_json(texto)
        if isinstance(resultado, list):
            return resultado
        # Si devuelve un dict con una clave que contiene la lista
        for v in resultado.values():
            if isinstance(v, list):
                return v
    except Exception as e:
        logger.warning("Error parseando lote de reseñas: %s", e)

    return []


def contrastar_kpis_web(nombre: str, ciudad: str) -> str:
    try:
        data = claude_call(
            messages=[{
                "role": "user",
                "content": (
                    f"Dame solo la nota pública actual visible de Google, Tripadvisor y TheFork "
                    f"del restaurante '{nombre}' en {ciudad}. Máximo 60 palabras."
                )
            }],
            max_tokens=180,
            tools=[{"type": "web_search_20250305", "name": "web_search"}]
        )
        return extraer_texto(data).strip()
    except Exception as e:
        logger.warning("No pude contrastar KPIs web: %s", e)
        return ""


def analizar_con_claude(nombre, ciudad, cocina, contexto_resenas, kpis_web, hist_txt):
    datos = contexto_resenas
    if kpis_web:
        datos += f"\nKPIs VERIFICADOS EN WEB:\n{kpis_web}\n"

    prompt = f"""Eres ChefPanda, experto en reputación online para restaurantes.
Analiza las reseñas REALES y genera un informe completo.
USA SOLO datos reales. Cita textualmente fragmentos cuando sea relevante.
Conciso: máximo 20 palabras por campo simple.

RESTAURANTE: {nombre} | COCINA: {cocina} | CIUDAD: {ciudad}
{hist_txt}

{datos}

Responde SOLO en JSON válido sin texto adicional:

{{
  "titular": "frase que resume el estado real basada en las reseñas",
  "pandascore": 52,
  "pandascore_estimado_30dias": 68,
  "tendencia": "estable",

  "score_reputacion": 6.5,
  "score_experiencia": 7.0,
  "score_engagement": 4.0,
  "score_presencia_social": 3.0,
  "score_conversion_digital": 5.0,
  "score_ticket_medio": 6.0,
  "score_conversion_calle": 7.5,
  "score_riesgo_reputacional": 3.5,

  "resumen_ejecutivo": "2-3 frases con el diagnóstico principal",
  "posicionamiento_actual": "cómo se percibe el restaurante vs competencia",
  "riesgo_principal": "el mayor riesgo reputacional detectado",
  "oportunidad_principal": "la mayor oportunidad de mejora detectada",
  "conclusion_final": "recomendación final concreta",
  "perdida_estimable": "estimación del impacto económico del problema principal",

  "puntuaciones_reales": {{
    "google_maps": "4.5 (320 reseñas)",
    "tripadvisor": "Sin datos",
    "thefork": "Sin datos"
  }},
  "fortalezas_top3": [
    "fortaleza con cita o evidencia real",
    "fortaleza con cita o evidencia real",
    "fortaleza con cita o evidencia real"
  ],
  "problemas_top3": [
    "problema con cita o evidencia real",
    "problema con cita o evidencia real",
    "problema con cita o evidencia real"
  ],
  "puntuaciones": {{
    "calidad_comida": 4.5,
    "servicio": 3.2,
    "ambiente": 4.0,
    "precio": 4.2,
    "gestion_online": 1.5
  }},
  "insights": [
    {{
      "categoria": "servicio",
      "subcategoria": "tiempos de espera",
      "titulo": "título breve del insight",
      "descripcion": "descripción con evidencia real de las reseñas",
      "impacto": "alto",
      "evidencia": {{"cita": "cita literal de reseña"}}
    }},
    {{
      "categoria": "producto",
      "subcategoria": null,
      "titulo": "título breve",
      "descripcion": "descripción con evidencia",
      "impacto": "medio",
      "evidencia": {{}}
    }},
    {{
      "categoria": "digital",
      "subcategoria": "respuestas",
      "titulo": "título breve",
      "descripcion": "descripción con evidencia",
      "impacto": "alto",
      "evidencia": {{}}
    }}
  ],
  "oportunidades": [
    {{
      "area": "digital",
      "descripcion": "oportunidad concreta con base en evidencia real",
      "impacto_pct_min": 5,
      "impacto_pct_max": 15,
      "confianza": "alta",
      "evidencia": {{"base": "fundamento de la estimación"}}
    }},
    {{
      "area": "experiencia",
      "descripcion": "segunda oportunidad concreta",
      "impacto_pct_min": 3,
      "impacto_pct_max": 10,
      "confianza": "media",
      "evidencia": {{}}
    }}
  ],
  "accion_urgente": "acción más importante para hacer HOY, muy concreta",
  "plan_semana": [
    {{"dia": "Hoy", "accion": "acción 1", "responsable": "quién", "metrica": "cómo medir", "categoria": "digital", "horizonte": "inmediato"}},
    {{"dia": "Esta semana", "accion": "acción 2", "responsable": "quién", "metrica": "cómo medir", "categoria": "servicio", "horizonte": "corto_plazo"}},
    {{"dia": "Este mes", "accion": "acción 3", "responsable": "quién", "metrica": "cómo medir", "categoria": "producto", "horizonte": "medio_plazo"}}
  ],
  "respuestas_sugeridas": [
    "Respuesta completa para reseña negativa típica. Tono humano.",
    "Respuesta completa para reseña positiva destacada."
  ],
  "resumen_telegram": "3 líneas: qué va bien, qué mejorar, acción concreta"
}}"""

    data = claude_call([{"role": "user", "content": prompt}], max_tokens=4000)
    return extraer_texto(data)
