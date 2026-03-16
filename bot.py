# -*- coding: utf-8 -*-
import os, json, logging, tempfile, base64 as _b64, io as _io
from datetime import date
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)

BOT_TOKEN     = os.environ["BOT_TOKEN"]
ADMIN_CHAT    = os.environ["ADMIN_CHAT_ID"]
SUPABASE_URL  = os.environ["SUPABASE_URL"]
SUPABASE_KEY  = os.environ["SUPABASE_KEY"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]

import urllib.request as ur

# ── SUPABASE ──────────────────────────────────────────────

def sb_get(tabla, filtro=None, campos="*"):
    url = f"{SUPABASE_URL}/rest/v1/{tabla}?select={campos}"
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

CAMPOS_RESTAURANTE = "id,nombre,ciudad,tipo_cocina,url_google,url_tripadvisor,ultima_analisis,ultima_actualizacion_resenas,activo,plan,notas"

# ── ANTHROPIC ─────────────────────────────────────────────

def claude_call(messages, max_tokens=3000, tools=None):
    body = {"model": "claude-haiku-4-5-20251001", "max_tokens": max_tokens, "messages": messages}
    if tools: body["tools"] = tools
    payload = json.dumps(body).encode()
    req = ur.Request("https://api.anthropic.com/v1/messages", data=payload, headers={
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
        "x-api-key": ANTHROPIC_KEY
    })
    try:
        with ur.urlopen(req, timeout=60) as r: return json.loads(r.read().decode())
    except ur.HTTPError as e:
        err = e.read().decode()
        print(f"Claude error {e.code}: {err}")
        raise Exception(f"Error API {e.code}: {err[:200]}")

def extraer_texto(data):
    return "".join(b.get("text","") for b in data.get("content",[]) if b.get("type")=="text")

# ── LOGO ──────────────────────────────────────────────────
LOGO_B64 = None
_logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logo.png')
if os.path.exists(_logo_path):
    with open(_logo_path, 'rb') as _f:
        LOGO_B64 = _b64.b64encode(_f.read()).decode()

# ── CONTEXTO DE RESENAS ────────────────────────────────────

def construir_contexto_resenas(rid, nombre, ciudad):
    """Lee las reseñas scrapeadas de Supabase y las formatea para Claude."""
    try:
        resenas = sb_get("resenas",
            f"restaurante_id=eq.{rid}&order=fecha_scrape.desc&limit=60",
            "plataforma,autor,fecha_resena,nota,texto,tiene_respuesta,respuesta_propietario"
        )
    except:
        return "", False

    if not resenas:
        return "", False

    notas = [r['nota'] for r in resenas if r.get('nota')]
    nota_media = round(sum(notas)/len(notas), 1) if notas else None
    total = len(resenas)

    por_plataforma = {}
    for r in resenas:
        plat = r.get('plataforma', 'desconocida')
        por_plataforma.setdefault(plat, []).append(r)

    contexto = f"RESENAS REALES DE {nombre.upper()} ({ciudad.upper()})\n"
    contexto += f"Total: {total} | Nota media calculada: {nota_media}/5\n"
    contexto += f"Fecha scrape: {resenas[0].get('fecha_scrape','?')}\n\n"

    for plat, rs in por_plataforma.items():
        notas_plat = [r['nota'] for r in rs if r.get('nota')]
        media_plat = round(sum(notas_plat)/len(notas_plat),1) if notas_plat else None
        respondidas = sum(1 for r in rs if r.get('tiene_respuesta'))
        contexto += f"--- {plat.upper()} ({len(rs)} resenas, nota media: {media_plat}, respondidas: {respondidas}/{len(rs)}) ---\n"
        for r in rs[:20]:
            if not r.get('texto') or len(r.get('texto','')) < 5:
                continue
            nota_str = f"{r['nota']}* " if r.get('nota') else ""
            resp_str = " [RESPONDIDA]" if r.get('tiene_respuesta') else " [SIN RESPUESTA]"
            contexto += f"[{nota_str}{r.get('autor','?')} - {r.get('fecha_resena','?')}]{resp_str}\n"
            contexto += f"{r.get('texto','')}\n"
            if r.get('respuesta_propietario'):
                contexto += f"Respuesta: {r['respuesta_propietario'][:200]}\n"
            contexto += "\n"

    return contexto, True

def contrastar_kpis_web(nombre, ciudad):
    """Búsqueda web pequeña para verificar KPIs públicos actuales."""
    try:
        data = claude_call(
            messages=[{"role": "user", "content":
                f"Dame solo la nota actual en Google Maps y TripAdvisor del restaurante '{nombre}' en {ciudad}. "
                f"Maximo 50 palabras."
            }],
            max_tokens=150,
            tools=[{"type": "web_search_20250305", "name": "web_search"}]
        )
        return extraer_texto(data).strip()
    except Exception as e:
        print(f"Web search error: {e}")
        return ""

