"""Auditoria rastreável de infraestrutura e transporte, sem alterações automáticas no Censo.
Os gráficos descrevem respostas originais. Ações são recomendações de verificação,
não declarações de irregularidade nem previsão de IDEB ou repasse.
"""
from collections import defaultdict, Counter
from pathlib import Path
import csv
import sqlite3
import unicodedata
from datetime import date
from core import safe_sqlite, idade_ano_corrente


def clean(v):
    return (str(v or '').strip())


def normalized(s):
    return ''.join(c for c in unicodedata.normalize('NFKD',str(s or '').lower()) if not unicodedata.combining(c)).strip()


def school_infra(conn, inep):
    rs=conn.execute('''SELECT a.nome,v.valor,a.coluna_origem,v.ano
      FROM infra_valores v JOIN infra_atributos a ON a.id=v.atributo_id
      WHERE v.inep=? AND v.ano=(SELECT MAX(ano) FROM infra_valores WHERE inep=?)
      ORDER BY a.coluna_origem''',(inep,inep)).fetchall()
    return [dict(name=r['nome'],value=clean(r['valor']),column=r['coluna_origem'],year=r['ano']) for r in rs]


def infrastructure_groups(attrs):
    groups=defaultdict(list)
    for a in attrs:
        col=a['column'];n=normalized(a['name'])
        if col <= 22:cat='01 • Funcionamento e serviços essenciais'
        elif 23 <= col <= 29:cat='02 • Ambientes administrativos e alimentação'
        elif 30 <= col <= 38:cat='03 • Sanitários, repouso e apoio'
        elif 39 <= col <= 45:cat='04 • Ambientes pedagógicos e AEE'
        elif 46 <= col <= 61:cat='05 • Esporte, lazer, artes e convivência'
        else:cat='06 • Acessibilidade e salas de aula'
        groups[cat].append(a)
    return groups


def infrastructure_checks(attrs):
    byname={normalized(a['name']):normalized(a['value']) for a in attrs}
    def val(search):
        for k,v in byname.items():
            if search in k:return v
        return ''
    checks=[]
    def alert(label,advice):checks.append((label,advice))
    if val('fornece agua potavel')=='nao':alert('Água potável não declarada','Conferir abastecimento, qualidade da água e registros oficiais antes de planejar a correção.')
    if 'fossa rudimentar' in val('esgotamento sanitario'):alert('Esgotamento por fossa rudimentar','Solicitar avaliação técnica do sistema sanitário e priorizar adequações conforme normas e condições locais.')
    if val('banheiro acessivel')=='nao':alert('Banheiro acessível não declarado','Avaliar acessibilidade arquitetônica e plano de adequação, inclusive demanda de estudantes e profissionais.')
    if val('sala de recursos multifuncionais')=='nao':alert('Sala de recursos AEE não declarada','Conferir a oferta de AEE e a infraestrutura efetiva; não presumir ausência de atendimento.')
    if 'queima' in val('destinacao do lixo'):alert('Queima de resíduos declarada','Avaliar alternativas de destinação e coleta com a gestão local e responsáveis ambientais.')
    if val('biblioteca')=='nao' and val('sala de leitura')=='nao':alert('Sem biblioteca ou sala de leitura declaradas','Avaliar ampliação de acervo, espaços e práticas de leitura, respeitando as condições da unidade.')
    if val('fonte de energia eletrica') in ('nao existe','nenhuma','nao ha'):alert('Energia elétrica não declarada','Conferir condições de fornecimento e priorizar continuidade dos serviços.')
    if val('situacao de funcionamento')=='em atividade' and val('situacao de fechamento')=='fechada':
        alert('Informações potencialmente divergentes sobre funcionamento','Confirmar se campos se referem ao mesmo período e, se necessário, revisar o cadastro original.')
    return checks


