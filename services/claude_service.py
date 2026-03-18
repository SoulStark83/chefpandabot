# -*- coding: utf-8 -*-
import json
import logging
import re
from collections import Counter
import urllib.request as ur
import urllib.error as ue

from config.settings import ANTHROPIC_KEY, VERSION_MODELO
from db.supabase_client import sb_get, get_resenas_analizadas
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


# ── INFORME CONSULTOR (usa datos ya analizados) ────────────

DIM_LABELS = {
    "food_quality":     "Calidad de la comida",
    "service":          "Servicio",
    "waiting_time":     "Tiempo de espera",
    "ambience":         "Ambiente",
    "price_perception": "Relación calidad-precio",
    "cleanliness":      "Limpieza",
}


def construir_contexto_analizadas(rid: int, nombre: str, ciudad: str):
    """
    Construye un contexto estructurado a partir de las reseñas ya analizadas
    (con sentimiento, dimensiones, platos). Devuelve (contexto, tiene_datos, kpis).
    """
    resenas = get_resenas_analizadas(rid, limit=500)
    if not resenas:
        return "", False, {"total": 0, "media": None}

    total = len(resenas)
    notas = [float(r["nota"]) for r in resenas if r.get("nota") is not None]
    nota_media = round(sum(notas) / len(notas), 2) if notas else None

    sent_count = Counter(r.get("sentimiento") for r in resenas if r.get("sentimiento"))
    criticas = sum(1 for r in resenas if r.get("es_critica"))
    sin_responder = sum(1 for r in resenas if r.get("requiere_respuesta"))
    con_respuesta = sum(1 for r in resenas if r.get("tiene_respuesta"))

    # ── PandaScore calculado en Python (no lo inventa Claude) ──
    # 50% nota media · 30% % positivas · 20% % respondidas
    pct_positivas   = sent_count.get("positivo", 0) / total if total else 0
    pct_respondidas = con_respuesta / total if total else 0
    nota_norm       = (nota_media / 5.0) if nota_media else 0.5
    pandascore = round(
        nota_norm       * 50 +
        pct_positivas   * 30 +
        pct_respondidas * 20
    )

    # Dimensiones: suma y conteo
    dim_scores: dict = {}
    dim_counts: dict = {}
    for r in resenas:
        for t in (r.get("temas_detectados") or []):
            dim = t.get("dimension")
            score = t.get("score")
            if dim and score is not None:
                try:
                    dim_scores[dim] = dim_scores.get(dim, 0) + float(score)
                    dim_counts[dim] = dim_counts.get(dim, 0) + 1
                except Exception:
                    pass

    # Platos más mencionados
    platos_counter: Counter = Counter()
    platos_percepcion: dict = {}
    for r in resenas:
        for p in (r.get("platos_mencionados") or []):
            nombre_plato = p.get("plato")
            perc = p.get("percepcion", "neutra")
            if nombre_plato:
                platos_counter[nombre_plato] += 1
                platos_percepcion.setdefault(nombre_plato, Counter())[perc] += 1

    ctx = f"RESTAURANTE: {nombre} | CIUDAD: {safe_text(ciudad).upper()}\n\n"
    ctx += f"DATOS DE ANÁLISIS ({total} reseñas analizadas):\n\n"
    ctx += "STATS GENERALES:\n"
    ctx += f"- Nota media: {nota_media}/5\n"
    ctx += (
        f"- Sentimiento: {sent_count.get('positivo',0)} positivas | "
        f"{sent_count.get('neutro',0)} neutras | {sent_count.get('negativo',0)} negativas\n"
    )
    ctx += f"- Reseñas críticas (≤2★): {criticas}\n"
    ctx += f"- Requieren respuesta del propietario: {sin_responder}\n\n"

    if dim_scores:
        ctx += "SCORES POR DIMENSIÓN (media de todas las reseñas):\n"
        for dim, total_score in sorted(dim_scores.items(), key=lambda x: -x[1] / dim_counts[x[0]]):
            n = dim_counts[dim]
            media = round(total_score / n, 2)
            label = DIM_LABELS.get(dim, dim)
            ctx += f"- {label}: {media}/5 (mencionada en {n} reseñas)\n"
        ctx += "\n"

    if platos_counter:
        ctx += "PLATOS MÁS MENCIONADOS:\n"
        for plato, count in platos_counter.most_common(8):
            percepcs = platos_percepcion.get(plato, Counter())
            total_p = sum(percepcs.values())
            pct_pos = round(percepcs.get("positiva", 0) / total_p * 100) if total_p else 0
            ctx += f"- {plato}: {count} veces ({pct_pos}% valoración positiva)\n"
        ctx += "\n"

    # Muestra de reseñas positivas
    pos = [r for r in resenas if r.get("sentimiento") == "positivo" and len(safe_text(r.get("texto"))) > 20][:5]
    if pos:
        ctx += "RESEÑAS POSITIVAS DESTACADAS:\n"
        for r in pos:
            fecha = r.get("fecha_resena") or r.get("fecha_resena_raw") or "?"
            nota_str = f"{r['nota']}★ " if r.get("nota") is not None else ""
            resp = "[RESPONDIDA]" if r.get("tiene_respuesta") else "[SIN RESPUESTA]"
            ctx += f"[{nota_str}{r.get('autor','?')} – {fecha}] {resp}\n"
            ctx += f"\"{truncate(safe_text(r.get('texto')), 300)}\"\n\n"

    # Muestra de reseñas negativas / críticas
    neg = {r["id"]: r for r in resenas if r.get("es_critica") and len(safe_text(r.get("texto"))) > 20}
    for r in resenas:
        if r.get("sentimiento") == "negativo" and len(safe_text(r.get("texto"))) > 20:
            neg[r["id"]] = r
    neg_list = list(neg.values())[:6]
    if neg_list:
        ctx += "RESEÑAS NEGATIVAS Y CRÍTICAS:\n"
        for r in neg_list:
            fecha = r.get("fecha_resena") or r.get("fecha_resena_raw") or "?"
            nota_str = f"{r['nota']}★ " if r.get("nota") is not None else ""
            resp = "[RESPONDIDA]" if r.get("tiene_respuesta") else "[SIN RESPUESTA]"
            ctx += f"[{nota_str}{r.get('autor','?')} – {fecha}] {resp}\n"
            ctx += f"\"{truncate(safe_text(r.get('texto')), 300)}\"\n\n"

    # Dimensiones: media por dimensión
    dims_medias = {
        dim: round(dim_scores[dim] / dim_counts[dim], 2)
        for dim in dim_scores
    }

    # Top platos estructurado
    top_platos = []
    for plato, count in platos_counter.most_common(8):
        percepcs = platos_percepcion.get(plato, Counter())
        total_p = sum(percepcs.values())
        top_platos.append({
            "nombre":     plato,
            "menciones":  count,
            "pct_pos":    round(percepcs.get("positiva", 0) / total_p * 100) if total_p else 0,
            "pct_neg":    round(percepcs.get("negativa", 0) / total_p * 100) if total_p else 0,
        })

    return ctx, True, {
        "total":         total,
        "media":         nota_media,
        "pandascore":    pandascore,
        "sentimientos":  {
            "positivo": sent_count.get("positivo", 0),
            "neutro":   sent_count.get("neutro", 0),
            "negativo": sent_count.get("negativo", 0),
        },
        "dimensiones":   dims_medias,
        "platos":        top_platos,
        "criticas":      criticas,
        "sin_responder": sin_responder,
    }


