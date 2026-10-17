"""Importação Educacenso e relatórios — standard library + SQLite, sem dependências externas.
Tipos e anos identificados no conteúdo, não inferidos apenas do nome do arquivo.
"""
from __future__ import annotations
import csv, hashlib, io, os, re, sqlite3, unicodedata, zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from itertools import chain
from xlsx_xml import read_sheets, numeric_identifier

BASE=Path(__file__).resolve().parent
SCHEMA=BASE/'schema.sql'
VALID_KINDS={'MATRICULA','RESULTADO','INFRAESTRUTURA'}

def norm(s):
    s=unicodedata.normalize('NFKD',str(s or ''))
    return re.sub(r'\s+',' ',''.join(x for x in s if not unicodedata.combining(x)).casefold()).strip()

def normkey(s):return re.sub(r'[^a-z0-9]','',norm(s))

def blank(s):
    x=str(s or '').strip()
    return None if x in ('','--','-','N/A','n/a','Não se aplica') else x

def code(s):
    t=numeric_identifier(s)
    return t if t.isdecimal() and 5<=len(t)<=20 else None

def inep_code(s):
    t=numeric_identifier(s)
    return t if len(t)==8 and t.isdigit() else None

def header_mapping(row):
    return {normkey(v):i for i,v in row.items()}

def ix(mapping,*choices):
    for c in choices:
        c=normkey(c)
        if c in mapping:return mapping[c]
    return None

def cell(row,m,*choices):
    pos=ix(m,*choices)
    return blank(row.get(pos)) if pos is not None else None

def find_year(first):
    matches=[]
    for _,row in first:
        for v in row.values():
            m=re.search(r'Censo\s+Escolar\s+(20\d\d)',v,re.I)
            if m:matches.append(int(m.group(1)))
    return matches[0] if matches else None

def find_school_info(first):
    info={}
    keys={'codigo da escola':'inep','nome da escola':'nome','municipio':'municipio','uf':'uf',
          'localizacao':'localizacao','dependencia administrativa':'dependencia', 'codigo entidade':'inep'}
    for _,r in first:
        for col,val in r.items():
            key=norm(val).rstrip(':').strip()
            if key not in keys:continue
            tail=[v for c,v in sorted(r.items()) if c>col and str(v).strip()]
            if tail and keys[key] not in info: info[keys[key]]=tail[0]
    if 'inep' in info:info['inep']=inep_code(info['inep'])
    return info

def find_data_header(first,kind):
    for num,row in first:
        m=header_mapping(row)
        if kind=='INFRAESTRUTURA':
            if ix(m,'Código da escola') is not None and ix(m,'Situação de Funcionamento') is not None and ix(m,'Nome da escola') is not None:return num,m,row
        elif kind=='RESULTADO':
            if ix(m,'Identificação única') is not None and ix(m,'Situação do aluno(a)','Situação do aluno') is not None:return num,m,row
        else:
            if ix(m,'Identificação única') is not None and ix(m,'Nome','Nome do aluno(a)') is not None and ix(m,'Data de nascimento') is not None:return num,m,row
    return None,None,None

def kind_from_name(name):
    n=normkey(os.path.basename(name))
    if 'relacaosituacao' in n:return 'RESULTADO'
    if 'relacaoalunoescola' in n or 'relacaodealunos' in n:return 'MATRICULA'
    if 'aspectosinfraestrutura' in n:return 'INFRAESTRUTURA'
    return None

def safe_sqlite(path):
    c=sqlite3.connect(str(path),timeout=30)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=30000')
    c.executescript(SCHEMA.read_text(encoding='utf-8'))
    if 'etapa_origem' not in [r[1] for r in c.execute('PRAGMA table_info(matriculas)')]:
        c.execute("ALTER TABLE matriculas ADD COLUMN etapa_origem TEXT NOT NULL DEFAULT 'NAO_INFORMADA'")
        c.commit()
    return c

def issue(c,batch,path,row,level,key,details):
    c.execute('INSERT INTO import_issues(batch_id,source_path,row_number,severity,code,detail) VALUES(?,?,?,?,?,?)',
              (batch,path,row,level,key,details[:1500]))

