# -*- coding: utf-8 -*-
import os
import json
import logging
from datetime import date
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)

# Variables de entorno
BOT_TOKEN    = os.environ["BOT_TOKEN"]
ADMIN_CHAT   = os.environ["ADMIN_CHAT_ID"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

# Cliente Supabase minimo sin libreria externa
import urllib.request

def sb_get(tabla, filtro=None):
    url = f"{SUPABASE_URL}/rest/v1/{tabla}?select=*"
    if filtro:
        url += f"&{filtro}"
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}"
    })
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())

def sb_insert(tabla, datos):
    payload = json.dumps(datos).encode()
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{tabla}",
        data=payload,
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        },
        method="POST"
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())

def sb_update(tabla, id_val, datos):
    payload = json.dumps(datos).encode()
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{tabla}?id=eq.{id_val}",
        data=payload,
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"
        },
        method="PATCH"
    )
    with urllib.request.urlopen(req) as r:
        return r.read().decode()

def is_admin(update: Update) -> bool:
    return str(update.effective_chat.id) == ADMIN_CHAT

# ── COMANDOS ──────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text(
        "🐼 *ChefPanda Admin* listo\n\n"
        "Comandos disponibles:\n"
        "/nuevo\\_cliente Nombre | Cocina | Ciudad\n"
        "/listar — ver todos los clientes\n"
        "/analizar ID — lanzar análisis\n"
        "/estado — resumen general\n"
        "/pausar ID — pausar cliente\n"
        "/activar ID — reactivar cliente",
        parse_mode="Markdown"
    )

