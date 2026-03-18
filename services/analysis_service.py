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
    construir_contexto_analizadas,
    generar_informe_consultor, parse_claude_json,
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
        # El nuevo formato devuelve strings; el antiguo devolvía dicts
        if isinstance(op, str):
            descripcion = safe_text(op)
            area = "general"
            extra = {}
        else:
            descripcion = safe_text(op.get("descripcion"))
            area = safe_text(op.get("area")) or "general"
            extra = {
                "impacto_pct_min": op.get("impacto_pct_min"),
                "impacto_pct_max": op.get("impacto_pct_max"),
                "confianza":       safe_text(op.get("confianza")) or None,
                "evidencia":       op.get("evidencia") or {},
            }
        if not descripcion:
            continue
        filas.append({
            "analisis_id":    analisis_id,
            "restaurante_id": rid,
            "area":           area,
            "descripcion":    descripcion,
            **extra,
        })
    if filas:
        sb_insert("analisis_oportunidades", filas)


def guardar_analisis_y_metricas(rid: int, resultado: dict, texto: str, kpis_locales: dict):
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
        "contexto_raw":     {"kpis_locales": kpis_locales},
        "estado":           "ready",
        "version_modelo":   VERSION_MODELO,
        "version_prompt":   VERSION_PROMPT,
        # Textos del informe consultor
        "resumen_ejecutivo":      safe_text(resultado.get("resumen_general")) or None,
        "posicionamiento_actual": safe_text(resultado.get("patron_curioso")) or None,
        "riesgo_principal":       safe_text(resultado.get("problema_principal")) or None,
        "oportunidad_principal":  safe_text(", ".join(resultado.get("oportunidades", []))) or None,
        "conclusion_final":       safe_text(resultado.get("conclusion")) or None,
        "perdida_estimable":      safe_text(resultado.get("accion_hoy")) or None,
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
            f"- Semana {h.get('semana')}/{h.get('anio')}: PandaScore {h.get('pandascore')}\n"
            for h in historico
        )

    await update.message.reply_text("Leyendo reseñas analizadas de la base de datos...")
    contexto, tiene_datos, kpis_locales = construir_contexto_analizadas(rid, nombre, ciudad)

    if not tiene_datos:
        await update.message.reply_text(
            f"No hay reseñas analizadas para {nombre}.\n\n"
            f"Ejecuta primero:\n"
            f"1. python scrape.py {rid}  (obtener reseñas)\n"
            f"2. /analizar_resenas {rid}  (analizar sentimientos)"
        )
        return

    await update.message.reply_text("Generando informe consultor con Claude...")
    pandascore = kpis_locales.get("pandascore", 50)
    texto = generar_informe_consultor(nombre, ciudad, cocina, contexto, hist_txt, pandascore)
    resultado = parse_claude_json(texto)

    analisis_id = guardar_analisis_y_metricas(rid, resultado, texto, kpis_locales)

    # ── Mensaje Telegram ──────────────────────────────────────
    t = resultado.get("tendencia", "estable")
    t_sym = {"mejora": "↑", "deterioro": "↓", "estable": "→"}.get(t, "→")
    titular = safe_text(resultado.get("titular", "")).replace("_", " ").replace("*", " ")

    valorado = "\n".join(
        f"  + {v}" for v in resultado.get("lo_que_valoran", [])
    )
    problema = safe_text(resultado.get("problema_principal", "")).replace("_", " ")
    otros = "\n".join(
        f"  · {p}" for p in resultado.get("otros_problemas", [])
    )
    accion = safe_text(resultado.get("accion_hoy", "")).replace("_", " ").replace("*", " ")
    msg = (
        f"Análisis completado: {nombre}\n"
        f"PandaScore: {resultado.get('pandascore','?')}/100  {t_sym} {t.capitalize()}\n\n"
        f"{titular}\n\n"
        f"LO QUE MÁS VALORAN:\n{valorado}\n\n"
        f"PROBLEMA PRINCIPAL:\n  {problema}\n"
    )
    if otros:
        msg += f"\nOTROS PROBLEMAS:\n{otros}\n"
    if resultado.get("patron_curioso"):
        msg += f"\nDESTACO:\n  {safe_text(resultado['patron_curioso'])}\n"
    msg += f"\nACCIÓN PARA HOY:\n  {accion}\n\nGenerando PDF..."

    await update.message.reply_text(msg[:4000])

    try:
        pdf_path = generar_pdf(nombre, ciudad, resultado, kpis_locales)
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
