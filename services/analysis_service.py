# -*- coding: utf-8 -*-
import io as _io
import logging
import os
from datetime import date

from telegram import Update

from config.settings import PDF_BUCKET, VERSION_MODELO, VERSION_PROMPT
from db.supabase_client import (
    sb_get, sb_insert, sb_upsert, sb_update_by_id, sb_delete_where,
    get_ultimos_analisis,
)
from handlers.helpers import safe_text
from services.claude_service import (
    construir_contexto_resenas, contrastar_kpis_web,
    analizar_con_claude, parse_claude_json,
)
from services.pdf_service import generar_pdf, upload_pdf_to_storage

logger = logging.getLogger("chefpanda-admin")


def guardar_acciones(analisis_id: int, rid: int, resultado: dict):
    acciones = []
    for paso in resultado.get("plan_semana", [])[:3]:
        descripcion = safe_text(paso.get("accion"))
        if not descripcion:
            continue
        acciones.append({
            "analisis_id":       analisis_id,
            "restaurante_id":    rid,
            "descripcion":       descripcion,
            "responsable":       safe_text(paso.get("responsable")) or None,
            "estado":            "pendiente",
            "prioridad":         "media",
            "categoria":         safe_text(paso.get("categoria")) or None,
            "horizonte":         safe_text(paso.get("horizonte")) or None,
            "metadata": {
                "dia":     safe_text(paso.get("dia")),
                "metrica": safe_text(paso.get("metrica")),
            }
        })
    if acciones:
        sb_insert("acciones", acciones)


def guardar_insights(analisis_id: int, rid: int, resultado: dict):
    insights_raw = resultado.get("insights", [])
    if not insights_raw:
        return
    sb_delete_where("analisis_insights", f"analisis_id=eq.{analisis_id}")
    filas = []
    for i, ins in enumerate(insights_raw):
        titulo = safe_text(ins.get("titulo"))
        if not titulo:
            continue
        filas.append({
            "analisis_id":    analisis_id,
            "restaurante_id": rid,
            "categoria":      safe_text(ins.get("categoria")) or "general",
            "subcategoria":   safe_text(ins.get("subcategoria")) or None,
            "titulo":         titulo,
            "descripcion":    safe_text(ins.get("descripcion")) or titulo,
            "impacto":        safe_text(ins.get("impacto")) or None,
            "evidencia":      ins.get("evidencia") or {},
            "orden":          i,
        })
    if filas:
        sb_insert("analisis_insights", filas)


def guardar_oportunidades(analisis_id: int, rid: int, resultado: dict):
    ops_raw = resultado.get("oportunidades", [])
    if not ops_raw:
        return
    sb_delete_where("analisis_oportunidades", f"analisis_id=eq.{analisis_id}")
    filas = []
    for op in ops_raw:
        descripcion = safe_text(op.get("descripcion"))
        if not descripcion:
            continue
        filas.append({
            "analisis_id":     analisis_id,
            "restaurante_id":  rid,
            "area":            safe_text(op.get("area")) or "general",
            "descripcion":     descripcion,
            "impacto_pct_min": op.get("impacto_pct_min"),
            "impacto_pct_max": op.get("impacto_pct_max"),
            "confianza":       safe_text(op.get("confianza")) or None,
            "evidencia":       op.get("evidencia") or {},
        })
    if filas:
        sb_insert("analisis_oportunidades", filas)


def _safe_score(resultado, key):
    try:
        v = resultado.get(key)
        return float(v) if v is not None else None
    except Exception:
        return None


