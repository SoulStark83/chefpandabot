# -*- coding: utf-8 -*-
import tempfile
from datetime import date

from config.settings import PDF_BUCKET
from db.supabase_client import sb_request

DIM_LABELS = {
    "food_quality":     "Calidad de la comida",
    "service":          "Servicio",
    "waiting_time":     "Tiempo de espera",
    "ambience":         "Ambiente",
    "price_perception": "Relación calidad-precio",
    "cleanliness":      "Limpieza",
}


def generar_pdf(restaurante: str, ciudad: str, resultado: dict, kpis: dict = None):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, KeepTogether,
    )
    from reportlab.lib.colors import HexColor

    if kpis is None:
        kpis = {}

    # ── Paleta ───────────────────────────────────────────────
    MARRON   = HexColor('#5C3D2E')
    MARRON_L = HexColor('#F5EDE8')
    ROSA     = HexColor('#E8527A')
    VERDE    = HexColor('#2D7A4F')
    VERDE_L  = HexColor('#E8F5EE')
    ROJO     = HexColor('#C0392B')
    ROJO_L   = HexColor('#FDECEA')
    GRIS     = HexColor('#F7F5F3')
    GRIS_M   = HexColor('#E8E4E0')
    BORDE    = HexColor('#E0D5D0')
    MUTED    = HexColor('#8A7A72')
    NEGRO    = HexColor('#2C2C2C')
    WHITE    = colors.white
    W, H     = A4
    TW       = W - 32 * mm

    def st(name, **kw):
        base = dict(fontName='Helvetica', fontSize=9, leading=14,
                    textColor=NEGRO, spaceAfter=2)
        base.update(kw)
        return ParagraphStyle(name, **base)

    S_BODY  = st('body', leading=15, alignment=TA_JUSTIFY, spaceAfter=4)
    S_CTR   = st('ctr', alignment=TA_CENTER)
    S_MUTED = st('muted', fontSize=7.5, leading=11, textColor=MUTED)
    def sec_hdr(num, title):
        return [
            Spacer(1, 4 * mm),
            Table([[Paragraph(
                f'<font color="white"><b>{num}  {title}</b></font>',
                st('sh', fontName='Helvetica-Bold', fontSize=10,
                   leading=14, textColor=WHITE)
            )]], colWidths=[TW],
                style=TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), MARRON),
                    ('TOPPADDING', (0, 0), (-1, -1), 5),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                    ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ])),
            Spacer(1, 3 * mm),
        ]

    def bullet_list(items, color_hex='#2D7A4F', prefix='+'):
        elems = []
        for item in items:
            elems.append(Paragraph(
                f'<font color="{color_hex}"><b>{prefix}</b></font>  {item}',
                st('li', fontSize=9, leading=14, spaceAfter=4)
            ))
        return elems

    def score_color(v):
        if v >= 4.0:
            return '#2D7A4F'
        if v >= 3.0:
            return '#E8890C'
        return '#C0392B'

    def bar_row(label, val, max_val=5.0):
        try:
            val = float(val)
        except Exception:
            val = 3.0
        bw = TW - 52 * mm
        fill = bw * (val / max_val)
        col = HexColor(score_color(val))
        return Table([[
            Paragraph(label, st('bl', fontSize=8)),
            Table([['']], colWidths=[fill],
                  style=TableStyle([('BACKGROUND', (0, 0), (-1, -1), col),
                                    ('TOPPADDING', (0, 0), (-1, -1), 4),
                                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4)])),
            Table([['']], colWidths=[bw - fill],
                  style=TableStyle([('BACKGROUND', (0, 0), (-1, -1), GRIS_M),
                                    ('TOPPADDING', (0, 0), (-1, -1), 4),
                                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4)])),
            Paragraph(f'<b><font color="{score_color(val)}">{val:.1f}</font></b>',
                      st('bv', fontSize=8.5, alignment=TA_RIGHT)),
        ]], colWidths=[38 * mm, fill, bw - fill, 14 * mm],
            style=TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 1),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
            ]))

    # ── Datos de kpis ────────────────────────────────────────
    ps         = resultado.get('pandascore') or kpis.get('pandascore', 50)
    tend       = resultado.get('tendencia', 'estable')
    tend_sym   = {'mejora': '↑ Mejora', 'deterioro': '↓ Bajando', 'estable': '→ Estable'}.get(tend, tend)
    tend_color = {'mejora': '#2D7A4F', 'deterioro': '#C0392B', 'estable': '#E8890C'}.get(tend, '#E8890C')
    nota_media = kpis.get('media')
    total_res  = kpis.get('total', 0)
    sents      = kpis.get('sentimientos', {})
    criticas   = kpis.get('criticas', 0)
    sin_resp   = kpis.get('sin_responder', 0)
    pct_pos    = round(sents.get('positivo', 0) / total_res * 100) if total_res else 0
    dims       = kpis.get('dimensiones', {})
    platos     = kpis.get('platos', [])

    ps_color = '#2D7A4F' if ps >= 70 else ('#E8890C' if ps >= 45 else '#C0392B')

    S = []

    # ── PORTADA ──────────────────────────────────────────────
    S += [Spacer(1, 5 * mm)]
    S += [Paragraph('ChefPanda', st('brand', fontName='Helvetica-Bold',
                                    fontSize=22, textColor=MARRON))]
    S += [Spacer(1, 2 * mm)]
    S += [Paragraph(restaurante, st('title', fontName='Helvetica-Bold',
                                    fontSize=28, leading=32, textColor=MARRON))]
    S += [Paragraph(
        f'{ciudad}  |  Informe de reputación  |  {date.today().strftime("%B %Y")}',
        st('sub', fontSize=10, textColor=MUTED)
    )]
    S += [Spacer(1, 3 * mm)]
    S += [HRFlowable(width='100%', thickness=2, color=ROSA, spaceAfter=3 * mm)]

    # Titular
    titular = resultado.get('titular', 'Informe de reputación')
    S += [Table([[Paragraph(titular, st('tit', fontName='Helvetica-Bold', fontSize=10,
                                        leading=15, textColor=MARRON,
                                        backColor=MARRON_L, borderPadding=(8, 10, 8, 10)))]],
                colWidths=[TW],
                style=TableStyle([('BOX', (0, 0), (-1, -1), 1.5, ROSA)]))]
    S += [Spacer(1, 4 * mm)]

    # ── KPI BLOCK ────────────────────────────────────────────
    nota_str = f'{nota_media:.1f}/5' if nota_media else '—'
    cw = TW / 5

    kpi_labels = [
        Paragraph('PandaScore', S_MUTED),
        Paragraph('Tendencia',  S_MUTED),
        Paragraph('Nota media', S_MUTED),
        Paragraph('Reseñas',    S_MUTED),
        Paragraph('% Positivas', S_MUTED),
    ]
    kpi_values = [
        Paragraph(f'<font size="24" color="{ps_color}"><b>{ps}</b></font>', S_CTR),
        Paragraph(f'<font size="13" color="{tend_color}"><b>{tend_sym}</b></font>', S_CTR),
        Paragraph(f'<font size="18" color="#5C3D2E"><b>{nota_str}</b></font>', S_CTR),
        Paragraph(f'<font size="18" color="#5C3D2E"><b>{total_res}</b></font>', S_CTR),
        Paragraph(f'<font size="18" color="#2D7A4F"><b>{pct_pos}%</b></font>', S_CTR),
    ]
    S += [Table([kpi_labels, kpi_values], colWidths=[cw] * 5,
                style=TableStyle([
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('TOPPADDING', (0, 0), (-1, -1), 5),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                    ('BACKGROUND', (0, 0), (-1, 0), GRIS),
                    ('BOX', (0, 0), (-1, -1), .5, BORDE),
                    ('LINEAFTER', (0, 0), (-1, -1), .3, BORDE),
                ]))]
    S += [Spacer(1, 3 * mm)]

    # ── BARRA DE SENTIMIENTOS ────────────────────────────────
    if total_res > 0:
        n_pos = sents.get('positivo', 0)
        n_neu = sents.get('neutro', 0)
        n_neg = sents.get('negativo', 0)
        pct_neu = round(n_neu / total_res * 100)
        pct_neg = round(n_neg / total_res * 100)

        # Calcular anchos exactos que sumen TW
        w_pos = round(TW * n_pos / total_res, 2)
        w_neu = round(TW * n_neu / total_res, 2)
        w_neg = round(TW - w_pos - w_neu, 2)

        cells, widths, bg_colors = [], [], []
        if w_pos > 2:
            cells.append(Paragraph(
                f'<font color="white">{pct_pos}% positivas</font>',
                st('sb', fontSize=7.5, alignment=TA_CENTER)))
            widths.append(w_pos)
            bg_colors.append(VERDE)
        if w_neu > 2:
            cells.append(Paragraph(
                f'{pct_neu}% neutras',
                st('sb2', fontSize=7.5, alignment=TA_CENTER, textColor=MUTED)))
            widths.append(w_neu)
            bg_colors.append(GRIS_M)
        if w_neg > 2:
            cells.append(Paragraph(
                f'<font color="white">{pct_neg}% negativas</font>',
                st('sb3', fontSize=7.5, alignment=TA_CENTER)))
            widths.append(w_neg)
            bg_colors.append(ROJO)

        if cells:
            # Ajustar último ancho para cubrir exactamente TW
            widths[-1] = TW - sum(widths[:-1])
            ts = TableStyle([
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ('LEFTPADDING', (0, 0), (-1, -1), 2),
                ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ])
            for idx, bg in enumerate(bg_colors):
                ts.add('BACKGROUND', (idx, 0), (idx, 0), bg)
            S += [Table([cells], colWidths=widths, style=ts)]

        # Alertas rápidas
        alertas = []
        if criticas > 0:
            alertas.append(Paragraph(
                f'<font color="#C0392B"><b>! {criticas} resenas criticas (2 estrellas o menos)</b></font>',
                st('al', fontSize=8, spaceAfter=2)
            ))
        if sin_resp > 0:
            alertas.append(Paragraph(
                f'<font color="#E8890C"><b>{sin_resp} resenas requieren respuesta del propietario</b></font>',
                st('al2', fontSize=8, spaceAfter=2)
            ))
        if alertas:
            S += [Spacer(1, 2 * mm)]
            S += alertas

    S += [Spacer(1, 3 * mm)]

    # ── DIMENSIONES ──────────────────────────────────────────
    if dims:
        S += sec_hdr('◆', 'ANÁLISIS POR DIMENSIÓN')
        ordered = ['food_quality', 'service', 'waiting_time',
                   'ambience', 'price_perception', 'cleanliness']
        for dim in ordered:
            if dim in dims:
                S += [bar_row(DIM_LABELS[dim], dims[dim]), Spacer(1, 1 * mm)]

    # ── TOP PLATOS ───────────────────────────────────────────
    if platos:
        S += sec_hdr('◆', 'PLATOS MÁS MENCIONADOS')
        rows = [[
            Paragraph('<b>Plato</b>', st('ph', fontSize=8)),
            Paragraph('<b>Menciones</b>', st('ph', fontSize=8, alignment=TA_CENTER)),
            Paragraph('<b>% Positiva</b>', st('ph', fontSize=8, alignment=TA_CENTER)),
            Paragraph('<b>% Negativa</b>', st('ph', fontSize=8, alignment=TA_CENTER)),
        ]]
        for p in platos[:8]:
            pct_p = p.get('pct_pos', 0)
            pct_n = p.get('pct_neg', 0)
            col_p = '#2D7A4F' if pct_p >= 70 else ('#E8890C' if pct_p >= 40 else '#C0392B')
            rows.append([
                Paragraph(p['nombre'], st('pr', fontSize=8)),
                Paragraph(str(p['menciones']), st('prc', fontSize=8, alignment=TA_CENTER)),
                Paragraph(f'<font color="{col_p}"><b>{pct_p}%</b></font>',
                          st('prp', fontSize=8, alignment=TA_CENTER)),
                Paragraph(f'<font color="#C0392B">{pct_n}%</font>',
                          st('prn', fontSize=8, alignment=TA_CENTER)),
            ])
        S += [Table(rows,
                    colWidths=[TW * 0.45, TW * 0.18, TW * 0.18, TW * 0.19],
                    style=TableStyle([
                        ('BACKGROUND', (0, 0), (-1, 0), GRIS),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0, 0), (-1, -1), 8),
                        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, GRIS]),
                        ('GRID', (0, 0), (-1, -1), 0.3, BORDE),
                        ('TOPPADDING', (0, 0), (-1, -1), 4),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                        ('LEFTPADDING', (0, 0), (-1, -1), 6),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ]))]

    # ── 7 SECCIONES DEL CONSULTOR ────────────────────────────
    S += sec_hdr('01', 'RESUMEN GENERAL DE LA EXPERIENCIA')
    S += [Paragraph(resultado.get('resumen_general', ''), S_BODY)]

    S += sec_hdr('02', 'LO QUE MÁS VALORAN LOS CLIENTES')
    S += bullet_list(resultado.get('lo_que_valoran', []), '#2D7A4F', '+')

    S += sec_hdr('03', 'EL PRINCIPAL PROBLEMA DETECTADO')
    problema = resultado.get('problema_principal', '')
    if problema:
        S += [Table([[Paragraph(problema, st('prob', fontSize=9, leading=14,
                                              textColor=ROJO, alignment=TA_JUSTIFY,
                                              backColor=ROJO_L,
                                              borderPadding=(6, 8, 6, 8)))]],
                    colWidths=[TW],
                    style=TableStyle([
                        ('BOX', (0, 0), (-1, -1), 1, ROJO),
                        ('LEFTPADDING', (0, 0), (-1, -1), 0),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                        ('TOPPADDING', (0, 0), (-1, -1), 0),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
                    ]))]

    otros = resultado.get('otros_problemas', [])
    if otros:
        S += sec_hdr('04', 'COSAS QUE PUEDEN AFECTAR LA EXPERIENCIA')
        S += bullet_list(otros, '#E8890C', '·')

    patron = resultado.get('patron_curioso', '')
    if patron:
        S += sec_hdr('05', 'ALGO QUE QUIZÁ NO HABÍAS VISTO')
        S += [Table([[Paragraph(f'"{patron}"', st('qt', fontName='Helvetica-Oblique',
                                                   fontSize=9, leading=14,
                                                   textColor=HexColor('#1A4A2A'),
                                                   alignment=TA_JUSTIFY))]],
                    colWidths=[TW],
                    style=TableStyle([
                        ('BACKGROUND', (0, 0), (-1, -1), VERDE_L),
                        ('BOX', (0, 0), (-1, -1), 1, VERDE),
                        ('LEFTPADDING', (0, 0), (-1, -1), 10),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                        ('TOPPADDING', (0, 0), (-1, -1), 7),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
                    ]))]

    S += sec_hdr('06', 'OPORTUNIDADES DE MEJORA SENCILLAS')
    S += bullet_list(resultado.get('oportunidades', []), '#2D7A4F', '→')

    S += sec_hdr('07', 'CONCLUSIÓN')
    S += [Paragraph(resultado.get('conclusion', ''), S_BODY)]

    # ── ACCIÓN PARA HOY ──────────────────────────────────────
    accion = resultado.get('accion_hoy', '')
    if accion:
        S += [Spacer(1, 5 * mm)]
        S += [KeepTogether([
            Table([[Paragraph(
                '<font color="white"><b>  ACCIÓN PARA HOY</b></font>',
                st('at', fontName='Helvetica-Bold', fontSize=10, textColor=WHITE)
            )]], colWidths=[TW],
                style=TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), ROSA),
                    ('TOPPADDING', (0, 0), (-1, -1), 6),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                    ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ])),
            Table([[Paragraph(accion, st('av', fontName='Helvetica-Bold',
                                         fontSize=10, leading=15,
                                         textColor=MARRON, alignment=TA_JUSTIFY))]],
                  colWidths=[TW],
                  style=TableStyle([
                      ('BOX', (0, 0), (-1, -1), 1.5, ROSA),
                      ('BACKGROUND', (0, 0), (-1, -1), MARRON_L),
                      ('TOPPADDING', (0, 0), (-1, -1), 10),
                      ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
                      ('LEFTPADDING', (0, 0), (-1, -1), 10),
                      ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                  ])),
        ])]

    # ── RENDER ───────────────────────────────────────────────
    tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
    doc = SimpleDocTemplate(
        tmp.name, pagesize=A4,
        leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=20 * mm, bottomMargin=16 * mm
    )

    def on_page(c, d):
        c.setFillColor(MARRON)
        c.rect(0, H - 16 * mm, W, 16 * mm, fill=1, stroke=0)
        c.setFont('Helvetica-Bold', 11)
        c.setFillColor(WHITE)
        c.drawString(6 * mm, H - 10 * mm, 'ChefPanda')
        c.setFont('Helvetica', 7)
        c.drawRightString(W - 6 * mm, H - 10 * mm, f'{restaurante} – {ciudad}')
        c.setFont('Helvetica', 7)
        c.setFillColor(MUTED)
        c.drawString(6 * mm, 4 * mm, 'ChefPanda – Gestión de reputación')
        c.drawRightString(W - 6 * mm, 4 * mm, f'Página {d.page}')

    doc.build(S, onFirstPage=on_page, onLaterPages=on_page)
    return tmp.name


def upload_pdf_to_storage(pdf_bytes: bytes, filename: str):
    if not PDF_BUCKET:
        return None
    path = f"object/{PDF_BUCKET}/{filename}"
    return sb_request(
        "POST", path, body=pdf_bytes, storage=True,
        extra_headers={"Content-Type": "application/pdf", "x-upsert": "true"}
    )
