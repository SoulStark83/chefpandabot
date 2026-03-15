# -*- coding: utf-8 -*-
import os, json, logging, tempfile
from datetime import date
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)

BOT_TOKEN    = os.environ["BOT_TOKEN"]
ADMIN_CHAT   = os.environ["ADMIN_CHAT_ID"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

import urllib.request as ur

def sb_get(tabla, filtro=None):
    url = f"{SUPABASE_URL}/rest/v1/{tabla}?select=*"
    if filtro: url += f"&{filtro}"
    req = ur.Request(url, headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"})
    with ur.urlopen(req) as r: return json.loads(r.read().decode())

def sb_insert(tabla, datos):
    payload = json.dumps(datos).encode()
    req = ur.Request(f"{SUPABASE_URL}/rest/v1/{tabla}", data=payload, headers={
        "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json", "Prefer": "return=representation"
    }, method="POST")
    with ur.urlopen(req) as r: return json.loads(r.read().decode())

def sb_update(tabla, id_val, datos):
    payload = json.dumps(datos).encode()
    req = ur.Request(f"{SUPABASE_URL}/rest/v1/{tabla}?id=eq.{id_val}", data=payload, headers={
        "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }, method="PATCH")
    with ur.urlopen(req) as r: return r.read().decode()

def is_admin(update): return str(update.effective_chat.id) == ADMIN_CHAT

# ── PDF GENERATOR ─────────────────────────────────────────────────────────────

def generar_pdf(restaurante, ciudad, resultado):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_JUSTIFY
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.lib.colors import HexColor

    ROSA=HexColor('#E8527A'); ROSA_L=HexColor('#FDEEF3'); ROSA_M=HexColor('#F7A8BF')
    MARRON=HexColor('#5C3D2E'); MARRON_L=HexColor('#F5EDE8'); NEGRO=HexColor('#2C2C2C')
    VERDE=HexColor('#2D7A4F'); VERDE_L=HexColor('#E8F5EE')
    ROJO=HexColor('#C0392B'); AMBER=HexColor('#E8890C'); AMBER_L=HexColor('#FFF4E0')
    AZUL=HexColor('#1D4ED8'); AZUL_L=HexColor('#EFF6FF')
    GRIS=HexColor('#F5F5F5'); BORDE=HexColor('#E0D5D0'); MUTED=HexColor('#8A7A72')
    WHITE=colors.white; W,H=A4; TW=W-32*mm

    def st(n,**k):
        d=dict(fontName='Helvetica',fontSize=9,leading=13,textColor=NEGRO,spaceAfter=3)
        d.update(k); return ParagraphStyle(n,**d)

    SB=st('b',fontName='Helvetica-Bold')
    SBO=st('bo',fontSize=9,leading=14,alignment=TA_JUSTIFY,spaceAfter=4)
    SAG=st('ag',textColor=HexColor('#1A4A2A'),backColor=VERDE_L,borderPadding=(5,8,5,8))
    SAR=st('ar',textColor=HexColor('#7A0000'),backColor=HexColor('#FDECEA'),borderPadding=(5,8,5,8))
    SC=st('c',alignment=TA_CENTER)

    def hdr(n,t):
        return [Spacer(1,4*mm),
            Table([[Paragraph('<font color="white"><b>%s  %s</b></font>'%(n,t),
                st('h',fontName='Helvetica-Bold',fontSize=10,leading=14,textColor=WHITE))]],
                colWidths=[TW],style=TableStyle([('BACKGROUND',(0,0),(-1,-1),MARRON),
                ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
                ('LEFTPADDING',(0,0),(-1,-1),8)])),
            Spacer(1,3*mm)]

    def bar_row(label,val,col):
        bw=TW-50*mm; f=bw*(val/5.0)
        return Table([[Paragraph(label,st('bl',fontSize=8)),
            Table([['']], colWidths=[f],   style=TableStyle([('BACKGROUND',(0,0),(-1,-1),col),  ('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)])),
            Table([['']], colWidths=[bw-f],style=TableStyle([('BACKGROUND',(0,0),(-1,-1),BORDE),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)])),
            Paragraph('<b>%.1f</b>'%val,st('bv',fontSize=8,alignment=TA_RIGHT))]],
            colWidths=[38*mm,f,bw-f,12*mm],
            style=TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),0),
                ('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),1),('BOTTOMPADDING',(0,0),(-1,-1),1)]))

    def kc(lbl,val,col,sub):
        cw=TW/4-4*mm
        return Table([[Paragraph('<font size="7" color="#888888">%s</font>'%lbl,SC),
            Paragraph('<font size="20" color="%s"><b>%s</b></font>'%(col,val),SC),
            Paragraph('<font size="7" color="#888888">%s</font>'%sub,SC)]],colWidths=[cw],
            style=TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
                ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
                ('BACKGROUND',(0,0),(-1,-1),GRIS),('BOX',(0,0),(-1,-1),.5,BORDE)]))

    S=[]

    # PORTADA
    S+=[Spacer(1,5*mm)]
    S+=[Table([[Paragraph('ChefPanda',st('br',fontName='Helvetica-Bold',fontSize=20,leading=24,textColor=WHITE)),
        Paragraph('Gestion de reputacion del restaurante',st('tg',fontSize=9,textColor=ROSA_M,alignment=TA_RIGHT,leading=12))]],
        colWidths=[TW*.55,TW*.45],style=TableStyle([('BACKGROUND',(0,0),(-1,-1),MARRON),
            ('TOPPADDING',(0,0),(-1,-1),10),('BOTTOMPADDING',(0,0),(-1,-1),10),
            ('LEFTPADDING',(0,0),(0,0),14),('RIGHTPADDING',(-1,0),(-1,0),14),('VALIGN',(0,0),(-1,-1),'MIDDLE')]))]
    S+=[Spacer(1,4*mm)]
    S+=[Table([[Paragraph('<font color="white"><b>INFORME DE REPUTACION ONLINE  -  Preparado por ChefPanda</b></font>',
        st('it',fontName='Helvetica-Bold',fontSize=9,textColor=WHITE,alignment=TA_CENTER))]],
        colWidths=[TW],style=TableStyle([('BACKGROUND',(0,0),(-1,-1),ROSA),
            ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))]
    S+=[Spacer(1,4*mm)]
    S+=[Paragraph(restaurante,st('T',fontName='Helvetica-Bold',fontSize=24,leading=28,textColor=MARRON))]
    S+=[Paragraph('%s  |  Marzo 2026'%ciudad,st('sub',fontSize=10,textColor=MUTED))]
    S+=[Spacer(1,3*mm)]
    S+=[HRFlowable(width='100%',thickness=2,color=ROSA,spaceAfter=3*mm)]

    titular = resultado.get('titular','Analisis de reputacion online')
    S+=[Table([[Paragraph(titular,st('tit',fontName='Helvetica-Bold',fontSize=10,leading=15,
        textColor=MARRON,backColor=MARRON_L,borderPadding=(8,10,8,10)))]],
        colWidths=[TW],style=TableStyle([('BOX',(0,0),(-1,-1),1.5,ROSA),
            ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0),
            ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)]))]
    S+=[Spacer(1,4*mm)]

    ps = resultado.get('pandascore', 50)
    ps30 = resultado.get('pandascore_estimado_30dias', ps+10)
    cw4=TW/4
    S+=[Table([[kc('PandaScore','%d'%ps,'#E8527A','score actual'),
        kc('En 30 dias','%d+'%ps30,'#2D7A4F','con mejoras'),
        kc('Tendencia',{'mejora':'↑','deterioro':'↓','estable':'→'}.get(resultado.get('tendencia','estable'),'→'),'#E8890C','esta semana'),
        kc('Fecha',date.today().strftime('%d/%m/%y'),'#5C3D2E','analisis')]],
        colWidths=[cw4]*4,style=TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),
            ('LEFTPADDING',(0,0),(-1,-1),2),('RIGHTPADDING',(0,0),(-1,-1),2)]))]
    S+=[Spacer(1,3*mm)]

    # S1 FORTALEZAS Y PROBLEMAS
    S+=hdr('01','FORTALEZAS Y AREAS DE MEJORA')
    fortalezas = resultado.get('fortalezas_top3',[])
    problemas  = resultado.get('problemas_top3',[])
    col_w = (TW-6*mm)/2
    S+=[Table([[Paragraph('<b>FORTALEZAS</b>',st('fh',fontName='Helvetica-Bold',fontSize=9,textColor=VERDE)),
                Paragraph('<b>AREAS DE MEJORA</b>',st('ph',fontName='Helvetica-Bold',fontSize=9,textColor=ROJO))]],
        colWidths=[col_w,col_w],style=TableStyle([('BACKGROUND',(0,0),(0,0),VERDE_L),
            ('BACKGROUND',(1,0),(1,0),HexColor('#FDECEA')),
            ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
            ('LEFTPADDING',(0,0),(-1,-1),8),('LINEAFTER',(0,0),(0,-1),1,BORDE)]))]
    maxf = max(len(fortalezas),len(problemas),1)
    rows=[]
    for i in range(maxf):
        f = fortalezas[i] if i<len(fortalezas) else ''
        p = problemas[i]  if i<len(problemas)  else ''
        rows.append([
            Paragraph('+ %s'%f if f else '', st('fi',fontSize=8,textColor=VERDE,leading=12)),
            Paragraph('- %s'%p if p else '', st('pi',fontSize=8,textColor=ROJO,leading=12)),
        ])
    S+=[Table(rows,colWidths=[col_w,col_w],style=TableStyle([
        ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
        ('LEFTPADDING',(0,0),(-1,-1),8),('LINEAFTER',(0,0),(0,-1),.5,BORDE),
        ('LINEBELOW',(0,0),(-1,-2),.3,BORDE),('BOX',(0,0),(-1,-1),.5,BORDE)]))]

    # S2 PUNTUACIONES
    S+=hdr('02','PUNTUACIONES POR CATEGORIA')
    puntuaciones = resultado.get('puntuaciones',{})
    if puntuaciones:
        cats = [('Calidad de la comida','calidad_comida'),('Servicio de sala','servicio'),
                ('Ambiente y decoracion','ambiente'),('Relacion calidad-precio','precio'),
                ('Gestion online','gestion_online')]
        for lbl,key in cats:
            v = puntuaciones.get(key, 3.5)
            try: v=float(v)
            except: v=3.5
            col = VERDE if v>=4.0 else (AMBER if v>=3.0 else ROJO)
            S+=[bar_row(lbl,v,col),Spacer(1,1*mm)]
    else:
        S+=[Paragraph('Puntuaciones no disponibles para este analisis.',SBO)]

    # S3 ACCION URGENTE
    S+=hdr('03','ACCION URGENTE — HACER HOY')
    accion = resultado.get('accion_urgente','')
    if accion:
        S+=[Table([[Paragraph(accion,st('au',fontName='Helvetica-Bold',fontSize=10,leading=15,
            textColor=MARRON,backColor=MARRON_L,borderPadding=(8,10,8,10)))]],
            colWidths=[TW],style=TableStyle([('BOX',(0,0),(-1,-1),2,ROSA),
                ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0),
                ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)]))]

    # S4 RESPUESTA SUGERIDA
    respuestas = resultado.get('respuestas_sugeridas',[])
    if respuestas:
        S+=hdr('04','RESPUESTAS LISTAS PARA PUBLICAR')
        for i,resp in enumerate(respuestas[:2]):
            S+=[Paragraph('<b>RESPUESTA %d</b>'%(i+1),SB),Spacer(1,1*mm)]
            S+=[Table([[Paragraph('"%s"'%resp,st('q',fontName='Helvetica-Oblique',fontSize=8.5,
                leading=13,textColor=HexColor('#1A4A2A'),backColor=VERDE_L,borderPadding=(5,8,5,8)))]],
                colWidths=[TW],style=TableStyle([('BOX',(0,0),(-1,-1),.5,VERDE),
                    ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0),
                    ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)]))]
            S+=[Spacer(1,3*mm)]

    # S5 OPORTUNIDADES
    oportunidades = resultado.get('oportunidades_estrategicas',[])
    if oportunidades:
        S+=hdr('05','OPORTUNIDADES ESTRATEGICAS')
        for i,op in enumerate(oportunidades[:3]):
            S+=[Table([[Paragraph('<font color="white"><b>%d</b></font>'%(i+1),
                    st('on',fontSize=12,alignment=TA_CENTER,textColor=WHITE)),
                Paragraph(op,st('op',fontSize=9,leading=13))]],
                colWidths=[10*mm,TW-10*mm],
                style=TableStyle([('BACKGROUND',(0,0),(0,0),VERDE),('BACKGROUND',(1,0),(1,0),VERDE_L),
                    ('ALIGN',(0,0),(0,0),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
                    ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
                    ('LEFTPADDING',(1,0),(1,0),8),('BOX',(0,0),(-1,-1),.5,BORDE)]))]
            S+=[Spacer(1,2*mm)]

    # CTA
    S+=[Spacer(1,4*mm),HRFlowable(width='100%',thickness=1.5,color=ROSA,spaceAfter=3*mm)]
    S+=[Table([[Paragraph('ChefPanda genera este informe automaticamente cada semana. '
        'Cada lunes recibes el nuevo PandaScore, las resenas pendientes con las respuestas '
        'ya redactadas y las alertas de la semana. Todo por 99 EUR/mes.',
        st('cta',fontSize=9,leading=14,fontName='Helvetica-Bold',
           textColor=MARRON,backColor=ROSA_L,borderPadding=(8,10,8,10),alignment=TA_CENTER))]],
        colWidths=[TW],style=TableStyle([('BOX',(0,0),(-1,-1),2,ROSA),
            ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0),
            ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)]))]

    # BUILD
    tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
    doc = SimpleDocTemplate(tmp.name, pagesize=A4,
        leftMargin=16*mm,rightMargin=16*mm,topMargin=20*mm,bottomMargin=16*mm)

    def on_page(c,d):
        c.setFillColor(MARRON); c.rect(0,H-16*mm,W,16*mm,fill=1,stroke=0)
        c.setFillColor(ROSA);   c.rect(0,H-16*mm,48*mm,16*mm,fill=1,stroke=0)
        c.setFont('Helvetica-Bold',11); c.setFillColor(WHITE)
        c.drawString(6*mm,H-10*mm,'ChefPanda')
        c.setFont('Helvetica',7); c.drawString(6*mm,H-14.5*mm,'Gestion de reputacion del restaurante')
        c.setFont('Helvetica-Bold',8); c.drawRightString(W-6*mm,H-9*mm,'%s - %s'%(restaurante,ciudad))
        c.setFont('Helvetica',7); c.setFillColor(HexColor('#CCBBAA'))
        c.drawRightString(W-6*mm,H-14*mm,'Informe semanal - %s'%date.today().strftime('%d/%m/%Y'))
        c.setFillColor(GRIS); c.rect(0,0,W,11*mm,fill=1,stroke=0)
        c.setStrokeColor(BORDE); c.setLineWidth(0.5); c.line(0,11*mm,W,11*mm)
        c.setFont('Helvetica',7); c.setFillColor(MUTED)
        c.drawString(6*mm,4*mm,'ChefPanda - Gestion de reputacion del restaurante')
        c.drawRightString(W-6*mm,4*mm,'Pagina %d'%d.page)

    doc.build(S,onFirstPage=on_page,onLaterPages=on_page)
    return tmp.name