def ensure_escola(c,inep,nome,meta=None):
    meta=meta or {}
    name=blank(nome) or f'Escola INEP {inep}'
    c.execute('''INSERT INTO escolas(inep,nome,municipio,uf,localizacao,dependencia)
                 VALUES(?,?,?,?,?,?) ON CONFLICT(inep) DO UPDATE SET
                 nome=CASE WHEN excluded.nome NOT LIKE 'Escola INEP %' THEN excluded.nome ELSE escolas.nome END,
                 municipio=COALESCE(excluded.municipio,escolas.municipio),
                 uf=COALESCE(excluded.uf,escolas.uf),
                 localizacao=COALESCE(excluded.localizacao,escolas.localizacao),
                 dependencia=COALESCE(excluded.dependencia,escolas.dependencia)''',
              (inep,name,meta.get('municipio'),meta.get('uf'),meta.get('localizacao'),meta.get('dependencia')))

def ensure_aluno(c,id_unico,nome,nascimento,batch,path,row):
    current=c.execute('SELECT nome_referencia,nascimento_referencia FROM alunos WHERE id_unico=?',(id_unico,)).fetchone()
    if not current:
        c.execute('INSERT INTO alunos(id_unico,nome_referencia,nascimento_referencia) VALUES(?,?,?)', (id_unico,nome or 'SEM NOME',nascimento))
    else:
        if nome and norm(current['nome_referencia']) != norm(nome):
            issue(c,batch,path,row,'WARNING','NOME_DIVERGENTE',f'ID {id_unico}: {current["nome_referencia"]} <> {nome}')
        if nascimento and current['nascimento_referencia'] and nascimento != current['nascimento_referencia']:
            issue(c,batch,path,row,'WARNING','NASCIMENTO_DIVERGENTE',f'ID {id_unico}: {current["nascimento_referencia"]} <> {nascimento}')
        if nascimento and not current['nascimento_referencia']:
            c.execute('UPDATE alunos SET nascimento_referencia=? WHERE id_unico=?',(nascimento,id_unico))

def ensure_turma(c,school,year,turma_code,name):
    if not turma_code:return None
    c.execute('INSERT INTO turmas(inep,ano,codigo,nome) VALUES(?,?,?,?) ON CONFLICT(inep,ano,codigo) DO UPDATE SET nome=COALESCE(excluded.nome,turmas.nome)',
              (school,year,turma_code,name))
    return c.execute('SELECT id FROM turmas WHERE inep=? AND ano=? AND codigo=?',(school,year,turma_code)).fetchone()[0]

def ensure_etapa(c,txt):
    if not txt:return None
    c.execute('INSERT OR IGNORE INTO etapas(descricao) VALUES(?)',(txt,))
    return c.execute('SELECT id FROM etapas WHERE descricao=?',(txt,)).fetchone()[0]

MULTI_ETAPA_HEADER = 'Etapa de vínculo do(a) aluno(a) (Para turmas do tipo multi)'

def etapa_individual(row, m):
    """Escolhe etapa por cabeçalho, não por posição fixa. Retorna etapa, origem e alerta."""
    col_multi = ix(m, MULTI_ETAPA_HEADER)
    col_geral = ix(m, 'Etapa de ensino')
    etapa_multi = blank(row.get(col_multi)) if col_multi is not None else None
    etapa_geral = blank(row.get(col_geral)) if col_geral is not None else None
    if etapa_multi:
        return etapa_multi, 'VINCULO_MULTI', ('DIVERGENCIA_ETAPA_TURMA' if etapa_geral and norm(etapa_multi)!=norm(etapa_geral) else None)
    return etapa_geral, 'ETAPA_TURMA' if etapa_geral else 'NAO_INFORMADA', None

def categoria(s):
    n=norm(s)
    if 'reprov' in n:return 'REPROVADO'
    if 'deixou de frequentar' in n or 'abandono' in n:return 'DEIXOU_DE_FREQUENTAR'
    if 'transfer' in n:return 'TRANSFERIDO'
    if 'aprov' in n:return 'APROVADO'
    return 'OUTRO'

