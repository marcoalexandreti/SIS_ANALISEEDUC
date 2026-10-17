-- Sistema de Fluxo Escolar | SQLite 3 | esquema relacional versionado
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY, installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO schema_version(version) VALUES(1);

CREATE TABLE IF NOT EXISTS import_batches(
 id INTEGER PRIMARY KEY, source_name TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 finished_at TEXT, status TEXT NOT NULL CHECK(status IN ('RUNNING','DONE','PARTIAL','FAILED')),
 files_detected INTEGER NOT NULL DEFAULT 0, files_imported INTEGER NOT NULL DEFAULT 0,
 records_imported INTEGER NOT NULL DEFAULT 0, notes TEXT
);
CREATE TABLE IF NOT EXISTS sources(
 id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES import_batches(id),
 logical_path TEXT NOT NULL, sha256 TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('MATRICULA','RESULTADO','INFRAESTRUTURA')),
 school_inep TEXT, school_year INTEGER NOT NULL CHECK(school_year BETWEEN 2000 AND 2200),
 sheet_name TEXT NOT NULL, header_row INTEGER NOT NULL, records INTEGER NOT NULL DEFAULT 0,
 imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(kind, school_inep, school_year)
);
CREATE INDEX IF NOT EXISTS ix_sources_batch ON sources(batch_id);

