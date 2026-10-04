# CineData Analytics — Agente GenAI Text-to-SQL

Projeto Python para consultar o catálogo de filmes da CineData Analytics em linguagem natural. O agente usa um modelo via OpenRouter para gerar consultas SQLite, valida o SQL antes da execução e apresenta os resultados em português por uma interface de linha de comando.

O desenvolvimento está organizado em etapas. O repositório contém o pacote Python, a leitura de configuração, a conexão SQLite em modo somente leitura, a inspeção de esquema e dados, o contexto do esquema, a extração das respostas SQL, os guardrails, o executor com limites de leitura, o cliente OpenRouter, o agente com correção única e resposta em português, a CLI e perguntas com consultas de referência para avaliação, com testes automatizados.

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

A instalação e a leitura da configuração não enviam chamadas ao OpenRouter nem consomem sua cota. Para fazer perguntas, use a CLI descrita na seção seguinte.

## Execução pela linha de comando

Depois de instalar as dependências, configurar o `.env` e colocar o `cinerocket.db` na raiz, execute no ambiente virtual:

```bash
python main.py
```

No PowerShell, também é possível executar sem ativar o ambiente:

```powershell
.\.venv\Scripts\python.exe main.py
```

Digite uma pergunta e pressione Enter. O agente mostra a resposta em português e espera a próxima pergunta. Para encerrar, digite `sair`, `exit` ou `quit`. Linhas em branco são ignoradas; EOF encerra a sessão; `Ctrl+C` interrompe a operação e fecha o cliente. Cada pergunta é independente, sem memória das anteriores. O esquema é coletado uma vez por sessão.

### Pergunta única e auditoria

```bash
python main.py --question "Quais são os cinco filmes com maior bilheteria?"
python main.py --debug --question "Quantos filmes existem no catálogo?"
python main.py --raw --question "Quais são os cinco filmes mais populares?"
python main.py --raw --debug
```

`--question` executa uma pergunta e encerra. Sem essa opção, a CLI é interativa. No modo padrão, mostra a resposta; `--debug` acrescenta pergunta, SQL executado, colunas, linhas, quantidade retornada e tempo da consulta SQLite final. Os logs das etapas aparecem em stderr e não incluem chave, pergunta, SQL, valores ou resposta gerada.

`--raw` usa `agent.query`: mostra SQL e dados sem enviar os resultados para redação pelo modelo. Normalmente consome uma chamada de geração; pode consumir mais uma para a correção única. O modo padrão normalmente faz duas chamadas, ou três com correção; nenhuma chamada de redação é feita para resultados sem linhas. Não há requisições automáticas ao iniciar ou ao encerrar a sessão.

A exibição raw/debug conserva a ordem das colunas e linhas, inclusive colunas com nomes repetidos, e mostra `NULL` como ausência. Textos são abreviados após 240 caracteres, BLOBs aparecem somente com seu tamanho e caracteres de controle do terminal são representados como texto. A CLI informa quando abrevia valores e quando o executor retorna um resultado parcial. Essa apresentação não é um formato de exportação JSON; os valores completos permanecem em `AgentResult.rows` na API Python.

### Configuração da execução

```bash
python main.py --env-file "config/local.env" --raw --max-rows 20 --query-timeout 10
python main.py --help
python -m cinedata --help
```

`--env-file` seleciona o arquivo de configuração; o padrão é `.env` na pasta de execução. O caminho relativo de `DATABASE_PATH` é resolvido contra a pasta desse arquivo. Variáveis de ambiente continuam tendo prioridade. `--max-rows` aceita de 1 a 1000, com padrão 100; `--query-timeout` aceita um prazo maior que zero e até 60 segundos, com padrão 5, aplicado separadamente a cada tentativa SQLite. O timeout da rede mantém o padrão do cliente OpenRouter. A ajuda funciona sem chave nem banco.