def analizar_con_claude(nombre, ciudad, cocina, contexto_resenas, kpis_web, hist_txt):
    datos = contexto_resenas
    if kpis_web:
        datos += f"\nKPIs VERIFICADOS EN WEB:\n{kpis_web}\n"

    prompt = f"""Eres ChefPanda, experto en reputacion online para restaurantes.
Analiza las resenas REALES y genera un informe completo.
USA SOLO datos reales. Cita textualmente fragmentos cuando sea relevante.
Conciso: maximo 20 palabras por campo simple.

RESTAURANTE: {nombre} | COCINA: {cocina} | CIUDAD: {ciudad}
{hist_txt}

{datos}

Responde SOLO en JSON valido sin texto adicional:

{{
  "titular": "frase que resume el estado real basada en las resenas",
  "pandascore": 52,
  "pandascore_estimado_30dias": 68,
  "tendencia": "estable",
  "puntuaciones_reales": {{
    "google_maps": "4.5 (320 resenas)",
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
  "resenas_destacadas": [
    {{
      "tipo": "positiva",
      "resumen": "cita literal o resumen de resena real positiva",
      "significado": "que revela sobre el negocio",
      "accion": "como capitalizar"
    }},
    {{
      "tipo": "negativa",
      "resumen": "cita literal o resumen de resena real negativa",
      "significado": "causa raiz del problema",
      "accion": "como resolver"
    }}
  ],
  "silencios_oportunidades": [
    {{"aspecto": "cosa que no mencionan", "interpretacion": "significado", "oportunidad": "accion"}},
    {{"aspecto": "segundo silencio", "interpretacion": "significado", "oportunidad": "accion"}},
    {{"aspecto": "tercer silencio", "interpretacion": "significado", "oportunidad": "accion"}}
  ],
  "accion_urgente": "accion mas importante para hacer HOY muy concreta",
  "plan_semana": [
    {{"dia": "Hoy", "accion": "accion 1", "responsable": "quien", "metrica": "como medir"}},
    {{"dia": "Esta semana", "accion": "accion 2", "responsable": "quien", "metrica": "como medir"}},
    {{"dia": "Este mes", "accion": "accion 3", "responsable": "quien", "metrica": "como medir"}}
  ],
  "respuestas_sugeridas": [
    "Respuesta completa para resena negativa tipica. Tono humano.",
    "Respuesta completa para resena positiva destacada."
  ],
  "oportunidades_estrategicas": [
    "Oportunidad 1 con evidencia y accion especifica",
    "Oportunidad 2 con evidencia y accion especifica",
    "Oportunidad 3 con evidencia y accion especifica"
  ],
  "resumen_telegram": "3 lineas: que va bien, que mejorar, accion concreta"
}}"""

    data = claude_call([{"role": "user", "content": prompt}], max_tokens=3000)
    return extraer_texto(data)

# ── PDF ────────────────────────────────────────────────────

