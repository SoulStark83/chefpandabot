# -*- coding: utf-8 -*-
import tempfile
from datetime import date

from config.settings import PDF_BUCKET
from db.supabase_client import sb_request


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
