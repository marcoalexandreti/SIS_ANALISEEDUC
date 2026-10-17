"""Painel gerencial e exportações — cálculos de diagnóstico escolar, não IDEB oficial.
Não edita o SQLite. Todas as localizações são verificadas pelo ID nas escolas importadas.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
from core import safe_sqlite, report_rows, REPORT_COLS, norm
from relatorios_escolas import origin_schools, school_analysis, status_category, grade

COLORS = {'header':'#17375B','light':'#EAF0F6','ink':'#263445','muted':'#58697B',
          'critical':'#A73940','warning':'#B9691B','ok':'#237A5A','accent':'#245987'}


def matches(row, school_inep=None, category='Todos', text=''):
    if school_inep and str(row[7])!=str(school_inep):return False
    condition=norm(row[11]);loc=row[12]
    if category=='Deixaram de frequentar' and 'deixou de frequentar' not in condition:return False
    if category=='Reprovados' and 'reprovado' not in condition:return False
    if category=='Transferidos' and 'transferido' not in condition:return False
    if category=='Busca ativa' and row[0] not in ('CRÍTICA','ALTA'):return False
    if category=='Mobilidade interna' and loc!='OUTRA ESCOLA DA REDE':return False
    if category=='Não localizados' and loc!='NÃO LOCALIZADO NA REDE IMPORTADA':return False
    if category=='Vínculos múltiplos' and loc!='MÚLTIPLAS ESCOLAS EM 2026':return False
    return not text or norm(text) in norm(' '.join(str(v or '') for v in row))


def filtered_rows(db_file,school_inep=None,category='Todos',text=''):
    c=safe_sqlite(db_file)
    try:return [r for r in report_rows(c) if matches(r,school_inep,category,text)]
    finally:c.close()


def school_indicators(db_file,log=None):
    """Um registro por escola, usando IDs distintos por categoria, sem somar taxas entre escolas."""
    c=safe_sqlite(db_file)
    try:
        all_rows=report_rows(c)
        records=[]
        for n,(inep,name) in enumerate(origin_schools(c),1):
            _,s=school_analysis(c,all_rows,inep,name)
            ef=s['rendimento']['EF anos iniciais (1º–5º)']
            cat=s['cats'];loc=s['locs']
            records.append({
              'inep':inep,'school':name,'students':s['students'],
              'enrollment25':s['enrollment25'],'enrollment26':s['enrollment26'],
              'approved':len(cat['APROVADO']),'failed':len(cat['REPROVADO']),
              'dropout':len(cat['DEIXOU_DE_FREQUENTAR']),'transferred':len(cat['TRANSFERIDO']),
              'missing26':len(loc['NÃO LOCALIZADO NA REDE IMPORTADA']),
              'internal':len(loc['OUTRA ESCOLA DA REDE']),'multiple':len(loc['MÚLTIPLAS ESCOLAS EM 2026']),
              'same':len(loc['MESMA ESCOLA']),'critical':s['critical'],
              'unresolved':s['unresolved'],'incoming':s['incoming'],
              'without_result':s['without_result'],
              'ef_n':ef['n'],'ef_ap':ef['aprovacao'],'ef_rep':ef['reprovacao'],
              'ef_dropout':ef['desfrequencia'],'ef_excluded':ef['excluidos'],
              'final_n':s['base'], 'raw':s,
            })
            if log and n%7==0:log(f'Indicadores calculados: {n} escolas')
        unique_status={k:set() for k in ('APROVADO','REPROVADO','DEIXOU_DE_FREQUENTAR','TRANSFERIDO')}
        unique_loc={k:set() for k in ('MESMA ESCOLA','OUTRA ESCOLA DA REDE','MÚLTIPLAS ESCOLAS EM 2026','NÃO LOCALIZADO NA REDE IMPORTADA')}
        all_ids=set();crit=set();unresolved=set()
        for r in all_rows:
            stu=r[1];all_ids.add(stu)
            sc=status_category(r[11])
            if sc in unique_status:unique_status[sc].add(stu)
            if r[12] in unique_loc:unique_loc[r[12]].add(stu)
            if sc=='DEIXOU_DE_FREQUENTAR' and r[12]=='NÃO LOCALIZADO NA REDE IMPORTADA':crit.add(stu)
            if sc in ('REPROVADO','DEIXOU_DE_FREQUENTAR') and r[12]=='NÃO LOCALIZADO NA REDE IMPORTADA':unresolved.add(stu)
        totals={'schools':len(records),'students':len(all_ids),'rows':len(all_rows),'critical':len(crit),
                'unresolved':len(unresolved),'status':{k:len(v) for k,v in unique_status.items()},
                'location':{k:len(v) for k,v in unique_loc.items()}}
        return records, totals
    finally:c.close()


def _reportlab():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4,A3,landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Table,TableStyle,Spacer,PageBreak,KeepTogether,Flowable
    from reportlab.lib.enums import TA_CENTER
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    # Usa fonte Unicode livre embutida; não redistribui arquivos de fonte.
    if 'GestaoFont' not in pdfmetrics.getRegisteredFontNames():
        for reg,bold in [('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'),
                         ('C:/Windows/Fonts/arial.ttf','C:/Windows/Fonts/arialbd.ttf')]:
            if Path(reg).exists():
                pdfmetrics.registerFont(TTFont('GestaoFont',reg))
                pdfmetrics.registerFont(TTFont('GestaoBold',bold))
                break
    font='GestaoFont' if 'GestaoFont' in pdfmetrics.getRegisteredFontNames() else 'Helvetica'
    bold='GestaoBold' if font=='GestaoFont' else 'Helvetica-Bold'
    return colors,A4,A3,landscape,ParagraphStyle,SimpleDocTemplate,Paragraph,Table,TableStyle,Spacer,PageBreak,KeepTogether,Flowable,TA_CENTER,font,bold


def _fmt(val):return '—' if val is None else str(val)


def export_management_pdf(db_file,filename,log=None):
    """PDF consolidado A4 paisagem; indicadores da base inteira, sem nomes de crianças."""
    rec,tot=school_indicators(db_file,log)
    (colors,A4,A3,landscape,ParagraphStyle,SimpleDocTemplate,Paragraph,Table,TableStyle,Spacer,PageBreak,KeepTogether,
     Flowable,TA_CENTER,font,bold)=_reportlab()
    navy=colors.HexColor(COLORS['header']);muted=colors.HexColor(COLORS['muted'])
    s_title=ParagraphStyle('MGtit',fontName=bold,fontSize=17,leading=23,textColor=navy,spaceAfter=10)
    s_sub=ParagraphStyle('MGsub',fontName=bold,fontSize=12,leading=16,textColor=navy,spaceBefore=12,spaceAfter=7)
    s_text=ParagraphStyle('MGtext',fontName=font,fontSize=8.4,leading=12,spaceAfter=9)
    s_small=ParagraphStyle('MGsmall',fontName=font,fontSize=7.3,leading=10,textColor=muted,spaceAfter=6)
    s_th=ParagraphStyle('MGhead',fontName=bold,fontSize=7,leading=9,textColor=colors.white)
    s_td=ParagraphStyle('MGcell',fontName=font,fontSize=7,leading=9.5)
    page_w,page_h=landscape(A4);inner=page_w-60
    dest=Path(filename);dest.parent.mkdir(parents=True,exist_ok=True)
    doc=SimpleDocTemplate(str(dest),pagesize=landscape(A4),leftMargin=30,rightMargin=30,topMargin=38,bottomMargin=37,
      author='Gestão Municipal de Educação',title='Diagnóstico de Fluxo Escolar - Rede Municipal 2025-2026')
    story=[Paragraph('PAINEL GERENCIAL DA REDE MUNICIPAL',s_title),
           Paragraph('Fluxo escolar 2025 → 2026 • Santa Maria das Barreiras/PA • consolidação das escolas importadas',s_text),
           Paragraph('Documento de uso institucional. Indicadores internos, sujeitos à conferência no Educacenso. Não equivale ao IDEB oficial.',s_small)]
    values=[('Escolas analisadas',tot['schools']),('Alunos com situação 2025',tot['students']),
       ('Casos prioritários não localizados (IDs)',tot['unresolved']),('Deixaram de frequentar e não localizados (IDs)',tot['critical'])]
    boxes=Table([[Paragraph(f'<b>{escape(k)}</b><br/><font size="15">{v:,}</font>'.replace(',','.'),s_text) for k,v in values]],colWidths=[inner/4]*4)
    boxes.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#EAF1F8')),
      ('BOX',(0,0),(-1,-1),.5,colors.HexColor('#CCD8E4')),('VALIGN',(0,0),(-1,-1),'TOP'),
      ('TOPPADDING',(0,0),(-1,-1),12),('BOTTOMPADDING',(0,0),(-1,-1),8)]))
    story.extend([boxes,Spacer(1,14),Paragraph('Situação final e localização na rede',s_sub)])
    summary=[('Aprovados',tot['status']['APROVADO']),('Reprovados',tot['status']['REPROVADO']),
       ('Deixaram de frequentar',tot['status']['DEIXOU_DE_FREQUENTAR']),('Transferidos',tot['status']['TRANSFERIDO']),
       ('Mesma escola em 2026',tot['location']['MESMA ESCOLA']),('Outra escola da rede',tot['location']['OUTRA ESCOLA DA REDE']),
       ('Mais de uma escola em 2026',tot['location']['MÚLTIPLAS ESCOLAS EM 2026']),
       ('Não localizados na base 2026',tot['location']['NÃO LOCALIZADO NA REDE IMPORTADA'])]
    class HorizontalBars(Flowable):
        def __init__(self,data,w):super().__init__();self.data=data;self.width=w;self.height=len(data)*30+21
        def draw(self):
            canv=self.canv;peak=max([v for _,v in self.data]+[1]);w=self.width
            for i,(label,count) in enumerate(self.data):
                y=self.height-23-30*i
                canv.setFont(font,8);canv.setFillColor(colors.HexColor('#243446'));canv.drawString(6,y+4,label)
                x=235;avail=w-x-62
                canv.setFillColor(colors.HexColor('#E5EDF5'));canv.roundRect(x,y,avail,15,3,stroke=0,fill=1)
                if count:
                    tone=colors.HexColor(COLORS['critical'] if 'não localiz' in norm(label) or 'deixaram' in norm(label) else COLORS['accent'])
                    canv.setFillColor(tone);canv.roundRect(x,y,max(2,count/peak*avail),15,3,stroke=0,fill=1)
                canv.setFillColor(navy);canv.setFont(bold,8);canv.drawRightString(w-6,y+4,str(count))
    story.append(HorizontalBars(summary,inner))
    story.extend([Paragraph('Categorias calculadas por identificação única em cada grupo; a mesma pessoa pode pertencer a mais de um grupo em razão de vínculos ou resultados múltiplos.',s_small),
       PageBreak(),Paragraph('Indicadores por unidade escolar',s_title),
       Paragraph('Listagem em ordem alfabética. Denominadores restritos às situações observadas em cada escola, sem comparação direta com índices oficiais.',s_small)])
    headers=['INEP','Escola','Alunos 2025','Reprov.','Deixaram','Transf.','Não localiz.','Outra escola','Prioritários','Aprov. EF 1º–5º*']
    widths=[49,180,54,47,49,46,57,58,57,inner-597]
    def P(x,style):return Paragraph(escape(_fmt(x)),style)
    table_data=[[P(h,s_th) for h in headers]]
    for r in rec:
        ap=r['ef_ap']
        last=f'{ap:.1f}% (n={r["ef_n"]})'.replace('.',',') if ap is not None else 's/ base'
        vals=[r['inep'],r['school'],r['students'],r['failed'],r['dropout'],r['transferred'],r['missing26'],r['internal'],r['unresolved'],last]
        table_data.append([P(v,s_td) for v in vals])
    t=Table(table_data,colWidths=widths,repeatRows=1,hAlign='LEFT')
    style=[('BACKGROUND',(0,0),(-1,0),navy),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
      ('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),
      ('TOPPADDING',(0,0),(-1,-1),2),('BOTTOMPADDING',(0,0),(-1,-1),2),
      ('LINEBELOW',(0,1),(-1,-1),.25,colors.HexColor('#D5E1EB'))]
    for i in range(2,len(table_data),2):style.append(('BACKGROUND',(0,i),(-1,i),colors.HexColor('#F6F9FC')))
    t.setStyle(TableStyle(style));story.extend([t,Spacer(1,10),Paragraph('* Aprovação EF 1º–5º: proporção diagnóstica entre IDs com resultado único classificável em ano seriado regular. Não representa taxa oficial do Inep.',s_small),
       Paragraph('A localização em 2026 foi pesquisada pelo ID em todas as escolas da base. Ausência na base importada não comprova abandono nem exclui matrícula em redes externas.',s_small),
       Paragraph('O IDEB combina desempenho no Saeb e fluxo/aprovação apurado pelo Inep. Sem resultados do Saeb e as apurações oficiais, estes indicadores não permitem calcular, simular ou prever o IDEB.',s_small)])
    story.extend([PageBreak(),Paragraph('Encaminhamentos institucionais recomendados',s_title),
        Paragraph('1. Solicitar a cada unidade comprovação do destino de alunos não localizados, priorizando aqueles que deixaram de frequentar ou foram reprovados em 2025.',s_text),
        Paragraph('2. Verificar mudanças entre escolas municipais pelo ID do aluno e pelo INEP de origem/destino, evitando registrar mobilidade interna como abandono.',s_text),
        Paragraph('3. Conferir registros com múltiplos vínculos em 2026, divergências cadastrais e matrículas iniciais de 2025 sem situação final.',s_text),
        Paragraph('4. Construir planos de recuperação de aprendizagem e acompanhamento de frequência, com responsáveis e prazos específicos por escola.',s_text),
        Paragraph('5. Utilizar os indicadores de aprovação dos anos iniciais apenas como sinais de atenção; cotejar com o rendimento oficial e os resultados do Saeb quando disponíveis.',s_text),
        Paragraph('Fontes: Relações de matrículas 2025 e 2026; Situação do Aluno 2025; bases locais importadas. Elaborado em '+datetime.now().strftime('%d/%m/%Y')+'.',s_small)])
    def footer(canvas,doc):
        canvas.saveState();canvas.setFillColor(muted);canvas.setFont(font,7)
        canvas.drawString(30,20,'Uso interno • dados do Censo Escolar importados • sem identificação nominal neste painel')
        canvas.drawRightString(page_w-30,20,f'Página {doc.page}');canvas.restoreState()
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    if log:log(f'PDF do painel gerencial: {dest.name} ({len(rec)} escolas)')
    return str(dest)


def export_filtered_pdf(db_file,filename,school_inep=None,category='Todos',search='',log=None):
    """Relação nominal filtrada, A3 horizontal com cabeçalho repetido, em PDF."""
    rows=filtered_rows(db_file,school_inep,category,search)
    (colors,A4,A3,landscape,ParagraphStyle,SimpleDocTemplate,Paragraph,Table,TableStyle,Spacer,PageBreak,KeepTogether,
     Flowable,TA_CENTER,font,bold)=_reportlab()
    size=landscape(A3);w=size[0]-44
    dest=Path(filename);dest.parent.mkdir(parents=True,exist_ok=True)
    style_t=ParagraphStyle('Ntitle',fontName=bold,fontSize=17,leading=21,textColor=colors.HexColor(COLORS['header']),spaceAfter=9)
    style_s=ParagraphStyle('Nsmall',fontName=font,fontSize=8,leading=11,spaceAfter=7)
    cell=ParagraphStyle('Ncell',fontName=font,fontSize=7,leading=9)
    head=ParagraphStyle('Nhead',fontName=bold,fontSize=7,leading=9,textColor=colors.white)
    doc=SimpleDocTemplate(str(dest),pagesize=size,leftMargin=22,rightMargin=22,topMargin=35,bottomMargin=35,
        title='Relação nominal filtrada 2025-2026',author='Gestão Municipal de Educação')
    unique=len({r[1] for r in rows});story=[Paragraph('RELAÇÃO NOMINAL DE ACOMPANHAMENTO 2025–2026',style_t),
       Paragraph(f'Filtro: {escape(category)} | Escola INEP: {escape(str(school_inep or "todas"))} | Pesquisa: {escape(search or "nenhuma")} | {len(rows)} registros; {unique} identificações únicas.',style_s),
       Paragraph('Em “não localizado”, a ausência refere-se exclusivamente às escolas de 2026 presentes no banco. Situação final de 2025 não se confunde com a localização de 2026.',style_s)]
    indices=[1,2,3,19,4,5,6,7,8,10,11,12,13,14,16,0]
    headers=['ID único','Aluno(a)','Nascimento','Idade','Sexo','Raça/cor 2025','Raça/cor 2026','INEP 2025',
        'Escola origem','Etapa 2025','Resultado 2025','Localização 2026','INEP 2026','Escola 2026','Etapa 2026','Prioridade']
    widths=[76,156,58,35,42,67,67,58,120,82,90,100,62,115,86,56]
    scale=w/sum(widths);widths=[z*scale for z in widths]
    def p(v,sty):return Paragraph(escape(_fmt(v)),sty)
    mat=[[p(h,head) for h in headers]]
    for r in rows:mat.append([p(r[i],cell) for i in indices])
    tab=Table(mat,colWidths=widths,repeatRows=1,hAlign='LEFT',splitByRow=1)
    styling=[('BACKGROUND',(0,0),(-1,0),colors.HexColor(COLORS['header'])),('VALIGN',(0,0),(-1,-1),'TOP'),
       ('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),
       ('TOPPADDING',(0,1),(-1,-1),5),('BOTTOMPADDING',(0,1),(-1,-1),5),
       ('LINEBELOW',(0,1),(-1,-1),.25,colors.HexColor('#D8E2E8'))]
    for ix,r in enumerate(rows,1):
        if r[0]=='CRÍTICA':styling.append(('BACKGROUND',(0,ix),(-1,ix),colors.HexColor('#FFF0EF')))
        elif r[0] in ('ALTA','VERIFICAR DESTINO'):styling.append(('BACKGROUND',(0,ix),(-1,ix),colors.HexColor('#FFF7E8')))
        elif ix%2==0:styling.append(('BACKGROUND',(0,ix),(-1,ix),colors.HexColor('#F7FAFC')))
    tab.setStyle(TableStyle(styling));story.append(tab)
    def footer(canvas,doc):
        canvas.saveState();canvas.setFont(font,7);canvas.setFillColor(colors.HexColor('#566779'))
        canvas.drawString(22,17,'Documento de uso interno • informações pessoais protegidas • conferência na fonte necessária')
        canvas.drawRightString(size[0]-22,17,f'{doc.page}');canvas.restoreState()
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    if log:log(f'PDF nominal: {len(rows)} linhas / {unique} alunos distintos')
    return str(dest),len(rows),unique


def export_filtered_excel(db_file,filename,school_inep=None,category='Todos',search='',log=None):
    """Exportação XLSX com artifact_tool e colunas em formato textual para preservar identificadores."""
    from artifact_tool import Workbook, SpreadsheetFile
    rows=filtered_rows(db_file,school_inep,category,search)
    wb=Workbook.create()
    sh=wb.worksheets.add('Relação nominal')
    titles=list(REPORT_COLS)
    sh.get_range_by_indexes(0,0,1,len(titles)).values=[titles]
    # Escrita em lotes reduz alocações e mantém a identificação única como texto.
    for start in range(0,len(rows),250):
        batch=[[str(v) if v is not None else '' for v in r] for r in rows[start:start+250]]
        sh.get_range_by_indexes(start+1,0,len(batch),len(titles)).values=batch
    hdr=sh.get_range_by_indexes(0,0,1,len(titles))
    hdr.format={'fill':'#17375B','font':{'bold':True,'color':'#FFFFFF'},'row_height':32,'wrap_text':True}
    sh.freeze_panes.freeze_rows(1)
    sh.get_range('A:A').format.column_width=17
    sh.get_range('B:B').format.column_width=20
    sh.get_range('C:C').format.column_width=38
    sh.get_range('D:G').format.column_width=18
    sh.get_range('H:H').format.column_width=18
    sh.get_range('I:I').format.column_width=37
    sh.get_range('J:J').format.column_width=25
    sh.get_range('K:K').format.column_width=39
    sh.get_range('L:L').format.column_width=28
    sh.get_range('M:M').format.column_width=27
    sh.get_range('N:N').format.column_width=18
    sh.get_range('O:O').format.column_width=38
    sh.get_range('P:Q').format.column_width=38
    sh.get_range('R:R').format.column_width=20
    sh.get_range('S:S').format.column_width=60
    sh.get_range('T:T').format.column_width=15
    if len(rows):
        sh.get_range(f'A2:T{len(rows)+1}').format.wrap_text=True
        sh.get_range(f'A2:T{len(rows)+1}').format.autofit_rows()
    if len(rows):
        try:sh.tables.add(f'A1:T{len(rows)+1}',True,'TabelaFluxoEscolar')
        except Exception:pass
    intro=wb.worksheets.add('Orientações')
    intro.get_range('A1:B8').values=[
       ['RELATÓRIO DE FLUXO 2025–2026','Santa Maria das Barreiras/PA'],
       ['Filtro de situação',category],['INEP 2025',str(school_inep or 'Todas as escolas')],
       ['Pesquisa textual',search or 'Nenhuma'],['Registros exportados',len(rows)],
       ['IDs distintos',len({r[1] for r in rows})],
       ['Interpretação','Não localizado significa ausência nas bases importadas; não caracteriza automaticamente abandono.'],
       ['IDEB','A planilha não representa nota IDEB nem taxa oficial do Inep.']]
    intro.get_range('A1:B1').format={'fill':'#17375B','font':{'bold':True,'color':'#FFFFFF'},'row_height':28}
    intro.get_range('A:A').format.column_width=28
    intro.get_range('B:B').format.column_width=100
    dest=Path(filename);dest.parent.mkdir(parents=True,exist_ok=True)
    SpreadsheetFile.export_xlsx(wb).save(str(dest))
    if log:log(f'Excel nominal: {len(rows)} registros em {dest.name}')
    return str(dest),len(rows)


def _export_excel_windows(db_file,filename,school_inep=None,category='Todos',search='',log=None):
    """Exportador para distribuição Windows, sem dependência de artefatos internos."""
    import xlsxwriter
    rows=filtered_rows(db_file,school_inep,category,search)
    out=Path(filename);out.parent.mkdir(parents=True,exist_ok=True)
    wb=xlsxwriter.Workbook(str(out))
    sh=wb.add_worksheet('Relação nominal')
    orient=wb.add_worksheet('Orientações')
    fmt_head=wb.add_format({'bold':True,'font_color':'white','bg_color':'#17375B',
                            'align':'center','valign':'vcenter','text_wrap':True,'border':1,'border_color':'#D4E0EB'})
    fmt_cell=wb.add_format({'font_size':10,'valign':'vcenter','border':0,'text_wrap':True})
    fmt_alt=wb.add_format({'font_size':10,'valign':'vcenter','bg_color':'#F2F7FC'})
    fmt_warning=wb.add_format({'font_size':10,'valign':'vcenter','bg_color':'#FFF1E9'})
    fmt_title=wb.add_format({'bold':True,'font_color':'white','bg_color':'#17375B','font_size':13})
    sh.freeze_panes(1,2)
    sh.set_row(0,42)
    widths=[18,20,43,18,15,18,18,17,44,24,41,31,30,18,43,34,38,19,72,15]
    for ix,width in enumerate(widths):sh.set_column(ix,ix,width)
    for j,h in enumerate(REPORT_COLS):sh.write_string(0,j,h,fmt_head)
    for i,r in enumerate(rows,1):
        fmt=fmt_warning if r[0] in ('CRÍTICA','ALTA') else fmt_alt if i%2==0 else fmt_cell
        sh.set_row(i, max(32, min(104, 16 * max(2, max((len(str(v or '')) // max(14, int(widths[j] * .8)) + 1) for j,v in enumerate(r))))))
        for j,v in enumerate(r):sh.write_string(i,j,str(v) if v is not None else '',fmt)
    if rows:sh.autofilter(0,0,len(rows),len(REPORT_COLS)-1)
    for i,(k,v) in enumerate([
        ('RELATÓRIO DE FLUXO 2025–2026','Santa Maria das Barreiras/PA'),
        ('Situação',category),('INEP de origem',str(school_inep or 'Todas as escolas')),
        ('Busca textual',search or 'Nenhuma'),('Linhas exportadas',str(len(rows))),
        ('IDs distintos',str(len({r[1] for r in rows}))),
        ('Não localizado','Ausência na rede importada, não comprova evasão.'),
        ('IDEB','Não é nota nem taxa oficial do Inep.')]):
        orient.write_string(i,0,k,fmt_title if i==0 else fmt_cell)
        orient.write_string(i,1,v,fmt_title if i==0 else fmt_cell)
    orient.set_column('A:A',30);orient.set_column('B:B',90);orient.set_row(0,30)
    wb.close()
    if log:log(f'Excel nominal para Windows: {len(rows)} registros')
    return str(out),len(rows)


# Distribuição: usar XlsxWriter em instalações Windows comuns, sem exigir módulo interno.
# Os XLSX de validação gerados neste ambiente utilizam artifact_tool conforme as
# diretrizes de produção de planilhas. O app distribuído é independente desse runtime.
_excel_artifact_export=export_filtered_excel

def export_filtered_excel(db_file,filename,school_inep=None,category='Todos',search='',log=None):
    return _export_excel_windows(db_file,filename,school_inep,category,search,log)
