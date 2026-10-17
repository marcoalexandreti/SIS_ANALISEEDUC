"""Relatórios PDF individuais por escola a partir do banco SQLite já normalizado.

Toda medida é diagnóstico da base importada: não calcula nem prevê o IDEB oficial.
A escrita é somente em arquivos PDF; não altera os registros do banco.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
import re
import unicodedata

from core import safe_sqlite, report_rows, norm, idade_ano_corrente
from infra_transporte import school_infra, infrastructure_groups, infrastructure_checks, school_transport

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A3, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph,
                                    PageBreak, Spacer, Table, TableStyle, KeepTogether,
                                    Flowable)
except ImportError as err:
    raise RuntimeError('Para gerar os PDFs, instale: python -m pip install reportlab>=4.0') from err

W, H = landscape(A3)
NAVY = colors.HexColor('#17375B')
BLUE = colors.HexColor('#244C74')
STEEL = colors.HexColor('#EAF0F6')
SOFT = colors.HexColor('#F6F8FA')
ORANGE = colors.HexColor('#B9691B')
RED = colors.HexColor('#A73940')
GREEN = colors.HexColor('#237A5A')
INK = colors.HexColor('#243446')
GRAY = colors.HexColor('#667688')
BORDER = colors.HexColor('#D7E0EA')

ASSET_DIR = Path(__file__).resolve().parent / 'assets'
LOGO_MUNICIPIO = ASSET_DIR / 'brasao_municipio.png'
LOGO_SEMEC = ASSET_DIR / 'logo_semec.png'


def _install_fonts():
    paths = [
        ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
         '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'),
        ('C:/Windows/Fonts/arial.ttf', 'C:/Windows/Fonts/arialbd.ttf'),
    ]
    for reg, bold in paths:
        if Path(reg).exists() and Path(bold).exists():
            pdfmetrics.registerFont(TTFont('EscolaRegular', reg))
            pdfmetrics.registerFont(TTFont('EscolaBold', bold))
            pdfmetrics.registerFontFamily('EscolaRegular', normal='EscolaRegular', bold='EscolaBold')
            return 'EscolaRegular', 'EscolaBold'
    return 'Helvetica', 'Helvetica-Bold'

FONT, FONT_B = _install_fonts()
ST = {
    'title': ParagraphStyle('TitleEsc', fontName=FONT_B, fontSize=17, leading=22,
                            textColor=NAVY, spaceAfter=8),
    'subtitle': ParagraphStyle('SubEsc', fontName=FONT_B, fontSize=12, leading=16,
                               textColor=NAVY, spaceBefore=12, spaceAfter=7),
    'text': ParagraphStyle('TxtEsc', fontName=FONT, fontSize=9.2, leading=13,
                           textColor=INK, spaceAfter=7),
    'small': ParagraphStyle('SmlEsc', fontName=FONT, fontSize=8.3, leading=11,
                            textColor=INK, spaceAfter=6),
    'cell': ParagraphStyle('CellEsc', fontName=FONT, fontSize=8.1, leading=10.7,
                           textColor=INK),
    'cell_bold': ParagraphStyle('CellBoldEsc', fontName=FONT_B, fontSize=8, leading=10.5,
                                textColor=INK),
    'head': ParagraphStyle('HeadEsc', fontName=FONT_B, fontSize=7.8, leading=10,
                           textColor=colors.white, alignment=TA_LEFT),
    'muted': ParagraphStyle('MutedEsc', fontName=FONT, fontSize=8.1, leading=11,
                            textColor=GRAY),
    'footer': ParagraphStyle('FooterEsc', fontName=FONT, fontSize=7.6, leading=10,
                             textColor=GRAY, alignment=TA_CENTER),
}


def paragraph(value, style='cell'):
    return Paragraph(escape('—' if value is None or str(value).strip() == '' else str(value)).replace('\n','<br/>'), ST[style])


def rich_paragraph(value, style='text'):
    raw = '—' if value is None or str(value).strip() == '' else str(value)
    return Paragraph(raw.replace('\n', '<br/>'), ST[style])


def short_stage(raw):
    s=str(raw or '').strip()
    m=re.search(r'fundamental de 9 anos\s*-\s*(\d+)\s*[º°o]?\s*ano',norm(s))
    if m:return f'EF {m.group(1)}º ano'
    n=norm(s)
    if 'pre-escola' in n:return 'EI pré-escola'
    if 'creche' in n:return 'EI creche'
    if 'infantil - unificada' in n:return 'EI unificada'
    if 'eja' in n and 'multietapa' in n:return 'EJA multietapa'
    if 'eja' in n and 'anos finais' in n:return 'EJA anos finais'
    if 'eja' in n and 'anos iniciais' in n:return 'EJA anos iniciais'
    if 'eja' in n:return 'EJA'
    if 'correcao de fluxo' in n:return 'EF correção de fluxo'
    if 'fundamental' in n and 'multi' in n:return 'EF multisseriada'
    if 'multietapa' in n:return 'EI/EF multietapa'
    return s


def grade(raw):
    m=re.search(r'ensino fundamental de 9 anos\s*-\s*(\d+)\s*[º°o]?\s*ano', norm(raw))
    if not m:return None
    v=int(m.group(1))
    return v if 1 <= v <= 9 else None


def status_category(result):
    n=norm(result)
    if 'deixou de frequentar' in n:return 'DEIXOU_DE_FREQUENTAR'
    if 'reprov' in n:return 'REPROVADO'
    if 'transfer' in n:return 'TRANSFERIDO'
    if 'aprov' in n:return 'APROVADO'
    if 'sem resultado' in n:return 'SEM_RESULTADO'
    return 'OUTRO'


def origin_schools(conn):
    return [(x['inep'],x['nome']) for x in conn.execute('''
        SELECT DISTINCT e.inep,e.nome FROM escolas e
        JOIN sources s ON s.school_inep=e.inep
        WHERE s.kind='RESULTADO' AND s.school_year=2025
        ORDER BY e.nome,e.inep''')]


def extra_without_result(conn,inep,report_data):
    """Inclui quem consta na matrícula inicial de 2025 sem situação na unidade.
    Usa report_data para localizar qualquer ID nas 21 escolas de 2026.
    """
    already={r[1] for r in report_data if r[7]==inep}
    school=conn.execute('SELECT nome FROM escolas WHERE inep=?',(inep,)).fetchone()['nome']
    cur=conn.execute('''SELECT m.student_id,a.nome_referencia,a.nascimento_referencia,
       et.descricao etapa,t.nome turma,
       (SELECT o.cor_raca FROM aluno_observacoes o WHERE o.student_id=m.student_id AND o.ano=2025
        AND o.escola_inep=m.inep ORDER BY o.id LIMIT 1) cor_raca,
       (SELECT o.sexo FROM aluno_observacoes o WHERE o.student_id=m.student_id AND o.ano=2025
        AND o.escola_inep=m.inep ORDER BY o.id LIMIT 1) sexo
       FROM matriculas m JOIN alunos a ON a.id_unico=m.student_id
       LEFT JOIN etapas et ON et.id=m.etapa_id LEFT JOIN turmas t ON t.id=m.turma_id
       WHERE m.ano=2025 AND m.inep=? ORDER BY a.nome_referencia,m.id''',(inep,))
    # records indexed once for student-id, reading whole network of matriculas2026
    by_id=defaultdict(list)
    for x in conn.execute('''SELECT m.student_id,m.inep,e.nome AS escola,
        et.descricao AS etapa,t.nome AS turma,
        (SELECT o.cor_raca FROM aluno_observacoes o WHERE o.student_id=m.student_id AND o.ano=2026
         AND o.escola_inep=m.inep ORDER BY o.id LIMIT 1) AS cor_raca
        FROM matriculas m JOIN escolas e ON e.inep=m.inep
        LEFT JOIN etapas et ON et.id=m.etapa_id LEFT JOIN turmas t ON t.id=m.turma_id
        WHERE m.ano=2026'''):
        by_id[x['student_id']].append(x)
    added=[];seen=set()
    for x in cur:
        stu=x['student_id']
        if stu in already or stu in seen:continue
        seen.add(stu)
        matches=by_id[stu]
        dests={a['inep'] for a in matches}
        loc=('NÃO LOCALIZADO NA REDE IMPORTADA' if not matches else
             'MÚLTIPLAS ESCOLAS EM 2026' if len(dests)>1 else
             'MESMA ESCOLA' if inep in dests else 'OUTRA ESCOLA DA REDE')
        race=next((a['cor_raca'] for a in matches if a['cor_raca']),None)
        unique=lambda name: ' | '.join(sorted({str(a[name]) for a in matches if a[name]}))
        added.append(['PENDÊNCIA',stu,x['nome_referencia'],x['nascimento_referencia'],x['sexo'],x['cor_raca'],race,
             inep,school,x['turma'],x['etapa'],'Sem resultado informado na base',loc,
             unique('inep'),unique('escola'),unique('turma'),unique('etapa'),len(matches),
             'Consta na matrícula inicial de 2025, mas não há situação final para esta escola; conferir.',
             idade_ano_corrente(x['nascimento_referencia'])])
    return added


def _dedup_by_id(rows):
    return {r[1] for r in rows if r[1]}


def school_analysis(conn, allrows, inep, school):
    base=[list(r) for r in allrows if r[7]==inep]
    extras=extra_without_result(conn,inep,base)
    roster=base+extras
    priority={'CRÍTICA':0,'ALTA':1,'PENDÊNCIA':2,'VERIFICAR DESTINO':3,
              'VERIFICAR':4,'RETORNOU':5,'ACOMPANHAR':6,'LOCALIZADO':7,'ROTINA':8}
    roster.sort(key=lambda r:(priority.get(r[0],9),norm(r[2]),r[1],norm(r[11])))

    categories={c:_dedup_by_id([r for r in base if status_category(r[11])==c])
                 for c in ['APROVADO','REPROVADO','DEIXOU_DE_FREQUENTAR','TRANSFERIDO','OUTRO']}
    locations={c:_dedup_by_id([r for r in roster if r[12]==c]) for c in
               ['MESMA ESCOLA','OUTRA ESCOLA DA REDE','MÚLTIPLAS ESCOLAS EM 2026','NÃO LOCALIZADO NA REDE IMPORTADA']}
    unresolved=_dedup_by_id([r for r in base if status_category(r[11]) in
                 ('REPROVADO','DEIXOU_DE_FREQUENTAR') and r[12]=='NÃO LOCALIZADO NA REDE IMPORTADA'])
    critical=_dedup_by_id([r for r in base if status_category(r[11])=='DEIXOU_DE_FREQUENTAR' and
                           r[12]=='NÃO LOCALIZADO NA REDE IMPORTADA'])
    returned_dropout=_dedup_by_id([r for r in base if status_category(r[11])=='DEIXOU_DE_FREQUENTAR' and
                                  r[12]!='NÃO LOCALIZADO NA REDE IMPORTADA'])
    incoming_ids=set()
    outside=conn.execute('''SELECT DISTINCT m.student_id FROM matriculas m
           WHERE m.inep=? AND m.ano=2026 AND EXISTS(
             SELECT 1 FROM resultados r WHERE r.student_id=m.student_id
              AND r.ano=2025 AND r.inep<>?)
          ''',(inep,inep))
    incoming_ids={x[0] for x in outside}
    summary={
        'base':len(base),'roster':len(roster),'students':len(_dedup_by_id(roster)),
        'enrollment25':conn.execute('SELECT COUNT(DISTINCT student_id) FROM matriculas WHERE inep=? AND ano=2025',(inep,)).fetchone()[0],
        'enrollment26':conn.execute('SELECT COUNT(DISTINCT student_id) FROM matriculas WHERE inep=? AND ano=2026',(inep,)).fetchone()[0],
        'without_result':len(extras),'cats':categories,'locs':locations,
        'unresolved':len(unresolved),'critical':len(critical),'returned_dropout':len(returned_dropout),
        'incoming':len(incoming_ids),'duplicates':len(roster)-len(_dedup_by_id(roster)),
    }
    # Recorte diagnóstico, restrito a séries regulares 1º-9º ano e resultados classificáveis.
    # Alunos com mais de uma situação/linha na escola e grupo são excluídos da taxa.
    stage_groups={'EF anos iniciais (1º–5º)':defaultdict(list),
                  'EF anos finais (6º–9º)':defaultdict(list)}
    for row in base:
        g=grade(row[10]);cat=status_category(row[11])
        if g is None:continue
        target='EF anos iniciais (1º–5º)' if g<=5 else 'EF anos finais (6º–9º)'
        stage_groups[target][row[1]].append(cat)
    calc={}
    for target, students in stage_groups.items():
        usable=Counter(one[0] for one in students.values()
                       if len(one)==1 and one[0] in ('APROVADO','REPROVADO','DEIXOU_DE_FREQUENTAR'))
        denominator=sum(usable.values())
        calc[target]={
            'n':denominator,'excluidos':len(students)-denominator,
            'aprovacao':100*usable['APROVADO']/denominator if denominator else None,
            'reprovacao':100*usable['REPROVADO']/denominator if denominator else None,
            'desfrequencia':100*usable['DEIXOU_DE_FREQUENTAR']/denominator if denominator else None,
            'numeradores':usable,
        }
    summary['rendimento']=calc
    return roster,summary


def loc_short(raw):
    return {'MESMA ESCOLA':'Mesma escola','OUTRA ESCOLA DA REDE':'Outra escola da rede',
        'MÚLTIPLAS ESCOLAS EM 2026':'Múltiplas escolas',
        'NÃO LOCALIZADO NA REDE IMPORTADA':'Não localizado'}.get(raw,raw)


class SchoolDocument(BaseDocTemplate):
    def __init__(self,filename,inep,school,**kwargs):
        super().__init__(str(filename),pagesize=(W,H),leftMargin=28,rightMargin=28,
                         topMargin=98,bottomMargin=44,**kwargs)
        self.inep=inep;self.school=school
        frame=Frame(self.leftMargin,self.bottomMargin,self.width,self.height,
                    leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)
        self.addPageTemplates(PageTemplate(id='normal',frames=[frame],onPage=self.decorate))
    def decorate(self,can,doc):
        can.saveState()
        can.setFillColor(colors.white)
        can.rect(0, H-100, W, 100, stroke=0, fill=1)
        can.setFillColor(NAVY)
        can.rect(0, H-12, W, 12, stroke=0, fill=1)

        if LOGO_MUNICIPIO.exists():
            can.drawImage(str(LOGO_MUNICIPIO), 28, H-86, width=62, height=62, preserveAspectRatio=True, mask='auto', anchor='sw')
        if LOGO_SEMEC.exists():
            can.drawImage(str(LOGO_SEMEC), W-220, H-86, width=192, height=58, preserveAspectRatio=True, mask='auto', anchor='sw')

        cx = W / 2
        can.setFillColor(NAVY)
        can.setFont(FONT_B, 13)
        can.drawCentredString(cx, H-28, 'PREFEITURA MUNICIPAL DE SANTA MARIA DAS BARREIRAS')
        can.setFont(FONT_B, 11)
        can.drawCentredString(cx, H-43, 'SECRETARIA MUNICIPAL DE EDUCAÇÃO E CULTURA — SEMEC')
        can.setFont(FONT, 9.2)
        can.setFillColor(INK)
        can.drawCentredString(cx, H-57, 'Departamento de Tecnologia da Informação e Conectividade')
        can.setFont(FONT_B, 10.2)
        can.setFillColor(BLUE)
        can.drawCentredString(cx, H-71, 'RELATÓRIO INSTITUCIONAL DE FLUXO ESCOLAR 2025–2026')

        can.setStrokeColor(BORDER)
        can.line(28, H-92, W-28, H-92)
        can.setFont(FONT_B, 8.5)
        can.setFillColor(NAVY)
        can.drawString(28, H-89, f'Escola: {self.school}')
        can.drawRightString(W-28, H-89, f'INEP {self.inep}')

        can.setStrokeColor(BORDER)
        can.line(28, 34, W-28, 34)
        can.setFont(FONT, 7.4)
        can.setFillColor(GRAY)
        can.drawString(28, 22, 'Uso administrativo restrito • Dados tratados com base nas informações do Inep/Censo Escolar')
        can.drawRightString(W-28, 22, f'Página {doc.page}')
        can.restoreState()


class BarPanel(Flowable):
    def __init__(self,title,items,width=1100,barheight=13):
        super().__init__();self.title=title;self.items=items;self.width=width
        self.height=45+len(items)*27;self.barheight=barheight
    def draw(self):
        c=self.canv;w=self.width
        c.setFillColor(SOFT);c.roundRect(0,0,w,self.height,8,stroke=0,fill=1)
        c.setFillColor(NAVY);c.setFont(FONT_B,11);c.drawString(16,self.height-21,self.title)
        max_val=max((v for _,v,_ in self.items),default=0)
        for i,(label,value,color) in enumerate(self.items):
            y=self.height-49-i*27
            c.setFont(FONT,8.4);c.setFillColor(INK);c.drawString(17,y+3,label)
            start=280;full=w-start-90
            c.setFillColor(colors.HexColor('#E2E9F1'));c.roundRect(start,y,full,self.barheight,4,fill=1,stroke=0)
            amount=full*(value/max(max_val,1))
            if amount:
                c.setFillColor(color);c.roundRect(start,y,max(3,amount),self.barheight,4,fill=1,stroke=0)
            c.setFillColor(NAVY);c.setFont(FONT_B,9)
            c.drawRightString(w-18,y+2,str(value))


class PercentPanel(Flowable):
    def __init__(self,title,values,width=1100):
        super().__init__();self.title=title;self.values=values;self.width=width
        self.height=48+len(values)*32
    def draw(self):
        c=self.canv;w=self.width
        c.setFillColor(SOFT);c.roundRect(0,0,w,self.height,8,stroke=0,fill=1)
        c.setFillColor(NAVY);c.setFont(FONT_B,11)
        c.drawString(16,self.height-22,self.title)
        for i,(label,num,denom) in enumerate(self.values):
            y=self.height-52-i*32
            c.setFont(FONT,8.7);c.setFillColor(INK);c.drawString(17,y+5,label)
            x=310;full=w-x-120
            c.setFillColor(colors.HexColor('#DCE6EF'));c.roundRect(x,y,full,14,4,stroke=0,fill=1)
            if num is not None:
                ratio=min(max(num,0),100)
                tone=GREEN if 'Aprovação' in label else ORANGE if 'Reprovação' in label else RED
                if ratio:
                    c.setFillColor(tone);c.roundRect(x,y,max(3,full*ratio/100),14,4,stroke=0,fill=1)
                val=f'{num:.1f}% (n={denom})'.replace('.',',')
            else:val='Sem base para cálculo'
            c.setFillColor(NAVY);c.setFont(FONT_B,8.5);c.drawRightString(w-16,y+4,val)


def _stat_table(data):
    widths=[W-56]
    rows=[[paragraph(text,'text')] for text in data]
    normalized=[]
    for item in data:
        normalized.append([item if hasattr(item, 'wrap') else paragraph(item,'text')])
    t=Table(normalized,colWidths=widths,hAlign='LEFT')
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),SOFT),('BOX',(0,0),(-1,-1),.5,colors.HexColor('#D5E0EB')),
        ('LEFTPADDING',(0,0),(-1,-1),12),('RIGHTPADDING',(0,0),(-1,-1),12),
        ('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7),
        ('LINEBELOW',(0,0),(-1,-2),.45,colors.HexColor('#E2EAF2')),
    ]))
    return t


def _overview(story, inep, school, info):
    story.append(Paragraph('RELATÓRIO INDIVIDUAL DE ACOMPANHAMENTO ESCOLAR',ST['title']))
    story.append(Paragraph(f'<b>Escola:</b> {escape(school)} &nbsp;&nbsp; <b>Código INEP:</b> {inep}',ST['text']))
    story.append(Paragraph('Referência: situação final declarada em 2025 × matrículas declaradas em 2026 nas 21 escolas importadas.',ST['text']))
    cards=[('Matrículas únicas 2025',info['enrollment25']),('Resultados 2025 (registros)',info['base']),
           ('Matrículas únicas 2026',info['enrollment26']),('Alunos na lista (IDs)',info['students'])]
    cells=[]
    for label,value in cards:
        cells.append(Paragraph(f'<font color="#65758A">{label}</font><br/><font size="19" color="#17375B"><b>{value}</b></font>',ST['small']))
    tab=Table([cells],colWidths=[(W-56)/4]*4,rowHeights=[58])
    tab.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),STEEL),('BOX',(0,0),(-1,-1),.7,colors.HexColor('#D2DEEA')),
        ('LINEBEFORE',(1,0),(-1,-1),.7,colors.white),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),15)]))
    story.append(tab)
    story.append(Paragraph('1. Quadro de acompanhamento para a direção escolar',ST['subtitle']))
    cats=info['cats'];loc=info['locs']
    items=[
        rich_paragraph(f'<b>{len(cats["DEIXOU_DE_FREQUENTAR"])} alunos:</b> deixaram de frequentar em 2025; '
        f'{info["critical"]} não localizados em 2026 e {info["returned_dropout"]} localizados na rede.', 'text'),
        rich_paragraph(f'<b>{len(cats["REPROVADO"])} alunos:</b> reprovados em 2025; conferir plano de acompanhamento pedagógico.', 'text'),
        rich_paragraph(f'<b>{len(cats["TRANSFERIDO"])} alunos:</b> registrados como transferidos em 2025; '
        f'{len(loc["OUTRA ESCOLA DA REDE"])} IDs da escola identificados exclusivamente em outra escola em 2026 (independentemente da situação final).', 'text'),
        rich_paragraph(f'<b>{len(loc["NÃO LOCALIZADO NA REDE IMPORTADA"])} alunos:</b> sem matrícula localizada na rede importada em 2026; '
        'não presumir abandono antes de verificar eventuais matrículas fora da base.', 'text'),
        rich_paragraph(f'<b>{info["without_result"]} alunos:</b> matrícula inicial em 2025 sem situação final encontrada na escola; '
        f'<b>{len(loc["MÚLTIPLAS ESCOLAS EM 2026"])} alunos:</b> identificados em mais de uma escola em 2026.', 'text'),
        rich_paragraph(f'<b>{info["incoming"]} alunos:</b> matriculados nesta escola em 2026 e com resultado de 2025 em outra escola importada; '
        'não implica necessariamente transferência formal direta.', 'text'),
    ]
    story.append(_stat_table(items))
    story.append(Spacer(1,13))
    story.append(Paragraph('<b>Solicitações objetivas à escola:</b> confirmar a localização de estudantes não encontrados; '
         'comprovar destinos de transferência; apresentar encaminhamentos para reprovados e estudantes que deixaram de frequentar; '
         'corrigir divergências e casos sem resultado; devolver as informações atualizadas à coordenação.',ST['text']))
    story.append(Paragraph('Os números por categoria referem-se a IDs distintos na categoria. Um mesmo aluno pode aparecer em mais de uma categoria se possuir múltiplos resultados. '
         'Os dados apresentados são cópia administrativa da base importada e não substituem a conferência no Educacenso.',ST['muted']))
    story.append(PageBreak())


def _roster(story,rows,school,inep):
    story.append(Paragraph('2. Relação nominal, situação e continuidade escolar',ST['title']))
    story.append(Paragraph(f'<b>{escape(school)} — INEP {inep}.</b> Ordenação: casos prioritários primeiro; todos os resultados de 2025, '
        'acrescidos de matrículas iniciais sem situação final. A identificação única é pesquisada em todas as 21 escolas.',ST['small']))
    legend=('Localização 2026: “Mesma escola”, “Outra escola da rede”, “Múltiplas escolas” ou “Não localizado”. '
            'Campos 2026 vazios significam ausência de matrícula correspondente na base importada. '
            'EI = Educação Infantil; EF = Ensino Fundamental; EJA = Educação de Jovens e Adultos.')
    story.append(Paragraph(legend,ST['muted']))
    headers=['Nº','ID único','Aluno(a)','Nasc.','Idade','Sexo 2025','Cor/raça 2025','Cor/raça 2026',
             'Etapa 2025','Situação 2025','Destino 2026','Escola / INEP 2026','Etapa 2026','Alerta']
    sizes=[22,78,174,59,39,52,61,61,103,88,95,169,106,56]
    total=sum(sizes);available=W-56
    sizes=[x*available/total for x in sizes]
    table_rows=[[paragraph(x,'head') for x in headers]]
    row_categories=[]
    for num,r in enumerate(rows,1):
        cat=status_category(r[11]);row_categories.append(cat)
        dest=(str(r[14] or '') + (' | INEP: '+str(r[13]) if r[13] else '')).strip(' |')
        row=[num,r[1],r[2],r[3],r[19] if len(r)>19 else idade_ano_corrente(r[3]),r[4],r[5],r[6],short_stage(r[10]),r[11],loc_short(r[12]),dest,
             short_stage(r[16]),r[0]]
        table_rows.append([paragraph(x,'cell_bold' if i==2 else 'cell') for i,x in enumerate(row)])
    table=Table(table_rows,colWidths=sizes,repeatRows=1,hAlign='LEFT',splitByRow=1)
    sty=[('BACKGROUND',(0,0),(-1,0),NAVY),('VALIGN',(0,0),(-1,-1),'TOP'),
         ('TOPPADDING',(0,0),(-1,0),8),('BOTTOMPADDING',(0,0),(-1,0),8),
         ('TOPPADDING',(0,1),(-1,-1),6),('BOTTOMPADDING',(0,1),(-1,-1),6),
         ('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),
         ('LINEBELOW',(0,0),(-1,-1),.35,colors.HexColor('#D7E1EB')),
         ('LINEAFTER',(1,0),(1,-1),.5,colors.HexColor('#D6DEE8'))]
    for i,cat in enumerate(row_categories,1):
        if cat in ('DEIXOU_DE_FREQUENTAR','REPROVADO'):
            sty.append(('BACKGROUND',(0,i),(-1,i),colors.HexColor('#FFF1EA')))
        elif cat=='SEM_RESULTADO':
            sty.append(('BACKGROUND',(0,i),(-1,i),colors.HexColor('#FFF7D9')))
        elif cat=='TRANSFERIDO':
            sty.append(('BACKGROUND',(0,i),(-1,i),colors.HexColor('#EEF4FC')))
        elif i%2==0:
            sty.append(('BACKGROUND',(0,i),(-1,i),colors.HexColor('#FAFBFD')))
    table.setStyle(TableStyle(sty))
    story.append(table)
    story.append(Spacer(1,10))
    story.append(Paragraph(f'<b>Total de linhas da relação: {len(rows)}</b> — '
        'cor/raça de cada ano corresponde ao dado declarado na matrícula daquele ano. '
        'Pode haver mais de uma linha para um ID em caso de múltiplas situações em 2025.',ST['muted']))
    story.append(PageBreak())


def _chart_page(story, info, school):
    cat=info['cats'];loc=info['locs']
    rates=info['rendimento'];cw=W-56
    story.append(Paragraph('3. Análise diagnóstica e providências recomendadas', ST['title']))
    story.append(Paragraph(f'{escape(school)} — análise dos registros de rendimento de 2025 e dos vínculos de 2026.',ST['small']))
    details=[]
    for group in ('EF anos iniciais (1º–5º)','EF anos finais (6º–9º)'):
        r=rates[group]
        details.append(rich_paragraph(f'<b>{group}:</b> base classificável de {r["n"]} IDs; '
              f'{r["excluidos"]} IDs fora do recorte (transferências, outras situações, registros múltiplos).', 'text'))
    details.append(rich_paragraph('<b>Leitura gerencial:</b> reprovação e interrupção da frequência merecem atenção por sua '
       'relação com a trajetória e o rendimento escolar. O IDEB oficial é calculado pelo Inep a partir '
       'de dados de aprovação do Censo Escolar e de aprendizagem medida pelo Saeb; não é possível '
       'estimar uma nota IDEB com estas planilhas.', 'text'))
    details.append(rich_paragraph('<b>Metodologia deste relatório:</b> a proporção diagnóstica utiliza como denominador '
       'os IDs com um único resultado classificável (aprovado, reprovado ou deixou de frequentar) '
       'em ano seriado regular do respectivo segmento do Ensino Fundamental. Educação Infantil, EJA, '
       'turmas multietapa e demais situações não fazem parte desse recorte. '
       'As proporções não são taxas oficiais de rendimento do Inep.', 'text'))
    story.append(_stat_table(details))
    story.append(Spacer(1,12))
    story.append(Paragraph('Encaminhamentos para devolutiva da direção escolar',ST['subtitle']))
    actions=[]
    if info['critical']:
        actions.append(f'Localizar e registrar providências para os {info["critical"]} estudantes que deixaram de frequentar e não foram localizados em 2026.')
    if info['unresolved']-info['critical']:
        actions.append(f'Verificar a continuidade escolar de {info["unresolved"]-info["critical"]} estudantes reprovados não localizados.')
    if len(cat['REPROVADO']):actions.append(f'Definir acompanhamento pedagógico para os {len(cat["REPROVADO"])} estudantes reprovados em 2025.')
    if len(cat['TRANSFERIDO']):actions.append(f'Conferir o destino dos {len(cat["TRANSFERIDO"])} estudantes declarados transferidos em 2025, inclusive fora da rede analisada.')
    if info['without_result']:actions.append(f'Conferir a situação final de {info["without_result"]} estudantes matriculados em 2025 sem resultado correspondente.')
    if len(loc['MÚLTIPLAS ESCOLAS EM 2026']):actions.append('Conferir vínculos múltiplos de 2026 e eventuais atualizações cadastrais.')
    if not actions:actions.append('Documentar a continuidade escolar, a aprendizagem e as ações por turma.')
    for i,action in enumerate(actions,1):
        story.append(Paragraph(f'<b>{i}.</b> {escape(action)}',ST['text']))
    story.append(Spacer(1,20))
    story.append(Paragraph('Responsável pela devolutiva: ____________________________________   '
        'Prazo: ____/____/________   Assinatura: ____________________________________',ST['text']))
    story.append(Paragraph('Fontes institucionais: Inep/Censo Escolar — Situação do Aluno; Inep — IDEB e indicadores '
        'de rendimento escolar. Conferir os dados na fonte antes de qualquer providência administrativa.',ST['muted']))
    story.append(PageBreak())

    # Gráficos são intencionalmente as últimas páginas de cada PDF.
    story.append(Paragraph('4. Gráficos de acompanhamento e fatores de atenção',ST['title']))
    story.append(Paragraph('Gráficos exclusivamente diagnósticos; não representam nem estimam o IDEB oficial.',ST['small']))
    story.append(BarPanel('Situação final informada em 2025 • alunos distintos por categoria',[
        ('Aprovados',len(cat['APROVADO']),GREEN),
        ('Reprovados',len(cat['REPROVADO']),ORANGE),
        ('Deixaram de frequentar',len(cat['DEIXOU_DE_FREQUENTAR']),RED),
        ('Transferidos (movimento)',len(cat['TRANSFERIDO']),BLUE),
        ('Outras situações',len(cat['OUTRO']),GRAY),
    ],width=cw))
    story.append(Spacer(1,10))
    story.append(BarPanel('Localização na rede em 2026 • IDs da lista de 2025',[
        ('Mesma escola',len(loc['MESMA ESCOLA']),GREEN),
        ('Outra escola da rede',len(loc['OUTRA ESCOLA DA REDE']),BLUE),
        ('Múltiplas escolas',len(loc['MÚLTIPLAS ESCOLAS EM 2026']),ORANGE),
        ('Não localizados na base',len(loc['NÃO LOCALIZADO NA REDE IMPORTADA']),RED),
    ],width=cw))
    story.append(Spacer(1,10))
    percent=[]
    for group in ('EF anos iniciais (1º–5º)','EF anos finais (6º–9º)'):
        r=rates[group]
        percent.extend([(f'{group} • Aprovação',r['aprovacao'],r['n']),
                        (f'{group} • Reprovação',r['reprovacao'],r['n']),
                        (f'{group} • Deixaram de frequentar',r['desfrequencia'],r['n'])])
    story.append(PercentPanel('Recorte do Ensino Fundamental • proporções internas (não taxas oficiais)',percent,width=cw))
    story.append(Spacer(1,8))
    story.append(Paragraph('N = IDs com único resultado classificável em turma seriada do Ensino Fundamental. '
        'Categorias de situação e localização usam IDs distintos em cada grupo; um ID pode constar '
        'em mais de uma categoria quando existirem resultados múltiplos.',ST['muted']))
    story.append(Spacer(1,12))
    story.append(Paragraph('5. Créditos institucionais', ST['subtitle']))
    story.append(Paragraph('Relatório elaborado pelo <b>Departamento de Tecnologia da Informação e Conectividade</b> — SEMEC.', ST['text']))
    story.append(Paragraph('Contato institucional: <b>dticsemec@gmail.com</b>.', ST['text']))
    story.append(Paragraph('Os dados foram tratados com base nas informações do <b>Inep/Censo Escolar</b> e consolidados no banco institucional de análise para apoio à gestão municipal. Os resultados possuem finalidade administrativa e devem ser validados pela unidade escolar antes da adoção de providências.', ST['muted']))


class InfraStatusBar(Flowable):
    """Indicador visual categórico: cor = resposta declarada, não pontuação da escola."""
    def __init__(self, value, width=130):
        super().__init__();self.value=str(value or 'Não informado');self.width=width;self.height=15
    def draw(self):
        n=norm(self.value)
        if n=='sim':tone=GREEN;caption='SIM'
        elif n=='nao':tone=ORANGE;caption='NÃO'
        elif not n or n in ('nao informado','--'):tone=GRAY;caption='N/I'
        else:tone=BLUE;caption='INFORMADO'
        c=self.canv;c.setFillColor(colors.HexColor('#EEF3F7'))
        c.roundRect(0,0,self.width,self.height,4,fill=1,stroke=0)
        c.setFillColor(tone);c.roundRect(0,0,self.width if n in ('sim','nao') else self.width*.73,self.height,4,fill=1,stroke=0)
        c.setFillColor(colors.white);c.setFont(FONT_B,7.2)
        c.drawCentredString(self.width/2,self.height/2-2.8,caption)


def _infrastructure_append(story, conn, inep):
    attrs=school_infra(conn,inep)
    story.append(PageBreak())
    story.append(Paragraph('5. Infraestrutura escolar — diagnóstico por código INEP', ST['title']))
    if not attrs:
        story.append(Paragraph('Não foi localizado registro de infraestrutura para este código INEP na base importada. Solicitar a planilha correspondente.',ST['text']))
        return
    story.append(Paragraph(f'Fonte: Aspectos_Infraestrutura_14092026.xlsx • ano declarado {attrs[0]["year"]}. Todas as {len(attrs)} respostas disponíveis nesta base são reproduzidas nos gráficos e tabelas a seguir.',ST['small']))
    story.append(Paragraph('A barra identifica a resposta informada (Sim, Não, Outra ou não informada). Não é uma nota de qualidade nem uma medida de impacto no IDEB. A existência ou ausência de ambientes depende da etapa atendida e das condições da unidade.',ST['muted']))
    for group,rows in infrastructure_groups(attrs).items():
        story.append(Paragraph(group,ST['subtitle']))
        table_data=[[paragraph('Indicador', 'head'),paragraph('Resposta visual','head'),paragraph('Valor declarado','head')]]
        for a in rows:
            table_data.append([paragraph(a['name'],'cell'),InfraStatusBar(a['value'],155),paragraph(a['value'] or 'Não informado','cell')])
        table=Table(table_data,colWidths=[(W-56)*.50,(W-56)*.18,(W-56)*.32],repeatRows=1,splitByRow=1,hAlign='LEFT')
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),NAVY),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,SOFT]),
            ('GRID',(0,0),(-1,-1),.32,BORDER),('LEFTPADDING',(0,0),(-1,-1),10),
            ('RIGHTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
        story.append(table)
        story.append(Spacer(1,9))
    checks=infrastructure_checks(attrs)
    story.append(Paragraph('6. Pontos de verificação e possíveis providências',ST['title']))
    if checks:
        story.append(_stat_table([rich_paragraph('<b>'+escape(label)+':</b> '+escape(advice),'text') for label,advice in checks]))
    else:
        story.append(Paragraph('Nenhum dos critérios automáticos de atenção foi acionado. Isso não substitui vistoria e avaliação técnica da unidade.',ST['text']))
    story.append(Spacer(1,8))
    story.append(Paragraph('Interpretação gerencial: infraestrutura adequada pode contribuir indiretamente para permanência, condições de aprendizagem, inclusão e execução de ações financiadas por programas públicos. Não integra diretamente a fórmula do IDEB, que combina aprovação e desempenho no Saeb. Os alertas não comprovam irregularidade nem elegibilidade a recursos.',ST['muted']))


def _transport_append(story,conn,inep):
    records=school_transport(conn,inep,2026)
    classes=Counter(r['situacao'] for r in records)
    story.append(PageBreak())
    story.append(Paragraph('7. Auditoria de transporte escolar — residência do estudante',ST['title']))
    story.append(Paragraph('Ano 2026 • Identificação única e código INEP • Cada estudante contado uma vez por escola, ainda que tenha matrícula regular e AEE.',ST['small']))
    story.append(BarPanel('Alunos com uso de transporte escolar declarado — segundo zona residencial',[
      ('Residência rural',classes['RURAL INFORMADA'],GREEN),
      ('Residência urbana • conferir',classes['ALERTA: RESIDÊNCIA URBANA'],RED),
      ('Zona ausente ou divergente',sum(v for k,v in classes.items() if k.startswith('VERIFICAR')),ORANGE)
    ],width=W-56))
    story.append(Spacer(1,10))
    warnings=[r for r in records if r['situacao'].startswith(('ALERTA','VERIFICAR'))]
    story.append(Paragraph(f'<b>{len(warnings)} estudantes</b> requerem conferência de zona de residência ou divergência. O uso do transporte com endereço urbano não significa automaticamente que o atendimento seja indevido. Para o PNATE, o critério federal considera residência rural e utilização do transporte; a localização rural da escola, por si só, não substitui o endereço real do estudante.',ST['text']))
    if warnings:
        cols=[100,207,61,103,105,95,W-56-100-207-61-103-105-95]
        table_rows=[[paragraph(x,'head') for x in ['ID único','Aluno(a)','Idade','Zona de residência','Transporte','Classificação','Providência']]]
        for r in warnings:
            table_rows.append([paragraph(v,'cell') for v in [r['id'],r['nome'],r['idade'],r['zona'],r['transporte'],r['situacao'],r['providencia']]])
        t=Table(table_rows,colWidths=cols,repeatRows=1,splitByRow=1)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),NAVY),('VALIGN',(0,0),(-1,-1),'TOP'),
          ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor('#FFF8EF'),colors.white]),
          ('LINEBELOW',(0,0),(-1,-1),.4,BORDER),('TOPPADDING',(0,0),(-1,-1),7),
          ('BOTTOMPADDING',(0,0),(-1,-1),7)]))
        story.append(t)
    story.append(Spacer(1,12))
    story.append(Paragraph('Encaminhamento: confirmar endereço de residência por documentação e junto à família, verificar a necessidade do transporte e os dados no Educacenso, e realizar eventual retificação apenas com respaldo documental e dentro das regras e prazos oficiais. Não transformar registros urbanos em rurais de forma automática.',ST['text']))
    story.append(Paragraph('Fonte e créditos: Departamento de Tecnologia da Informação e Conectividade — SEMEC | dticsemec@gmail.com. Dados tratados com base nas informações do Inep/Censo Escolar e arquivo de infraestrutura disponibilizado. Uso restrito da gestão.',ST['muted']))


def _safe_name(s):
    cleaned=''.join(c for c in unicodedata.normalize('NFKD',str(s)) if not unicodedata.combining(c))
    cleaned=re.sub(r'[^A-Za-z0-9_-]+','_',cleaned).strip('_')
    return cleaned[:72] or 'ESCOLA'


def export_school_reports(db_path,output_dir,school_inep=None,log=None):
    """Exporta um PDF A3 paisagem por escola participante (21 no ZIP validado).

    school_inep permite somente uma escola. A exportação não modifica o SQLite.
    Retorna (quantidade, lista de caminhos). 
    """
    dest=Path(output_dir);dest.mkdir(parents=True,exist_ok=True)
    conn=safe_sqlite(db_path)
    try:
        schools=origin_schools(conn)
        if school_inep:
            schools=[s for s in schools if s[0]==str(school_inep)]
            if not schools:raise ValueError('INEP selecionado não possui resultado 2025 importado: '+str(school_inep))
        if not schools:raise ValueError('Não há escolas com resultado 2025 importado para gerar relatórios.')
        allrows=report_rows(conn)
        created=[]
        for k,(inep,name) in enumerate(schools,1):
            if log:log(f'[{k}/{len(schools)}] Gerando PDF: {name} ({inep})')
            rows,metrics=school_analysis(conn,allrows,inep,name)
            target=dest / f'{inep}_{_safe_name(name)}_Fluxo_2025_2026.pdf'
            doc=SchoolDocument(target,inep,name,author='Coordenação do Censo Escolar',
                title=f'Fluxo escolar 2025–2026 • {name} • INEP {inep}',
                subject='Relatório individual; continuidade escolar; indicadores diagnósticos')
            story=[]
            _overview(story,inep,name,metrics)
            _roster(story,rows,name,inep)
            _chart_page(story,metrics,name)
            _infrastructure_append(story,conn,inep)
            _transport_append(story,conn,inep)
            doc.build(story)
            created.append(str(target))
        if log:log(f'Concluídos {len(created)} relatórios individuais; nenhum dado do SQLite foi modificado.')
        return len(created),created
    finally:conn.close()
