# Sistema de Fluxo Escolar

Aplicativo desktop em Python/PySide6 para comparar matrículas de 2025 e 2026, consultar a situação final de 2025 e apoiar a Busca Ativa Escolar. Inclui consultas de infraestrutura e transporte escolar.

**Esta distribuição contém código e estrutura do banco. Não inclui cadastros de alunos, bancos preenchidos, relatórios reais ou planilhas de origem.** Cada instalação cria seu próprio banco SQLite vazio.

## Instalação e primeiro uso

Requer Python 3.11 ou superior, de 64 bits. No Windows, extraia o projeto e execute `INICIAR.bat`. A primeira execução precisa de internet se as dependências ainda não estiverem instaladas. O inicializador prepara o banco local sem importar dados e abre a interface.

Instalação manual (Windows, Linux ou macOS):

```sh
python -m venv .venv
```

Ative no Windows com `.venv\Scripts\activate`; no Linux/macOS, com `source .venv/bin/activate`. Depois:

```sh
python -m pip install -r requirements.txt
python inicializar_banco.py
python gui.py
```

O banco local padrão é `fluxo_escolar.sqlite`, na pasta do projeto. Ele não deve ser enviado ao GitHub. A pasta precisa permitir gravação.

## Recursos

- Importação em lote de arquivos XLSX contidos em ZIP, com identificação de escola, ano e tipo de informação.
- Comparação pelo ID único do aluno e código INEP da escola.
- Painel gerencial, filtros nominais e listas prioritárias de Busca Ativa.
- Mobilidade entre escolas, vínculos múltiplos e auditoria da importação.
- Relatórios institucionais por escola em PDF, painel consolidado e relações filtradas em PDF/Excel.
- Importação de infraestrutura e consultas de transporte, com exportações.
- Exportação de CSV e cópia SQL para uso administrativo local.

## Documentação

- [Manual de uso](docs/MANUAL.md)
- [Arquitetura e banco de dados](docs/ARQUITETURA.md)
- [Publicação no GitHub e cuidados com dados](docs/PUBLICACAO.md)
- [Validação da distribuição](docs/VALIDACAO.md)

## Testes sem dados reais

```sh
python -m unittest discover -s tests -v
```

Os testes criam bancos temporários e usam apenas registros fictícios.

## Limites conhecidos

A comparação está vinculada a 2025–2026 em consultas e relatórios. Não é um sistema genérico de seleção de quaisquer anos. Ausência de matrícula na base importada não comprova evasão. Indicadores internos não equivalem ao IDEB oficial. Matrículas e alunos distintos são medidas diferentes: confira cada relatório antes de agregar vínculos de AEE com escolarização regular.

Este é um aplicativo desktop; publicar o código no GitHub não o transforma em um sistema web nem hospeda o banco. Não há autenticação centralizada ou gestão de usuários neste pacote. Proteja o computador e os arquivos locais.

## Licenciamento

Nenhuma licença de uso ou redistribuição foi definida nesta preparação. O responsável pelo projeto deve escolher uma licença antes de autorizar reutilização por terceiros. Marcas institucionais em `assets/` devem ser usadas com a autorização correspondente.
