"""Painel desktop de fluxo escolar — Windows/Linux, PySide6.
O banco é SQLite local. A importação roda em thread para manter a interface responsiva.
"""
import sys,sqlite3,traceback
from pathlib import Path
from collections import Counter
from PySide6.QtCore import Qt,QAbstractTableModel,QSortFilterProxyModel,QObject,QThread,Signal
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QTabWidget,
    QLabel,QPushButton,QFileDialog,QLineEdit,QComboBox,QTableView,QTableWidget,QTableWidgetItem,
    QTextEdit,QMessageBox,QGroupBox,QGridLayout,QHeaderView,QSplitter,QFrame,QProgressBar,QScrollArea,QAbstractItemView)
from core import safe_sqlite,import_zip,import_infra,export_all,dump_sql,metrics,report_rows,REPORT_COLS,norm
from relatorios_escolas import export_school_reports,origin_schools
from painel_gerencial import school_indicators, export_management_pdf, export_filtered_pdf, export_filtered_excel
from infra_transporte import school_transport,transport_network,export_transport_csv,export_transport_pdf

ROOT=Path(__file__).resolve().parent
DEFAULT_DB=ROOT/'fluxo_escolar.sqlite'

class DataModel(QAbstractTableModel):
    def __init__(self, headers=None, rows=None):
        super().__init__();self.headers=headers or [];self.rows=rows or []
    def rowCount(self,parent=None):return 0 if parent and parent.isValid() else len(self.rows)
    def columnCount(self,parent=None):return len(self.headers)
    def data(self,index,role=Qt.DisplayRole):
        if not index.isValid():return None
        if role==Qt.DisplayRole:
            val=self.rows[index.row()][index.column()]
            return '' if val is None else str(val)
        if role==Qt.ToolTipRole:return str(self.rows[index.row()][index.column()] or '')
        if role==Qt.BackgroundRole and self.headers and self.headers[0]=='Prioridade':
            from PySide6.QtGui import QColor
            state=self.rows[index.row()][0]
            if state=='CRÍTICA':return QColor('#ffebee')
            if state in ('ALTA','VERIFICAR DESTINO'):return QColor('#fff4df')
            if state=='RETORNOU':return QColor('#e4f5e9')
        if role==Qt.TextAlignmentRole and self.headers[index.column()] in ('ID único','INEP 2025','INEP 2026'):
            return Qt.AlignVCenter | Qt.AlignLeft
        return None
    def headerData(self,section,orientation,role=Qt.DisplayRole):
        if role!=Qt.DisplayRole:return None
        return self.headers[section] if orientation==Qt.Horizontal else str(section+1)
    def replace(self,rows,headers=None):
        self.beginResetModel();self.rows=rows
        if headers is not None:self.headers=headers
        self.endResetModel()

class SearchProxy(QSortFilterProxyModel):
    def __init__(self):super().__init__();self.query='';self.kind='Todos';self.school_inep=None
    def filterAcceptsRow(self,row,parent):
        m=self.sourceModel()
        data=m.rows[row]
        if self.school_inep and str(data[7])!=str(self.school_inep):return False
        if self.kind!='Todos':
            cat=norm(str(data[11]))
            if self.kind=='Deixaram de frequentar' and 'deixou de frequentar' not in cat:return False
            if self.kind=='Reprovados' and 'reprovado' not in cat:return False
            if self.kind=='Transferidos' and 'transferido' not in cat:return False
            if self.kind=='Busca ativa' and data[0] not in ('CRÍTICA','ALTA'):return False
            if self.kind=='Mobilidade interna' and data[12]!='OUTRA ESCOLA DA REDE':return False
            if self.kind=='Não localizados' and data[12]!='NÃO LOCALIZADO NA REDE IMPORTADA':return False
            if self.kind=='Vínculos múltiplos' and data[12]!='MÚLTIPLAS ESCOLAS EM 2026':return False
        return not self.query or self.query in norm(' '.join(str(v or '') for v in data))
    def update(self,query,kind,school_inep=None):
        self.query=norm(query);self.kind=kind;self.school_inep=school_inep;self.invalidateFilter()