def guardar_analisis_y_metricas(rid: int, resultado: dict, texto: str, kpis_locales: dict, kpis_web: str):
    hoy = date.today()
    semana = hoy.isocalendar()[1]

    analisis_rows = sb_upsert("analisis", {
        "restaurante_id":   rid,
        "semana":           semana,
        "anio":             hoy.year,
        "fecha":            str(hoy),
        "total_resenas":    kpis_locales.get("total"),
        "media_nota":       kpis_locales.get("media"),
        "pandascore":       resultado.get("pandascore", 0),
        "titular":          resultado.get("titular", ""),
        "resumen_telegram": resultado.get("resumen_telegram", ""),
        "informe_texto":    texto,
        "contexto_raw": {
            "kpis_web":     kpis_web,
            "kpis_locales": kpis_locales,
        },
        "estado":           "ready",
        "version_modelo":   VERSION_MODELO,
        "version_prompt":   VERSION_PROMPT,
        # Scores (0-10)
        "score_reputacion":          _safe_score(resultado, "score_reputacion"),
        "score_experiencia":         _safe_score(resultado, "score_experiencia"),
        "score_engagement":          _safe_score(resultado, "score_engagement"),
        "score_presencia_social":    _safe_score(resultado, "score_presencia_social"),
        "score_conversion_digital":  _safe_score(resultado, "score_conversion_digital"),
        "score_ticket_medio":        _safe_score(resultado, "score_ticket_medio"),
        "score_conversion_calle":    _safe_score(resultado, "score_conversion_calle"),
        "score_riesgo_reputacional": _safe_score(resultado, "score_riesgo_reputacional"),
        # Textos ejecutivos
        "resumen_ejecutivo":     safe_text(resultado.get("resumen_ejecutivo")) or None,
        "posicionamiento_actual":safe_text(resultado.get("posicionamiento_actual")) or None,
        "riesgo_principal":      safe_text(resultado.get("riesgo_principal")) or None,
        "oportunidad_principal": safe_text(resultado.get("oportunidad_principal")) or None,
        "conclusion_final":      safe_text(resultado.get("conclusion_final")) or None,
        "perdida_estimable":     safe_text(resultado.get("perdida_estimable")) or None,
    }, "restaurante_id,anio,semana")

    analisis_id = analisis_rows[0]["id"]
    sb_update_by_id("restaurantes", rid, {"ultima_analisis": str(hoy)})

    # Métricas semanales
    res = sb_get("resenas", f"restaurante_id=eq.{rid}", "plataforma,texto,nota,tiene_respuesta")
    por_plataforma = {}
    for rr in res:
        p = rr.get("plataforma") or "desconocida"
        por_plataforma[p] = por_plataforma.get(p, 0) + 1

    total = len(res)
    total_con_texto = sum(1 for rr in res if safe_text(rr.get("texto")))
    con_resp = sum(1 for rr in res if rr.get("tiene_respuesta"))
    pct_resp = round((con_resp / total) * 100, 2) if total else 0

    sb_upsert("metricas_semanales_restaurante", {
        "restaurante_id":   rid,
        "anio":             hoy.year,
        "semana":           semana,
        "fecha":            str(hoy),
        "total_resenas":    total,
        "total_con_texto":  total_con_texto,
        "media_nota":       kpis_locales.get("media"),
        "pct_con_respuesta": pct_resp,
        "total_google":     por_plataforma.get("google", 0),
        "total_tripadvisor":por_plataforma.get("tripadvisor", 0),
        "total_thefork":    por_plataforma.get("thefork", 0),
        "total_facebook":   por_plataforma.get("facebook", 0),
        "total_instagram":  por_plataforma.get("instagram", 0),
        "total_justeat":    por_plataforma.get("justeat", 0),
        "total_glovo":      por_plataforma.get("glovo", 0),
        "total_ubereats":   por_plataforma.get("ubereats", 0),
        "total_yelp":       por_plataforma.get("yelp", 0),
        "total_foursquare": por_plataforma.get("foursquare", 0),
        "temas":            {},
    }, "restaurante_id,anio,semana")

    guardar_acciones(analisis_id, rid, resultado)
    guardar_insights(analisis_id, rid, resultado)
    guardar_oportunidades(analisis_id, rid, resultado)

    return analisis_id