def ensure_situacao(c,txt):
    txt=blank(txt) or 'Não informada'
    c.execute('INSERT OR IGNORE INTO situacoes(descricao,categoria) VALUES(?,?)',(txt,categoria(txt)))
    return c.execute('SELECT id FROM situacoes WHERE descricao=?',(txt,)).fetchone()[0]

def process_workbook(c,batch,logical_path,data,kind=None):
    kind=kind or kind_from_name(logical_path)
    if kind not in VALID_KINDS:return (0,'IGNORED')
    datahash=hashlib.sha256(data).hexdigest()
    selected=None
    try:
        for sheetname,rows_gen in read_sheets(data):
            it=iter(rows_gen)
            first=[]
            for _ in range(65):
                try:first.append(next(it))
                except StopIteration:break
            header_num,m,h=find_data_header(first,kind)
            if header_num is not None:
                selected=(sheetname,header_num,m,h,first,it)
                break
    except Exception as e:
        issue(c,batch,logical_path,None,'ERROR','XLSX_INVALIDO',str(e))
        return (0,'ERROR')
    if not selected:
        issue(c,batch,logical_path,None,'ERROR','LAYOUT_DESCONHECIDO',f'Não identificado cabeçalho do modelo {kind} nas abas')
        return (0,'ERROR')
    sheet,header_num,m,header,first,it=selected
    year=find_year(first)
    school_info=find_school_info(first)
    school=school_info.get('inep')
    if kind=='INFRAESTRUTURA':
        if year is None:
            issue(c,batch,logical_path,None,'ERROR','ANO_AUSENTE','Cabeçalho Censo Escolar não encontrado');return (0,'ERROR')
    else:
        expected_year=2025 if kind=='RESULTADO' or ('relacaodealunos' in normkey(os.path.basename(logical_path))) else 2026
        if year != expected_year:
            issue(c,batch,logical_path,None,'ERROR','ANO_INCOMPATIVEL',f'Ano do cabeçalho={year}, esperado={expected_year}; planilha rejeitada')
            return (0,'ERROR')
        if not school:
            issue(c,batch,logical_path,None,'ERROR','INEP_INVALIDO','Código INEP ausente ou inválido no cabeçalho');return (0,'ERROR')
        match=re.search(r'Relação_de_alunos_(\d{8})',os.path.basename(logical_path),re.I)
        if match and match.group(1)!=school:
            issue(c,batch,logical_path,None,'ERROR','INEP_DIVERGENTE',f'Nome do arquivo diz {match.group(1)}; cabeçalho diz {school}; rejeitado')
            return (0,'ERROR')
    if kind!='INFRAESTRUTURA':
        ensure_escola(c,school,school_info.get('nome'),school_info)
    previous=c.execute('SELECT id,sha256 FROM sources WHERE kind=? AND school_inep IS ? AND school_year=?', (kind,school,year)).fetchone()
    if previous and previous['sha256']==datahash:
        issue(c,batch,logical_path,None,'INFO','JA_IMPORTADO','Arquivo de conteúdo idêntico já incorporado, sem duplicar registros')
        return (0,'SKIPPED')
    if previous:
        c.execute('DELETE FROM sources WHERE id=?',(previous['id'],))
        issue(c,batch,logical_path,None,'INFO','FONTE_ATUALIZADA','Fonte antiga substituída com exclusão em cascata de vínculos da fonte')
    cur=c.execute('INSERT INTO sources(batch_id,logical_path,sha256,kind,school_inep,school_year,sheet_name,header_row) VALUES(?,?,?,?,?,?,?,?)',
                  (batch,logical_path,datahash,kind,school,year,sheet,header_num))
    source_id=cur.lastrowid
    if kind!='INFRAESTRUTURA':
        c.execute('''INSERT OR REPLACE INTO escola_observacoes(inep,ano,source_id,nome,municipio,uf,localizacao,dependencia) VALUES(?,?,?,?,?,?,?,?)''',
                  (school,year,source_id,school_info.get('nome') or f'Escola {school}',school_info.get('municipio'),school_info.get('uf'),school_info.get('localizacao'),school_info.get('dependencia')))
    count=0
    for rownum,row in chain(first,it):
        if rownum<=header_num:continue
        if kind=='INFRAESTRUTURA':
            inep=inep_code(cell(row,m,'Código da escola'))
            if not inep:continue
            ensure_escola(c,inep,cell(row,m,'Nome da escola'),{'municipio':cell(row,m,'Município')})
            for col,title in sorted(header.items()):
                if normkey(title) in ('ordem','codigodaescola','nomedaescola'):continue
                if not title.strip():continue
                c.execute('INSERT OR IGNORE INTO infra_atributos(nome,coluna_origem) VALUES(?,?)',(title,col+1))
                aid=c.execute('SELECT id FROM infra_atributos WHERE nome=?',(title,)).fetchone()[0]
                c.execute('INSERT INTO infra_valores(inep,ano,atributo_id,valor,source_id) VALUES(?,?,?,?,?) ON CONFLICT(inep,ano,atributo_id) DO UPDATE SET valor=excluded.valor,source_id=excluded.source_id',
                          (inep,year,aid,blank(row.get(col)),source_id))
            count+=1
            continue
        id_unico=code(cell(row,m,'Identificação única'))
        nome=cell(row,m,'Nome','Nome do(a) Aluno(a)','Nome do aluno(a)')
        if not id_unico:
            # linhas de rodapé ou cabeçalho não constituem alunos
            if nome and re.match(r'^\d',str(row.get(0,''))):issue(c,batch,logical_path,rownum,'WARNING','ID_INVALIDO','Registro sem identificação única válida')
            continue
        if not nome:
            issue(c,batch,logical_path,rownum,'WARNING','NOME_AUSENTE',f'ID {id_unico}: nome ausente')
            nome='SEM NOME'
        nascimento=cell(row,m,'Data de nascimento')
        ensure_aluno(c,id_unico,nome,nascimento,batch,logical_path,rownum)
        if kind=='MATRICULA':
            etapa,origem_etapa,divergencia=etapa_individual(row,m)
            if divergencia:
                issue(c,batch,logical_path,rownum,'INFO',divergencia,
                      f'ID {id_unico}: etapa individual da turma multi = {etapa}; etapa geral da turma = {cell(row,m,"Etapa de ensino")}. Priorizada a individual.')
            if origem_etapa=='NAO_INFORMADA':
                issue(c,batch,logical_path,rownum,'WARNING','ETAPA_NAO_INFORMADA',f'ID {id_unico}: sem etapa individual ou geral')
            turma_code=code(cell(row,m,'Código da turma','Cód. Turma'))
            turma_nome=cell(row,m,'Nome da turma')
            matricula=code(cell(row,m,'Código da Matrícula')) or f'SEM_COD:{id_unico}:{turma_code or rownum}'
            tid=ensure_turma(c,school,year,turma_code,turma_nome)
            eid=ensure_etapa(c,etapa)
            try:
                c.execute('''INSERT INTO matriculas(student_id,inep,ano,turma_id,etapa_id,codigo_matricula,transporte,zona_residencia,source_id,row_number,etapa_origem)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                          (id_unico,school,year,tid,eid,matricula,cell(row,m,'Transporte escolar (Sim/Não)'),cell(row,m,'Localização/Zona de residência'),source_id,rownum,origem_etapa))
            except sqlite3.IntegrityError:
                issue(c,batch,logical_path,rownum,'WARNING','MATRICULA_DUPLICADA',f'Matrícula {matricula} repetida na escola {school}, ano {year}')
                continue
            c.execute('''INSERT INTO aluno_observacoes(student_id,source_id,row_number,ano,escola_inep,nome,nascimento,sexo,cor_raca,cpf,nacionalidade)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                      (id_unico,source_id,rownum,year,school,nome,nascimento,cell(row,m,'Sexo'),cell(row,m,'Cor/Raça'),cell(row,m,'CPF'),cell(row,m,'Nacionalidade')))
        else:
            turma_code=code(cell(row,m,'Cód. Turma','Código da turma'))
            tid=ensure_turma(c,school,year,turma_code,cell(row,m,'Nome da turma'))
            eid=ensure_etapa(c,cell(row,m,'Etapa','Etapa de ensino'))
            situation=ensure_situacao(c,cell(row,m,'Situação do aluno(a)','Situação do aluno'))
            c.execute('''INSERT INTO resultados(student_id,inep,ano,turma_id,etapa_id,situacao_id,admitido,concluinte,source_id,row_number)
                         VALUES(?,?,?,?,?,?,?,?,?,?)''',
                      (id_unico,school,year,tid,eid,situation,cell(row,m,'Admitido (a)'),cell(row,m,'Concluinte'),source_id,rownum))
        count+=1
    c.execute('UPDATE sources SET records=? WHERE id=?',(count,source_id))
    if count==0:
        issue(c,batch,logical_path,None,'WARNING','SEM_REGISTROS','Arquivo com estrutura válida, mas sem registros de alunos/escolas')
    return count,'DONE'

