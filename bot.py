# -*- coding: utf-8 -*-
import os
import json
import re
import io as _io
import logging
import tempfile
import urllib.request as ur
import urllib.parse as up
import urllib.error as ue
from datetime import date, datetime
from typing import Optional, Dict, Any, List, Tuple

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# =========================================================
# CONFIG
# =========================================================

load_dotenv()

BOT_TOKEN        = os.environ["BOT_TOKEN"]
ADMIN_CHAT       = os.environ["ADMIN_CHAT_ID"]
SUPABASE_URL     = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY     = os.environ["SUPABASE_KEY"]
ANTHROPIC_KEY    = os.environ["ANTHROPIC_API_KEY"]
PDF_BUCKET       = os.environ.get("SUPABASE_PDF_BUCKET", "")  # opcional, ej: pdfs

PLATAFORMAS_VALIDAS = {
    "google",
    "tripadvisor",
    "thefork",
    "facebook",
    "instagram",
    "justeat",
    "glovo",
    "ubereats",
    "yelp",
    "foursquare",
}

# scraping/manual/api/import/disabled
MODOS_VALIDOS = {"scraping", "manual", "api", "import", "disabled"}

CAMPOS_RESTAURANTE = (
    "id,nombre,ciudad,tipo_cocina,direccion,telefono,telegram_chat_id,"
    "plan,activo,fecha_alta,ultima_analisis,ultima_actualizacion_resenas,notas,metadata"
)

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("chefpanda-admin")

# =========================================================
# HELPERS
# =========================================================

def is_admin(update: Update) -> bool:
    return str(update.effective_chat.id) == ADMIN_CHAT

def safe_text(s: Any) -> str:
    return "" if s is None else str(s).strip()

def is_valid_url(s: str) -> bool:
    return bool(re.match(r"^https?://", safe_text(s), re.IGNORECASE))

def truncate(s: str, n: int = 120) -> str:
    s = safe_text(s)
    return s if len(s) <= n else s[:n-3] + "..."

def default_modo_for_plataforma(plataforma: str) -> str:
    # Puedes cambiar esta estrategia después.
    if plataforma == "google":
        return "scraping"
    return "manual"

def today_iso() -> str:
    return str(date.today())

# =========================================================
# SUPABASE
# =========================================================

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
        "POST",
        tabla,
        query=query,
        body=datos,
        prefer="resolution=merge-duplicates,return=representation"
    ) or []

def sb_update_by_id(tabla: str, id_val: int, datos: dict):
    return sb_request("PATCH", tabla, query=f"id=eq.{id_val}", body=datos)

def sb_update_where(tabla: str, filtro: str, datos: dict):
    return sb_request("PATCH", tabla, query=filtro, body=datos)

# =========================================================
# ANTHROPIC
# =========================================================