def school_transport(conn, inep, year=2026):
    """Um registro por aluno e escola/ano, priorizando o alerta em caso de vínculos conflitantes.
    Conserva os valores originais, sem inferir zona pela localização da escola.
    """
    sql='''SELECT m.student_id AS id, a.nome_referencia AS nome, a.nascimento_referencia AS nascimento,
      m.inep,e.nome AS escola,m.ano,m.transporte,m.zona_residencia,
      t.nome AS turma,ep.descricao AS etapa,m.codigo_matricula
      FROM matriculas m JOIN alunos a ON a.id_unico=m.student_id
      JOIN escolas e ON e.inep=m.inep
      LEFT JOIN turmas t ON t.id=m.turma_id LEFT JOIN etapas ep ON ep.id=m.etapa_id
      WHERE m.ano=? AND (? IS NULL OR m.inep=?)
      ORDER BY a.nome_referencia,m.student_id'''
    rows=conn.execute(sql,(year,inep,inep)).fetchall()
    dedup=defaultdict(list)
    for r in rows:dedup[(str(r['id']),r['inep'])].append(r)
    result=[]
    for (sid,school),rr in dedup.items():
        transports={normalized(r['transporte']) for r in rr if clean(r['transporte'])}
        zones={normalized(r['zona_residencia']) for r in rr if clean(r['zona_residencia'])}
        use=('sim' in transports)
        if not use: continue
        if zones=={'urbana'}:cls='ALERTA: RESIDÊNCIA URBANA'
        elif zones=={'rural'}:cls='RURAL INFORMADA'
        elif not zones:cls='VERIFICAR: ZONA AUSENTE'
        else:cls='VERIFICAR: ZONA DIVERGENTE'
        first=rr[0]
        result.append({
          'id':sid,'nome':first['nome'],'nascimento':first['nascimento'],
          'idade':idade_ano_corrente(first['nascimento']), 'inep':school,'escola':first['escola'],
          'ano':year,'transporte':' / '.join(sorted({clean(r['transporte']) for r in rr if clean(r['transporte'])})),
          'zona':' / '.join(sorted({clean(r['zona_residencia']) for r in rr if clean(r['zona_residencia'])})),
          'turmas':' / '.join(sorted({clean(r['turma']) for r in rr if clean(r['turma'])})),
          'etapas':' / '.join(sorted({clean(r['etapa']) for r in rr if clean(r['etapa'])})),
          'matriculas':len(rr),'situacao':cls,
          'providencia':('Conferir endereço do estudante, comprovante e declaração de residência; validar necessidade e atendimento; corrigir no Censo somente após comprovação e conforme regras do sistema.' if cls.startswith('ALERTA') else
                        'Conferir zona de residência e vínculos no cadastro original antes de ajustar dados.' if cls.startswith('VERIFICAR') else
                        'Registro compatível com recorte rural declarado; conferir documentação e atendimento.'),
        })
    return sorted(result,key=lambda a:(0 if a['situacao'].startswith('ALERTA') else 1 if a['situacao'].startswith('VERIFICAR') else 2,a['nome'],a['id']))


def transport_network(conn,year=2026):
    all_data=[]
    for r in conn.execute('SELECT DISTINCT inep FROM matriculas WHERE ano=? ORDER BY inep',(year,)):
        all_data.extend(school_transport(conn,r[0],year))
    return all_data


TRANSPORT_HEADERS=['Ano','INEP','Escola','ID único','Nome do estudante','Data de nascimento','Idade','Usa transporte','Zona da residência','Turmas','Etapas','Vínculos','Classificação','Providência orientada']

def record_as_list(row):
    return [row[k] for k in ('ano','inep','escola','id','nome','nascimento','idade','transporte','zona','turmas','etapas','matriculas','situacao','providencia')]


def export_transport_csv(db,outfile,year=2026,inep=None,only_alerts=False,zone_filter=None):
    conn=safe_sqlite(db)
    try:
        records=school_transport(conn,inep,year) if inep else transport_network(conn,year)
    finally:conn.close()
    if only_alerts:records=[r for r in records if r['situacao'].startswith(('ALERTA','VERIFICAR'))]
    if zone_filter=='urbana':records=[r for r in records if r['situacao'].startswith('ALERTA')]
    elif zone_filter=='rural':records=[r for r in records if r['situacao']=='RURAL INFORMADA']
    Path(outfile).parent.mkdir(parents=True,exist_ok=True)
    with open(outfile,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f,delimiter=';');w.writerow(TRANSPORT_HEADERS)
        w.writerows(record_as_list(r) for r in records)
    return len(records),outfile