def import_zip(db_path,zip_path,log=None):
    c=safe_sqlite(db_path)
    stats=Counter()
    with c:
        batch=c.execute('INSERT INTO import_batches(source_name,status) VALUES(?,?)',(Path(zip_path).name,'RUNNING')).lastrowid
    try:
        with zipfile.ZipFile(zip_path) as bundle:
            members=[i for i in bundle.infolist() if not i.is_dir()]
            paths=[i for i in members if i.filename.lower().endswith('.xlsx') and kind_from_name(i.filename)]
            with c:c.execute('UPDATE import_batches SET files_detected=? WHERE id=?',(len(paths),batch))
            schools=set()
            for item in members:
                n=item.filename.replace('\\','/')
                if '/' in n and n.split('/')[0] not in ('__MACOSX','Nova pasta') and kind_from_name(n):schools.add(n.split('/')[0])
            if log:log(f'{len(schools)} pastas reconhecidas; {len(paths)} planilhas elegíveis.')
            for entry in paths:
                path=entry.filename
                quantity,status=0,'ERROR'
                with c:
                    c.execute('SAVEPOINT arquivo')
                    try:
                        if entry.file_size>150_000_000:raise ValueError('Arquivo XLSX excede limite de segurança de 150 MB')
                        data=bundle.read(entry)
                        quantity,status=process_workbook(c,batch,path,data)
                        stats[status]+=1
                        stats['records']+=quantity
                        c.execute('RELEASE arquivo')
                    except Exception as e:
                        c.execute('ROLLBACK TO arquivo');c.execute('RELEASE arquivo')
                        issue(c,batch,path,None,'ERROR','FALHA_PROCESSAMENTO',str(e))
                        stats['ERROR']+=1
                if log:log(f'{status if "status" in locals() else "ERROR"}: {path} ({quantity if "quantity" in locals() else 0})')
            state='PARTIAL' if stats['ERROR'] else 'DONE'
            with c:c.execute('''UPDATE import_batches SET finished_at=CURRENT_TIMESTAMP,status=?,files_imported=?,records_imported=?,notes=? WHERE id=?''',
                             (state,stats['DONE'],stats['records'],f'{len(schools)} pastas; {stats["SKIPPED"]} fontes já importadas; {stats["ERROR"]} erros',batch))
    except Exception:
        with c:c.execute('UPDATE import_batches SET status=?,finished_at=CURRENT_TIMESTAMP WHERE id=?',('FAILED',batch))
        c.close();raise
    c.close()
    return batch,dict(stats)

