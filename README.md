# CineData Analytics — Agente GenAI Text-to-SQL

Projeto Python para consultar o catálogo de filmes da CineData Analytics em linguagem natural. O agente usará um modelo via OpenRouter para gerar consultas SQLite, validará o SQL antes da execução e apresentará os resultados em português por uma interface de linha de comando.

O desenvolvimento está organizado em etapas. O repositório contém a estrutura inicial do pacote, as dependências, o exemplo de configuração, a conexão SQLite em modo somente leitura, a inspeção de esquema e dados, o contexto do esquema e a extração das respostas SQL do modelo, com testes automatizados. A validação de consultas, o agente e a CLI serão adicionados nos próximos passos.

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

## Inspeção do esquema

Com o ambiente virtual ativo, execute:

```bash
python scripts/inspect_database.py
```

O script abre o `cinerocket.db` da raiz do projeto em modo somente leitura e mostra tabelas, colunas, tipos declarados, valores padrão, chaves primárias, chaves estrangeiras, contagens e pequenas amostras. Para outro arquivo:

```bash
python scripts/inspect_database.py --database "caminho/do/banco.db"
```

A função `list_tables`, em `src/cinedata/schema.py`, consulta o catálogo do banco principal, exclui as tabelas internas `sqlite_*` e preserva as tabelas de controle da aplicação. O banco fornecido retorna dez tabelas do modelo dimensional e `alembic_version`. Views, índices e triggers não são incluídos nessa listagem.

As funções `inspect_table` e `inspect_schema` retornam estruturas imutáveis definidas em `src/cinedata/models.py`. Elas leem `table_xinfo` e `foreign_key_list` por funções PRAGMA com parâmetros, preservando nomes especiais sem interpolar SQL. As chaves compostas mantêm a ordem declarada. Referências sem coluna de destino explícita conservam `None`, indicando uma referência à PK da tabela de destino.

O indicador `NOT NULL declarado` reproduz o metadado do SQLite; ele não infere nulabilidade a partir de outras restrições. O campo `hidden` identifica colunas ocultas ou geradas, quando presentes. Os tipos também são os declarados no esquema, sem inferência a partir das linhas.

No banco da atividade, as três tabelas `bridge_movie_*` ligam filmes a gêneros, pessoas e produtoras. `fact_movies_performance`, `dim_reviews` e `movie_reviews` referenciam `dim_movies` por `sk_movie_id`. Esses relacionamentos são extraídos das FKs declaradas no arquivo.

Por padrão, a inspeção conta todos os registros e mostra até três linhas por tabela, ordenadas pela PK quando disponível. Textos acima de 160 caracteres são truncados com `...` antes de chegar ao Python. BLOBs aparecem apenas com seu tamanho, sem expor o conteúdo binário. Esses resultados são diagnósticos locais e não integram o contexto do modelo.

```bash
# Somente esquema, sem contagens nem leitura de amostras
python scripts/inspect_database.py --schema-only

# Contagens sem amostras
python scripts/inspect_database.py --sample-rows 0

# Cinco linhas por tabela, com textos limitados a 100 caracteres
python scripts/inspect_database.py --sample-rows 5 --max-cell-chars 100
```

O limite máximo é de dez linhas por tabela e mil caracteres por texto. Contagens são exatas e podem custar mais tempo em bancos grandes; use `--schema-only` para evitá-las. Os modos `--schema-only` e `--llm-context` são mutuamente exclusivos. O parâmetro `--database` escolhe o arquivo; o script ainda não lê `DATABASE_PATH` do `.env`.

As contagens, os papéis das pessoas e as faixas de notas observados no banco fornecido estão registrados em [docs/database.md](docs/database.md).

## Contexto do esquema para o modelo

Para visualizar a representação que será utilizada na geração de SQL:

```bash
python scripts/inspect_database.py --llm-context
```

A função `format_schema_for_llm` recebe os metadados já coletados e produz JSON compacto com dialeto SQLite, nomes, tipos, indicadores de `NOT NULL`, colunas ocultas/geradas, PKs e FKs. Ela não consulta o banco nem inclui linhas dos dados. O JSON preserva nomes com aspas e outros caracteres especiais. Em FKs implícitas, `null` representa uma referência à PK da tabela de destino, disponível no mesmo esquema.

A inspeção normal preserva `alembic_version`; o contexto para o modelo exclui essa tabela de controle por padrão. O contexto do banco da atividade contém as dez tabelas de dados. No código, o parâmetro `excluded_tables` permite alterar as exclusões. As tabelas são ordenadas para que o contexto permaneça estável e possa ser reutilizado em memória pelo agente.

## Extração da resposta SQL

A função `sanitize_sql`, em `src/cinedata/guardrails.py`, extrai uma consulta de SQL puro, de um objeto JSON com somente o campo `sql` ou de um bloco Markdown completo (`sql`, `json` ou sem rótulo). Ela rejeita respostas vazias, JSON inválido, chaves duplicadas, blocos incompletos, formatos ambíguos e respostas acima de 20 mil caracteres.

```python
from cinedata.guardrails import sanitize_sql

sql = sanitize_sql('{"sql": "SELECT titulo FROM dim_movies LIMIT 5"}')
print(sql)
```

A extração preserva comentários, literais e todas as instruções presentes na resposta. Ela não executa o SQL. A validação de somente leitura e de uma única instrução será implementada na próxima etapa, antes de conectar respostas do modelo à execução.

## Testes

Com o ambiente virtual ativo, execute:

```bash
python -m pytest -v
```

No PowerShell, sem ativar o ambiente:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

Os testes atuais criam bancos temporários e verificam leitura, bloqueio de escrita, caminhos especiais, arquivos ausentes ou inválidos, fechamento da conexão, introspecção, contagens, amostras limitadas, contexto JSON e extração das respostas SQL. Há casos para PK/FK compostas, colunas geradas, referências implícitas, textos longos, BLOBs e nomes de tabela contendo aspas e pontuação SQL. Também verificam que o contexto não inclui valores das linhas e que a extração preserva o SQL para validação posterior. Eles não dependem do banco da atividade nem de chave OpenRouter.