def generar_informe_consultor(nombre: str, ciudad: str, cocina: str, contexto: str, hist_txt: str, pandascore: int = 50) -> str:
    prompt = f"""Actúa como un consultor experto en experiencia de cliente en restauración.

Tu objetivo no es solo analizar reseñas, sino detectar patrones que un dueño de restaurante normalmente no ve cuando lee las opiniones una a una.

A partir de los datos proporcionados genera un informe claro, corto y útil para el dueño del restaurante.

IMPORTANTE:
- El informe debe estar escrito como si se lo entregaras directamente al dueño.
- Fácil de leer, sin lenguaje técnico, centrado en clientes, reputación y experiencia.
- USA SOLO datos reales basados en los datos proporcionados.

RESTAURANTE: {nombre} | COCINA: {cocina} | CIUDAD: {ciudad}
PANDASCORE (calculado): {pandascore}/100
{hist_txt}

{contexto}

Responde SOLO con un JSON válido con esta estructura exacta, sin texto adicional:

{{
  "pandascore": {pandascore},
  "tendencia": "mejora",
  "titular": "Una frase que resume el estado del restaurante",
  "resumen_general": "Párrafo de 3-4 frases describiendo la impresión general según las reseñas",
  "lo_que_valoran": [
    "punto que aparece repetidamente en reseñas positivas",
    "punto que aparece repetidamente en reseñas positivas",
    "punto que aparece repetidamente en reseñas positivas"
  ],
  "problema_principal": "El patrón negativo más importante, con evidencia concreta de las reseñas",
  "otros_problemas": [
    "problema secundario que aparece en varias reseñas",
    "problema secundario que aparece en varias reseñas"
  ],
  "patron_curioso": "Algo interesante que el dueño probablemente no ha visto: un empleado muy valorado, un problema muy localizado, un patrón temporal, etc.",
  "oportunidades": [
    "mejora sencilla y realista que se puede aplicar sin grandes cambios",
    "mejora sencilla y realista que se puede aplicar sin grandes cambios",
    "mejora sencilla y realista que se puede aplicar sin grandes cambios"
  ],
  "conclusion": "Párrafo final: qué está funcionando bien y qué debería vigilar para mejorar la reputación",
  "accion_hoy": "La acción más concreta y realista que el dueño puede hacer HOY",
  "resumen_telegram": "Línea 1: qué va bien\\nLínea 2: qué mejorar\\nLínea 3: acción concreta"
}}

Reglas:
- pandascore: usa EXACTAMENTE el valor {pandascore} que se te ha proporcionado, no lo cambies
- tendencia: "mejora", "estable" o "deterioro" (basada en el histórico si existe, o en la distribución de sentimientos)
- Directo, natural, sin tecnicismos
- No menciones que eres IA ni el proceso de análisis"""

    data = claude_call([{"role": "user", "content": prompt}], max_tokens=3000)
    return extraer_texto(data)