def import_infra(db_path,file_path,log=None):
    c=safe_sqlite(db_path)
    with c:
        batch=c.execute('INSERT INTO import_batches(source_name,status,files_detected) VALUES(?,?,1)',(Path(file_path).name,'RUNNING')).lastrowid
        try:
            with open(file_path,'rb') as f:quantity,status=process_workbook(c,batch,Path(file_path).name,f.read(),'INFRAESTRUTURA')
            c.execute('UPDATE import_batches SET finished_at=CURRENT_TIMESTAMP,status=?,files_imported=?,records_imported=? WHERE id=?',
                      ('DONE' if status!='ERROR' else 'PARTIAL',int(status=='DONE'),quantity,batch))
        except Exception as e:
            issue(c,batch,str(file_path),None,'ERROR','ERRO_INFRA',str(e))
            c.execute('UPDATE import_batches SET finished_at=CURRENT_TIMESTAMP,status=? WHERE id=?',('FAILED',batch))
            raise
    c.close()
    if log:log(f'Infraestrutura: {quantity} escolas; {status}')
    return batch,quantity,status

def idade_ano_corrente(nascimento, referencia=None):
    """Idade em anos completos na data atual; devolve vazio para nascimento inválido.

    Não armazena idade no cadastro: recalcula em cada consulta/exportação.
    """
    from datetime import date, datetime
    if referencia is None:
        referencia = date.today()
    if not nascimento:
        return ''
    if isinstance(nascimento, datetime):
        nascimento = nascimento.date()
    if not isinstance(nascimento, date):
        raw = str(nascimento).strip().split(' ')[0]
        parsed = None
        for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%Y/%m/%d'):
            try:
                parsed = datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                continue
        nascimento = parsed
    if nascimento is None or nascimento > referencia or nascimento.year < 1900:
        return ''
    return referencia.year - nascimento.year - ((referencia.month, referencia.day) < (nascimento.month, nascimento.day))


