# -*- coding: utf-8 -*-
import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config.settings import PLATAFORMAS_VALIDAS
from db.supabase_client import (
    sb_get, sb_insert, sb_upsert, sb_update_by_id,
    get_restaurante, get_restaurantes_activos,
    get_fuentes_restaurante, get_fuente,
    contar_resenas_restaurante, get_ultima_ejecucion_fuente,
    get_competidores,
)
from handlers.helpers import is_admin, safe_text, is_valid_url, truncate, default_modo_for_plataforma
from services.analysis_service import lanzar_analisis

logger = logging.getLogger("chefpanda-admin")


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.message.reply_text(
        "ChefPanda Admin\n\n"
        "CLIENTES\n"
        "/nuevo_cliente Nombre | Cocina | Ciudad\n"
        "/listar\n"
        "/ver ID\n"
        "/estado\n\n"
        "FUENTES\n"
        "/fuentes ID\n"
        "/urls ID plataforma URL\n"
        "/activar_fuente ID plataforma\n"
        "/desactivar_fuente ID plataforma\n\n"
        "ANÁLISIS\n"
        "/analizar ID\n"
        "/analizar ID forzar\n\n"
        "COMPETIDORES\n"
        "/competidores ID\n\n"
        "GESTIÓN\n"
        "/pausar ID\n"
        "/activar ID"
    )