class Background(QObject):
    finished=Signal(object)
    message=Signal(str)
    def __init__(self,action,db,path,school_inep=None,filters=None):
        super().__init__();self.action=action;self.db=db;self.path=path;self.school_inep=school_inep
        self.filters=filters or {}
    def run(self):
        try:
            if self.action=='zip':result=import_zip(self.db,self.path,lambda s:self.message.emit(s))
            elif self.action=='infra':result=import_infra(self.db,self.path,lambda s:self.message.emit(s))
            elif self.action=='reports':result=export_all(self.db,self.path)
            elif self.action=='sql':result=dump_sql(self.db,self.path)
            elif self.action=='school_pdf':result=export_school_reports(self.db,self.path,self.school_inep,lambda s:self.message.emit(s))
            elif self.action=='management_pdf':result=export_management_pdf(self.db,self.path,lambda s:self.message.emit(s))
            elif self.action=='filtered_pdf':result=export_filtered_pdf(self.db,self.path,log=lambda s:self.message.emit(s),**self.filters)
            elif self.action=='filtered_excel':result=export_filtered_excel(self.db,self.path,log=lambda s:self.message.emit(s),**self.filters)
            elif self.action=='transport_csv':result=export_transport_csv(self.db,self.path,**self.filters)
            elif self.action=='transport_pdf':result=export_transport_pdf(self.db,self.path,**self.filters)
            self.finished.emit(('ok',result))
        except Exception:self.finished.emit(('error',traceback.format_exc()))