# ── COMANDOS ──────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text(
        "🐼 *ChefPanda Admin* listo\n\n"
        "Comandos disponibles:\n"
        "/nuevo\\_cliente Nombre | Cocina | Ciudad\n"
        "/listar — ver todos los clientes\n"
        "/analizar ID — lanzar análisis + PDF\n"
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
                "Formato: /nuevo\\_cliente Nombre | Cocina | Ciudad",
                parse_mode="Markdown"
            )
            return
        nombre,cocina,ciudad = partes[0],partes[1],partes[2]
        res = sb_insert("restaurantes",{"nombre":nombre,"tipo_cocina":cocina,"ciudad":ciudad,"plan":"pro","activo":True})
        rid = res[0]["id"]
        await update.message.reply_text(
            f"✅ *Cliente añadido*\n\n🍽 {nombre}\n🍴 {cocina} · {ciudad}\nID: `{rid}`\n\nUsa /analizar {rid} para el primer análisis",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def listar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    try:
        clientes = sb_get("restaurantes","activo=eq.true&order=id")
        if not clientes:
            await update.message.reply_text("No hay clientes activos aún.")
            return
        msg = "🐼 *Clientes ChefPanda*\n\n"
        for c in clientes:
            ultimo = c.get("ultima_analisis") or "nunca"
            msg += f"*{c['id']}* — {c['nombre']}\n    {c.get('tipo_cocina','?')} · {c.get('ciudad','?')}\n    Último análisis: {ultimo}\n\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def estado(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    try:
        clientes = sb_get("restaurantes","activo=eq.true")
        total = len(clientes)
        await update.message.reply_text(
            f"📊 *Estado ChefPanda*\n\nClientes activos: *{total}*\nIngresos recurrentes: *{total*99}€/mes*",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def analizar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Uso: /analizar ID")
        return
    try:
        rid = int(ctx.args[0])
        clientes = sb_get("restaurantes",f"id=eq.{rid}")
        if not clientes:
            await update.message.reply_text(f"❌ No existe cliente con ID {rid}")
            return
        c = clientes[0]
        await update.message.reply_text(
            f"⚡ Analizando *{c['nombre']}*...\nEsto puede tardar 30-60 segundos.",
            parse_mode="Markdown"
        )
        await lanzar_analisis(rid, c, update)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def lanzar_analisis(rid, restaurante, update):
    historico = sb_get("analisis",f"restaurante_id=eq.{rid}&order=fecha.desc&limit=4")
    hist_txt = ""
    if historico:
        hist_txt = "HISTORICO:\n"
        for h in historico:
            hist_txt += f"- Semana {h.get('semana','?')}: PandaScore {h.get('pandascore','?')}\n"

    nombre  = restaurante['nombre']
    ciudad  = restaurante.get('ciudad','')
    cocina  = restaurante.get('tipo_cocina','')

    prompt = f"""Eres ChefPanda, sistema experto en reputacion online para restaurantes.

RESTAURANTE: {nombre}
COCINA: {cocina}
CIUDAD: {ciudad}
{hist_txt}

Analiza la reputacion online de este restaurante basandote en lo que conoces.
Responde SOLO en JSON valido con estas claves exactas (sin texto adicional):

{{
  "titular": "frase orientada a oportunidad que capture el estado real",
  "pandascore": 52,
  "pandascore_estimado_30dias": 70,
  "tendencia": "estable",
  "fortalezas_top3": ["fortaleza 1", "fortaleza 2", "fortaleza 3"],
  "problemas_top3": ["problema 1", "problema 2", "problema 3"],
  "puntuaciones": {{
    "calidad_comida": 4.5,
    "servicio": 3.2,
    "ambiente": 4.0,
    "precio": 4.2,
    "gestion_online": 1.5
  }},
  "accion_urgente": "accion concreta mas importante para hacer hoy",
  "respuestas_sugeridas": [
    "respuesta lista para copiar y pegar en Google/TripAdvisor para resena negativa tipica",
    "respuesta para resena positiva destacada"
  ],
  "oportunidades_estrategicas": [
    "oportunidad 1 con evidencia y accion especifica",
    "oportunidad 2 con evidencia y accion especifica",
    "oportunidad 3 con evidencia y accion especifica"
  ],
  "resumen_telegram": "resumen ejecutivo de 3 lineas para el dueno del restaurante"
}}"""

    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 2000,
        "messages": [{"role":"user","content":prompt}]
    }).encode()

    req = ur.Request("https://api.anthropic.com/v1/messages", data=payload, headers={
        "Content-Type":"application/json",
        "anthropic-version":"2023-06-01",
        "x-api-key": os.environ["ANTHROPIC_API_KEY"]
    })

    try:
        with ur.urlopen(req) as r:
            data = json.loads(r.read().decode())
    except ur.HTTPError as e:
        error_body = e.read().decode()
        print(f"ERROR ANTHROPIC {e.code}: {error_body}")
        await update.message.reply_text(f"❌ Error API {e.code}: {error_body[:300]}")
        return

    texto = "".join(b["text"] for b in data.get("content",[]) if b.get("type")=="text")
    texto_limpio = texto.replace("```json","").replace("```","").strip()

    try:
        resultado = json.loads(texto_limpio)
    except Exception as e:
        await update.message.reply_text(f"❌ Error parseando JSON: {e}\n\nRespuesta raw:\n{texto[:500]}")
        return

    # Guardar en Supabase
    hoy = date.today()
    semana = hoy.isocalendar()[1]
    anio = hoy.year

    sb_insert("analisis",{
        "restaurante_id": rid,
        "semana": semana,
        "año": anio,
        "informe_texto": texto,
        "pandascore": resultado.get("pandascore",0),
        "titular": resultado.get("titular",""),
        "resumen_telegram": resultado.get("resumen_telegram","")
    })
    sb_insert("pandascore_historico",{
        "restaurante_id": rid, "semana": semana,
        "año": anio, "score": resultado.get("pandascore",0)
    })
    sb_update("restaurantes", rid, {"ultima_analisis": str(hoy)})

    # Resumen en Telegram
    t = resultado.get("tendencia","estable")
    t_e = {"mejora":"↑ Mejora","deterioro":"↓ Deterioro","estable":"→ Estable"}.get(t,t)
    problemas  = "\n".join(["  · "+p for p in resultado.get("problemas_top3",[])])
    fortalezas = "\n".join(["  · "+f for f in resultado.get("fortalezas_top3",[])])

    msg = (
        f"✅ *{nombre}* — Análisis completado\n\n"
        f"🐼 PandaScore: *{resultado.get('pandascore','?')}/100*  {t_e}\n"
        f"📈 En 30 días: *{resultado.get('pandascore_estimado_30dias','?')}/100* con mejoras\n\n"
        f"📌 _{resultado.get('titular','')}_\n\n"
        f"💪 *Fortalezas:*\n{fortalezas}\n\n"
        f"⚠️ *Problemas:*\n{problemas}\n\n"
        f"🎯 *Acción urgente:*\n_{resultado.get('accion_urgente','')}_\n\n"
        f"📄 Generando PDF..."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

    # Generar y enviar PDF
    try:
        pdf_path = generar_pdf(nombre, ciudad, resultado)
        with open(pdf_path, 'rb') as f:
            await update.message.reply_document(
                document=f,
                filename=f"ChefPanda_{nombre.replace(' ','_')}_{hoy}.pdf",
                caption=f"🐼 Informe completo ChefPanda — {nombre}"
            )
        os.unlink(pdf_path)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Resumen enviado. Error generando PDF: {e}")

async def pausar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Uso: /pausar ID"); return
    try:
        rid = int(ctx.args[0])
        sb_update("restaurantes", rid, {"activo": False})
        await update.message.reply_text(f"⏸ Cliente {rid} pausado.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def activar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Uso: /activar ID"); return
    try:
        rid = int(ctx.args[0])
        sb_update("restaurantes", rid, {"activo": True})
        await update.message.reply_text(f"✅ Cliente {rid} activado.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# ── MAIN ──────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",         start))
    app.add_handler(CommandHandler("nuevo_cliente", nuevo_cliente))
    app.add_handler(CommandHandler("listar",        listar))
    app.add_handler(CommandHandler("estado",        estado))
    app.add_handler(CommandHandler("analizar",      analizar))
    app.add_handler(CommandHandler("pausar",        pausar))
    app.add_handler(CommandHandler("activar",       activar))
    print("🐼 ChefPanda Bot arrancado...")
    app.run_polling()

if __name__ == "__main__":
    main()