Perguntas devem ter até quatro mil caracteres. Erros de configuração, banco, API e SQL são apresentados em português sem stack traces. Se apenas a redação falhar, a CLI mostra o resultado SQL já obtido e não faz outra chamada. Em modo interativo, uma falha de consulta permite ao usuário fazer outra pergunta; não repete a anterior automaticamente.

Na pergunta única, os códigos de saída são: `0` para sucesso, `1` para falha de consulta/redação, `2` para erro de argumentos ou inicialização e `130` para interrupção. A saída voluntária ou EOF do modo interativo retorna `0`, mesmo que uma pergunta anterior tenha falhado.

### Exemplos de perguntas

- Quais são os dez filmes com maior receita em USD?
- Quais são os cinco filmes mais populares?
- Quantos filmes existem por gênero?
- Quais são os cinco filmes mais avaliados pelos usuários?
- Quais filmes apresentam a maior diferença entre nota dos usuários e nota IMDb?

As perguntas e os comandos de consulta acima usam a API real quando executados com uma chave válida. Os testes automatizados usam clientes simulados.

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

Cada `complete` realiza uma única tentativa. O SDK recebe `max_retries=0`: falhas de rede, HTTP 429 e erros do provedor não provocam repetições automáticas. Não há troca automática de modelo. A correção de SQL é uma operação separada do agente, limitada a uma tentativa.

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

`generate_sql` faz uma única chamada por pergunta, aceita os formatos suportados por `sanitize_sql` e passa o SQL extraído por `validate_sql`. Perguntas inválidas param antes da chamada. Respostas inválidas, consultas bloqueadas e erros da API são propagados sem repetição automática e sem executar SQL. A verificação completa de sintaxe, nomes de colunas e permissões ocorre no executor SQLite, usado pelo método `query` abaixo.

O agente recebe o cliente pelo contrato simples `TextCompletionClient` e não é responsável por fechá-lo. O bloco `with OpenRouterClient(...)` administra esse recurso. As propriedades `schema_context` e `allowed_tables` expõem, respectivamente, os metadados sem linhas e os nomes das tabelas desse mesmo contexto para uso pelo executor. O caminho do banco é resolvido uma vez para permanecer estável se a pasta atual mudar. Se o esquema do arquivo for alterado, crie uma nova instância para recolher os metadados atualizados.

Os logs dessa etapa registram inicialização, geração e rejeições, sem registrar a pergunta, o SQL ou valores dos dados. Os testes incluem um fluxo com o SDK real e transporte HTTP simulado. Isso verifica a integração dos módulos sem avaliar a qualidade de um modelo externo nem consumir cota da API.

## Consulta pelo agente

O método `CineDataAgent.query` conecta a geração ao executor protegido: `pergunta → modelo → extração → guardrails → SQLite → resultado`. Ele usa as tabelas do mesmo esquema enviado ao modelo e abre uma conexão exclusiva de execução em modo somente leitura, com autorizador e limites de recursos. A criação do agente continua fazendo apenas a introspecção local, sem chamada ao modelo.

O exemplo abaixo faz **uma chamada real ao modelo** e executa a consulta gerada no banco local. Se ocorrer um erro recuperável de SQL, pode haver mais uma chamada para correção. O método `query` retorna os dados sem fazer uma chamada para redigir a resposta; use `ask`, descrito abaixo, para essa etapa adicional.

```python
from cinedata.agent import CineDataAgent
from cinedata.config import load_settings
from cinedata.exceptions import CineDataError
from cinedata.llm import OpenRouterClient

try:
    settings = load_settings()
    with OpenRouterClient(settings) as client:
        agent = CineDataAgent(
            settings.database_path,
            client,
            max_rows=100,
            query_timeout_seconds=5.0,
        )
        result = agent.query("Quais são os cinco filmes com maior bilheteria?")
        print(result.sql)
        print(result.columns)
        for row in result.rows:
            print(row)
        if not result.rows:
            print("Nenhum resultado foi encontrado.")
        if result.truncated:
            print("Resultado parcial: há mais linhas do que o limite retornado.")
        if result.correction_attempted:
            print("Foi utilizada uma tentativa de correção do SQL.")
except (CineDataError, ValueError) as error:
    print(error)
```