def generar_pdf(restaurante, ciudad, resultado):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_JUSTIFY
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether
    from reportlab.lib.colors import HexColor

    ROSA=HexColor('#E8527A'); ROSA_L=HexColor('#FDEEF3'); ROSA_M=HexColor('#F7A8BF')
    MARRON=HexColor('#5C3D2E'); MARRON_L=HexColor('#F5EDE8')
    VERDE=HexColor('#2D7A4F'); VERDE_L=HexColor('#E8F5EE')
    ROJO=HexColor('#C0392B'); ROJO_L=HexColor('#FDECEA')
    AMBER=HexColor('#E8890C'); AMBER_L=HexColor('#FFF4E0')
    AZUL=HexColor('#1D4ED8'); AZUL_L=HexColor('#EFF6FF')
    GRIS=HexColor('#F5F5F5'); BORDE=HexColor('#E0D5D0'); MUTED=HexColor('#8A7A72')
    NEGRO=HexColor('#2C2C2C'); WHITE=colors.white; W,H=A4; TW=W-32*mm

    def st(n,**k):
        d=dict(fontName='Helvetica',fontSize=9,leading=13,textColor=NEGRO,spaceAfter=3)
        d.update(k); return ParagraphStyle(n,**d)

    SB=st('b',fontName='Helvetica-Bold')
    SBO=st('bo',fontSize=9,leading=14,alignment=TA_JUSTIFY,spaceAfter=4)
    SSM=st('sm',fontSize=7.5,leading=11,textColor=MUTED)
    SAG=st('ag',textColor=HexColor('#1A4A2A'),backColor=VERDE_L,borderPadding=(5,8,5,8))
    SQ=st('q',fontName='Helvetica-Oblique',fontSize=8.5,leading=13,
          textColor=HexColor('#1A4A2A'),backColor=VERDE_L,borderPadding=(5,8,5,8))
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
        try: val=float(val)
        except: val=3.0
        bw=TW-50*mm; f=bw*(val/5.0)
        return Table([[Paragraph(label,st('bl',fontSize=8)),
            Table([['']], colWidths=[f],   style=TableStyle([('BACKGROUND',(0,0),(-1,-1),col),  ('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)])),
            Table([['']], colWidths=[bw-f],style=TableStyle([('BACKGROUND',(0,0),(-1,-1),BORDE),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)])),
            Paragraph('<b>%.1f</b>'%val,st('bv',fontSize=8,alignment=TA_RIGHT))]],
            colWidths=[38*mm,f,bw-f,12*mm],
            style=TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),0),
                ('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),1),('BOTTOMPADDING',(0,0),(-1,-1),1)]))

    def accion_block(n, que, quien, plazo, metrica):
        c=[ROJO,AMBER,VERDE][n-1]
        head=Table([[
            Paragraph('<font color="white"><b>%d</b></font>'%n,st('an',fontSize=14,alignment=TA_CENTER,textColor=WHITE)),
            Paragraph('<b>%s</b>'%que,st('aq',fontSize=9,leading=13))
        ]],colWidths=[10*mm,TW-10*mm],style=TableStyle([
            ('BACKGROUND',(0,0),(0,0),c),('BACKGROUND',(1,0),(1,0),GRIS),
            ('ALIGN',(0,0),(0,0),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
            ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
            ('LEFTPADDING',(1,0),(1,0),8),('BOX',(0,0),(-1,-1),.5,BORDE)]))
        body=Table([
            [Paragraph('<b>QUIEN</b>',SSM),Paragraph(quien,SBO)],
            [Paragraph('<b>PLAZO</b>',SSM),Paragraph(plazo,SBO)],
            [Paragraph('<b>METRICA</b>',SSM),Paragraph(metrica,SBO)],
        ],colWidths=[18*mm,TW-18*mm],style=TableStyle([
            ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),3),
            ('BOTTOMPADDING',(0,0),(-1,-1),3),('LEFTPADDING',(1,0),(1,-1),8),
            ('BACKGROUND',(0,0),(0,-1),GRIS),('BOX',(0,0),(-1,-1),.5,BORDE),
            ('LINEBELOW',(0,0),(-1,-2),.3,BORDE)]))
        return KeepTogether([head,body,Spacer(1,3*mm)])

    S=[]
    S+=[Spacer(1,5*mm)]

    # HEADER con logo
    if LOGO_B64:
        try:
            from reportlab.platypus import Image as RLImage
            logo_buf = _io.BytesIO(_b64.b64decode(LOGO_B64))
            logo_img = RLImage(logo_buf, width=26*mm, height=26*mm)
            S+=[Table([[
                logo_img,
                Paragraph('ChefPanda',st('bn',fontName='Helvetica-Bold',fontSize=22,leading=26,textColor=MARRON)),
                Paragraph('Gestion de reputacion\ndel restaurante',st('tg',fontSize=8,textColor=ROSA,leading=11,alignment=TA_RIGHT)),
            ]],colWidths=[30*mm,TW-30*mm-50*mm,50*mm],style=TableStyle([
                ('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),0),
                ('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),0),
                ('BOTTOMPADDING',(0,0),(-1,-1),0)]))]
        except Exception as e:
            print(f"Logo error: {e}")
            S+=[Paragraph('ChefPanda',st('bn',fontName='Helvetica-Bold',fontSize=22,textColor=MARRON))]
    else:
        S+=[Paragraph('ChefPanda',st('bn',fontName='Helvetica-Bold',fontSize=22,textColor=MARRON))]

    S+=[Spacer(1,4*mm)]
    S+=[Table([[Paragraph('<font color="white"><b>INFORME DE REPUTACION ONLINE  -  Preparado por ChefPanda</b></font>',
        st('it',fontName='Helvetica-Bold',fontSize=9,textColor=WHITE,alignment=TA_CENTER))]],
        colWidths=[TW],style=TableStyle([('BACKGROUND',(0,0),(-1,-1),ROSA),
            ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))]
    S+=[Spacer(1,4*mm)]
    S+=[Paragraph(restaurante,st('T',fontName='Helvetica-Bold',fontSize=24,leading=28,textColor=MARRON))]
    S+=[Paragraph('%s  |  %s'%(ciudad,date.today().strftime('%B %Y')),st('sub',fontSize=10,textColor=MUTED))]
    S+=[Spacer(1,3*mm)]
    S+=[HRFlowable(width='100%',thickness=2,color=ROSA,spaceAfter=3*mm)]
    titular=resultado.get('titular','Analisis de reputacion online')
    S+=[Table([[Paragraph(titular,st('tit',fontName='Helvetica-Bold',fontSize=10,leading=15,
        textColor=MARRON,backColor=MARRON_L,borderPadding=(8,10,8,10)))]],
        colWidths=[TW],style=TableStyle([('BOX',(0,0),(-1,-1),1.5,ROSA),
            ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0),
            ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)]))]
    S+=[Spacer(1,4*mm)]

    # KPIs
    ps=resultado.get('pandascore',50); ps30=resultado.get('pandascore_estimado_30dias',ps+10)
    tend=resultado.get('tendencia','estable')
    tend_sym={'mejora':'+','deterioro':'-','estable':'='}.get(tend,'=')
    cw4=TW/4
    kpi_rows=[
        [Paragraph('PandaScore',st('kl',fontSize=7,textColor=MUTED,alignment=TA_CENTER)),
         Paragraph('En 30 dias',st('kl2',fontSize=7,textColor=MUTED,alignment=TA_CENTER)),
         Paragraph('Tendencia',st('kl3',fontSize=7,textColor=MUTED,alignment=TA_CENTER)),
         Paragraph('Fecha',st('kl4',fontSize=7,textColor=MUTED,alignment=TA_CENTER))],
        [Paragraph('<font size="22" color="#E8527A"><b>%d</b></font>'%ps,SC),
         Paragraph('<font size="22" color="#2D7A4F"><b>%d+</b></font>'%ps30,SC),
         Paragraph('<font size="16" color="#E8890C"><b>%s</b></font>'%tend_sym,SC),
         Paragraph('<font size="11" color="#5C3D2E"><b>%s</b></font>'%date.today().strftime('%d/%m/%y'),SC)],
        [Paragraph('score actual',st('ks',fontSize=7,textColor=MUTED,alignment=TA_CENTER)),
         Paragraph('con mejoras',st('ks2',fontSize=7,textColor=MUTED,alignment=TA_CENTER)),
         Paragraph('esta semana',st('ks3',fontSize=7,textColor=MUTED,alignment=TA_CENTER)),
         Paragraph('analisis',st('ks4',fontSize=7,textColor=MUTED,alignment=TA_CENTER))],
    ]
    S+=[Table(kpi_rows,colWidths=[cw4]*4,style=TableStyle([
        ('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
        ('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),
        ('BACKGROUND',(0,0),(-1,0),MARRON),('TEXTCOLOR',(0,0),(-1,0),WHITE),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('BACKGROUND',(0,1),(-1,2),GRIS),
        ('LINEAFTER',(0,0),(-2,-1),.3,BORDE),('BOX',(0,0),(-1,-1),.5,BORDE)]))]
    S+=[Spacer(1,3*mm)]

    # S01 Puntuaciones reales
    punts_reales=resultado.get('puntuaciones_reales',{})
    if any(v and 'Sin datos' not in str(v) for v in punts_reales.values()):
        S+=hdr('01','PUNTUACIONES REALES EN PLATAFORMAS')
        plats=[[Paragraph('<b>%s</b>'%k.replace('_',' ').title(),st('pl',fontSize=8)),
                Paragraph(str(v),st('pv',fontSize=8,fontName='Helvetica-Bold',textColor=VERDE))]
               for k,v in punts_reales.items() if v and 'Sin datos' not in str(v)]
        if plats:
            S+=[Table(plats,colWidths=[50*mm,TW-50*mm],style=TableStyle([
                ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),5),
                ('BOTTOMPADDING',(0,0),(-1,-1),5),('LEFTPADDING',(0,0),(-1,-1),8),
                ('BACKGROUND',(0,0),(-1,-1),GRIS),('BOX',(0,0),(-1,-1),.5,BORDE),
                ('LINEBELOW',(0,0),(-1,-2),.3,BORDE)]))]
        S+=[Spacer(1,2*mm)]

    # S02 Fortalezas y problemas
    S+=hdr('02','FORTALEZAS Y AREAS DE MEJORA')
    fortalezas=resultado.get('fortalezas_top3',[]); problemas=resultado.get('problemas_top3',[])
    col_w=(TW-4*mm)/2
    S+=[Table([[Paragraph('<b>FORTALEZAS</b>',st('fh',fontName='Helvetica-Bold',fontSize=9,textColor=VERDE)),
                Paragraph('<b>AREAS DE MEJORA</b>',st('ph',fontName='Helvetica-Bold',fontSize=9,textColor=ROJO))]],
        colWidths=[col_w,col_w],style=TableStyle([('BACKGROUND',(0,0),(0,0),VERDE_L),
            ('BACKGROUND',(1,0),(1,0),ROJO_L),('TOPPADDING',(0,0),(-1,-1),5),
            ('BOTTOMPADDING',(0,0),(-1,-1),5),('LEFTPADDING',(0,0),(-1,-1),8),
            ('LINEAFTER',(0,0),(0,-1),1,BORDE)]))]
    rows=[]
    for i in range(max(len(fortalezas),len(problemas),1)):
        f=fortalezas[i] if i<len(fortalezas) else ''
        p=problemas[i]  if i<len(problemas)  else ''
        rows.append([
            Paragraph('+ %s'%f if f else '',st('fi',fontSize=8,textColor=VERDE,leading=12)),
            Paragraph('- %s'%p if p else '',st('pi',fontSize=8,textColor=ROJO,leading=12)),
        ])
    S+=[Table(rows,colWidths=[col_w,col_w],style=TableStyle([
        ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),5),
        ('BOTTOMPADDING',(0,0),(-1,-1),5),('LEFTPADDING',(0,0),(-1,-1),8),
        ('LINEAFTER',(0,0),(0,-1),.5,BORDE),('LINEBELOW',(0,0),(-1,-2),.3,BORDE),
        ('BOX',(0,0),(-1,-1),.5,BORDE)]))]

    # S03 Puntuaciones categoria
    S+=hdr('03','PUNTUACIONES POR CATEGORIA')
    for lbl,key in [('Calidad de la comida','calidad_comida'),('Servicio de sala','servicio'),
                    ('Ambiente y decoracion','ambiente'),('Relacion calidad-precio','precio'),
                    ('Gestion online y respuestas','gestion_online')]:
        v=resultado.get('puntuaciones',{}).get(key,3.0)
        try: v=float(v)
        except: v=3.0
        col=VERDE if v>=4.0 else (AMBER if v>=3.0 else ROJO)
        S+=[bar_row(lbl,v,col),Spacer(1,1*mm)]

    # S04 Reseñas reales
    resenas=resultado.get('resenas_destacadas',[])
    if resenas:
        S+=hdr('04','LO QUE DICEN LOS CLIENTES — RESENAS REALES')
        for res in resenas[:2]:
            tipo=res.get('tipo',''); col_tipo=VERDE if tipo=='positiva' else ROJO
            S+=[Table([[
                Paragraph('<font color="white"><b>%s</b></font>'%tipo.upper(),
                    st('rt',fontSize=7,fontName='Helvetica-Bold',alignment=TA_CENTER,textColor=WHITE)),
                Paragraph('"%s"'%res.get('resumen',''),st('rr',fontName='Helvetica-Oblique',fontSize=8.5,leading=13)),
            ],[
                Paragraph('',st('re')),
                Paragraph('<b>Significa:</b> %s  |  <b>Accion:</b> %s'%(
                    res.get('significado',''),res.get('accion','')),
                    st('ra',fontSize=8,leading=12,textColor=HexColor('#444444'))),
            ]],colWidths=[16*mm,TW-16*mm],style=TableStyle([
                ('BACKGROUND',(0,0),(0,-1),col_tipo),('ALIGN',(0,0),(0,-1),'CENTER'),
                ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),5),
                ('BOTTOMPADDING',(0,0),(-1,-1),5),('LEFTPADDING',(1,0),(1,-1),8),
                ('BOX',(0,0),(-1,-1),.5,BORDE),('LINEBELOW',(0,0),(-1,-2),.3,BORDE),
                ('SPAN',(0,0),(0,-1))]))]
            S+=[Spacer(1,2*mm)]

    # S05 Silencios
    silencios=resultado.get('silencios_oportunidades',[])
    if silencios:
        S+=hdr('05','OPORTUNIDADES OCULTAS EN LOS SILENCIOS')
        S+=[Paragraph('Lo que los clientes NO mencionan — cada silencio es una oportunidad:',
            st('si',fontSize=8,textColor=MUTED,leading=12,spaceAfter=4))]
        w1,w23=28*mm,TW-28*mm
        for sil in silencios:
            S+=[Table([[
                Paragraph('<b>%s</b>'%sil.get('aspecto',''),st('sa',fontSize=8,fontName='Helvetica-Bold')),
                Paragraph('<i>Silencio:</i> %s'%sil.get('interpretacion',''),SBO),
                Paragraph('<b>Oportunidad:</b> %s'%sil.get('oportunidad',''),
                    st('so',fontSize=8,fontName='Helvetica-Bold',textColor=VERDE)),
            ]],colWidths=[w1,w23*0.43,w23*0.57],style=TableStyle([
                ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),5),
                ('BOTTOMPADDING',(0,0),(-1,-1),5),('LEFTPADDING',(0,0),(0,0),6),
                ('LEFTPADDING',(1,0),(2,0),8),('BACKGROUND',(0,0),(0,0),GRIS),
                ('LINEBELOW',(0,0),(-1,-1),.3,BORDE),('LINEAFTER',(0,0),(-2,-1),.3,BORDE)]))]

    # S06 Plan de accion
    S+=hdr('06','PLAN DE ACCION — 3 PASOS PARA ESTA SEMANA')
    accion=resultado.get('accion_urgente','')
    if accion:
        S+=[Table([[Paragraph('ACCION URGENTE: %s'%accion,st('au',fontName='Helvetica-Bold',
            fontSize=10,leading=15,textColor=MARRON,backColor=MARRON_L,borderPadding=(8,10,8,10)))]],
            colWidths=[TW],style=TableStyle([('BOX',(0,0),(-1,-1),2,ROSA),
                ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0),
                ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)]))]
        S+=[Spacer(1,3*mm)]
    for i,paso in enumerate(resultado.get('plan_semana',[])[:3]):
        S+=[accion_block(i+1,paso.get('accion',''),paso.get('responsable',''),
                         paso.get('dia',''),paso.get('metrica',''))]

    # S07 Respuestas
    respuestas=resultado.get('respuestas_sugeridas',[])
    if respuestas:
        S+=hdr('07','RESPUESTAS LISTAS PARA PUBLICAR')
        for i,resp in enumerate(respuestas[:2]):
            S+=[Paragraph('<b>RESPUESTA %d</b>'%(i+1),SB),Spacer(1,1*mm)]
            S+=[Paragraph('"%s"'%resp,SQ)]
            S+=[Spacer(1,3*mm)]

    # S08 Oportunidades
    oportunidades=resultado.get('oportunidades_estrategicas',[])
    if oportunidades:
        S+=hdr('08','OPORTUNIDADES ESTRATEGICAS')
        for i,op in enumerate(oportunidades[:3]):
            S+=[Table([[
                Paragraph('<font color="white"><b>%d</b></font>'%(i+1),
                    st('on',fontSize=12,alignment=TA_CENTER,textColor=WHITE)),
                Paragraph(str(op),st('op',fontSize=9,leading=13))
            ]],colWidths=[10*mm,TW-10*mm],style=TableStyle([
                ('BACKGROUND',(0,0),(0,0),VERDE),('BACKGROUND',(1,0),(1,0),VERDE_L),
                ('ALIGN',(0,0),(0,0),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
                ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
                ('LEFTPADDING',(1,0),(1,0),8),('BOX',(0,0),(-1,-1),.5,BORDE)]))]
            S+=[Spacer(1,2*mm)]

    # BUILD
    tmp=tempfile.NamedTemporaryFile(suffix='.pdf',delete=False)
    doc=SimpleDocTemplate(tmp.name,pagesize=A4,
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
        "ChefPanda Admin\n\n"
        "CLIENTES\n"
        "/nuevo_cliente Nombre | Cocina | Ciudad\n"
        "/listar\n"
        "/ver ID\n"
        "/estado\n\n"
        "ANALISIS\n"
        "/analizar ID          — solo si hay scrape nuevo\n"
        "/analizar ID forzar   — regenera aunque no haya cambios\n"
        "/analizar ID fecha    — resetea fecha y fuerza regeneracion\n\n"
        "DATOS\n"
        "/urls ID URL_google   — guardar URL de Google Maps\n\n"
        "GESTION\n"
        "/pausar ID / /activar ID"
    )

async def nuevo_cliente(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    try:
        partes=[p.strip() for p in " ".join(ctx.args).split("|")]
        if len(partes)<3:
            await update.message.reply_text("Formato: /nuevo_cliente Nombre | Cocina | Ciudad")
            return
        res=sb_insert("restaurantes",{"nombre":partes[0],"tipo_cocina":partes[1],"ciudad":partes[2],"plan":"pro","activo":True})
        rid=res[0]["id"]
        await update.message.reply_text(
            f"Cliente anadido: {partes[0]}\nID: {rid}\n\n"
            f"Paso 1: python scrape.py {rid}\n"
            f"Paso 2: /analizar {rid}"
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def listar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    try:
        clientes=sb_get("restaurantes","activo=eq.true&order=id",CAMPOS_RESTAURANTE)
        if not clientes:
            await update.message.reply_text("No hay clientes activos.")
            return
        msg="Clientes ChefPanda\n\n"
        for c in clientes:
            scrape  = c.get('ultima_actualizacion_resenas') or 'nunca'
            analisis= c.get('ultima_analisis') or 'nunca'
            msg+=f"{c['id']} - {c['nombre']} ({c.get('ciudad','')})\n"
            msg+=f"   Scrape: {scrape} | Analisis: {analisis}\n\n"
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def estado(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    try:
        clientes=sb_get("restaurantes","activo=eq.true")
        await update.message.reply_text(
            f"Estado ChefPanda\n\nClientes: {len(clientes)}\nIngresos: {len(clientes)*99} EUR/mes"
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def cmd_ver(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Uso: /ver ID"); return
    try:
        rid=int(ctx.args[0])
        clientes=sb_get("restaurantes",f"id=eq.{rid}",CAMPOS_RESTAURANTE)
        if not clientes:
            await update.message.reply_text(f"No existe ID {rid}"); return
        c=clientes[0]
        try: n=len(sb_get("resenas",f"restaurante_id=eq.{rid}"))
        except: n=0
        await update.message.reply_text(
            f"Ficha: {c['nombre']}\n"
            f"Cocina: {c.get('tipo_cocina','?')} | Ciudad: {c.get('ciudad','?')}\n"
            f"Ultimo scrape:   {c.get('ultima_actualizacion_resenas','nunca')}\n"
            f"Ultimo analisis: {c.get('ultima_analisis','nunca')}\n"
            f"Resenas en BBDD: {n}\n"
            f"URL Google: {c.get('url_google','no configurada')[:60] if c.get('url_google') else 'no configurada'}"
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def cmd_urls(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args or len(ctx.args)<2:
        await update.message.reply_text("Uso: /urls ID URL_google"); return
    try:
        sb_update("restaurantes",int(ctx.args[0]),{"url_google":ctx.args[1]})
        await update.message.reply_text(f"URL guardada para ID {ctx.args[0]}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def analizar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Uso: /analizar ID [forzar|fecha]"); return
    try:
        rid    = int(ctx.args[0])
        flag   = ctx.args[1].lower() if len(ctx.args) > 1 else ""
        forzar = flag in ("forzar", "fecha")

        clientes = sb_get("restaurantes", f"id=eq.{rid}", CAMPOS_RESTAURANTE)
        if not clientes:
            await update.message.reply_text(f"No existe ID {rid}"); return
        c = clientes[0]

        if flag == "fecha":
            sb_update("restaurantes", rid, {"ultima_analisis": None})
            await update.message.reply_text("Fecha reseteada. Regenerando...")

        ultimo_scrape   = c.get('ultima_actualizacion_resenas')
        ultimo_analisis = c.get('ultima_analisis')

        if not ultimo_scrape:
            await update.message.reply_text(
                f"No hay resenas scrapeadas para {c['nombre']}.\n\n"
                f"Ejecuta primero:\npython scrape.py {rid}"
            ); return

        if not forzar and ultimo_scrape and ultimo_analisis:
            if ultimo_analisis >= ultimo_scrape:
                await update.message.reply_text(
                    f"No hay resenas nuevas desde el ultimo analisis.\n"
                    f"Ultimo scrape:   {ultimo_scrape}\n"
                    f"Ultimo analisis: {ultimo_analisis}\n\n"
                    f"Usa /analizar {rid} forzar para regenerar."
                )
                # Intentar recuperar ultimo PDF guardado
                try:
                    pdfs = sb_get("pdfs", f"restaurante_id=eq.{rid}&order=fecha.desc&limit=1",
                                  "id,fecha,pdf_base64")
                    if pdfs and pdfs[0].get('pdf_base64'):
                        pdf_bytes = _b64.b64decode(pdfs[0]['pdf_base64'])
                        await update.message.reply_document(
                            document=_io.BytesIO(pdf_bytes),
                            filename=f"ChefPanda_{c['nombre'].replace(' ','_')}_cached.pdf",
                            caption=f"Ultimo informe: {pdfs[0].get('fecha','?')}"
                        )
                except Exception as e:
                    print(f"Error recuperando PDF cache: {e}")
                return

        await update.message.reply_text(f"Analizando {c['nombre']}...")
        await lanzar_analisis(rid, c, update)

    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def lanzar_analisis(rid, restaurante, update):
    nombre = restaurante['nombre']
    ciudad = restaurante.get('ciudad','')
    cocina = restaurante.get('tipo_cocina','')

    historico = sb_get("analisis", f"restaurante_id=eq.{rid}&order=fecha.desc&limit=3",
                       "semana,pandascore,titular")
    hist_txt = ""
    if historico:
        hist_txt = "HISTORICO:\n" + "".join(
            f"- Semana {h.get('semana')}: Score {h.get('pandascore')}\n" for h in historico)

    # Paso 1: leer resenas de Supabase
    await update.message.reply_text("Leyendo resenas de la base de datos...")
    contexto_resenas, tiene_resenas = construir_contexto_resenas(rid, nombre, ciudad)

    if not tiene_resenas:
        await update.message.reply_text(
            f"No hay resenas en BBDD para {nombre}.\n"
            f"Ejecuta: python scrape.py {rid}"
        ); return

    # Paso 2: contrastar KPIs web
    await update.message.reply_text("Verificando KPIs publicos...")
    kpis_web = contrastar_kpis_web(nombre, ciudad)

    # Paso 3: analisis Claude
    await update.message.reply_text("Generando analisis...")
    texto = analizar_con_claude(nombre, ciudad, cocina, contexto_resenas, kpis_web, hist_txt)
    texto_limpio = texto.replace("```json","").replace("```","").strip()

    # Parsear JSON con fallback
    try:
        resultado = json.loads(texto_limpio)
    except:
        try:
            r = texto_limpio
            if r.count('"') % 2 != 0: r += '"'
            r += ']' * max(0, r.count('[') - r.count(']'))
            r += '}' * max(0, r.count('{') - r.count('}'))
            resultado = json.loads(r)
        except Exception as e:
            await update.message.reply_text(f"Error parseando respuesta: {e}\n\n{texto_limpio[:400]}")
            return

    # Guardar analisis en Supabase
    hoy = date.today()
    semana = hoy.isocalendar()[1]
    try:
        sb_insert("analisis", {
            "restaurante_id": rid, "semana": semana, "año": hoy.year,
            "informe_texto": texto, "pandascore": resultado.get("pandascore", 0),
            "titular": resultado.get("titular",""),
            "resumen_telegram": resultado.get("resumen_telegram","")
        })
        sb_insert("pandascore_historico", {
            "restaurante_id": rid, "semana": semana, "año": hoy.year,
            "score": resultado.get("pandascore", 0)
        })
        sb_update("restaurantes", rid, {"ultima_analisis": str(hoy)})
    except Exception as e:
        print(f"Error guardando analisis: {e}")

    # Resumen Telegram
    t   = resultado.get('tendencia','estable')
    t_e = {"mejora":"Mejora","deterioro":"Bajando","estable":"Estable"}.get(t, t)
    problemas  = "\n".join(["  - "+str(p) for p in resultado.get("problemas_top3",[])])
    fortalezas = "\n".join(["  + "+str(f) for f in resultado.get("fortalezas_top3",[])])
    titular    = str(resultado.get('titular','')).replace('_',' ').replace('*',' ').replace('`',' ')
    accion     = str(resultado.get('accion_urgente','')).replace('_',' ').replace('*',' ')
    punts      = resultado.get('puntuaciones_reales',{})
    punts_txt  = "".join(f"  {k.replace('_',' ').title()}: {v}\n"
                         for k,v in punts.items() if v and 'Sin datos' not in str(v))

    msg = (
        "Analisis completado: " + nombre + "\n\n"
        "PandaScore: " + str(resultado.get('pandascore','?')) + "/100  " + t_e + "\n"
        "En 30 dias: " + str(resultado.get('pandascore_estimado_30dias','?')) + "/100\n\n"
    )
    if punts_txt: msg += "Puntuaciones:\n" + punts_txt + "\n"
    msg += (titular + "\n\nFortalezas:\n" + fortalezas +
            "\n\nProblemas:\n" + problemas +
            "\n\nAccion urgente:\n" + accion +
            "\n\nGenerando PDF...")
    await update.message.reply_text(msg)

    # Generar PDF
    try:
        pdf_path = generar_pdf(nombre, ciudad, resultado)
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()
        os.unlink(pdf_path)

        # Guardar PDF en Supabase para cache
        try:
            sb_insert("pdfs", {
                "restaurante_id": rid,
                "fecha":          str(hoy),
                "semana":         semana,
                "año":            hoy.year,
                "pdf_base64":     _b64.b64encode(pdf_bytes).decode()
            })
            print(f"PDF guardado en BBDD para restaurante {rid}")
        except Exception as e:
            print(f"Error guardando PDF en BBDD: {e}")

        # Enviar PDF por Telegram
        await update.message.reply_document(
            document=_io.BytesIO(pdf_bytes),
            filename=f"ChefPanda_{nombre.replace(' ','_')}_{hoy}.pdf",
            caption=f"Informe ChefPanda - {nombre}"
        )
    except Exception as e:
        await update.message.reply_text(f"Error generando PDF: {e}")

async def pausar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Uso: /pausar ID"); return
    try:
        sb_update("restaurantes",int(ctx.args[0]),{"activo":False})
        await update.message.reply_text(f"Cliente {ctx.args[0]} pausado.")
    except Exception as e: await update.message.reply_text(f"Error: {e}")

async def activar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Uso: /activar ID"); return
    try:
        sb_update("restaurantes",int(ctx.args[0]),{"activo":True})
        await update.message.reply_text(f"Cliente {ctx.args[0]} activado.")
    except Exception as e: await update.message.reply_text(f"Error: {e}")

# ── MAIN ──────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",         start))
    app.add_handler(CommandHandler("nuevo_cliente", nuevo_cliente))
    app.add_handler(CommandHandler("listar",        listar))
    app.add_handler(CommandHandler("estado",        estado))
    app.add_handler(CommandHandler("ver",           cmd_ver))
    app.add_handler(CommandHandler("urls",          cmd_urls))
    app.add_handler(CommandHandler("analizar",      analizar))
    app.add_handler(CommandHandler("pausar",        pausar))
    app.add_handler(CommandHandler("activar",       activar))
    print("ChefPanda Bot arrancado...")
    app.run_polling()

if __name__ == "__main__":
    main()
