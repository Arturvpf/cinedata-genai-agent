# CineData Analytics — Agente GenAI Text-to-SQL

Projeto Python para consultar o catálogo de filmes da CineData Analytics em linguagem natural. O agente usará um modelo via OpenRouter para gerar consultas SQLite, validará o SQL antes da execução e apresentará os resultados em português por uma interface de linha de comando.

O desenvolvimento está organizado em etapas. O repositório contém a estrutura inicial do pacote, as dependências, a leitura de configuração, a conexão SQLite em modo somente leitura, a inspeção de esquema e dados, o contexto do esquema, a extração das respostas SQL, os guardrails, o executor com limites de leitura, o cliente OpenRouter e o agente com geração de SQL, com testes automatizados. A execução pelo agente, a recuperação de erros, a resposta final e a CLI serão adicionadas nos próximos passos.

## Banco de dados local

A atividade fornece `cinerocket.db` dentro do arquivo `cinerocket-db.zip`. Extraia o banco para a raiz do projeto com esse nome. O banco fica fora do Git porque o arquivo descompactado tem cerca de 581 MB e ultrapassa o limite de tamanho de arquivo do GitHub. O ZIP original também não é versionado.

O banco fornecido contém dez tabelas de dados do modelo dimensional e uma tabela adicional de controle de migração (`alembic_version`). O projeto consultará o esquema real do arquivo, sem presumir nomes de colunas.

## Requisitos

- Python 3.11 ou superior.
- O arquivo local `cinerocket.db` para as consultas.
- Uma chave OpenRouter para chamadas reais ao modelo.

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