REPORT_COLS=[
 'Prioridade','ID único','Aluno','Nascimento','Sexo 2025','Raça/cor 2025','Raça/cor 2026',
 'INEP 2025','Escola 2025','Turma 2025','Etapa 2025','Resultado 2025',
 'Localização 2026','INEP 2026','Escola(s) 2026','Turma(s) 2026','Etapa(s) 2026',
 'Matrículas 2026 (qtd)','Observação gerencial','Idade (anos)']

def report_rows(c):
    # Resultados de 2025 são o ponto central; inclui vínculos 2025 sem resultado em relatório próprio.
    statuses=c.execute('''SELECT r.id,r.student_id,r.inep, e.nome AS escola, a.nome_referencia,a.nascimento_referencia,
        s.descricao,s.categoria,et.descricao AS etapa,t.nome AS turma
        FROM resultados r JOIN alunos a ON a.id_unico=r.student_id
        JOIN escolas e ON e.inep=r.inep JOIN situacoes s ON s.id=r.situacao_id
        LEFT JOIN etapas et ON et.id=r.etapa_id LEFT JOIN turmas t ON t.id=r.turma_id
        WHERE r.ano=2025 ORDER BY e.nome,a.nome_referencia,r.id''').fetchall()
    enrollment_26=defaultdict(list)
    for r in c.execute('''SELECT m.student_id,m.inep,e.nome AS escola,et.descricao AS etapa,t.nome AS turma
        FROM matriculas m JOIN escolas e ON e.inep=m.inep
        LEFT JOIN etapas et ON et.id=m.etapa_id LEFT JOIN turmas t ON t.id=m.turma_id WHERE m.ano=2026'''):
        enrollment_26[r['student_id']].append(r)
    obs25={}
    obs26={}
    for ob in c.execute('''SELECT student_id,ano,escola_inep,nome,nascimento,cor_raca,sexo
        FROM aluno_observacoes WHERE ano IN (2025,2026) ORDER BY source_id,row_number'''):
        target=obs25 if ob['ano']==2025 else obs26
        target.setdefault((ob['student_id'],ob['escola_inep']),ob)
    entries=[]
    for r in statuses:
        matches=enrollment_26[r['student_id']]
        schools={x['inep'] for x in matches}
        if not matches:location='NÃO LOCALIZADO NA REDE IMPORTADA'
        elif len(schools)>1:location='MÚLTIPLAS ESCOLAS EM 2026'
        elif r['inep'] in schools:location='MESMA ESCOLA'
        else:location='OUTRA ESCOLA DA REDE'
        cat=r['categoria']
        if cat=='DEIXOU_DE_FREQUENTAR': priority='CRÍTICA' if not matches else 'RETORNOU'
        elif cat=='REPROVADO':priority='ALTA' if not matches else 'ACOMPANHAR'
        elif cat=='TRANSFERIDO':priority='VERIFICAR DESTINO' if not matches else 'LOCALIZADO'
        elif not matches:priority='VERIFICAR'
        else:priority='ROTINA'
        origin=obs25.get((r['student_id'],r['inep']))
        dest=next((obs26.get((r['student_id'],x['inep'])) for x in matches if obs26.get((r['student_id'],x['inep']))),None)
        note=('Localização restrita às escolas importadas; não comprova evasão.' if not matches else
              'Aluno aparece em mais de uma escola em 2026; conferir vínculos.' if len(schools)>1 else
              'Mudança entre escolas detectada pelo ID; data de transferência não comprovada.' if location=='OUTRA ESCOLA DA REDE' else
              'Matrícula de 2026 localizada na escola de origem.')
        def joined(k):return ' | '.join(sorted({str(x[k]) for x in matches if x[k]}))
        entries.append([
            priority,r['student_id'], origin['nome'] if origin else r['nome_referencia'],
            origin['nascimento'] if origin and origin['nascimento'] else r['nascimento_referencia'],
            origin['sexo'] if origin else None,origin['cor_raca'] if origin else None,dest['cor_raca'] if dest else None,
            r['inep'],r['escola'],r['turma'],r['etapa'],r['descricao'],location,
            joined('inep'),joined('escola'),joined('turma'),joined('etapa'),len(matches),note,
            idade_ano_corrente(origin['nascimento'] if origin and origin['nascimento'] else r['nascimento_referencia'])
        ])
    return entries