async def nuevo_cliente(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    try:
        partes = [p.strip() for p in " ".join(ctx.args).split("|")]
        if len(partes) < 3:
            await update.message.reply_text("Formato: /nuevo_cliente Nombre | Cocina | Ciudad")
            return

        res = sb_insert("restaurantes", {
            "nombre":      partes[0],
            "tipo_cocina": partes[1],
            "ciudad":      partes[2],
            "plan":        "pro",
            "activo":      True,
        })
        rid = res[0]["id"]
        await update.message.reply_text(
            f"Cliente añadido: {partes[0]}\n"
            f"ID: {rid}\n\n"
            f"Añade la fuente Google con:\n"
            f"/urls {rid} google https://maps.google.com/..."
        )
    except Exception as e:
        logger.exception("Error en nuevo_cliente")
        await update.message.reply_text(f"Error: {e}")


async def listar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    try:
        clientes = get_restaurantes_activos()
        if not clientes:
            await update.message.reply_text("No hay clientes activos.")
            return

        msg = "Clientes ChefPanda\n\n"
        for c in clientes:
            scrape   = c.get("ultima_actualizacion_resenas") or "nunca"
            analisis = c.get("ultima_analisis") or "nunca"
            fuentes  = get_fuentes_restaurante(c["id"])
            fuentes_txt = ", ".join(
                f"{f['plataforma']}{'' if f.get('activa') else ' (off)'}"
                for f in fuentes
            ) or "sin fuentes"

            msg += f"{c['id']} - {c['nombre']} ({c.get('ciudad','')})\n"
            msg += f"   Fuentes: {fuentes_txt}\n"
            msg += f"   Scrape: {scrape} | Análisis: {analisis}\n\n"

        await update.message.reply_text(msg[:4000])
    except Exception as e:
        logger.exception("Error en listar")
        await update.message.reply_text(f"Error: {e}")


async def estado(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    try:
        clientes          = sb_get("restaurantes", "activo=eq.true", "id")
        fuentes           = sb_get("fuentes_restaurante", "activa=eq.true", "id")
        acciones_pend     = sb_get("acciones", "estado=eq.pendiente", "id")
        resenas_pend      = sb_get("vw_resenas_pendientes_respuesta", None, "id")
        await update.message.reply_text(
            f"Estado ChefPanda\n\n"
            f"Clientes activos:        {len(clientes)}\n"
            f"Fuentes activas:         {len(fuentes)}\n"
            f"Acciones pendientes:     {len(acciones_pend)}\n"
            f"Reseñas sin responder:   {len(resenas_pend)}\n"
            f"Ingresos estimados:      {len(clientes)*99} EUR/mes"
        )
    except Exception as e:
        logger.exception("Error en estado")
        await update.message.reply_text(f"Error: {e}")


async def cmd_ver(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not ctx.args:
        await update.message.reply_text("Uso: /ver ID")
        return
    try:
        rid = int(ctx.args[0])
        c = get_restaurante(rid)
        if not c:
            await update.message.reply_text(f"No existe ID {rid}")
            return

        n       = contar_resenas_restaurante(rid)
        fuentes = get_fuentes_restaurante(rid)
        ultimas = get_ultima_ejecucion_fuente(rid)

        fuentes_txt = ""
        for f in fuentes:
            ultima  = next((u for u in ultimas if u.get("fuente_id") == f["id"]), None)
            estado_f = ultima.get("estado") if ultima else (f.get("ultimo_estado") or "sin ejecuciones")
            fuentes_txt += (
                f"- {f['plataforma']} | activa={f.get('activa')} | "
                f"modo={f.get('modo_acceso')} | estado={estado_f}\n"
            )

        web_txt = ""
        if c.get("website"):
            web_txt += f"Web: {c['website']}\n"
        if c.get("instagram_handle"):
            web_txt += f"Instagram: @{c['instagram_handle']}\n"

        await update.message.reply_text(
            f"Ficha: {c['nombre']}\n"
            f"Cocina: {c.get('tipo_cocina','?')} | Ciudad: {c.get('ciudad','?')}\n"
            f"{web_txt}"
            f"Último scrape:   {c.get('ultima_actualizacion_resenas','nunca')}\n"
            f"Último análisis: {c.get('ultima_analisis','nunca')}\n"
            f"Reseñas en BBDD: {n}\n\n"
            f"Fuentes:\n{fuentes_txt or 'Sin fuentes'}"
        )
    except Exception as e:
        logger.exception("Error en ver")
        await update.message.reply_text(f"Error: {e}")


async def cmd_fuentes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not ctx.args:
        await update.message.reply_text("Uso: /fuentes ID")
        return
    try:
        rid = int(ctx.args[0])
        c   = get_restaurante(rid)
        if not c:
            await update.message.reply_text(f"No existe ID {rid}")
            return

        fuentes = get_fuentes_restaurante(rid)
        if not fuentes:
            await update.message.reply_text(f"{c['nombre']} no tiene fuentes configuradas.")
            return

        msg = f"Fuentes de {c['nombre']}\n\n"
        for f in fuentes:
            msg += (
                f"{f['plataforma']}\n"
                f"  activa: {f.get('activa')}\n"
                f"  modo:   {f.get('modo_acceso')}\n"
                f"  estado: {f.get('ultimo_estado') or '-'}\n"
                f"  error:  {truncate(f.get('ultimo_error') or '-', 120)}\n"
                f"  url:    {truncate(f.get('url') or '-', 180)}\n\n"
            )
        await update.message.reply_text(msg[:4000])
    except Exception as e:
        logger.exception("Error en fuentes")
        await update.message.reply_text(f"Error: {e}")


async def cmd_urls(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not ctx.args or len(ctx.args) < 3:
        await update.message.reply_text("Uso: /urls ID plataforma URL")
        return
    try:
        rid        = int(ctx.args[0])
        plataforma = safe_text(ctx.args[1]).lower()
        url        = safe_text(ctx.args[2])

        if plataforma not in PLATAFORMAS_VALIDAS:
            await update.message.reply_text(f"Plataforma no válida: {plataforma}\nVálidas: {', '.join(sorted(PLATAFORMAS_VALIDAS))}")
            return
        if not is_valid_url(url):
            await update.message.reply_text("URL no válida. Debe empezar por http:// o https://")
            return

        restaurante = get_restaurante(rid)
        if not restaurante:
            await update.message.reply_text(f"No existe restaurante ID {rid}")
            return

        modo = default_modo_for_plataforma(plataforma)
        sb_upsert("fuentes_restaurante", {
            "restaurante_id": rid,
            "plataforma":     plataforma,
            "url":            url,
            "activa":         True,
            "modo_acceso":    modo,
            "updated_at":     datetime.now(timezone.utc).isoformat(),
        }, "restaurante_id,plataforma")

        await update.message.reply_text(
            f"Fuente guardada:\n"
            f"Restaurante: {restaurante['nombre']}\n"
            f"Plataforma:  {plataforma}\n"
            f"Modo:        {modo}"
        )
    except Exception as e:
        logger.exception("Error en urls")
        await update.message.reply_text(f"Error: {e}")


async def activar_fuente(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Uso: /activar_fuente ID plataforma")
        return
    try:
        rid        = int(ctx.args[0])
        plataforma = safe_text(ctx.args[1]).lower()
        fuente     = get_fuente(rid, plataforma)
        if not fuente:
            await update.message.reply_text("No existe esa fuente.")
            return
        sb_update_by_id("fuentes_restaurante", fuente["id"], {"activa": True})
        await update.message.reply_text(f"Fuente activada: {plataforma} para restaurante {rid}")
    except Exception as e:
        logger.exception("Error en activar_fuente")
        await update.message.reply_text(f"Error: {e}")


async def desactivar_fuente(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Uso: /desactivar_fuente ID plataforma")
        return
    try:
        rid        = int(ctx.args[0])
        plataforma = safe_text(ctx.args[1]).lower()
        fuente     = get_fuente(rid, plataforma)
        if not fuente:
            await update.message.reply_text("No existe esa fuente.")
            return
        sb_update_by_id("fuentes_restaurante", fuente["id"], {"activa": False})
        await update.message.reply_text(f"Fuente desactivada: {plataforma} para restaurante {rid}")
    except Exception as e:
        logger.exception("Error en desactivar_fuente")
        await update.message.reply_text(f"Error: {e}")


async def analizar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not ctx.args:
        await update.message.reply_text("Uso: /analizar ID [forzar]")
        return
    try:
        rid   = int(ctx.args[0])
        flag  = safe_text(ctx.args[1]).lower() if len(ctx.args) > 1 else ""
        forzar = flag == "forzar"

        c = get_restaurante(rid)
        if not c:
            await update.message.reply_text(f"No existe ID {rid}")
            return

        ultimo_scrape   = c.get("ultima_actualizacion_resenas")
        ultimo_analisis = c.get("ultima_analisis")

        if not ultimo_scrape:
            await update.message.reply_text(
                f"No hay reseñas scrapeadas para {c['nombre']}.\n\n"
                f"Ejecuta primero:\npython scrape.py {rid}"
            )
            return

        if not forzar and ultimo_scrape and ultimo_analisis:
            if str(ultimo_analisis) >= str(ultimo_scrape):
                await update.message.reply_text(
                    f"No hay reseñas nuevas desde el último análisis.\n"
                    f"Último scrape:   {ultimo_scrape}\n"
                    f"Último análisis: {ultimo_analisis}\n\n"
                    f"Usa /analizar {rid} forzar para regenerar."
                )
                return

        await update.message.reply_text(f"Analizando {c['nombre']}...")
        await lanzar_analisis(rid, c, update)

    except Exception as e:
        logger.exception("Error en analizar")
        await update.message.reply_text(f"Error: {e}")


async def cmd_competidores(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not ctx.args:
        await update.message.reply_text("Uso: /competidores ID")
        return
    try:
        rid = int(ctx.args[0])
        c   = get_restaurante(rid)
        if not c:
            await update.message.reply_text(f"No existe ID {rid}")
            return

        competidores = get_competidores(rid)
        if not competidores:
            await update.message.reply_text(f"{c['nombre']} no tiene competidores registrados.")
            return

        msg = f"Competidores de {c['nombre']}\n\n"
        for comp in competidores:
            dist = f" | {comp['distancia_metros']}m" if comp.get("distancia_metros") else ""
            msg += (
                f"{comp['nombre']}{dist}\n"
                f"  Cocina: {comp.get('tipo_cocina') or '-'} | "
                f"Plataforma: {comp.get('plataforma_principal') or '-'}\n"
                f"  URL: {truncate(comp.get('url') or '-', 80)}\n\n"
            )
        await update.message.reply_text(msg[:4000])
    except Exception as e:
        logger.exception("Error en competidores")
        await update.message.reply_text(f"Error: {e}")


async def pausar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not ctx.args:
        await update.message.reply_text("Uso: /pausar ID")
        return
    try:
        rid = int(ctx.args[0])
        sb_update_by_id("restaurantes", rid, {"activo": False})
        await update.message.reply_text(f"Cliente {rid} pausado.")
    except Exception as e:
        logger.exception("Error en pausar")
        await update.message.reply_text(f"Error: {e}")


async def activar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not ctx.args:
        await update.message.reply_text("Uso: /activar ID")
        return
    try:
        rid = int(ctx.args[0])
        sb_update_by_id("restaurantes", rid, {"activo": True})
        await update.message.reply_text(f"Cliente {rid} activado.")
    except Exception as e:
        logger.exception("Error en activar")
        await update.message.reply_text(f"Error: {e}")


def register_handlers(app: Application):
    app.add_handler(CommandHandler("start",             start))
    app.add_handler(CommandHandler("nuevo_cliente",     nuevo_cliente))
    app.add_handler(CommandHandler("listar",            listar))
    app.add_handler(CommandHandler("estado",            estado))
    app.add_handler(CommandHandler("ver",               cmd_ver))
    app.add_handler(CommandHandler("fuentes",           cmd_fuentes))
    app.add_handler(CommandHandler("urls",              cmd_urls))
    app.add_handler(CommandHandler("activar_fuente",    activar_fuente))
    app.add_handler(CommandHandler("desactivar_fuente", desactivar_fuente))
    app.add_handler(CommandHandler("analizar",          analizar))
    app.add_handler(CommandHandler("competidores",      cmd_competidores))
    app.add_handler(CommandHandler("pausar",            pausar))
    app.add_handler(CommandHandler("activar",           activar))