O [roteador gratuito `openrouter/free`](https://openrouter.ai/openrouter/free) seleciona um modelo gratuito disponível. O modelo pode ser configurado pelo `.env` sem editar o código e já é utilizado pelo cliente da API.

### Leitura das configurações

A função `load_settings`, em `src/cinedata/config.py`, lê o `.env` em UTF-8, inclusive com BOM do Windows, e devolve configurações imutáveis. Variáveis de ambiente têm prioridade sobre os valores do arquivo, inclusive quando estão vazias. O carregamento não altera o ambiente do processo, não abre o banco e não faz chamadas à API.

```python
from cinedata.config import load_settings
from cinedata.exceptions import ConfigurationError

try:
    settings = load_settings()
except ConfigurationError as error:
    print(error)
else:
    print(settings.model)
    print(settings.database_path)
```

Por padrão, apenas o `.env` da pasta atual é lido. É possível indicar outro arquivo com `load_settings("config/.env")`; não há busca automática em pastas superiores. Um `DATABASE_PATH` relativo é resolvido a partir da pasta desse arquivo, mesmo quando o valor vem do ambiente. Caminhos absolutos são preservados e `~` é expandido. A existência e a validade do banco são verificadas ao abrir a conexão.

O `.env` é opcional quando a chave já está definida no ambiente. A chave é obrigatória; sua ausência levanta `MissingAPIKeyError`, com orientação em português. Se modelo e caminho não forem definidos, os padrões são `openrouter/free` e `cinerocket.db`. Valores explicitamente vazios ou com caracteres de controle são rejeitados com `ConfigurationError`. Os valores são lidos diretamente, sem expansão de referências `${VAR}` no `.env`.

A chave é omitida de `repr(settings)` e `str(settings)`. Não imprima `settings.api_key`; ela será utilizada somente pelo cliente da API. O carregador não registra os valores de configuração em logs.

Nesta etapa, a instalação não envia chamadas ao OpenRouter nem consome sua cota. As instruções de execução da CLI serão adicionadas quando essa funcionalidade estiver pronta.

## Cliente OpenRouter

O `OpenRouterClient`, em `src/cinedata/llm.py`, utiliza o SDK OpenAI instalado com a base URL `https://openrouter.ai/api/v1` e a chave OpenRouter de `Settings`. O método `complete` envia uma mensagem `system` e uma mensagem `user` pelo endpoint de Chat Completions e devolve texto. A inicialização não realiza requisições. O cliente pode ser reutilizado e não conserva histórico de conversas.

O exemplo abaixo faz **uma chamada real** quando executado com uma chave válida. Os testes automatizados usam transporte HTTP simulado e não consomem cota.

```python
from cinedata.config import load_settings
from cinedata.exceptions import CineDataError
from cinedata.llm import OpenRouterClient

try:
    settings = load_settings()
    with OpenRouterClient(settings) as client:
        text = client.complete(
            "Responda em português de forma breve.",
            "Diga olá em uma frase.",
            max_tokens=100,
        )
        print(text)
except CineDataError as error:
    print(error)
```

Cada `complete` realiza uma única tentativa. O SDK recebe `max_retries=0`: falhas de rede, HTTP 429 e erros do provedor não provocam repetições automáticas. Não há troca automática de modelo. A futura correção de SQL será uma operação separada do agente, limitada a uma tentativa.

O timeout de rede padrão é de trinta segundos; `timeout_seconds` aceita valores maiores que zero e até cento e vinte segundos. Esse timeout controla as operações de rede do SDK e não representa um prazo total para toda a geração. A chamada usa `max_tokens=2048` por padrão, configurável entre um e 8192 tokens. O cliente aceita até cem mil caracteres no conjunto dos prompts e até cinquenta mil caracteres na resposta textual. A validação de SQL mantém seu limite próprio de vinte mil caracteres.

O cliente exige uma única resposta textual finalizada. Respostas vazias, malformadas, incompletas por limite de tokens, recusas e chamadas de ferramentas são rejeitadas com `InvalidModelResponseError`; nenhum SQL é executado por esse módulo. A interpretação de `finish_reason` segue o formato de [Chat Completions da documentação oficial OpenAI](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).

Erros de autenticação/acesso levantam `LLMAuthenticationError`; limite de requisições levanta `LLMRateLimitError`; timeout de rede levanta `LLMTimeoutError`. Falhas de conexão, créditos insuficientes, modelo inexistente e outros erros HTTP levantam `LLMServiceError`, com mensagens em português. O código HTTP fica disponível em `status_code`, quando aplicável. A aplicação não imprime nem registra o corpo do erro HTTP, a chave, os prompts ou o conteúdo gerado. Use o bloco `with` ou chame `close()` para fechar o cliente, inclusive após falhas.

## Conexão SQLite

O módulo `src/cinedata/database.py` abre apenas arquivos existentes, com `mode=ro`, e ativa `query_only`. A conexão é fechada ao sair do bloco `with`, inclusive se uma consulta falhar. Caminhos com espaços e caracteres especiais são convertidos em URI pelo `pathlib`.

Depois da instalação, a conexão pode ser usada em Python:

```python
from cinedata.database import readonly_connection

with readonly_connection("cinerocket.db") as connection:
    row = connection.execute("SELECT COUNT(*) AS total FROM dim_movies").fetchone()
    print(row["total"])
```

O módulo também desativa `trusted_schema` e apresenta erros específicos para arquivo ausente ou inválido. Para consultas geradas pelo modelo, use também a validação e o autorizador descritos na seção de guardrails.

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

A extração preserva comentários, literais e todas as instruções presentes na resposta. Ela não executa o SQL. A consulta extraída deve passar por `validate_sql` e pela autorização do SQLite antes da execução.

## Prompt Text-to-SQL

O módulo `src/cinedata/prompts.py` centraliza as instruções para geração de SQL. `build_sql_prompt` recebe uma pergunta e o contexto JSON produzido por `format_schema_for_llm`, sem abrir o banco nem chamar o modelo. Ele devolve `PromptMessages` com as mensagens `system` e `user` separadas. A pergunta, o esquema, a data de referência e o limite de resultados ficam em um objeto JSON na mensagem `user`; aspas, quebras de linha e nomes especiais são preservados como dados.

```python
from datetime import date
from cinedata.database import readonly_connection
from cinedata.prompts import build_sql_prompt
from cinedata.schema import format_schema_for_llm, inspect_schema

with readonly_connection("cinerocket.db") as connection:
    schema_context = format_schema_for_llm(inspect_schema(connection))

messages = build_sql_prompt(
    "Quais são os cinco filmes com maior bilheteria?",
    schema_context,
    reference_date=date(2026, 10, 4),
    max_rows=100,
)
# Preparar as mensagens não faz chamada à API.
print(messages.user)
```

O agente envia `messages.system` e `messages.user` ao modelo. A saída solicitada é exclusivamente `{"sql":"SELECT ..."}`, sem Markdown nem explicações. Se o esquema não permitir responder, a instrução é retornar SQL vazio, que a sanitização rejeita. Isso evita instruir o modelo a preencher lacunas com resultados inventados.

As regras orientam o uso de nomes reais, JOINs pelas FKs, chaves compostas, aliases, agregações, tratamento de `NULL`, divisão real e proteção contra divisão por zero. Também orientam a evitar duplicações de valores financeiros ao combinar pontes e relações de várias linhas por filme. Listas sem quantidade pedida usam o limite informado; quantidades explícitas são preservadas para que o executor possa identificar resultado parcial.

O prompt recebe uma data de referência explícita ou usa a data local atual. Para “últimos N anos” sem outro intervalo, o critério é uma janela móvel entre essa data menos N anos e a data de referência, incluindo as extremidades e excluindo lançamentos futuros. Perguntas com anos de calendário explícitos seguem os anos solicitados.

### Critérios de análise

- Receita, faturamento e bilheteria são tratados como sinônimos financeiros.
- A moeda padrão é USD; pedidos em reais usam BRL. Receita e orçamento devem usar a mesma moeda.
- Lucro é `Receita - Orçamento`, com ambos os valores informados.
- A margem adotada é o retorno percentual sobre orçamento: `100.0 * (Receita - Orçamento) / NULLIF(Orçamento, 0)`, com orçamento positivo e receita não nula.
- Receita informada significa receita não nula. Valores ausentes não são substituídos automaticamente por zero.
- Para papéis de pessoas, o contexto descreve os valores `Ator`, `Diretor` e `Roteirista` observados no banco fornecido.
- Avaliações individuais e indicadores agregados por filme recebem orientações diferentes; as notas de usuários observadas estão na faixa de 0 a 10.

As orientações de domínio são incluídas somente quando as tabelas e colunas correspondentes existem no contexto. As observações de valores referem-se ao banco fornecido e estão detalhadas em [docs/database.md](docs/database.md). Perguntas podem explicitar outra definição de margem ou outros critérios de análise.

O construtor rejeita perguntas vazias, perguntas acima de quatro mil caracteres, contexto inválido ou acima de sessenta mil caracteres, ausência de tabelas e metadados básicos inválidos. A separação das mensagens orienta o modelo a tratar pedidos embutidos na pergunta ou no esquema como dados; a proteção efetiva continua sendo a validação SQL, o autorizador e a conexão de leitura. Os testes desta etapa verificam a montagem das mensagens; a qualidade do SQL produzido por um modelo real ainda depende da avaliação do agente.

## Geração de SQL pelo agente

O `CineDataAgent`, em `src/cinedata/agent.py`, reúne o fluxo `pergunta → prompt com esquema → cliente → extração → validação textual`. Na inicialização, ele abre o banco em modo somente leitura, coleta os metadados, exclui `alembic_version` e fecha a conexão. O esquema é guardado em memória e reutilizado nas gerações seguintes; a inicialização não chama o modelo nem coleta amostras dos dados.

O exemplo abaixo faz **uma chamada real ao modelo** quando executado com chave válida. Nesta etapa, ele mostra apenas o SQL gerado e validado textualmente, sem executá-lo.

```python
from cinedata.agent import CineDataAgent
from cinedata.config import load_settings
from cinedata.exceptions import CineDataError
from cinedata.llm import OpenRouterClient

try:
    settings = load_settings()
    with OpenRouterClient(settings) as client:
        agent = CineDataAgent(settings.database_path, client, max_rows=100)
        sql = agent.generate_sql("Quais são os cinco filmes com maior bilheteria?")
        print(sql)
except (CineDataError, ValueError) as error:
    print(error)
```

`generate_sql` faz uma única chamada por pergunta, aceita os formatos suportados por `sanitize_sql` e passa o SQL extraído por `validate_sql`. Perguntas inválidas param antes da chamada. Respostas inválidas, consultas bloqueadas e erros da API são propagados sem repetição automática e sem executar SQL. A verificação completa de sintaxe, nomes de colunas e permissões continua sob responsabilidade do executor SQLite; a ligação com esse executor será implementada na próxima etapa.

O agente recebe o cliente pelo contrato simples `TextCompletionClient` e não é responsável por fechá-lo. O bloco `with OpenRouterClient(...)` administra esse recurso. As propriedades `schema_context` e `allowed_tables` expõem, respectivamente, os metadados sem linhas e os nomes das tabelas desse mesmo contexto para uso pelo executor. O caminho do banco é resolvido uma vez para permanecer estável se a pasta atual mudar. Se o esquema do arquivo for alterado, crie uma nova instância para recolher os metadados atualizados.

Os logs dessa etapa registram inicialização, geração e rejeições, sem registrar a pergunta, o SQL ou valores dos dados. Os testes incluem um fluxo com o SDK real e transporte HTTP simulado. Isso verifica a integração dos módulos sem avaliar a qualidade de um modelo externo nem consumir cota da API.

## Guardrails para consultas

A função `validate_sql` permite uma única instrução iniciada por `SELECT` ou `WITH` de leitura. A análise distingue comentários, literais e identificadores entre aspas; ponto e vírgula dentro de um texto não conta como outra instrução. Comandos de escrita ou configuração, múltiplas instruções, funções perigosas e funções `pragma_*` são bloqueados. A sintaxe completa continua sendo verificada pelo SQLite.

O `install_readonly_authorizer` instala uma política na conexão usando o [autorizador do SQLite](https://www.sqlite.org/c3ref/set_authorizer.html). Durante a compilação da consulta, ele permite somente leituras das tabelas informadas e uma lista explícita de funções de consulta, como `COUNT`, `SUM`, `AVG`, funções de datas e funções de janela. Operações de escrita, configuração, acesso a outras tabelas e funções fora dessa lista são negadas antes da execução.

Colete os metadados antes de instalar a política. Use uma conexão nova, exclusiva para consultas, que contenha somente o banco principal; a instalação rejeita conexões com bancos anexados ou temporários. O autorizador permanece ativo até o fechamento da conexão.

```python
from cinedata.database import readonly_connection
from cinedata.guardrails import (
    install_readonly_authorizer,
    sanitize_sql,
    validate_sql,
)
from cinedata.schema import inspect_schema

with readonly_connection("cinerocket.db") as connection:
    schema = inspect_schema(connection)
    allowed_tables = {table.name for table in schema if table.name != "alembic_version"}
    install_readonly_authorizer(connection, allowed_tables)
    sql = validate_sql(sanitize_sql("SELECT titulo FROM dim_movies LIMIT 5"))
    rows = connection.execute(sql).fetchall()
    for row in rows:
        print(row["titulo"])
```

Erros de formato levantam `InvalidModelResponseError`; consultas rejeitadas pela validação ou pela instalação da política levantam `QueryBlockedError`. Uma operação negada pelo autorizador durante `execute` levanta um erro do SQLite, e o objeto retornado pela instalação registra o motivo em `denied_reason`. O executor abaixo converte esses bloqueios em erros da aplicação.

## Execução segura de consultas

A função `execute_readonly`, em `src/cinedata/database.py`, recebe SQL extraído e as tabelas de dados permitidas pela aplicação. Ela valida a consulta, abre uma conexão exclusiva em modo somente leitura, instala o autorizador e devolve um `QueryResult` imutável com `sql`, `columns`, `rows`, `truncated` e `elapsed_seconds`. Os nomes das colunas, a ordem das linhas e os valores originais são preservados, inclusive `NULL` e BLOBs.

```python
from cinedata.database import execute_readonly, readonly_connection
from cinedata.guardrails import sanitize_sql
from cinedata.schema import inspect_schema

with readonly_connection("cinerocket.db") as connection:
    schema = inspect_schema(connection)
allowed_tables = {table.name for table in schema if table.name != "alembic_version"}

result = execute_readonly(
    "cinerocket.db",
    sanitize_sql("SELECT titulo FROM dim_movies ORDER BY titulo LIMIT 5"),
    allowed_tables=allowed_tables,
    max_rows=100,
    timeout_seconds=5.0,
)
print(result.columns)
for row in result.rows:
    print(row)
if result.truncated:
    print("Resultado parcial: há mais linhas do que o limite retornado.")
```

Por padrão, o executor retorna até cem linhas. `max_rows` aceita de uma a mil linhas. Ele busca apenas uma linha adicional para detectar resultado parcial, sem acrescentar `LIMIT` ou reescrever a consulta. `truncated=True` indica que o resultado retornado não contém todas as linhas; não fornece a contagem total. Resultados vazios conservam os nomes das colunas.

O prazo padrão é de cinco segundos, configurável com `timeout_seconds` maior que zero e até sessenta segundos. Um [progress handler](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.set_progress_handler) interrompe consultas demoradas e o prazo também é verificado durante a leitura das linhas. A espera por bloqueios do banco é limitada ao menor valor entre esse prazo e cinco segundos. O prazo da consulta começa após a abertura e configuração da conexão; a abertura tem seu próprio timeout de cinco segundos.

O executor reduz os [limites nativos do SQLite](https://www.sqlite.org/limits.html) para cem colunas, valores/linhas codificadas de até um milhão de bytes e SQL de até oitenta mil bytes, mantendo a validação de até vinte mil caracteres. O conteúdo das colunas e linhas retornadas também possui um orçamento de um milhão de bytes: textos são medidos em UTF-8, BLOBs pelo tamanho e números/`NULL` por uma estimativa de oito bytes. Ao exceder esse orçamento, o executor rejeita o resultado sem truncar valores individuais. Esses limites não representam um teto para toda a memória utilizada pelo processo.

Bloqueios de segurança levantam `QueryBlockedError`; prazo excedido levanta `QueryTimeoutError`; tamanho excedido levanta `QueryLimitError`. Outros erros SQLite são convertidos em `QueryExecutionError`, com mensagem em português. Para erros de SQL, `recoverable=True` e `sqlite_error` preserva o diagnóstico para uma futura tentativa de correção. Timeout e excesso de tamanho não são recuperáveis. O executor não faz chamadas ao modelo nem repete consultas; a única tentativa de correção será implementada no agente. A conexão é fechada em todos os casos, e o log registra somente quantidade de linhas, indicador de resultado parcial e tempo, sem valores retornados.

## Testes

Com o ambiente virtual ativo, execute:

```bash
python -m pytest -v
```

No PowerShell, sem ativar o ambiente:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

Os testes atuais criam bancos temporários e verificam leitura, bloqueio de escrita, caminhos especiais, arquivos ausentes ou inválidos, fechamento da conexão, introspecção, contagens, amostras limitadas, contexto JSON, extração das respostas SQL, guardrails e execução segura. Há casos para PK/FK compostas, colunas geradas, referências implícitas, textos longos, BLOBs e nomes de tabela contendo aspas e pontuação SQL. Também verificam que o contexto não inclui valores das linhas, que a extração preserva o SQL para validação posterior e que o autorizador bloqueia escrita, tabelas não permitidas e funções perigosas mesmo sem a validação textual. Os testes comprovam que funções bloqueadas não chegam a ser chamadas, que consultas recursivas sem fim são interrompidas e que limites de linhas e bytes são aplicados. Os testes de configuração usam chaves fictícias para verificar precedência do ambiente, erros de configuração, caminhos relativos e absolutos, UTF-8/BOM e ocultação da chave na representação textual. Os testes do cliente usam o SDK real com transporte HTTP simulado para verificar requisições, autenticação, ausência de retries, erros de rede/HTTP, validação de respostas e fechamento do cliente. Os testes dos prompts verificam serialização, preservação de nomes/chaves, seleção das orientações conforme as colunas disponíveis e limites de entrada. Os testes do agente verificam coleta única do esquema, geração com uma chamada, rejeição de escrita, erros sem repetição e ausência de execução durante a geração. Eles não dependem do banco da atividade nem de chave OpenRouter e não enviam chamadas reais à API.