def export_csv(db_path,dest,filter_categories=None):
    c=safe_sqlite(db_path)
    rows=report_rows(c)
    if filter_categories:
        terms={norm(t) for t in filter_categories}
        rows=[r for r in rows if any(t in norm(r[11]) for t in terms)]
    with open(dest,'w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f,delimiter=';');w.writerow(REPORT_COLS);w.writerows(rows)
    c.close()
    return len(rows)

def export_audit(c,directory):
    os.makedirs(directory,exist_ok=True)
    for tab,sql,columns in [
      ('auditoria_importacao.csv', '''SELECT b.id,b.source_name,b.status,s.logical_path,s.kind,s.school_inep,s.school_year,s.records FROM import_batches b LEFT JOIN sources s ON s.batch_id=b.id ORDER BY b.id,s.logical_path''',
       ['lote','arquivo_zip','status','caminho','tipo','INEP','ano','registros']),
      ('inconsistencias.csv','SELECT batch_id,source_path,row_number,severity,code,detail FROM import_issues ORDER BY id',
       ['lote','arquivo','linha','severidade','código','detalhe']),
      ('infraestrutura.csv','''SELECT iv.inep,e.nome,iv.ano,ia.nome,iv.valor FROM infra_valores iv JOIN escolas e ON e.inep=iv.inep JOIN infra_atributos ia ON ia.id=iv.atributo_id ORDER BY e.nome,ia.coluna_origem''',
       ['INEP','escola','ano','indicador','valor']),
      ('matriculas_2025_sem_resultado.csv','''SELECT m.student_id,a.nome_referencia,m.inep,e.nome,m.ano FROM matriculas m JOIN alunos a ON a.id_unico=m.student_id JOIN escolas e ON e.inep=m.inep WHERE m.ano=2025 AND NOT EXISTS(SELECT 1 FROM resultados r WHERE r.student_id=m.student_id AND r.inep=m.inep AND r.ano=2025) ORDER BY e.nome,a.nome_referencia''',
       ['ID único','nome','INEP','escola','ano'])]:
        with open(Path(directory)/tab,'w',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f,delimiter=';');w.writerow(columns)
            for row in c.execute(sql):w.writerow(list(row))

def export_all(db_path,output_dir):
    output=Path(output_dir);output.mkdir(parents=True,exist_ok=True)
    c=safe_sqlite(db_path)
    data=report_rows(c)
    def write(name,filtered):
        with open(output/name,'w',newline='',encoding='utf-8-sig') as f:
            w=csv.writer(f,delimiter=';');w.writerow(REPORT_COLS);w.writerows(filtered)
    write('01_fluxo_geral_2025_2026.csv',data)
    write('02_busca_ativa_nao_localizados.csv',[r for r in data if r[0] in ('CRÍTICA','ALTA')])
    write('03_deixaram_de_frequentar.csv',[r for r in data if 'deixou de frequentar' in norm(r[11])])
    write('04_reprovados.csv',[r for r in data if 'reprovado' in norm(r[11])])
    write('05_transferidos.csv',[r for r in data if 'transferido' in norm(r[11])])
    write('06_mobilidade_interna_2026.csv',[r for r in data if r[12]=='OUTRA ESCOLA DA REDE'])
    write('07_multiplas_matriculas_2026.csv',[r for r in data if r[12]=='MÚLTIPLAS ESCOLAS EM 2026'])
    # Resumo por INEP de origem e comparação entre escolas de 2026.
    byschool=defaultdict(list)
    for r in data:byschool[(r[7],r[8])].append(r)
    with open(output/'08_resumo_por_escola.csv','w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f,delimiter=';')
        w.writerow(['INEP','Escola','Situações 2025','Alunos únicos','Deixaram de frequentar','Reprovados','Transferidos 2025',
                    'Mesmo INEP 2026','Outro INEP 2026','Não localizados','Múltiplas escolas 2026','Busca ativa crítica'])
        for (inep,school),items in sorted(byschool.items(),key=lambda kv:kv[0][1]):
            w.writerow([inep,school,len(items),len({x[1] for x in items}),
                        sum('deixou de frequentar' in norm(x[11]) for x in items),
                        sum('reprovado' in norm(x[11]) for x in items),
                        sum('transferido' in norm(x[11]) for x in items),
                        sum(x[12]=='MESMA ESCOLA' for x in items),
                        sum(x[12]=='OUTRA ESCOLA DA REDE' for x in items),
                        sum(x[12]=='NÃO LOCALIZADO NA REDE IMPORTADA' for x in items),
                        sum(x[12]=='MÚLTIPLAS ESCOLAS EM 2026' for x in items),
                        sum(x[0]=='CRÍTICA' for x in items)])
    flux=defaultdict(set)
    for r in data:
        if r[12]=='OUTRA ESCOLA DA REDE':
            for dest_inep in r[13].split(' | '):
                if dest_inep:flux[(r[7],r[8],dest_inep,r[14])].add(r[1])
    with open(output/'09_matriz_mobilidade_entre_escolas.csv','w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f,delimiter=';')
        w.writerow(['INEP origem 2025','Escola origem 2025','INEP destino 2026','Escola destino 2026','Alunos únicos'])
        for key,ids in sorted(flux.items()):w.writerow([*key,len(ids)])
    export_audit(c,output)
    summary=Counter(r[12] for r in data)
    with open(output/'00_resumo_gerencial.txt','w',encoding='utf-8') as f:
        f.write('FLUXO ESCOLAR 2025–2026 — SANTA MARIA DAS BARREIRAS\n')
        f.write('Base central: resultado final informado em 2025; destino: matrículas de 2026 importadas.\n')
        f.write('Não localizado NÃO equivale automaticamente a evasão.\n\n')
        f.write(f'Registros de situação de 2025: {len(data)}\n')
        for key,num in summary.most_common():f.write(f'{key}: {num}\n')
        f.write('\nIndicadores referem-se a registros de situação, não necessariamente alunos únicos.\n')
    c.close()
    return len(data),summary

def dump_sql(db_path,sql_file):
    conn=safe_sqlite(db_path)
    with open(sql_file,'w',encoding='utf-8') as f:
        f.write('-- Backup SQL completo da base SQLite; dados pessoais: conservar em local restrito.\n')
        for line in conn.iterdump():f.write(line+'\n')
    conn.close()

def metrics(db_path):
    c=safe_sqlite(db_path)
    tables=['escolas','alunos','matriculas','resultados','infra_atributos','infra_valores','sources','import_issues']
    out={t:c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in tables}
    out['integridade']=c.execute('PRAGMA integrity_check').fetchone()[0]
    out['foreign_keys']=len(c.execute('PRAGMA foreign_key_check').fetchall())
    c.close();return out