O retorno `AgentResult` é imutável e acrescenta `question`, `correction_attempted`, `answer` e `answer_context_limited` aos campos do `QueryResult`: `sql`, `columns`, `rows`, `truncated` e `elapsed_seconds`. Em `query`, `answer=None` e `answer_context_limited=False`. O SQL retornado é o que produziu o resultado final. Esse tempo corresponde à execução e leitura da consulta SQLite final, sem incluir as chamadas ao modelo nem uma tentativa anterior que falhou. Resultados vazios conservam os nomes das colunas, e resultados parciais mantêm o indicador do executor.

O limite padrão é de cem linhas e cinco segundos para a consulta. `max_rows` aceita de uma a mil linhas; `query_timeout_seconds` deve ser maior que zero e até sessenta segundos. O timeout é validado antes da geração para evitar consumir uma chamada com configuração inválida. Os limites de bytes e colunas do executor também se aplicam.

`query` gera SQL e tenta executá-lo. Quando o executor informa um erro de SQL recuperável, o agente solicita uma única correção e tenta executar a nova consulta. Escrita, acesso fora do esquema e funções não permitidas levantam `QueryBlockedError`; timeout, limites de recursos e erros da API não provocam correção. Os detalhes estão na seção seguinte.

## Correção única de SQL

Erros recuperáveis do SQLite, como coluna ou tabela inexistente e erro de sintaxe, podem provocar uma chamada adicional ao modelo. `build_sql_correction_prompt`, em `src/cinedata/prompts.py`, envia a pergunta original, o mesmo esquema, os mesmos critérios e limites, o SQL que falhou e o diagnóstico SQLite como dados JSON. Não são enviadas linhas do banco para essa correção.

A consulta corrigida passa novamente por `sanitize_sql`, `validate_sql` e pelo executor protegido, incluindo o autorizador, o modo de leitura e os limites de tempo, linhas, bytes e colunas. A segunda execução usa uma conexão nova. Se a correção também falhar, o erro é propagado: não há terceira chamada para gerar/corrigir SQL nem terceira execução. Resposta corrigida vazia, malformada ou bloqueada encerra o fluxo antes da segunda execução.

O fluxo usa uma chamada de geração e, somente quando necessário, uma chamada de correção. Falhas de rede, autenticação, rate limit, configuração, banco indisponível, formato da resposta, segurança, timeout e tamanho/complexidade não provocam essa chamada adicional. Limites nativos de colunas, quantidade de termos em `UNION` e profundidade de expressões também são tratados como erros de recursos, sem correção automática.

O diagnóstico enviado é limitado a dois mil caracteres, com um indicador de truncamento. O SQL anterior conserva seu limite de vinte mil caracteres. As duas mensagens da correção, juntas, não podem exceder cem mil caracteres; o construtor rejeita contexto maior antes da chamada. A data de referência é capturada uma vez por `query` e permanece a mesma na correção, inclusive se o processo atravessar a meia-noite.

O prazo de execução se aplica separadamente a cada tentativa SQLite; não é um prazo total para as chamadas de rede e as duas execuções. `correction_attempted=True` no resultado indica que a correção foi utilizada; sucesso na primeira consulta deixa esse campo como `False`. A tentativa é registrada no log sem incluir a pergunta, o SQL, o diagnóstico bruto ou valores retornados.

## Resposta em português

`CineDataAgent.ask(pergunta)` realiza o mesmo fluxo protegido de `query` e acrescenta uma resposta em `AgentResult.answer`. Para resultados com linhas, faz uma chamada adicional ao modelo, sem repetição automática. O resultado conserva o SQL executado, as colunas, as linhas originais e os indicadores de correção e resultado parcial. Para nenhuma linha, devolve localmente “Nenhum resultado foi encontrado para esta consulta.”, sem consumir uma chamada de redação. Agregações com uma linha contendo zero ou `NULL` seguem para redação normalmente.