def claude_call(messages, max_tokens=3000, tools=None):
    body = {
        "model": "claude-haiku-4-5-20251001",
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

    # intento directo
    try:
        return json.loads(raw)
    except Exception:
        pass

    # intentar extraer primer bloque {...}
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except Exception:
            raw = candidate

    # parche básico
    try:
        r = raw
        if r.count('"') % 2 != 0:
            r += '"'
        r += "]" * max(0, r.count("[") - r.count("]"))
        r += "}" * max(0, r.count("{") - r.count("}"))
        return json.loads(r)
    except Exception as e:
        raise Exception(f"No pude parsear JSON de Claude: {e}")

# =========================================================
# DATA ACCESS
# =========================================================

def get_restaurante(rid: int) -> Optional[dict]:
    rows = sb_get("restaurantes", f"id=eq.{rid}&limit=1", CAMPOS_RESTAURANTE)
    return rows[0] if rows else None

def get_restaurantes_activos() -> List[dict]:
    return sb_get("restaurantes", "activo=eq.true&order=id.asc", CAMPOS_RESTAURANTE)

def get_fuentes_restaurante(rid: int, solo_activas: bool = False) -> List[dict]:
    filtro = f"restaurante_id=eq.{rid}&order=prioridad.asc,id.asc"
    if solo_activas:
        filtro = f"restaurante_id=eq.{rid}&activa=eq.true&order=prioridad.asc,id.asc"
    return sb_get(
        "fuentes_restaurante",
        filtro,
        "id,restaurante_id,plataforma,url,activa,modo_acceso,prioridad,ultima_actualizacion,ultimo_estado,ultimo_error,metadata"
    )

def get_fuente(rid: int, plataforma: str) -> Optional[dict]:
    rows = sb_get(
        "fuentes_restaurante",
        f"restaurante_id=eq.{rid}&plataforma=eq.{plataforma}&limit=1",
        "id,restaurante_id,plataforma,url,activa,modo_acceso,prioridad,ultima_actualizacion,ultimo_estado,ultimo_error,metadata"
    )
    return rows[0] if rows else None

def contar_resenas_restaurante(rid: int) -> int:
    return len(sb_get("resenas", f"restaurante_id=eq.{rid}", "id"))

def get_ultima_ejecucion_fuente(rid: int) -> List[dict]:
    try:
        return sb_get("v_ultima_ejecucion_fuente", f"restaurante_id=eq.{rid}", "*")
    except Exception:
        return []

def get_ultimos_analisis(rid: int, limit: int = 3) -> List[dict]:
    return sb_get(
        "analisis",
        f"restaurante_id=eq.{rid}&order=fecha.desc&limit={limit}",
        "id,semana,anio,pandascore,titular,fecha"
    )

# =========================================================
# CONTEXTO Y ANÁLISIS
# =========================================================

def construir_contexto_resenas(rid: int, nombre: str, ciudad: str):
    try:
        resenas = sb_get(
            "resenas",
            f"restaurante_id=eq.{rid}&order=fecha_scrape.desc,created_at.desc&limit=80",
            "plataforma,autor,fecha_resena_raw,fecha_resena,nota,titulo,texto,tiene_respuesta,respuesta_propietario,fecha_scrape"
        )
    except Exception:
        return "", False, {"total": 0, "media": None}

    if not resenas:
        return "", False, {"total": 0, "media": None}

    # priorizar reseñas con texto
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
  "resenas_destacadas": [
    {{
      "tipo": "positiva",
      "resumen": "cita literal o resumen de reseña real positiva",
      "significado": "qué revela sobre el negocio",
      "accion": "cómo capitalizar"
    }},
    {{
      "tipo": "negativa",
      "resumen": "cita literal o resumen de reseña real negativa",
      "significado": "causa raíz del problema",
      "accion": "cómo resolver"
    }}
  ],
  "silencios_oportunidades": [
    {{"aspecto": "cosa que no mencionan", "interpretacion": "significado", "oportunidad": "acción"}},
    {{"aspecto": "segundo silencio", "interpretacion": "significado", "oportunidad": "acción"}},
    {{"aspecto": "tercer silencio", "interpretacion": "significado", "oportunidad": "acción"}}
  ],
  "accion_urgente": "acción más importante para hacer HOY muy concreta",
  "plan_semana": [
    {{"dia": "Hoy", "accion": "acción 1", "responsable": "quién", "metrica": "cómo medir"}},
    {{"dia": "Esta semana", "accion": "acción 2", "responsable": "quién", "metrica": "cómo medir"}},
    {{"dia": "Este mes", "accion": "acción 3", "responsable": "quién", "metrica": "cómo medir"}}
  ],
  "respuestas_sugeridas": [
    "Respuesta completa para reseña negativa típica. Tono humano.",
    "Respuesta completa para reseña positiva destacada."
  ],
  "oportunidades_estrategicas": [
    "Oportunidad 1 con evidencia y acción específica",
    "Oportunidad 2 con evidencia y acción específica",
    "Oportunidad 3 con evidencia y acción específica"
  ],
  "resumen_telegram": "3 líneas: qué va bien, qué mejorar, acción concreta"
}}"""

    data = claude_call([{"role": "user", "content": prompt}], max_tokens=3500)
    return extraer_texto(data)

# =========================================================
# PDF
# =========================================================

def generar_pdf(restaurante, ciudad, resultado):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_JUSTIFY
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether
    from reportlab.lib.colors import HexColor

    ROSA=HexColor('#E8527A'); MARRON=HexColor('#5C3D2E')
    MARRON_L=HexColor('#F5EDE8'); VERDE=HexColor('#2D7A4F'); VERDE_L=HexColor('#E8F5EE')
    ROJO=HexColor('#C0392B'); AMBER=HexColor('#E8890C')
    GRIS=HexColor('#F5F5F5'); BORDE=HexColor('#E0D5D0'); MUTED=HexColor('#8A7A72')
    NEGRO=HexColor('#2C2C2C'); WHITE=colors.white; W,H=A4; TW=W-32*mm

    def st(n,**k):
        d=dict(fontName='Helvetica',fontSize=9,leading=13,textColor=NEGRO,spaceAfter=3)
        d.update(k); return ParagraphStyle(n,**d)

    SB=st('b',fontName='Helvetica-Bold')
    SBO=st('bo',fontSize=9,leading=14,alignment=TA_JUSTIFY,spaceAfter=4)
    SSM=st('sm',fontSize=7.5,leading=11,textColor=MUTED)
    SQ=st('q',fontName='Helvetica-Oblique',fontSize=8.5,leading=13,
          textColor=HexColor('#1A4A2A'),backColor=VERDE_L,borderPadding=(5,8,5,8))
    SC=st('c',alignment=TA_CENTER)

    def hdr(n,t):
        return [Spacer(1,4*mm),
            Table([[Paragraph(f'<font color="white"><b>{n}  {t}</b></font>',
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
            Table([['']], colWidths=[f],   style=TableStyle([('BACKGROUND',(0,0),(-1,-1),col),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)])),
            Table([['']], colWidths=[bw-f],style=TableStyle([('BACKGROUND',(0,0),(-1,-1),BORDE),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)])),
            Paragraph(f'<b>{val:.1f}</b>',st('bv',fontSize=8,alignment=TA_RIGHT))]],
            colWidths=[38*mm,f,bw-f,12*mm],
            style=TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),0),
                ('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),1),('BOTTOMPADDING',(0,0),(-1,-1),1)]))

    def accion_block(n, que, quien, plazo, metrica):
        c=[ROJO,AMBER,VERDE][n-1]
        head=Table([[
            Paragraph(f'<font color="white"><b>{n}</b></font>',st('an',fontSize=14,alignment=TA_CENTER,textColor=WHITE)),
            Paragraph(f'<b>{que}</b>',st('aq',fontSize=9,leading=13))
        ]],colWidths=[10*mm,TW-10*mm],style=TableStyle([
            ('BACKGROUND',(0,0),(0,0),c),('BACKGROUND',(1,0),(1,0),GRIS),
            ('ALIGN',(0,0),(0,0),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
            ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
            ('LEFTPADDING',(1,0),(1,0),8),('BOX',(0,0),(-1,-1),.5,BORDE)]))
        body=Table([
            [Paragraph('<b>QUIÉN</b>',SSM),Paragraph(quien,SBO)],
            [Paragraph('<b>PLAZO</b>',SSM),Paragraph(plazo,SBO)],
            [Paragraph('<b>MÉTRICA</b>',SSM),Paragraph(metrica,SBO)],
        ],colWidths=[18*mm,TW-18*mm],style=TableStyle([
            ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),3),
            ('BOTTOMPADDING',(0,0),(-1,-1),3),('LEFTPADDING',(1,0),(1,-1),8),
            ('BACKGROUND',(0,0),(0,-1),GRIS),('BOX',(0,0),(-1,-1),.5,BORDE),
            ('LINEBELOW',(0,0),(-1,-2),.3,BORDE)]))
        return KeepTogether([head,body,Spacer(1,3*mm)])

    S=[]
    S+=[Spacer(1,5*mm)]
    S+=[Paragraph('ChefPanda',st('bn',fontName='Helvetica-Bold',fontSize=22,textColor=MARRON))]
    S+=[Spacer(1,4*mm)]
    S+=[Paragraph(restaurante,st('T',fontName='Helvetica-Bold',fontSize=24,leading=28,textColor=MARRON))]
    S+=[Paragraph(f'{ciudad}  |  {date.today().strftime("%B %Y")}',st('sub',fontSize=10,textColor=MUTED))]
    S+=[Spacer(1,3*mm)]
    S+=[HRFlowable(width='100%',thickness=2,color=ROSA,spaceAfter=3*mm)]

    titular=resultado.get('titular','Análisis de reputación online')
    S+=[Table([[Paragraph(titular,st('tit',fontName='Helvetica-Bold',fontSize=10,leading=15,
        textColor=MARRON,backColor=MARRON_L,borderPadding=(8,10,8,10)))]],
        colWidths=[TW],style=TableStyle([('BOX',(0,0),(-1,-1),1.5,ROSA)]))]
    S+=[Spacer(1,4*mm)]

    ps=resultado.get('pandascore',50); ps30=resultado.get('pandascore_estimado_30dias',ps+10)
    tend=resultado.get('tendencia','estable')
    tend_sym={'mejora':'+','deterioro':'-','estable':'='}.get(tend,'=')
    cw4=TW/4
    kpi_rows=[
        [Paragraph('PandaScore',st('kl',fontSize=7,textColor=MUTED,alignment=TA_CENTER)),
         Paragraph('En 30 días',st('kl2',fontSize=7,textColor=MUTED,alignment=TA_CENTER)),
         Paragraph('Tendencia',st('kl3',fontSize=7,textColor=MUTED,alignment=TA_CENTER)),
         Paragraph('Fecha',st('kl4',fontSize=7,textColor=MUTED,alignment=TA_CENTER))],
        [Paragraph(f'<font size="22" color="#E8527A"><b>{ps}</b></font>',SC),
         Paragraph(f'<font size="22" color="#2D7A4F"><b>{ps30}+</b></font>',SC),
         Paragraph(f'<font size="16" color="#E8890C"><b>{tend_sym}</b></font>',SC),
         Paragraph(f'<font size="11" color="#5C3D2E"><b>{date.today().strftime("%d/%m/%y")}</b></font>',SC)],
    ]
    S+=[Table(kpi_rows,colWidths=[cw4]*4,style=TableStyle([
        ('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
        ('BACKGROUND',(0,0),(-1,0),GRIS),('BOX',(0,0),(-1,-1),.5,BORDE)
    ]))]
    S+=[Spacer(1,3*mm)]

    S+=hdr('01','FORTALEZAS Y ÁREAS DE MEJORA')
    fortalezas=resultado.get('fortalezas_top3',[])
    problemas=resultado.get('problemas_top3',[])
    for f in fortalezas[:3]:
        S.append(Paragraph(f'+ {f}', st('f', fontSize=9, textColor=VERDE)))
    S.append(Spacer(1,2*mm))
    for p in problemas[:3]:
        S.append(Paragraph(f'- {p}', st('p', fontSize=9, textColor=ROJO)))

    S+=hdr('02','PUNTUACIONES POR CATEGORÍA')
    for lbl,key in [('Calidad de la comida','calidad_comida'),('Servicio','servicio'),
                    ('Ambiente','ambiente'),('Precio','precio'),('Gestión online','gestion_online')]:
        v=resultado.get('puntuaciones',{}).get(key,3.0)
        try: v=float(v)
        except: v=3.0
        col=VERDE if v>=4.0 else (AMBER if v>=3.0 else ROJO)
        S+=[bar_row(lbl,v,col),Spacer(1,1*mm)]

    S+=hdr('03','PLAN DE ACCIÓN')
    accion=resultado.get('accion_urgente','')
    if accion:
        S.append(Paragraph(f'<b>Acción urgente:</b> {accion}', st('au', fontSize=10, textColor=MARRON)))
        S.append(Spacer(1,2*mm))
    for i,paso in enumerate(resultado.get('plan_semana',[])[:3]):
        S+=[accion_block(i+1,paso.get('accion',''),paso.get('responsable',''),
                         paso.get('dia',''),paso.get('metrica',''))]

    respuestas=resultado.get('respuestas_sugeridas',[])
    if respuestas:
        S+=hdr('04','RESPUESTAS SUGERIDAS')
        for i,resp in enumerate(respuestas[:2]):
            S.append(Paragraph(f'<b>Respuesta {i+1}</b>', SB))
            S.append(Paragraph(f'"{resp}"', SQ))
            S.append(Spacer(1,2*mm))

    tmp=tempfile.NamedTemporaryFile(suffix='.pdf',delete=False)
    doc=SimpleDocTemplate(tmp.name,pagesize=A4,leftMargin=16*mm,rightMargin=16*mm,topMargin=20*mm,bottomMargin=16*mm)

    def on_page(c,d):
        c.setFillColor(MARRON); c.rect(0,H-16*mm,W,16*mm,fill=1,stroke=0)
        c.setFont('Helvetica-Bold',11); c.setFillColor(WHITE)
        c.drawString(6*mm,H-10*mm,'ChefPanda')
        c.setFont('Helvetica',7)
        c.drawRightString(W-6*mm,H-10*mm,f'{restaurante} - {ciudad}')
        c.setFont('Helvetica',7); c.setFillColor(MUTED)
        c.drawString(6*mm,4*mm,'ChefPanda - Gestión de reputación')
        c.drawRightString(W-6*mm,4*mm,f'Página {d.page}')

    doc.build(S,onFirstPage=on_page,onLaterPages=on_page)
    return tmp.name

# =========================================================
# STORAGE PDF
# =========================================================

def upload_pdf_to_storage(pdf_bytes: bytes, filename: str):
    if not PDF_BUCKET:
        return None

    path = f"object/{PDF_BUCKET}/{filename}"
    return sb_request(
        "POST",
        path,
        body=pdf_bytes,
        storage=True,
        extra_headers={
            "Content-Type": "application/pdf",
            "x-upsert": "true"
        }
    )

# =========================================================
# PERSISTENCIA DE NEGOCIO
# =========================================================

def guardar_acciones_desde_resultado(analisis_id: int, rid: int, resultado: dict):
    acciones = []
    for paso in resultado.get("plan_semana", [])[:3]:
        descripcion = safe_text(paso.get("accion"))
        if not descripcion:
            continue
        acciones.append({
            "analisis_id": analisis_id,
            "restaurante_id": rid,
            "descripcion": descripcion,
            "responsable": safe_text(paso.get("responsable")) or None,
            "plazo_fecha": None,
            "estado": "pendiente",
            "prioridad": "media",
            "metadata": {
                "dia": safe_text(paso.get("dia")),
                "metrica": safe_text(paso.get("metrica"))
            }
        })

    if acciones:
        sb_insert("acciones", acciones)

def guardar_analisis_y_metricas(rid: int, resultado: dict, texto: str, kpis_locales: dict, kpis_web: str):
    hoy = date.today()
    semana = hoy.isocalendar()[1]

    analisis_rows = sb_upsert("analisis", {
        "restaurante_id": rid,
        "semana": semana,
        "anio": hoy.year,
        "fecha": str(hoy),
        "total_resenas": kpis_locales.get("total"),
        "media_nota": kpis_locales.get("media"),
        "pandascore": resultado.get("pandascore", 0),
        "titular": resultado.get("titular", ""),
        "resumen_telegram": resultado.get("resumen_telegram", ""),
        "informe_texto": texto,
        "contexto_raw": {
            "kpis_web": kpis_web,
            "kpis_locales": kpis_locales
        }
    }, "restaurante_id,anio,semana")

    analisis_id = analisis_rows[0]["id"]

    sb_update_by_id("restaurantes", rid, {"ultima_analisis": str(hoy)})

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
        "restaurante_id": rid,
        "anio": hoy.year,
        "semana": semana,
        "fecha": str(hoy),
        "total_resenas": total,
        "total_con_texto": total_con_texto,
        "media_nota": kpis_locales.get("media"),
        "pct_con_respuesta": pct_resp,
        "total_google": por_plataforma.get("google", 0),
        "total_tripadvisor": por_plataforma.get("tripadvisor", 0),
        "total_thefork": por_plataforma.get("thefork", 0),
        "total_facebook": por_plataforma.get("facebook", 0),
        "total_instagram": por_plataforma.get("instagram", 0),
        "total_justeat": por_plataforma.get("justeat", 0),
        "total_glovo": por_plataforma.get("glovo", 0),
        "total_ubereats": por_plataforma.get("ubereats", 0),
        "total_yelp": por_plataforma.get("yelp", 0),
        "total_foursquare": por_plataforma.get("foursquare", 0),
        "temas": {}
    }, "restaurante_id,anio,semana")

    guardar_acciones_desde_resultado(analisis_id, rid, resultado)
    return analisis_id

# =========================================================
# COMANDOS TELEGRAM
# =========================================================

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
        "/analizar ID forzar\n"
        "/analizar ID fecha\n\n"
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
            "nombre": partes[0],
            "tipo_cocina": partes[1],
            "ciudad": partes[2],
            "plan": "pro",
            "activo": True
        })
        rid = res[0]["id"]

        await update.message.reply_text(
            f"Cliente añadido: {partes[0]}\n"
            f"ID: {rid}\n\n"
            f"Añade fuentes con:\n"
            f"/urls {rid} google https://...\n"
            f"/urls {rid} tripadvisor https://...\n"
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
            scrape = c.get("ultima_actualizacion_resenas") or "nunca"
            analisis = c.get("ultima_analisis") or "nunca"
            fuentes = get_fuentes_restaurante(c["id"])
            fuentes_txt = ", ".join(
                [f"{f['plataforma']}{'' if f.get('activa') else ' (off)'}" for f in fuentes]
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
        clientes = sb_get("restaurantes", "activo=eq.true", "id")
        fuentes = sb_get("fuentes_restaurante", "activa=eq.true", "id")
        acciones_pendientes = sb_get("acciones", "estado=eq.pendiente", "id")
        await update.message.reply_text(
            f"Estado ChefPanda\n\n"
            f"Clientes activos: {len(clientes)}\n"
            f"Fuentes activas: {len(fuentes)}\n"
            f"Acciones pendientes: {len(acciones_pendientes)}\n"
            f"Ingresos estimados: {len(clientes)*99} EUR/mes"
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

        n = contar_resenas_restaurante(rid)
        fuentes = get_fuentes_restaurante(rid)
        ultimas = get_ultima_ejecucion_fuente(rid)

        fuentes_txt = ""
        for f in fuentes:
            ultima = next((u for u in ultimas if u.get("fuente_id") == f["id"]), None)
            estado = ultima.get("estado") if ultima else (f.get("ultimo_estado") or "sin ejecuciones")
            fuentes_txt += (
                f"- {f['plataforma']} | activa={f.get('activa')} | modo={f.get('modo_acceso')} | "
                f"estado={estado}\n"
            )

        await update.message.reply_text(
            f"Ficha: {c['nombre']}\n"
            f"Cocina: {c.get('tipo_cocina','?')} | Ciudad: {c.get('ciudad','?')}\n"
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
        c = get_restaurante(rid)
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
                f"  modo: {f.get('modo_acceso')}\n"
                f"  estado: {f.get('ultimo_estado') or '-'}\n"
                f"  error: {truncate(f.get('ultimo_error') or '-', 120)}\n"
                f"  url: {truncate(f.get('url') or '-', 180)}\n\n"
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
        rid = int(ctx.args[0])
        plataforma = safe_text(ctx.args[1]).lower()
        url = safe_text(ctx.args[2])

        if plataforma not in PLATAFORMAS_VALIDAS:
            await update.message.reply_text(f"Plataforma no válida: {plataforma}")
            return

        if not is_valid_url(url):
            await update.message.reply_text("La URL no parece válida. Debe empezar por http:// o https://")
            return

        restaurante = get_restaurante(rid)
        if not restaurante:
            await update.message.reply_text(f"No existe restaurante ID {rid}")
            return

        modo = default_modo_for_plataforma(plataforma)

        sb_upsert("fuentes_restaurante", {
            "restaurante_id": rid,
            "plataforma": plataforma,
            "url": url,
            "activa": True,
            "modo_acceso": modo,
            "updated_at": datetime.utcnow().isoformat()  # por si no corre trigger en upsert
        }, "restaurante_id,plataforma")

        await update.message.reply_text(
            f"Fuente guardada:\n"
            f"Restaurante: {restaurante['nombre']}\n"
            f"Plataforma: {plataforma}\n"
            f"Modo: {modo}"
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
        rid = int(ctx.args[0])
        plataforma = safe_text(ctx.args[1]).lower()

        fuente = get_fuente(rid, plataforma)
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
        rid = int(ctx.args[0])
        plataforma = safe_text(ctx.args[1]).lower()

        fuente = get_fuente(rid, plataforma)
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
        await update.message.reply_text("Uso: /analizar ID [forzar|fecha]")
        return

    try:
        rid = int(ctx.args[0])
        flag = safe_text(ctx.args[1]).lower() if len(ctx.args) > 1 else ""
        forzar = flag in ("forzar", "fecha")

        c = get_restaurante(rid)
        if not c:
            await update.message.reply_text(f"No existe ID {rid}")
            return

        if flag == "fecha":
            sb_update_by_id("restaurantes", rid, {"ultima_analisis": None})
            await update.message.reply_text("Fecha reseteada. Regenerando...")

        ultimo_scrape = c.get("ultima_actualizacion_resenas")
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
            f"Ejecuta: python scrape.py {rid}"
        )
        return

    await update.message.reply_text("Verificando KPIs públicos...")
    kpis_web = contrastar_kpis_web(nombre, ciudad)

    await update.message.reply_text("Generando análisis...")
    texto = analizar_con_claude(nombre, ciudad, cocina, contexto_resenas, kpis_web, hist_txt)
    resultado = parse_claude_json(texto)

    analisis_id = guardar_analisis_y_metricas(rid, resultado, texto, kpis_locales, kpis_web)

    t = resultado.get("tendencia", "estable")
    t_e = {"mejora": "Mejora", "deterioro": "Bajando", "estable": "Estable"}.get(t, t)
    problemas = "\n".join(["  - " + str(p) for p in resultado.get("problemas_top3", [])])
    fortalezas = "\n".join(["  + " + str(f) for f in resultado.get("fortalezas_top3", [])])
    titular = safe_text(resultado.get("titular")).replace("_", " ").replace("*", " ").replace("`", " ")
    accion = safe_text(resultado.get("accion_urgente")).replace("_", " ").replace("*", " ")
    punts = resultado.get("puntuaciones_reales", {})
    punts_txt = "".join(
        f"  {k.replace('_',' ').title()}: {v}\n"
        for k, v in punts.items()
        if v and "Sin datos" not in str(v)
    )

    msg = (
        f"Análisis completado: {nombre}\n\n"
        f"PandaScore: {resultado.get('pandascore','?')}/100  {t_e}\n"
        f"En 30 días: {resultado.get('pandascore_estimado_30dias','?')}/100\n\n"
    )
    if punts_txt:
        msg += "Puntuaciones:\n" + punts_txt + "\n"
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
        storage_path = filename

        try:
            if PDF_BUCKET:
                upload_pdf_to_storage(pdf_bytes, filename)

            sb_insert("pdfs", {
                "restaurante_id": rid,
                "analisis_id": analisis_id,
                "fecha": str(hoy),
                "semana": semana,
                "anio": hoy.year,
                "storage_path": storage_path
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

# =========================================================
# MAIN
# =========================================================

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("nuevo_cliente", nuevo_cliente))
    app.add_handler(CommandHandler("listar", listar))
    app.add_handler(CommandHandler("estado", estado))
    app.add_handler(CommandHandler("ver", cmd_ver))
    app.add_handler(CommandHandler("fuentes", cmd_fuentes))
    app.add_handler(CommandHandler("urls", cmd_urls))
    app.add_handler(CommandHandler("activar_fuente", activar_fuente))
    app.add_handler(CommandHandler("desactivar_fuente", desactivar_fuente))
    app.add_handler(CommandHandler("analizar", analizar))
    app.add_handler(CommandHandler("pausar", pausar))
    app.add_handler(CommandHandler("activar", activar))

    logger.info("ChefPanda Admin Bot arrancado...")
    app.run_polling()

if __name__ == "__main__":
    main()