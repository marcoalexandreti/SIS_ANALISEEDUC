"""Interface de linha de comando. Exemplos no README.md."""
import argparse
from pathlib import Path
from core import safe_sqlite, import_zip, import_infra, export_all, dump_sql, metrics
from relatorios_escolas import export_school_reports
from painel_gerencial import export_management_pdf,export_filtered_pdf,export_filtered_excel

def main():
    p=argparse.ArgumentParser(description='Fluxo Escolar: banco SQLite normalizado, importação e relatórios')
    p.add_argument('--db',default=str(Path(__file__).resolve().parent/'fluxo_escolar.sqlite'),help='Caminho do banco SQLite')
    sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('iniciar',help='Cria/verifica estrutura SQLite')
    t=sub.add_parser('importar',help='Importa um ZIP de escolas em lote');t.add_argument('arquivo')
    t=sub.add_parser('infraestrutura',help='Importa XLSX de infraestrutura');t.add_argument('arquivo')
    t=sub.add_parser('relatorios',help='Gera relatórios CSV e auditoria');t.add_argument('pasta')
    t=sub.add_parser('relatorios-escolas',help='PDFs individuais por INEP (todas ou uma escola)');t.add_argument('pasta');t.add_argument('--inep',default=None)
    t=sub.add_parser('painel-pdf',help='Exporta painel gerencial consolidado em PDF');t.add_argument('arquivo')
    t=sub.add_parser('lista-pdf',help='Exporta relação nominal filtrada em PDF');t.add_argument('arquivo');t.add_argument('--inep');t.add_argument('--situacao',default='Todos');t.add_argument('--busca',default='')
    t=sub.add_parser('lista-excel',help='Exporta relação nominal filtrada em XLSX');t.add_argument('arquivo');t.add_argument('--inep');t.add_argument('--situacao',default='Todos');t.add_argument('--busca',default='')
    t=sub.add_parser('sql',help='Exporta dump SQL completo');t.add_argument('arquivo')
    sub.add_parser('validar',help='Verifica contagens, integridade e chaves estrangeiras')
    a=p.parse_args()
    Path(a.db).parent.mkdir(parents=True,exist_ok=True)
    c=safe_sqlite(a.db);c.close()
    if a.cmd=='iniciar':print('Banco inicializado:',a.db)
    elif a.cmd=='importar':print(import_zip(a.db,a.arquivo,print))
    elif a.cmd=='infraestrutura':print(import_infra(a.db,a.arquivo,print))
    elif a.cmd=='relatorios':print(export_all(a.db,a.pasta))
    elif a.cmd=='relatorios-escolas':
        num,_=export_school_reports(a.db,a.pasta,a.inep,print);print('PDFs criados:',num)
    elif a.cmd=='painel-pdf':print(export_management_pdf(a.db,a.arquivo,print))
    elif a.cmd=='lista-pdf':print(export_filtered_pdf(a.db,a.arquivo,a.inep,a.situacao,a.busca,print))
    elif a.cmd=='lista-excel':print(export_filtered_excel(a.db,a.arquivo,a.inep,a.situacao,a.busca,print))
    elif a.cmd=='sql':dump_sql(a.db,a.arquivo);print('SQL:',a.arquivo)
    elif a.cmd=='validar':print(metrics(a.db))

if __name__=='__main__':main()
