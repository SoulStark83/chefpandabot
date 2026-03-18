# -*- coding: utf-8 -*-
import tempfile
from datetime import date

from config.settings import PDF_BUCKET
from db.supabase_client import sb_request


def generar_pdf(restaurante: str, ciudad: str, resultado: dict):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, KeepTogether,
    )
    from reportlab.lib.colors import HexColor

    MARRON   = HexColor('#5C3D2E')
    MARRON_L = HexColor('#F5EDE8')
    ROSA     = HexColor('#E8527A')
    VERDE_L  = HexColor('#E8F5EE')
    ROJO     = HexColor('#C0392B')
    GRIS     = HexColor('#F7F5F3')
    BORDE    = HexColor('#E0D5D0')
    MUTED    = HexColor('#8A7A72')
    NEGRO    = HexColor('#2C2C2C')
    WHITE    = colors.white
    W, H     = A4
    TW       = W - 32 * mm

    def st(name, **kw):
        base = dict(fontName='Helvetica', fontSize=9, leading=14,
                    textColor=NEGRO, spaceAfter=3)
        base.update(kw)
        return ParagraphStyle(name, **base)

    S_BODY  = st('body', leading=15, alignment=TA_JUSTIFY, spaceAfter=5)
    S_QUOTE = st('quote', fontName='Helvetica-Oblique', fontSize=8.5, leading=13,
                 textColor=HexColor('#1A4A2A'), backColor=VERDE_L,
                 borderPadding=(5, 8, 5, 8))
    S_CTR   = st('ctr', alignment=TA_CENTER)

    def section_header(num: str, title: str):
        return [
            Spacer(1, 4 * mm),
            Table(
                [[Paragraph(
                    f'<font color="white"><b>{num}  {title}</b></font>',
                    st('sh', fontName='Helvetica-Bold', fontSize=10,
                       leading=14, textColor=WHITE)
                )]],
                colWidths=[TW],
                style=TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), MARRON),
                    ('TOPPADDING', (0, 0), (-1, -1), 5),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                    ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ])
            ),
            Spacer(1, 3 * mm),
        ]

    def bullet_list(items, color_hex='#2D7A4F', prefix='+'):
        elems = []
        for item in items:
            elems.append(
                Paragraph(
                    f'<font color="{color_hex}"><b>{prefix}</b></font>  {item}',
                    st('li', fontSize=9, leading=14, spaceAfter=4)
                )
            )
        return elems

    # ── Construcción del documento ────────────────────────────
    S = []

    # Portada / cabecera
    S += [Spacer(1, 5 * mm)]
    S += [Paragraph('ChefPanda', st('brand', fontName='Helvetica-Bold',
                                    fontSize=22, textColor=MARRON))]
    S += [Spacer(1, 3 * mm)]
    S += [Paragraph(restaurante, st('title', fontName='Helvetica-Bold',
                                    fontSize=24, leading=28, textColor=MARRON))]
    S += [Paragraph(
        f'{ciudad}  |  Informe de reputación  |  {date.today().strftime("%B %Y")}',
        st('sub', fontSize=10, textColor=MUTED)
    )]
    S += [Spacer(1, 3 * mm)]
    S += [HRFlowable(width='100%', thickness=2, color=ROSA, spaceAfter=3 * mm)]

    # Titular + PandaScore
    titular = resultado.get('titular', 'Informe de reputación')
    S += [Table(
        [[Paragraph(titular, st('tit', fontName='Helvetica-Bold', fontSize=10,
                                leading=15, textColor=MARRON,
                                backColor=MARRON_L, borderPadding=(8, 10, 8, 10)))]],
        colWidths=[TW],
        style=TableStyle([('BOX', (0, 0), (-1, -1), 1.5, ROSA)])
    )]
    S += [Spacer(1, 4 * mm)]

    ps   = resultado.get('pandascore', 50)
    tend = resultado.get('tendencia', 'estable')
    tend_sym = {'mejora': '↑ Mejora', 'deterioro': '↓ Bajando', 'estable': '→ Estable'}.get(tend, tend)
    cw2 = TW / 2
    S += [Table(
        [
            [Paragraph('PandaScore', st('kl', fontSize=7, textColor=MUTED, alignment=TA_CENTER)),
             Paragraph('Tendencia',  st('kl', fontSize=7, textColor=MUTED, alignment=TA_CENTER))],
            [Paragraph(f'<font size="26" color="#E8527A"><b>{ps}</b></font>', S_CTR),
             Paragraph(f'<font size="14" color="#E8890C"><b>{tend_sym}</b></font>', S_CTR)],
        ],
        colWidths=[cw2, cw2],
        style=TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('BACKGROUND', (0, 0), (-1, 0), GRIS),
            ('BOX', (0, 0), (-1, -1), .5, BORDE),
            ('LINEAFTER', (0, 0), (0, -1), .5, BORDE),
        ])
    )]
    S += [Spacer(1, 5 * mm)]

    # 01 – Resumen general
    S += section_header('01', 'RESUMEN GENERAL DE LA EXPERIENCIA')
    S += [Paragraph(resultado.get('resumen_general', ''), S_BODY)]

    # 02 – Lo que valoran
    S += section_header('02', 'LO QUE MÁS VALORAN LOS CLIENTES')
    S += bullet_list(resultado.get('lo_que_valoran', []), color_hex='#2D7A4F', prefix='+')

    # 03 – Problema principal
    S += section_header('03', 'EL PRINCIPAL PROBLEMA DETECTADO')
    problema = resultado.get('problema_principal', '')
    if problema:
        S += [Table(
            [[Paragraph(problema, st('prob', fontSize=9, leading=14,
                                     textColor=ROJO, alignment=TA_JUSTIFY))]],
            colWidths=[TW],
            style=TableStyle([
                ('BOX', (0, 0), (-1, -1), 1, ROJO),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ])
        )]

    # 04 – Otros problemas
    otros = resultado.get('otros_problemas', [])
    if otros:
        S += section_header('04', 'COSAS QUE PUEDEN AFECTAR LA EXPERIENCIA')
        S += bullet_list(otros, color_hex='#E8890C', prefix='·')

    # 05 – Patrón curioso
    patron = resultado.get('patron_curioso', '')
    if patron:
        S += section_header('05', 'ALGO QUE QUIZÁ NO HABÍAS VISTO')
        S += [Paragraph(f'"{patron}"', S_QUOTE)]

    # 06 – Oportunidades
    S += section_header('06', 'OPORTUNIDADES DE MEJORA SENCILLAS')
    S += bullet_list(resultado.get('oportunidades', []), color_hex='#2D7A4F', prefix='→')

    # 07 – Conclusión
    S += section_header('07', 'CONCLUSIÓN')
    S += [Paragraph(resultado.get('conclusion', ''), S_BODY)]

    # Acción para hoy
    accion = resultado.get('accion_hoy', '')
    if accion:
        S += [Spacer(1, 4 * mm)]
        S += [KeepTogether([
            Table(
                [[Paragraph('ACCIÓN PARA HOY', st('at', fontName='Helvetica-Bold',
                                                   fontSize=9, textColor=WHITE))]],
                colWidths=[TW],
                style=TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), ROSA),
                    ('TOPPADDING', (0, 0), (-1, -1), 5),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                    ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ])
            ),
            Table(
                [[Paragraph(accion, st('av', fontSize=10, leading=15,
                                       fontName='Helvetica-Bold', textColor=MARRON))]],
                colWidths=[TW],
                style=TableStyle([
                    ('BOX', (0, 0), (-1, -1), 1.5, ROSA),
                    ('TOPPADDING', (0, 0), (-1, -1), 8),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                    ('LEFTPADDING', (0, 0), (-1, -1), 10),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                ])
            ),
        ])]

    # ── Render ────────────────────────────────────────────────
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
        "POST",
        path,
        body=pdf_bytes,
        storage=True,
        extra_headers={
            "Content-Type": "application/pdf",
            "x-upsert": "true"
        }
    )