class Dashboard(QMainWindow):
    def __init__(self):
        super().__init__();self.db=str(DEFAULT_DB);self.thread=None;self.worker=None;self.all_rows=[]
        self.setWindowTitle('Fluxo Escolar | Gestão Educacional 2025–2026')
        self.resize(1450,850)
        self.setStyleSheet('''QWidget{font-family:Arial,Segoe UI,sans-serif;font-size:12px;color:#1B3048} QMainWindow{background:#f4f6fa} QGroupBox{background:white;border:1px solid #dce3ec;border-radius:8px;margin-top:12px;padding:12px;font-weight:600} QGroupBox::title{subcontrol-origin:margin;left:12px;padding:0 4px} QPushButton{background:#1c4d79;color:white;border:0;border-radius:5px;padding:9px 13px;font-weight:600} QPushButton:disabled{background:#9baabd} QLineEdit,QComboBox{padding:8px;background:#ffffff;color:#14263d;border:1px solid #b6c5d8;border-radius:6px;selection-background-color:#1c4d79;selection-color:#ffffff} QComboBox::drop-down{border:0;width:28px;background:#e9f0f9;border-top-right-radius:6px;border-bottom-right-radius:6px} QComboBox QAbstractItemView{background:#ffffff;color:#14263d;border:1px solid #9aacc2;selection-background-color:#175c96;selection-color:#ffffff;outline:0;padding:5px;min-height:120px} QComboBox QAbstractItemView::item{min-height:30px;padding:6px} QLabel{color:#223b56} QTextEdit,QTableView,QTableWidget{background:#ffffff;color:#172B43;border:1px solid #CCD9E7;alternate-background-color:#F0F5FA;selection-background-color:#205B91;selection-color:#ffffff;gridline-color:#DFE6EF;font-size:12px} QHeaderView::section{background:#17375B;color:#FFFFFF;padding:9px 8px;font-weight:700;border:0;border-right:1px solid #42627F;border-bottom:1px solid #42627F} QTableCornerButton::section{background:#17375B;border:0} QTableView::item{padding:6px 8px;color:#172B43} QTableView::item:selected{background:#205B91;color:#ffffff} QTabWidget::pane{border:1px solid #dce3ec;background:#f4f6fa} QTabBar::tab{background:#e6edf5;padding:12px 18px} QTabBar::tab:selected{background:white;font-weight:bold}''')
        central=QWidget();self.setCentralWidget(central);layout=QVBoxLayout(central);layout.setSpacing(10)
        title=QLabel('SISTEMA DE FLUXO ESCOLAR E BUSCA ATIVA');title.setStyleSheet('font-size:21px;font-weight:800;color:#15395b')
        layout.addWidget(title)
        hint=QLabel('Santa Maria das Barreiras • 2025 × 2026 • vínculo pelo ID único • escola pelo INEP')
        hint.setStyleSheet('color:#52657a');layout.addWidget(hint)
        head=QHBoxLayout();self.db_label=QLabel();head.addWidget(self.db_label,1)
        b=QPushButton('Escolher banco SQLite');b.clicked.connect(self.choose_db);head.addWidget(b)
        layout.addLayout(head)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        self.dashboard_metrics=[]
        self.build_dashboard();self.build_import();self.build_analysis();self.build_schools();self.build_school_reports();self.build_infra();self.build_transport();self.build_issues()
        for grid in (self.main_table,self.school_table,self.infra_table,self.transport_table,self.issue_table):
            self._configure_grid(grid)
        self.statusBar().showMessage('Banco local — dados pessoais devem permanecer em pasta restrita')
        self.refresh()

    def _configure_grid(self,grid):
        from PySide6.QtGui import QFont
        grid.setFont(QFont('Arial', 11))
        grid.setAlternatingRowColors(True)
        grid.setWordWrap(True)
        grid.setSelectionBehavior(QAbstractItemView.SelectRows)
        grid.setSelectionMode(QAbstractItemView.SingleSelection)
        grid.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        grid.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        grid.verticalHeader().setDefaultSectionSize(58)
        grid.verticalHeader().setMinimumSectionSize(38)
        grid.horizontalHeader().setMinimumSectionSize(84)
        grid.horizontalHeader().setDefaultSectionSize(180)
        grid.horizontalHeader().setFixedHeight(48)
        grid.setStyleSheet('QTableView{background:#FFFFFF;color:#172B43;alternate-background-color:#F2F7FC;selection-background-color:#205B91;selection-color:#FFFFFF;gridline-color:#DBE4ED} QTableView::item{padding:6px 8px} QTableView::item:selected{background:#205B91;color:#FFFFFF}')

    def btn(self,txt,fn):
        b=QPushButton(txt);b.clicked.connect(fn);return b
    def tab(self,name):
        x=QWidget();v=QVBoxLayout(x);v.setContentsMargins(14,14,14,14);self.tabs.addTab(x,name);return v
    def build_dashboard(self):
        v=self.tab('Painel gerencial')
        row=QHBoxLayout();row.addWidget(QLabel('Indicadores:'))
        self.panel_school=QComboBox();self.panel_school.addItem('Toda a rede (21 escolas)',None)
        row.addWidget(self.panel_school,1)
        row.addWidget(self.btn('Atualizar indicadores',self.refresh))
        row.addWidget(self.btn('Exportar painel em PDF',self.export_management_pdf_ui))
        v.addLayout(row)
        self.panel_cards=QLabel();self.panel_cards.setWordWrap(True)
        self.panel_cards.setStyleSheet('font-size:15px;background:#e9f1f9;padding:15px;border-radius:8px;color:#17375b;font-weight:600')
        v.addWidget(self.panel_cards)
        g=QGroupBox('Distribuição de situações e continuidade');bars=QVBoxLayout(g)
        self.panel_bars={}
        labels=[('approved','Aprovados em 2025'),('failed','Reprovados em 2025'),
                ('dropout','Deixaram de frequentar em 2025'),('transferred','Transferidos (situação 2025)'),
                ('same','Permaneceram na escola de origem'),('internal','Outra escola da rede em 2026'),
                ('missing26','Não localizados na base 2026')]
        for key,title in labels:
            line=QHBoxLayout();caption=QLabel(title);caption.setMinimumWidth(278);line.addWidget(caption)
            bar=QProgressBar();bar.setRange(0,1000);bar.setTextVisible(True)
            bar.setStyleSheet('QProgressBar{border:1px solid #d8e1eb;border-radius:4px;background:#edf2f6;text-align:center;min-height:20px} QProgressBar::chunk{background:#477da7;border-radius:3px}')
            line.addWidget(bar,1);bars.addLayout(line);self.panel_bars[key]=bar
        v.addWidget(g)
        g=QGroupBox('Recorte de rendimento — Ensino Fundamental 1º ao 5º ano');rend=QVBoxLayout(g)
        self.ef_note=QLabel();self.ef_note.setWordWrap(True);rend.addWidget(self.ef_note)
        self.ef_bars={}
        for key,title in [('ef_ap','Aprovação'),('ef_rep','Reprovação'),('ef_dropout','Deixaram de frequentar')]:
            line=QHBoxLayout();cap=QLabel(title);cap.setMinimumWidth(278);line.addWidget(cap)
            bar=QProgressBar();bar.setRange(0,1000);bar.setTextVisible(True);line.addWidget(bar,1)
            self.ef_bars[key]=bar;rend.addLayout(line)
        v.addWidget(g)
        self.panel_school.currentIndexChanged.connect(self.render_dashboard)
        self.panel_hint=QLabel('Diagnóstico interno: o IDEB oficial requer rendimento apurado pelo Inep e desempenho no Saeb. Dados ausentes não são estimados.')
        self.panel_hint.setWordWrap(True);self.panel_hint.setStyleSheet('color:#4f6277');v.addWidget(self.panel_hint)
        v.addStretch(1)

    def render_dashboard(self,*_):
        if not hasattr(self,'panel_cards') or not hasattr(self,'panel_school'):return
        inep=self.panel_school.currentData()
        info=next((m for m in self.dashboard_metrics if m['inep']==inep),None) if inep else None
        if info:
            summary={'approved':info['approved'],'failed':info['failed'],'dropout':info['dropout'],
             'transferred':info['transferred'],'same':info['same'],'internal':info['internal'],
             'missing26':info['missing26']}
            self.panel_cards.setText(f'ESCOLA: {info["school"]} • INEP {info["inep"]}    |    '
             f'IDs na relação: {info["students"]:,}    |    Busca ativa prioritária: {info["unresolved"]:,}    |    '
             f'Matrículas 2025: {info["enrollment25"]:,}    •    2026: {info["enrollment26"]:,}'.replace(',','.'))
            ef_n=info['ef_n'];self.ef_note.setText(f'Base diagnóstica: {ef_n} IDs com situação única classificável; {info["ef_excluded"]} fora do denominador. Educação Infantil e EJA não entram nesse recorte.')
            ef={k:info[k] for k in self.ef_bars}
        else:
            totals=getattr(self,'dashboard_totals',None)
            if not totals:return
            summary={'approved':totals['status']['APROVADO'],'failed':totals['status']['REPROVADO'],
                'dropout':totals['status']['DEIXOU_DE_FREQUENTAR'],'transferred':totals['status']['TRANSFERIDO'],
                'same':totals['location']['MESMA ESCOLA'],'internal':totals['location']['OUTRA ESCOLA DA REDE'],
                'missing26':totals['location']['NÃO LOCALIZADO NA REDE IMPORTADA']}
            self.panel_cards.setText(f'REDE MUNICIPAL    |    {totals["schools"]} escolas    |    '
                 f'{totals["students"]:,} IDs com resultado 2025    |    '
                 f'{totals["unresolved"]:,} IDs prioritários não localizados em 2026'.replace(',','.'))
            # As bases por escola se sobrepõem: não agregar proporções sem recomputar o denominador.
            ef_n=0;ef={k:None for k in self.ef_bars}
            self.ef_note.setText('Selecione uma escola para visualizar o recorte de aprovação/reprovação do 1º ao 5º ano. Taxas escolares não são somadas para gerar taxa municipal.')
        top=max(max(summary.values(),default=0),1)
        for key,bar in self.panel_bars.items():
            count=summary[key]
            bar.setValue(round(count/top*1000));bar.setFormat(f'{count:,}'.replace(',','.'))
        for key,bar in self.ef_bars.items():
            value=ef[key]
            if value is None:bar.setValue(0);bar.setFormat('Sem base para taxa')
            else:bar.setValue(round(value*10));bar.setFormat(f'{value:.1f}% • n={ef_n}'.replace('.',','))

    def export_management_pdf_ui(self):
        f,_=QFileDialog.getSaveFileName(self,'Salvar painel PDF',str(Path.home()/'Painel_Gerencial_Rede_2025_2026.pdf'),'PDF (*.pdf)')
        if f:self.busy('management_pdf',f)

    def current_filters(self):
        return {'school_inep':self.analysis_school.currentData(),
                'category':self.kind.currentText(),'search':self.search.text().strip()}
    def export_filtered_pdf_ui(self):
        f,_=QFileDialog.getSaveFileName(self,'Salvar relação nominal filtrada em PDF',str(Path.home()/'Relacao_Filtrada_2025_2026.pdf'),'PDF (*.pdf)')
        if f:self.busy('filtered_pdf',f,filters=self.current_filters())
    def export_filtered_excel_ui(self):
        f,_=QFileDialog.getSaveFileName(self,'Salvar relação nominal filtrada em Excel',str(Path.home()/'Relacao_Filtrada_2025_2026.xlsx'),'Excel (*.xlsx)')
        if f:self.busy('filtered_excel',f,filters=self.current_filters())

    def build_import(self):
        v=self.tab('Importação e exportação')
        g=QGroupBox('1. Importação massiva — ZIP contendo as escolas');gl=QVBoxLayout(g)
        gl.addWidget(QLabel('Detecta ano no cabeçalho. Processa as planilhas de matrícula e resultado; ignora PDFs/TXT/ZIP internos.'))
        gl.addWidget(self.btn('Importar ZIP completo das escolas',self.load_zip));v.addWidget(g)
        g=QGroupBox('2. Infraestrutura 2026 — atributos preservados individualmente');gl=QVBoxLayout(g)
        gl.addWidget(self.btn('Importar Aspectos_Infraestrutura.xlsx',self.load_infra));v.addWidget(g)
        g=QGroupBox('3. Saídas e backups');h=QHBoxLayout(g)
        h.addWidget(self.btn('Exportar todos os relatórios CSV',self.export_reports))
        h.addWidget(self.btn('Exportar banco em SQL (.sql)',self.export_sql))
        h.addWidget(self.btn('Verificar integridade',self.validate))
        v.addWidget(g)
        self.log=QTextEdit();self.log.setReadOnly(True);self.log.setPlaceholderText('O processamento será registrado aqui ...');v.addWidget(self.log,1)
    def build_analysis(self):
        v=self.tab('Fluxo e Busca Ativa')
        self.cards=QLabel('');self.cards.setWordWrap(True);self.cards.setStyleSheet('font-size:14px;background:#eaf2fa;padding:12px;border-radius:6px;')
        v.addWidget(self.cards)
        h=QHBoxLayout();h.addWidget(QLabel('Filtrar:'))
        self.analysis_school=QComboBox();self.analysis_school.addItem('Todas as escolas',None);h.addWidget(self.analysis_school)
        self.kind=QComboBox();self.kind.addItems(['Todos','Deixaram de frequentar','Reprovados','Transferidos','Busca ativa','Mobilidade interna','Não localizados','Vínculos múltiplos'])
        h.addWidget(self.kind)
        self.search=QLineEdit();self.search.setPlaceholderText('Pesquisar ID, nome, escola, INEP, etapa ou resultado…');h.addWidget(self.search,1)
        h.addWidget(self.btn('Atualizar dados',self.refresh));v.addLayout(h)
        self.main_model=DataModel(REPORT_COLS,[]);self.proxy=SearchProxy();self.proxy.setSourceModel(self.main_model)
        self.kind.currentTextChanged.connect(self.search_filter);self.search.textChanged.connect(self.search_filter)
        self.analysis_school.currentIndexChanged.connect(self.search_filter)
        self.main_table=QTableView();self.main_table.setModel(self.proxy)
        self.main_table.setAlternatingRowColors(True);self.main_table.setSortingEnabled(False)
        self.main_table.horizontalHeader().setDefaultSectionSize(188)
        self.main_table.setWordWrap(True)
        self.main_table.setColumnWidth(2,300)
        self.main_table.setColumnWidth(8,280)
        self.main_table.setColumnWidth(14,300)
        self.main_table.setColumnWidth(18,480)
        self.main_table.setColumnWidth(19,105)
        v.addWidget(self.main_table,1)
        actions=QHBoxLayout()
        actions.addWidget(self.btn('Exportar lista filtrada em PDF',self.export_filtered_pdf_ui))
        actions.addWidget(self.btn('Exportar lista filtrada em Excel',self.export_filtered_excel_ui))
        actions.addStretch(1);v.addLayout(actions)
        self.total_label=QLabel();v.addWidget(self.total_label)
    def build_schools(self):
        v=self.tab('Escolas e mobilidade')
        v.addWidget(QLabel('Escolas participantes: têm matrículas no ZIP; escolas adicionais do arquivo de infraestrutura são preservadas separadamente.'))
        self.school_table=QTableView();self.school_model=DataModel();self.school_table.setModel(self.school_model)
        self.school_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        v.addWidget(self.school_table,1)
        v.addWidget(QLabel('As quantidades por escola referem-se a vínculos. Uma mesma pessoa pode ter mais de um vínculo.'))
    def build_school_reports(self):
        v=self.tab('Relatórios individuais (PDF)')
        note=QLabel('Exporta um PDF separado para cada escola, com a relação nominal de alunos, situação 2025, localização em 2026 e gráficos ao final. Os documentos trazem índices internos de atenção ao fluxo, sem calcular o IDEB oficial.')
        note.setWordWrap(True);note.setStyleSheet('font-size:13px;color:#354c63;padding:6px')
        v.addWidget(note)
        g=QGroupBox('Exportação das 21 escolas em lote');lv=QVBoxLayout(g)
        lv.addWidget(QLabel('Será criada uma pasta com um PDF por escola, identificado pelo código INEP. Formato de impressão: A3 paisagem para acomodar todas as colunas.'))
        lv.addWidget(self.btn('GERAR TODOS OS RELATÓRIOS POR ESCOLA (PDF)',self.export_all_school_pdf))
        v.addWidget(g)
        g2=QGroupBox('Exportar somente uma escola');lv2=QVBoxLayout(g2)
        h=QHBoxLayout();h.addWidget(QLabel('Escola:'))
        self.report_school=QComboBox();h.addWidget(self.report_school,1);lv2.addLayout(h)
        lv2.addWidget(self.btn('Gerar PDF da escola selecionada',self.export_one_school_pdf))
        v.addWidget(g2)
        v.addWidget(QLabel('Indicadores: situação final e mobilidade 2025→2026; recorte de aprovação/reprovação/não frequência dos anos regulares do Ensino Fundamental. Sem proficiência Saeb, não há cálculo do IDEB.'))
        v.addStretch(1)
    def export_all_school_pdf(self):
        folder=QFileDialog.getExistingDirectory(self,'Escolha onde salvar os 21 PDFs individuais')
        if folder:self.busy('school_pdf',str(Path(folder)/'RELATORIOS_POR_ESCOLA'))
    def export_one_school_pdf(self):
        inep=self.report_school.currentData()
        if not inep:QMessageBox.warning(self,'Seleção obrigatória','Selecione uma escola.');return
        folder=QFileDialog.getExistingDirectory(self,'Escolha a pasta para o PDF da escola')
        if folder:self.busy('school_pdf',folder,inep)
    def build_infra(self):
        v=self.tab('Infraestrutura escolar')
        h=QHBoxLayout();h.addWidget(QLabel('Escola:'))
        self.infra_select=QComboBox();h.addWidget(self.infra_select,1)
        self.infra_select.currentIndexChanged.connect(self.show_infra)
        v.addLayout(h)
        self.infra_table=QTableView();self.infra_model=DataModel(['Indicador','Informação'],[])
        self.infra_table.setModel(self.infra_model)
        self.infra_table.horizontalHeader().setStretchLastSection(True)
        self.infra_table.horizontalHeader().setDefaultSectionSize(460)
        v.addWidget(self.infra_table,1)
    def build_transport(self):
        v=self.tab('Transporte escolar')
        v.addWidget(QLabel('Auditoria dos estudantes que utilizam transporte escolar: zona de residência, situação por escola e possíveis reflexos no PNATE.'))
        h=QHBoxLayout();h.addWidget(QLabel('Ano:'))
        self.transport_year=QComboBox();self.transport_year.addItems(['2026','2025']);h.addWidget(self.transport_year)
        h.addWidget(QLabel('Escola:'))
        self.transport_school=QComboBox();self.transport_school.addItem('Todas as escolas',None);h.addWidget(self.transport_school,1)
        h.addWidget(QLabel('Exibir:'))
        self.transport_filter=QComboBox();self.transport_filter.addItems(['Todos que usam transporte','Somente alertas e divergências','Residência urbana','Residência rural'])
        h.addWidget(self.transport_filter)
        h.addWidget(self.btn('Atualizar',self.show_transport));v.addLayout(h)
        self.transport_year.currentIndexChanged.connect(self.show_transport)
        self.transport_school.currentIndexChanged.connect(self.show_transport)
        self.transport_filter.currentIndexChanged.connect(self.show_transport)
        self.transport_summary=QLabel();self.transport_summary.setWordWrap(True)
        self.transport_summary.setStyleSheet('background:#EAF0F6;color:#17375B;padding:12px;font-weight:700;border-radius:6px')
        v.addWidget(self.transport_summary)
        self.transport_model=DataModel(['ID único','Aluno(a)','Nascimento','Idade','Escola / INEP','Ano','Transporte','Zona residência','Turma(s)','Classificação','Providência'],[])
        self.transport_table=QTableView();self.transport_table.setModel(self.transport_model)
        self.transport_table.horizontalHeader().setDefaultSectionSize(195)
        v.addWidget(self.transport_table,1)
        acts=QHBoxLayout()
        acts.addWidget(self.btn('Exportar filtragem (CSV)',self.export_transport_csv_ui))
        acts.addWidget(self.btn('Exportar filtragem (PDF)',self.export_transport_pdf_ui))
        acts.addStretch(1);v.addLayout(acts)
        note=QLabel('ALERTA não confirma irregularidade. O PNATE utiliza estudantes residentes na zona rural que usam transporte. Antes de retificar, conferir endereço real e documentação.')
        note.setWordWrap(True);v.addWidget(note)

    def _transport_filters(self):
        return {'year':int(self.transport_year.currentText()),'inep':self.transport_school.currentData(),
                'only_alerts':self.transport_filter.currentText()=='Somente alertas e divergências',
                'zone_filter':('urbana' if self.transport_filter.currentText()=='Residência urbana' else 'rural' if self.transport_filter.currentText()=='Residência rural' else None)}

    def show_transport(self,*_):
        if not hasattr(self,'transport_model'):return
        try:
            f=self._transport_filters();conn=safe_sqlite(self.db)
            try:
                rows=school_transport(conn,f['inep'],f['year']) if f['inep'] else transport_network(conn,f['year'])
            finally:conn.close()
            alerts=sum(1 for r in rows if r['situacao'].startswith('ALERTA'))
            verify=sum(1 for r in rows if r['situacao'].startswith('VERIFICAR'))
            rural=sum(1 for r in rows if r['situacao']=='RURAL INFORMADA')
            typ=self.transport_filter.currentText()
            if typ=='Somente alertas e divergências':rows=[r for r in rows if r['situacao'].startswith(('ALERTA','VERIFICAR'))]
            elif typ=='Residência urbana':rows=[r for r in rows if r['situacao'].startswith('ALERTA')]
            elif typ=='Residência rural':rows=[r for r in rows if r['situacao']=='RURAL INFORMADA']
            result=[]
            for r in rows:
                result.append([r['id'],r['nome'],r['nascimento'],r['idade'],f"{r['escola']} [{r['inep']}]",r['ano'],r['transporte'],r['zona'],r['turmas'],r['situacao'],r['providencia']])
            self.transport_model.replace(result)
            self.transport_summary.setText(f'Usuários do transporte na seleção: {rural+alerts+verify} IDs/vínculos escolares  |  Rural: {rural}  |  Urbana (alerta): {alerts}  |  Zona a verificar: {verify}  |  Exibidos: {len(rows)}')
        except Exception as e:
            self.transport_summary.setText('Falha ao carregar auditoria do transporte: '+str(e))

    def export_transport_csv_ui(self):
        from pathlib import Path
        f,_=QFileDialog.getSaveFileName(self,'Salvar auditoria de transporte em CSV',str(Path.home()/'Auditoria_Transporte_Escolar.csv'),'CSV (*.csv)')
        if f:self.busy('transport_csv',f,filters=self._transport_filters())

    def export_transport_pdf_ui(self):
        from pathlib import Path
        f,_=QFileDialog.getSaveFileName(self,'Salvar auditoria de transporte em PDF',str(Path.home()/'Auditoria_Transporte_Escolar.pdf'),'PDF (*.pdf)')
        if f:self.busy('transport_pdf',f,filters=self._transport_filters())

    def build_issues(self):
        v=self.tab('Auditoria e alertas')
        v.addWidget(QLabel('Divergências cadastrais e qualidade de dados identificadas na importação.'))
        self.issue_table=QTableView();self.issue_model=DataModel();self.issue_table.setModel(self.issue_model)
        self.issue_table.horizontalHeader().setDefaultSectionSize(190)
        v.addWidget(self.issue_table,1)
    def choose_db(self):
        filename,_=QFileDialog.getSaveFileName(self,'Escolher/criar banco SQLite',self.db,'Banco SQLite (*.sqlite *.db)')
        if filename:
            self.db=filename;self.refresh()
    def busy(self,action,target,school_inep=None,filters=None):
        if self.thread is not None:QMessageBox.warning(self,'Aguarde','Há uma tarefa em execução.');return
        self.log.append(f'>>> {action.upper()}: {target}')
        self.worker=Background(action,self.db,target,school_inep,filters);self.thread=QThread(self)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.message.connect(self.log.append)
        self.worker.finished.connect(self.task_done)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.end_thread)
        self.thread.start()
        self.statusBar().showMessage('Processando...')
    def task_done(self,result):
        state,data=result
        if state=='ok':
            self.log.append(f'CONCLUÍDO: {data}')
            self.refresh();self.statusBar().showMessage('Operação concluída')
        else:
            self.log.append('ERRO: '+data)
            QMessageBox.critical(self,'Falha no processamento',data[-1800:])
    def end_thread(self):
        self.thread.deleteLater();self.thread=None;self.worker=None
    def load_zip(self):
        f,_=QFileDialog.getOpenFileName(self,'Selecionar pasta geral compactada','','Arquivos ZIP (*.zip)')
        if f:self.busy('zip',f)
    def load_infra(self):
        f,_=QFileDialog.getOpenFileName(self,'Selecionar planilha de infraestrutura','','Excel XLSX (*.xlsx)')
        if f:self.busy('infra',f)
    def export_reports(self):
        f=QFileDialog.getExistingDirectory(self,'Pasta de destino dos relatórios')
        if f:self.busy('reports',f)
    def export_sql(self):
        f,_=QFileDialog.getSaveFileName(self,'Exportar SQL',str(Path(self.db).with_suffix('.sql')),'Scripts SQL (*.sql)')
        if f:self.busy('sql',f)
    def search_filter(self,*_):
        self.proxy.update(self.search.text(),self.kind.currentText(),self.analysis_school.currentData())
        self.total_label.setText(f'{self.proxy.rowCount():,} registros exibidos de {len(self.all_rows):,} registros de resultado 2025'.replace(',','.'))
    def validate(self):
        try:QMessageBox.information(self,'Integridade do banco','\n'.join(f'{k}: {v}' for k,v in metrics(self.db).items()))
        except Exception as e:QMessageBox.critical(self,'Erro',str(e))
    def refresh(self):
        try:
            c=safe_sqlite(self.db)
            self.db_label.setText('Banco: '+self.db)
            self.all_rows=report_rows(c)
            self.main_model.replace(self.all_rows,REPORT_COLS)
            self.search_filter()
            total=Counter(r[12] for r in self.all_rows)
            cats=Counter(norm(r[11]) for r in self.all_rows)
            crit=sum(1 for r in self.all_rows if r[0]=='CRÍTICA')
            self.cards.setText(f'Resultados 2025: {len(self.all_rows):,}    |    Mesmo INEP: {total["MESMA ESCOLA"]:,}    |    Outra escola: {total["OUTRA ESCOLA DA REDE"]:,}    |    Não localizados: {total["NÃO LOCALIZADO NA REDE IMPORTADA"]:,}    |    Busca ativa crítica: {crit:,}'.replace(',','.'))
            school_rows=[]
            for r in c.execute('''SELECT e.inep,e.nome,
                  (SELECT COUNT(*) FROM matriculas m WHERE m.inep=e.inep AND m.ano=2025),
                  (SELECT COUNT(*) FROM resultados s WHERE s.inep=e.inep AND s.ano=2025),
                  (SELECT COUNT(*) FROM matriculas m WHERE m.inep=e.inep AND m.ano=2026),
                  (SELECT COUNT(*) FROM resultados s JOIN situacoes t ON s.situacao_id=t.id WHERE s.inep=e.inep AND s.ano=2025 AND t.categoria='TRANSFERIDO')
                  FROM escolas e WHERE EXISTS(SELECT 1 FROM sources so WHERE so.school_inep=e.inep)
                  ORDER BY e.nome'''):
                school_rows.append(list(r))
            self.school_model.replace(school_rows,['INEP','Escola','Matrículas 2025','Resultados 2025','Matrículas 2026','Transferidos (2025)'])
            issues=[list(r) for r in c.execute('''SELECT severity,code,source_path,row_number,detail FROM import_issues ORDER BY id DESC LIMIT 3000''')]
            self.issue_model.replace(issues,['Nível','Código','Arquivo','Linha','Descrição'])
            self.dashboard_metrics,self.dashboard_totals=school_indicators(self.db)
            self.panel_school.blockSignals(True)
            prev_panel=self.panel_school.currentData();self.panel_school.clear()
            self.panel_school.addItem('Toda a rede (21 escolas)',None)
            self.analysis_school.blockSignals(True)
            previous_analysis=self.analysis_school.currentData();self.analysis_school.clear()
            self.analysis_school.addItem('Todas as escolas',None)
            for school in self.dashboard_metrics:
                label=f'{school["school"]}  [{school["inep"]}]'
                self.panel_school.addItem(label,school['inep'])
                self.analysis_school.addItem(label,school['inep'])
            index=self.panel_school.findData(prev_panel)
            if index>=0:self.panel_school.setCurrentIndex(index)
            self.panel_school.blockSignals(False)
            index=self.analysis_school.findData(previous_analysis)
            if index>=0:self.analysis_school.setCurrentIndex(index)
            self.analysis_school.blockSignals(False)
            self.render_dashboard();self.search_filter()
            self.infra_select.blockSignals(True);previous=self.infra_select.currentData();self.infra_select.clear()
            for r in c.execute('''SELECT DISTINCT e.inep,e.nome FROM escolas e JOIN infra_valores v ON v.inep=e.inep ORDER BY e.nome'''):
                self.infra_select.addItem(f'{r[1]}  [{r[0]}]',r[0])
            index=self.infra_select.findData(previous)
            if index>=0:self.infra_select.setCurrentIndex(index)
            self.infra_select.blockSignals(False)
            if hasattr(self,'transport_school'):
                old=self.transport_school.currentData()
                self.transport_school.blockSignals(True)
                self.transport_school.clear();self.transport_school.addItem('Todas as escolas',None)
                for i,n in origin_schools(c):self.transport_school.addItem(f'{n} [{i}]',i)
                ix=self.transport_school.findData(old)
                if ix>=0:self.transport_school.setCurrentIndex(ix)
                self.transport_school.blockSignals(False)
            prev=self.report_school.currentData()
            self.report_school.blockSignals(True);self.report_school.clear()
            for inep,nome in origin_schools(c):
                self.report_school.addItem(f'{nome}  [{inep}]',inep)
            found=self.report_school.findData(prev)
            if found>=0:self.report_school.setCurrentIndex(found)
            self.report_school.blockSignals(False)
            c.close();self.show_infra();self.show_transport()
        except Exception as e:
            self.last_refresh_error = traceback.format_exc()
            self.log.append('ERRO REFRESH: '+self.last_refresh_error) if hasattr(self,'log') else None
            self.statusBar().showMessage('Erro ao carregar banco: '+str(e))
    def show_infra(self,*_):
        inep=self.infra_select.currentData()
        if not inep:self.infra_model.replace([]);return
        try:
            c=safe_sqlite(self.db)
            rows=[list(r) for r in c.execute('''SELECT a.nome,COALESCE(v.valor,'—') FROM infra_valores v JOIN infra_atributos a ON a.id=v.atributo_id WHERE v.inep=? ORDER BY a.coluna_origem''',(inep,))]
            c.close();self.infra_model.replace(rows)
        except Exception as e:self.statusBar().showMessage(str(e))

def main():
    try:
        app=QApplication(sys.argv)
        win=Dashboard()
        win.show()
        if getattr(win, 'last_refresh_error', None):
            QMessageBox.warning(win, 'Problema ao carregar dados',
                'O sistema abriu, mas houve falha ao ler o banco SQLite.\n'
                'O erro completo ficara no arquivo logs/ultima_execucao.log.\n\n'
                + win.last_refresh_error[-1100:])
        return app.exec()
    except Exception:
        info=traceback.format_exc()
        print('ERRO FATAL NA INTERFACE:\n'+info, file=sys.stderr, flush=True)
        try:
            QMessageBox.critical(None,'Erro ao abrir Fluxo Escolar',
                'A interface nao conseguiu iniciar.\nVerifique logs/ultima_execucao.log\n\n'+info[-1600:])
        except Exception:
            pass
        return 1
if __name__=='__main__':
    sys.exit(main())