O exemplo abaixo faz **chamadas reais** com uma chave válida: normalmente duas (geração de SQL e redação), ou três se houver a correção única. Resultados vazios dispensam a redação. A CLI usa o mesmo método no modo padrão.

```python
from cinedata.agent import CineDataAgent
from cinedata.config import load_settings
from cinedata.exceptions import AnswerGenerationError, CineDataError
from cinedata.llm import OpenRouterClient

try:
    settings = load_settings()
    with OpenRouterClient(settings) as client:
        agent = CineDataAgent(settings.database_path, client)
        result = agent.ask("Quais são os cinco filmes com maior bilheteria?")
        print(result.answer)
        print(result.sql)
except AnswerGenerationError as error:
    print(error)
    print(error.result.sql)
    print(error.result.columns)
    for row in error.result.rows:
        print(row)
except (CineDataError, ValueError) as error:
    print(error)
```

O contexto da redação inclui a pergunta, o SQL final, colunas e até cinquenta linhas, sem o esquema completo ou o diagnóstico de correção. As linhas são listas posicionais para preservar colunas com nomes repetidos. Textos acima de mil caracteres são reduzidos com um indicador; BLOBs são representados somente pelo tamanho; números não finitos são marcados como indisponíveis. `NULL` e valores numéricos finitos são preservados.

O orçamento das duas mensagens da redação é de cinquenta mil caracteres, medido após serializar o JSON completo, incluindo aspas e escapes. Linhas que não couberem e as que excederem cinquenta são omitidas do contexto. Se os metadados ou a primeira linha não couberem, a redação é rejeitada antes de fazer outra chamada. Essas reduções não alteram `result.rows`: `answer_context_limited=True` sinaliza que alguma linha ou valor não foi enviado integralmente ao modelo.

A aplicação acrescenta avisos à resposta quando `truncated=True` ou `answer_context_limited=True`, mesmo que o modelo omita essa informação. `truncated` se refere ao limite do executor; `answer_context_limited` se refere à redução adicional para redação. Nenhum desses indicadores informa a contagem total de linhas disponíveis.

O prompt orienta o modelo a responder somente a partir dos resultados, tratar textos das linhas como dados e não completar informações omitidas. Ele diferencia ausência de dados de zero, exige atenção a moedas e escalas de notas e orienta a evitar conclusões globais baseadas em uma amostra parcial. Essas instruções não verificam automaticamente a correção semântica da resposta: o SQL e as linhas ficam disponíveis para conferência.

Texto retornado pelo modelo vazio, inválido, com NUL ou acima de vinte mil caracteres, falhas da API e contexto excessivo levantam `AnswerGenerationError`. Os avisos locais de limitação são acrescentados depois dessa validação. O resultado obtido continua acessível em `error.result`, e a causa fica em `error.__cause__`. Não há nova consulta, correção ou chamada de redação por causa dessa falha. Os logs registram a etapa e a classe do erro, sem pergunta, SQL, valores ou resposta gerada. Falhas na consulta continuam sendo propagadas antes de iniciar a redação.

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

Bloqueios de segurança levantam `QueryBlockedError`; prazo excedido levanta `QueryTimeoutError`; tamanho ou complexidade excedida levanta `QueryLimitError`. Outros erros SQLite são convertidos em `QueryExecutionError`, com mensagem em português. Para erros de SQL, `recoverable=True` e `sqlite_error` preserva o diagnóstico para a correção única do agente. Timeout e limites de recursos não são recuperáveis. O executor não faz chamadas ao modelo nem repete consultas; a tentativa de correção é administrada pelo agente. A conexão é fechada em todos os casos, e o log registra somente quantidade de linhas, indicador de resultado parcial e tempo, sem valores retornados.

## Perguntas e consultas de avaliação

`tests/evaluation_questions.json` reúne 21 perguntas, critérios de revisão e SQL de referência para as categorias do enunciado, perguntas adicionais e sinônimos de receita. Para listar ou executar as referências no banco local, sem chave e sem chamadas à API:

```bash
python scripts/evaluate_reference_queries.py --list
python scripts/evaluate_reference_queries.py
python scripts/evaluate_reference_queries.py --case top_receita --show-results
```

O avaliador usa o mesmo executor protegido, com limite de mil linhas e prazo padrão de trinta segundos por consulta. Não altera o banco. As referências extensas de elenco usam SQLite 3.35 ou superior. A pergunta dos últimos cinco anos utiliza a data fixa `2026-10-04`, substituível por `--reference-date`.

Executar o SQL de referência verifica essas consultas e seus resultados; não mede automaticamente a qualidade do modelo. O procedimento para comparar uma pergunta real, reproduzir a data e revisar critérios está em [docs/evaluation.md](docs/evaluation.md).

## Testes

Os testes de avaliação executam as 21 referências em um banco temporário, com valores controlados. Verificam sinônimos, empates, margem percentual, nulos, fronteiras de datas, papéis, mínimo de filmes avaliados, grupos vazios, diferenças de notas e agregações sem multiplicar filmes por outras pontes. Também validam o formato do conjunto e a execução do avaliador sem API.

Os testes da CLI verificam o fluxo com configuração e banco temporários, consulta única, interação, raw/debug, limites, correção única, erros de configuração/API/SQL, recuperação dos dados após falha de redação, saída por comando/EOF/Ctrl+C e fechamento do cliente. Também verificam que a ajuda e argumentos inválidos não acessam a API e que a saída não interpreta controles de terminal vindos dos dados.

Os testes de redação usam SQLite local e modelos simulados para verificar respostas, resultados vazios e parciais, agregações zero/`NULL`, correção seguida de redação, limites de contexto após escapes JSON, preservação das linhas originais e recuperação do resultado após falha da API. Também verificam que a redação não executa o texto do modelo e que os logs não expõem o conteúdo consultado. Nenhuma chamada real é feita nesses testes.

Com o ambiente virtual ativo, execute:

```bash
python -m pytest -v
```

No PowerShell, sem ativar o ambiente:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

Os testes atuais criam bancos temporários e verificam leitura, bloqueio de escrita, caminhos especiais, arquivos ausentes ou inválidos, fechamento da conexão, introspecção, contagens, amostras limitadas, contexto JSON, extração das respostas SQL, guardrails e execução segura. Há casos para PK/FK compostas, colunas geradas, referências implícitas, textos longos, BLOBs e nomes de tabela contendo aspas e pontuação SQL. Também verificam que o contexto não inclui valores das linhas, que a extração preserva o SQL para validação posterior e que o autorizador bloqueia escrita, tabelas não permitidas e funções perigosas mesmo sem a validação textual. Os testes comprovam que funções bloqueadas não chegam a ser chamadas, que consultas recursivas sem fim são interrompidas e que limites de linhas e bytes são aplicados. Os testes de configuração usam chaves fictícias para verificar precedência do ambiente, erros de configuração, caminhos relativos e absolutos, UTF-8/BOM e ocultação da chave na representação textual. Os testes do cliente usam o SDK real com transporte HTTP simulado para verificar requisições, autenticação, ausência de retries, erros de rede/HTTP, validação de respostas e fechamento do cliente. Os testes dos prompts verificam serialização, preservação de nomes/chaves, seleção das orientações conforme as colunas disponíveis e limites de entrada. Os testes do agente verificam coleta única do esquema, geração com uma chamada, rejeição de escrita e ausência de execução em `generate_sql`. Os testes do fluxo `query` combinam modelo simulado com SQLite real para verificar resultados, JOINs, agregações e limites. Os testes de correção verificam sucesso após erro de SQL, revalidação da nova consulta, limite de uma tentativa, contexto estável e ausência de correção para segurança, recursos e falhas da API. Eles não dependem do banco da atividade nem de chave OpenRouter e não enviam chamadas reais à API.