async def lanzar_analisis(rid: int, restaurante: dict, update: Update):
    nombre = restaurante["nombre"]
    ciudad = restaurante.get("ciudad", "")
    cocina = restaurante.get("tipo_cocina", "")

    historico = get_ultimos_analisis(rid, 3)
    hist_txt = ""
    if historico:
        hist_txt = "HISTÓRICO:\n" + "".join(
            f"- Semana {h.get('semana')}/{h.get('anio')}: Score {h.get('pandascore')}\n"
            for h in historico
        )

    await update.message.reply_text("Leyendo reseñas de la base de datos...")
    contexto_resenas, tiene_resenas, kpis_locales = construir_contexto_resenas(rid, nombre, ciudad)

    if not tiene_resenas:
        await update.message.reply_text(
            f"No hay reseñas en BBDD para {nombre}.\n"
            f"Ejecuta primero el scraper: python scrape.py {rid}"
        )
        return

    await update.message.reply_text("Verificando KPIs públicos...")
    kpis_web = contrastar_kpis_web(nombre, ciudad)

    await update.message.reply_text("Generando análisis con Claude...")
    texto = analizar_con_claude(nombre, ciudad, cocina, contexto_resenas, kpis_web, hist_txt)
    resultado = parse_claude_json(texto)

    analisis_id = guardar_analisis_y_metricas(rid, resultado, texto, kpis_locales, kpis_web)

    t = resultado.get("tendencia", "estable")
    t_e = {"mejora": "Mejora", "deterioro": "Bajando", "estable": "Estable"}.get(t, t)
    problemas  = "\n".join(["  - " + str(p) for p in resultado.get("problemas_top3", [])])
    fortalezas = "\n".join(["  + " + str(f) for f in resultado.get("fortalezas_top3", [])])
    titular    = safe_text(resultado.get("titular")).replace("_", " ").replace("*", " ").replace("`", " ")
    accion     = safe_text(resultado.get("accion_urgente")).replace("_", " ").replace("*", " ")

    scores_txt = ""
    for key, label in [
        ("score_reputacion",         "Reputación"),
        ("score_experiencia",        "Experiencia"),
        ("score_engagement",         "Engagement"),
        ("score_conversion_digital", "Conv. digital"),
    ]:
        v = resultado.get(key)
        if v is not None:
            scores_txt += f"  {label}: {v}/10\n"

    msg = (
        f"Análisis completado: {nombre}\n\n"
        f"PandaScore: {resultado.get('pandascore','?')}/100  {t_e}\n"
        f"En 30 días: {resultado.get('pandascore_estimado_30dias','?')}/100\n\n"
    )
    if scores_txt:
        msg += "Scores:\n" + scores_txt + "\n"
    if resultado.get("riesgo_principal"):
        msg += f"Riesgo: {resultado['riesgo_principal']}\n\n"

    msg += (
        titular + "\n\nFortalezas:\n" + fortalezas +
        "\n\nProblemas:\n" + problemas +
        "\n\nAcción urgente:\n" + accion +
        "\n\nGenerando PDF..."
    )
    await update.message.reply_text(msg[:4000])

    try:
        pdf_path = generar_pdf(nombre, ciudad, resultado)
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        hoy = date.today()
        semana = hoy.isocalendar()[1]
        filename = f"ChefPanda_{rid}_{hoy.isoformat()}.pdf"

        try:
            if PDF_BUCKET:
                upload_pdf_to_storage(pdf_bytes, filename)
            sb_insert("pdfs", {
                "restaurante_id": rid,
                "analisis_id":    analisis_id,
                "fecha":          str(hoy),
                "semana":         semana,
                "anio":           hoy.year,
                "storage_path":   filename,
            })
        except Exception as e:
            logger.warning("No pude guardar referencia PDF: %s", e)

        await update.message.reply_document(
            document=_io.BytesIO(pdf_bytes),
            filename=f"ChefPanda_{nombre.replace(' ','_')}_{hoy}.pdf",
            caption=f"Informe ChefPanda - {nombre}"
        )

        try:
            os.unlink(pdf_path)
        except Exception:
            pass

    except Exception as e:
        logger.exception("Error generando/enviando PDF")
        await update.message.reply_text(f"Error generando PDF: {e}")
