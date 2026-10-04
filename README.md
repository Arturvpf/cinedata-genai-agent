# CineData Analytics — Agente GenAI Text-to-SQL

Projeto Python para consultar o catálogo de filmes da CineData Analytics em linguagem natural. O agente usará um modelo via OpenRouter para gerar consultas SQLite, validará o SQL antes da execução e apresentará os resultados em português por uma interface de linha de comando.

O desenvolvimento está organizado em etapas. O repositório contém a estrutura inicial do pacote, as dependências, o exemplo de configuração, a conexão SQLite em modo somente leitura e a listagem reutilizável de tabelas, com testes automatizados. A introspecção de colunas e relacionamentos, o agente e a CLI serão adicionados nos próximos passos.

## Banco de dados local

A atividade fornece `cinerocket.db` dentro do arquivo `cinerocket-db.zip`. Extraia o banco para a raiz do projeto com esse nome. O banco fica fora do Git porque o arquivo descompactado tem cerca de 581 MB e ultrapassa o limite de tamanho de arquivo do GitHub. O ZIP original também não é versionado.

O banco fornecido contém dez tabelas de dados do modelo dimensional e uma tabela adicional de controle de migração (`alembic_version`). O projeto consultará o esquema real do arquivo, sem presumir nomes de colunas.

## Requisitos

- Python 3.11 ou superior.
- O arquivo local `cinerocket.db` para as consultas.
- Uma chave OpenRouter quando a integração com o modelo estiver pronta.

## Instalação

Abra um terminal na raiz do projeto. Crie um ambiente virtual para manter as dependências separadas das de outros projetos.

### Windows — PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Se o PowerShell bloquear a ativação, você pode usar diretamente o Python do ambiente:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Linux/macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

O `requirements.txt` instala o pacote local em modo editável e inclui o pytest. As versões das dependências diretas ficam definidas no `pyproject.toml`: OpenAI Python SDK para o cliente compatível com OpenRouter, python-dotenv para o arquivo `.env` e pytest para os testes.

## Configuração local

Crie sua conta no [OpenRouter](https://openrouter.ai/) e gere uma chave na [página de chaves](https://openrouter.ai/keys). Copie o arquivo de exemplo:

```powershell
# Windows — PowerShell
Copy-Item .env.example .env
```

```bash
# Linux/macOS
cp .env.example .env
```

Edite o `.env` local e preencha `OPENROUTER_API_KEY`. A chave fica fora do Git. O exemplo está sem chave e pode ser versionado com segurança.

| Variável | Uso | Valor do exemplo |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | Chave da API OpenRouter | Vazio; preencha localmente |
| `OPENROUTER_MODEL` | Identificador do modelo | `openrouter/free` |
| `DATABASE_PATH` | Caminho do arquivo SQLite | `cinerocket.db` |

O [roteador gratuito `openrouter/free`](https://openrouter.ai/openrouter/free) seleciona um modelo gratuito disponível. Você poderá trocar o modelo pelo `.env` sem editar o código. A integração com a API e a leitura dessas configurações serão implementadas nas próximas etapas.

Nesta etapa, a instalação não envia chamadas ao OpenRouter nem consome sua cota. As instruções de execução da CLI serão adicionadas quando essa funcionalidade estiver pronta.

## Conexão SQLite

O módulo `src/cinedata/database.py` abre apenas arquivos existentes, com `mode=ro`, e ativa `query_only`. A conexão é fechada ao sair do bloco `with`, inclusive se uma consulta falhar. Caminhos com espaços e caracteres especiais são convertidos em URI pelo `pathlib`.

Depois da instalação, a conexão pode ser usada em Python:

```python
from cinedata.database import readonly_connection

with readonly_connection("cinerocket.db") as connection:
    row = connection.execute("SELECT COUNT(*) AS total FROM dim_movies").fetchone()
    print(row["total"])
```

O módulo também desativa `trusted_schema` e apresenta erros específicos para arquivo ausente ou inválido. A validação do SQL gerado pelo modelo será adicionada na etapa de guardrails.

## Inspeção de tabelas

Com o ambiente virtual ativo, execute:

```bash
python scripts/inspect_database.py
```

O script abre o `cinerocket.db` da raiz do projeto em modo somente leitura e lista as tabelas reais em ordem alfabética. Para outro arquivo:

```bash
python scripts/inspect_database.py --database "caminho/do/banco.db"
```

A função `list_tables`, em `src/cinedata/schema.py`, pode ser reutilizada pelo agente. Ela consulta o catálogo do banco principal, exclui as tabelas internas `sqlite_*` e preserva as tabelas de controle da aplicação. O banco fornecido retorna dez tabelas do modelo dimensional e `alembic_version`. Views, índices e triggers não são incluídos nessa listagem.

Nesta etapa, o script mostra apenas os nomes das tabelas. Colunas, chaves, contagens e amostras serão acrescentadas nas próximas etapas de introspecção. O parâmetro `--database` escolhe o arquivo; o script ainda não lê `DATABASE_PATH` do `.env`.

## Testes

Com o ambiente virtual ativo, execute:

```bash
python -m pytest -v
```

No PowerShell, sem ativar o ambiente:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

Os testes atuais criam bancos temporários e verificam leitura, bloqueio de escrita, caminhos especiais, arquivos ausentes ou inválidos, fechamento da conexão e listagem de tabelas. Eles não dependem do banco da atividade nem de chave OpenRouter.