CREATE TABLE IF NOT EXISTS escolas(
 inep TEXT PRIMARY KEY CHECK(length(inep) = 8 AND inep NOT GLOB '*[^0-9]*'),
 nome TEXT NOT NULL, municipio TEXT, uf TEXT, localizacao TEXT, dependencia TEXT
);
CREATE TABLE IF NOT EXISTS alunos(
 id_unico TEXT PRIMARY KEY CHECK(length(id_unico) BETWEEN 5 AND 20),
 nome_referencia TEXT NOT NULL,
 nascimento_referencia TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS aluno_observacoes(
 id INTEGER PRIMARY KEY, student_id TEXT NOT NULL REFERENCES alunos(id_unico),
 source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
 row_number INTEGER NOT NULL, ano INTEGER NOT NULL,
 escola_inep TEXT NOT NULL REFERENCES escolas(inep), nome TEXT NOT NULL,
 nascimento TEXT, sexo TEXT, cor_raca TEXT, cpf TEXT, nacionalidade TEXT,
 UNIQUE(source_id, row_number)
);
CREATE INDEX IF NOT EXISTS ix_observacoes_student_ano ON aluno_observacoes(student_id, ano);

CREATE TABLE IF NOT EXISTS etapas(
 id INTEGER PRIMARY KEY, descricao TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS turmas(
 id INTEGER PRIMARY KEY, inep TEXT NOT NULL REFERENCES escolas(inep), ano INTEGER NOT NULL,
 codigo TEXT NOT NULL, nome TEXT,
 UNIQUE(inep, ano, codigo)
);
CREATE TABLE IF NOT EXISTS matriculas(
 id INTEGER PRIMARY KEY,
 student_id TEXT NOT NULL REFERENCES alunos(id_unico),
 inep TEXT NOT NULL REFERENCES escolas(inep), ano INTEGER NOT NULL,
 turma_id INTEGER REFERENCES turmas(id), etapa_id INTEGER REFERENCES etapas(id),
 codigo_matricula TEXT NOT NULL,
 transporte TEXT, zona_residencia TEXT, etapa_origem TEXT NOT NULL DEFAULT 'NAO_INFORMADA',
 source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
 row_number INTEGER NOT NULL,
 UNIQUE(inep, ano, codigo_matricula), UNIQUE(source_id, row_number)
);
CREATE INDEX IF NOT EXISTS ix_matriculas_student_year ON matriculas(student_id,ano);
CREATE INDEX IF NOT EXISTS ix_matriculas_escola_year ON matriculas(inep,ano);

CREATE TABLE IF NOT EXISTS situacoes(
 id INTEGER PRIMARY KEY, descricao TEXT NOT NULL UNIQUE, categoria TEXT NOT NULL CHECK(categoria IN ('APROVADO','REPROVADO','TRANSFERIDO','DEIXOU_DE_FREQUENTAR','OUTRO'))
);
CREATE TABLE IF NOT EXISTS resultados(
 id INTEGER PRIMARY KEY,
 student_id TEXT NOT NULL REFERENCES alunos(id_unico),
 inep TEXT NOT NULL REFERENCES escolas(inep), ano INTEGER NOT NULL,
 turma_id INTEGER REFERENCES turmas(id), etapa_id INTEGER REFERENCES etapas(id),
 situacao_id INTEGER NOT NULL REFERENCES situacoes(id), admitido TEXT, concluinte TEXT,
 source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
 row_number INTEGER NOT NULL,
 UNIQUE(source_id, row_number)
);
CREATE INDEX IF NOT EXISTS ix_resultados_student_year ON resultados(student_id,ano);
CREATE INDEX IF NOT EXISTS ix_resultados_situacao ON resultados(situacao_id,ano);

CREATE TABLE IF NOT EXISTS infra_atributos(
 id INTEGER PRIMARY KEY, nome TEXT NOT NULL UNIQUE, coluna_origem INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS infra_valores(
 inep TEXT NOT NULL REFERENCES escolas(inep),
 ano INTEGER NOT NULL, atributo_id INTEGER NOT NULL REFERENCES infra_atributos(id),
 valor TEXT, source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
 PRIMARY KEY(inep,ano,atributo_id)
);
CREATE INDEX IF NOT EXISTS ix_infra_ano ON infra_valores(ano);

CREATE TABLE IF NOT EXISTS import_issues(
 id INTEGER PRIMARY KEY, batch_id INTEGER REFERENCES import_batches(id),
 source_path TEXT, row_number INTEGER, severity TEXT NOT NULL CHECK(severity IN ('INFO','WARNING','ERROR')),
 code TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_issues_batch ON import_issues(batch_id);

-- Visão: observação cadastral escolhida prioritariamente na matrícula do ano correspondente.
CREATE VIEW IF NOT EXISTS vw_aluno_contexto AS
SELECT a.id_unico, a.nome_referencia, a.nascimento_referencia,
       o.ano, o.escola_inep, o.nome, o.nascimento, o.cor_raca, o.sexo
FROM alunos a JOIN aluno_observacoes o ON o.student_id=a.id_unico;

-- Histórico de nomes e características cadastrais da escola por fonte/ano:
CREATE TABLE IF NOT EXISTS escola_observacoes(
 id INTEGER PRIMARY KEY, inep TEXT NOT NULL REFERENCES escolas(inep),
 ano INTEGER NOT NULL, source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
 nome TEXT NOT NULL, municipio TEXT, uf TEXT, localizacao TEXT, dependencia TEXT,
 UNIQUE(inep,source_id)
);
CREATE INDEX IF NOT EXISTS ix_escola_obs_inep_ano ON escola_observacoes(inep,ano);

-- Visões SQL de consulta, sem persistência ou duplicação de fatos.
CREATE VIEW IF NOT EXISTS vw_matriculas_detalhadas AS
SELECT m.id AS matricula_pk,m.codigo_matricula,m.ano,m.student_id,
       a.nome_referencia,a.nascimento_referencia,m.inep,e.nome AS escola,
       t.codigo AS codigo_turma,t.nome AS turma,et.descricao AS etapa,m.transporte
FROM matriculas m JOIN alunos a ON a.id_unico=m.student_id
JOIN escolas e ON e.inep=m.inep
LEFT JOIN turmas t ON t.id=m.turma_id
LEFT JOIN etapas et ON et.id=m.etapa_id;

CREATE VIEW IF NOT EXISTS vw_resultados_detalhados AS
SELECT r.id AS resultado_pk,r.ano,r.student_id,a.nome_referencia,r.inep,
       e.nome AS escola,si.descricao AS situacao,si.categoria,
       et.descricao AS etapa,t.nome AS turma
FROM resultados r JOIN alunos a ON a.id_unico=r.student_id
JOIN escolas e ON e.inep=r.inep
JOIN situacoes si ON si.id=r.situacao_id
LEFT JOIN etapas et ON et.id=r.etapa_id
LEFT JOIN turmas t ON t.id=r.turma_id;

CREATE VIEW IF NOT EXISTS vw_localizacao_rede_2026 AS
SELECT r.resultado_pk,r.student_id,r.nome_referencia,r.inep AS inep_2025,r.escola AS escola_2025,
       r.situacao AS situacao_2025,r.etapa AS etapa_2025,
       (SELECT COUNT(*) FROM matriculas m WHERE m.student_id=r.student_id AND m.ano=2026) AS quantidade_matriculas_2026,
       (SELECT COUNT(DISTINCT m.inep) FROM matriculas m WHERE m.student_id=r.student_id AND m.ano=2026) AS escolas_distintas_2026,
       (SELECT group_concat(DISTINCT e.nome) FROM matriculas m JOIN escolas e ON e.inep=m.inep
        WHERE m.student_id=r.student_id AND m.ano=2026) AS nomes_escolas_2026,
       CASE
         WHEN NOT EXISTS(SELECT 1 FROM matriculas m WHERE m.student_id=r.student_id AND m.ano=2026)
              THEN 'NAO_LOCALIZADO'
         WHEN (SELECT COUNT(DISTINCT m.inep) FROM matriculas m WHERE m.student_id=r.student_id AND m.ano=2026)>1
              THEN 'MULTIPLAS_ESCOLAS'
         WHEN EXISTS(SELECT 1 FROM matriculas m WHERE m.student_id=r.student_id AND m.ano=2026 AND m.inep=r.inep)
              THEN 'MESMA_ESCOLA'
         ELSE 'OUTRA_ESCOLA_DA_REDE'
       END AS localizacao_2026
FROM vw_resultados_detalhados r WHERE r.ano=2025;