async def nuevo_cliente(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    try:
        texto = " ".join(ctx.args)
        partes = [p.strip() for p in texto.split("|")]
        if len(partes) < 3:
            await update.message.reply_text(
                "Formato: /nuevo\\_cliente Nombre | Cocina | Ciudad\n"
                "Ejemplo: /nuevo\\_cliente Il Tiramisu | Italiana | Mostoles",
                parse_mode="Markdown"
            )
            return
        nombre, cocina, ciudad = partes[0], partes[1], partes[2]
        resultado = sb_insert("restaurantes", {
            "nombre": nombre,
            "tipo_cocina": cocina,
            "ciudad": ciudad,
            "plan": "pro",
            "activo": True
        })
        rid = resultado[0]["id"]
        await update.message.reply_text(
            f"✅ *Cliente añadido*\n\n"
            f"🍽 {nombre}\n"
            f"🍴 {cocina} · {ciudad}\n"
            f"ID: `{rid}`\n\n"
            f"Usa /analizar {rid} para lanzar el primer análisis",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def listar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    try:
        clientes = sb_get("restaurantes", "activo=eq.true&order=id")
        if not clientes:
            await update.message.reply_text("No hay clientes activos aún.")
            return
        msg = "🐼 *Clientes ChefPanda*\n\n"
        for c in clientes:
            ultimo = c.get("ultima_analisis") or "nunca"
            msg += f"*{c['id']}* — {c['nombre']}\n"
            msg += f"    {c.get('tipo_cocina','?')} · {c.get('ciudad','?')}\n"
            msg += f"    Último análisis: {ultimo}\n\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def estado(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    try:
        clientes = sb_get("restaurantes", "activo=eq.true")
        total = len(clientes)
        ingresos = total * 99
        msg = (
            f"📊 *Estado ChefPanda*\n\n"
            f"Clientes activos: *{total}*\n"
            f"Ingresos recurrentes: *{ingresos}€/mes*\n\n"
            f"Usa /listar para ver el detalle"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def analizar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Uso: /analizar ID")
        return
    try:
        rid = int(ctx.args[0])
        clientes = sb_get("restaurantes", f"id=eq.{rid}")
        if not clientes:
            await update.message.reply_text(f"❌ No existe cliente con ID {rid}")
            return
        c = clientes[0]
        await update.message.reply_text(
            f"⚡ Lanzando análisis de *{c['nombre']}*...\n"
            f"Esto puede tardar 30-60 segundos.",
            parse_mode="Markdown"
        )
        await lanzar_analisis(rid, c, update)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def lanzar_analisis(rid, restaurante, update):
    import urllib.request as ur

    # Recuperar historico para contexto de tendencias
    historico = sb_get("analisis", f"restaurante_id=eq.{rid}&order=fecha.desc&limit=4")
    hist_txt = ""
    if historico:
        hist_txt = "HISTORICO ULTIMAS SEMANAS:\n"
        for h in historico:
            hist_txt += f"- Semana {h.get('semana','?')}: PandaScore {h.get('pandascore','?')} — {h.get('titular','')}\n"

    prompt = f"""Eres ChefPanda, sistema de gestion de reputacion online para restaurantes.

RESTAURANTE: {restaurante['nombre']}
COCINA: {restaurante.get('tipo_cocina', 'No especificada')}
CIUDAD: {restaurante.get('ciudad', 'No especificada')}

{hist_txt}

Busca informacion publica de este restaurante y genera un informe de reputacion.
Responde SOLO en JSON con estas claves exactas:

{{
  "titular": "frase que capture el estado de reputacion orientada a oportunidad",
  "pandascore": numero del 0 al 100,
  "pandascore_estimado_30dias": numero del 0 al 100 con mejoras,
  "problemas_top3": ["problema 1", "problema 2", "problema 3"],
  "fortalezas_top3": ["fortaleza 1", "fortaleza 2", "fortaleza 3"],
  "accion_urgente": "la accion mas importante a hacer hoy",
  "resumen_telegram": "resumen de 5 lineas max para enviar al dueno del restaurante",
  "tendencia": "mejora|deterioro|estable"
}}"""

    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1000,
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
        "messages": [{"role": "user", "content": prompt}]
    }).encode()

    req = ur.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": os.environ["ANTHROPIC_API_KEY"]
        }
    )

    with ur.urlopen(req) as r:
        data = json.loads(r.read().decode())

    # Extraer texto de la respuesta
    texto = ""
    for bloque in data.get("content", []):
        if bloque.get("type") == "text":
            texto += bloque["text"]

    # Parsear JSON limpiando posibles bloques de codigo
    texto_limpio = texto.replace("```json", "").replace("```", "").strip()
    resultado = json.loads(texto_limpio)

    # Guardar en Supabase
    hoy = date.today()
    semana = hoy.isocalendar()[1]
    anio = hoy.year

    sb_insert("analisis", {
        "restaurante_id": rid,
        "semana": semana,
        "año": anio,
        "informe_texto": texto,
        "pandascore": resultado.get("pandascore", 0),
        "titular": resultado.get("titular", ""),
        "resumen_telegram": resultado.get("resumen_telegram", "")
    })

    sb_insert("pandascore_historico", {
        "restaurante_id": rid,
        "semana": semana,
        "año": anio,
        "score": resultado.get("pandascore", 0)
    })

    sb_update("restaurantes", rid, {"ultima_analisis": str(hoy)})

    # Emoji de tendencia
    tendencia = resultado.get("tendencia", "estable")
    t_emoji = {"mejora": "↑", "deterioro": "↓", "estable": "→"}.get(tendencia, "→")
    t_color = {"mejora": "🟢", "deterioro": "🔴", "estable": "🟡"}.get(tendencia, "🟡")

    # Enviar resumen al admin
    problemas = "\n".join([f"  · {p}" for p in resultado.get("problemas_top3", [])])
    fortalezas = "\n".join([f"  · {f}" for f in resultado.get("fortalezas_top3", [])])

    msg = (
        f"✅ *Análisis completado — {restaurante['nombre']}*\n\n"
        f"🐼 PandaScore: *{resultado.get('pandascore', '?')}/100* {t_emoji}\n"
        f"📈 En 30 días: *{resultado.get('pandascore_estimado_30dias', '?')}/100* con mejoras\n"
        f"{t_color} Tendencia: {tendencia}\n\n"
        f"📌 _{resultado.get('titular', '')}_\n\n"
        f"💪 Fortalezas:\n{fortalezas}\n\n"
        f"⚠️ Problemas:\n{problemas}\n\n"
        f"🎯 Acción urgente:\n_{resultado.get('accion_urgente', '')}_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def pausar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Uso: /pausar ID")
        return
    try:
        rid = int(ctx.args[0])
        sb_update("restaurantes", rid, {"activo": False})
        await update.message.reply_text(f"⏸ Cliente {rid} pausado.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def activar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Uso: /activar ID")
        return
    try:
        rid = int(ctx.args[0])
        sb_update("restaurantes", rid, {"activo": True})
        await update.message.reply_text(f"✅ Cliente {rid} activado.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# ── MAIN ──────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",          start))
    app.add_handler(CommandHandler("nuevo_cliente",  nuevo_cliente))
    app.add_handler(CommandHandler("listar",         listar))
    app.add_handler(CommandHandler("estado",         estado))
    app.add_handler(CommandHandler("analizar",       analizar))
    app.add_handler(CommandHandler("pausar",         pausar))
    app.add_handler(CommandHandler("activar",        activar))
    print("🐼 ChefPanda Bot arrancado...")
    app.run_polling()

if __name__ == "__main__":
    main()