def export_transport_pdf(db,outfile,year=2026,inep=None,only_alerts=False,zone_filter=None):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape,A3
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate,Table,TableStyle,Paragraph,Spacer,KeepTogether
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from xml.sax.saxutils import escape
    from relatorios_escolas import FONT,FONT_B
    c=safe_sqlite(db)
    try:data=school_transport(c,inep,year) if inep else transport_network(c,year)
    finally:c.close()
    if only_alerts:data=[x for x in data if x['situacao'].startswith(('ALERTA','VERIFICAR'))]
    if zone_filter=='urbana':data=[r for r in data if r['situacao'].startswith('ALERTA')]
    elif zone_filter=='rural':data=[r for r in data if r['situacao']=='RURAL INFORMADA']
    Path(outfile).parent.mkdir(parents=True,exist_ok=True)
    doc=SimpleDocTemplate(str(outfile),pagesize=landscape(A3),leftMargin=33,rightMargin=33,topMargin=44,bottomMargin=38)
    title=ParagraphStyle('tr_title',fontName=FONT_B,fontSize=16,leading=22,textColor=colors.HexColor('#17375B'),spaceAfter=11)
    info=ParagraphStyle('tr_info',fontName=FONT,fontSize=9,leading=13,spaceAfter=10)
    cell=ParagraphStyle('tr_cell',fontName=FONT,fontSize=8.2,leading=11)
    head=ParagraphStyle('tr_head',fontName=FONT_B,fontSize=8.1,leading=10,textColor=colors.white)
    story=[Paragraph('SEMEC | AUDITORIA NOMINAL DO TRANSPORTE ESCOLAR',title),
           Paragraph(f'Ano: {year} • Escola: {escape(str(inep or "Toda a rede"))} • Estudantes usuários do transporte, deduplicados por ID e escola: {len(data)}',info),
           Paragraph('ALERTA: estudante usuário do transporte com residência declarada URBANA. Solicitar conferência documental, sem alteração automática da zona de residência. O PNATE federal toma como referência estudantes da educação básica pública residentes em área rural que utilizam transporte. A categoria urbana não representa, isoladamente, fraude nem impede todo financiamento ou direito ao transporte.',info)]
    headers=['ID único','Nome do estudante','Idade','Escola / INEP','Transporte','Zona da residência','Turma(s)','Classificação / providência']
    widths=[95,180,42,154,77,81,160,landscape(A3)[0]-66-95-180-42-154-77-81-160]
    rows=[[Paragraph(escape(s),head) for s in headers]]
    for r in data:
        values=[r['id'],r['nome'],r['idade'] or '',f"{r['escola']} / {r['inep']}",r['transporte'],r['zona'],r['turmas'],r['situacao']+' — '+r['providencia']]
        rows.append([Paragraph(escape(str(v or '—')),cell) for v in values])
    tab=Table(rows,colWidths=widths,repeatRows=1,splitByRow=1)
    styling=[('BACKGROUND',(0,0),(-1,0),colors.HexColor('#17375B')),('VALIGN',(0,0),(-1,-1),'TOP'),
      ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),
      ('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7),
      ('LINEBELOW',(0,0),(-1,-1),.4,colors.HexColor('#D8E1EC'))]
    for i,r in enumerate(data,1):
        if r['situacao'].startswith('ALERTA'):color='#FFF0E7'
        elif r['situacao'].startswith('VERIFICAR'):color='#FFF9E5'
        else:color='#F5F9FC' if i%2==0 else '#FFFFFF'
        styling.append(('BACKGROUND',(0,i),(-1,i),colors.HexColor(color)))
    tab.setStyle(TableStyle(styling));story.append(tab)
    story.append(Spacer(1,13))
    story.append(Paragraph('Créditos: Departamento de Tecnologia da Informação e Conectividade — SEMEC | dticsemec@gmail.com. Dados tratados com base nas informações do Inep/Censo Escolar. Uso administrativo restrito; validar na fonte antes de correções cadastrais.',info))
    doc.build(story)
    return len(data),str(outfile)
