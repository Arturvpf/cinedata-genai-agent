# Avaliação das perguntas

O conjunto `tests/evaluation_questions.json` contém 21 perguntas com identificador, categoria, critérios de revisão e SQL de referência escrito a partir do esquema real. Ele cobre as perguntas principais do enunciado, cinco perguntas adicionais e duas variações de sinônimos para receita.

Essas consultas são referências para conferir dados e critérios. Executá-las com sucesso não mede a qualidade do SQL gerado pelo modelo nem da resposta em português. Os testes automatizados verificam referências em SQLite real, com dados controlados e sem API.

## Verificação realizada

No banco fornecido, as 21 referências executaram sem falhas, sem resultado parcial e sem chamadas ao modelo. O tamanho e a data de modificação do arquivo SQLite permaneceram iguais. A suíte automatizada passou com 591 testes, incluindo os casos de avaliação.

Com a referência temporal fixada em 2026-10-04, a consulta de atores retornou Eric Roberts com 71 filmes. A consulta de dupla retornou Joe Anoa'i e Kevin Dunn com 37 filmes. Esses valores descrevem este arquivo e os critérios definidos; podem mudar com outra versão do banco.

## Execução local, sem chave

Com as dependências instaladas e o ambiente virtual ativo, na raiz do projeto:

```bash
python scripts/evaluate_reference_queries.py --list
python scripts/evaluate_reference_queries.py
python scripts/evaluate_reference_queries.py --case top_receita --show-results
python scripts/evaluate_reference_queries.py --case ator_ultimos_cinco_anos --show-results
python scripts/evaluate_reference_queries.py --case dupla_ator_diretor --timeout 60
```

No PowerShell, sem ativar o ambiente, substitua `python` por `.\.venv\Scripts\python.exe`.

O script não lê `.env` e não chama o OpenRouter. `--list` também dispensa o banco. Por padrão, usa o `cinerocket.db` e o arquivo de perguntas da raiz do projeto, independentemente da pasta de execução. `--database` e `--questions` permitem escolher outros arquivos. `--case` pode ser repetido para selecionar mais de uma pergunta.

As consultas passam pelos mesmos guardrails, autorizador e executor somente leitura do agente, excluindo `alembic_version`. O limite de saída é de mil linhas; resultados parciais são sinalizados. Os limites de bytes e colunas permanecem ativos. O prazo padrão da avaliação é de trinta segundos por consulta, configurável com `--timeout` até sessenta segundos. A CLI principal mantém cinco segundos por padrão; consultas extensas podem exigir `--query-timeout 30`.

O resumo registra execução, quantidade retornada e tempo, sem avaliar se o modelo acertaria a pergunta. `--show-results` acrescenta um objeto JSON por consulta com SQL, colunas e linhas. Nessa apresentação, BLOBs são representados pelo tamanho e números não finitos como indisponíveis. Os códigos de saída são `0` se todas as consultas selecionadas executarem, `1` se alguma falhar e `2` para erros de configuração do avaliador. Uma consulta com falha não impede as seguintes.

## Critérios principais

| Grupo | IDs | Critérios que precisam ser conferidos |
| --- | --- | --- |
| Bilheteria e finanças | `top_receita`, `lucro_medio_genero`, `top_margem` | USD; nulos; lucro = receita − orçamento; margem percentual sobre orçamento positivo |
| Popularidade e engajamento | `top_popularidade`, `divergencia_tmdb_imdb`, `imdb_por_ano` | Popularidade separada de nota; diferença absoluta; média por ano |
| Elenco e equipe | `ator_ultimos_cinco_anos`, `diretores_nota_minimo_cinco`, `dupla_ator_diretor` | Papéis na dimensão; ponte por filme; janela de datas; mínimo de filmes avaliados |
| Gêneros e produtoras | `filmes_por_genero`, `produtora_maior_lucro`, `genero_maior_margem` | Identidades separadas de nomes; gênero sem filmes; soma de lucros; média de margens individuais |
| Avaliações de usuários | `mais_avaliados_usuarios`, `divergencia_usuarios_imdb` | Quantidade agregada de avaliações; média de usuários contra IMDb; escalas preservadas |
| Perguntas adicionais | `ano_maior_receita`, `generos_nota_imdb`, `diretores_mais_filmes`, `produtoras_mais_filmes`, `orcamento_alto_receita_baixa` | Agregar antes do ranking; limiares explícitos; não tratar receita ausente como baixa |
| Sinônimos | `sinonimo_bilheteria`, `sinonimo_faturamento` | Mesmos resultados de `top_receita` |

Filmes associados a vários gêneros ou produtoras contribuem para cada grupo correspondente. Não há percentuais de participação para ratear receita ou lucro entre produtoras. Para diretores com mínimo de cinco filmes, contam-se somente filmes com nota IMDb informada. Para margem média, calcula-se a média dos percentuais por filme, não o percentual obtido a partir de totais.

`dim_reviews` fornece indicadores agregados por filme. No arquivo fornecido, as 40.267 linhas correspondem a 40.267 filmes distintos. As referências de avaliações usam essa granularidade e não misturam essas quantidades com linhas individuais de `movie_reviews`. Outro banco pode exigir rever esse critério.

## Data e desempenho

O caso dos últimos cinco anos fixa `reference_date` em **2026-10-04** para reprodução. A janela inclui as duas extremidades e exclui datas nulas e lançamentos futuros. Para mudar a referência sem editar o conjunto:

```bash
python scripts/evaluate_reference_queries.py --case ator_ultimos_cinco_anos --reference-date 2025-12-31 --show-results
```

As duas referências mais extensas de elenco materializam os grupos filtrados e agregam por identificadores antes de buscar os nomes do ranking final. Essa escolha foi feita após a versão inicial da dupla ator–diretor exceder trinta segundos no banco fornecido. Os tempos variam conforme máquina, armazenamento e cache.

Essas referências usam `AS MATERIALIZED`, disponível no SQLite 3.35 ou superior; o ambiente validado utiliza SQLite 3.49.1. A materialização avalia a CTE e reutiliza o resultado intermediário, conforme a [documentação de WITH do SQLite](https://www.sqlite.org/lang_with.html#materialization_hints). Não são criados índices ou tabelas persistentes nem alterado o arquivo do banco.

## Conferência com o modelo real

Escolha inicialmente uma pergunta. O comando abaixo faz **uma chamada real de geração**, podendo acrescentar a única correção SQL. Não faz a chamada de redação:

```bash
python main.py --raw --debug --question "Quais são os dez filmes com maior receita em USD?"
```

Compare com `--case top_receita --show-results`. Confira filtros, joins, agrupamento, ordenação, nulos, valores e quantidade de linhas. Consultas diferentes podem produzir resultados equivalentes; não exija SQL ou aliases textualmente iguais. Diferenças numéricas pequenas em agregações de ponto flutuante devem ser examinadas com tolerância adequada. Resultado parcial precisa ser considerado antes de comparar.

Para conferir a pergunta temporal com exatamente a mesma data, use a API Python. O exemplo faz **chamadas reais** quando a chave é válida, somente para uma pergunta:

```python
from cinedata.agent import CineDataAgent
from cinedata.config import load_settings
from cinedata.evaluation import load_evaluation_questions
from cinedata.exceptions import CineDataError
from cinedata.llm import OpenRouterClient

try:
    case = next(
        item for item in load_evaluation_questions("tests/evaluation_questions.json")
        if item.id == "ator_ultimos_cinco_anos"
    )
    settings = load_settings()
    with OpenRouterClient(settings) as client:
        agent = CineDataAgent(
            settings.database_path, client,
            reference_date=case.reference_date, query_timeout_seconds=30,
        )
        result = agent.query(case.question)
        print(result.sql)
        print(result.columns)
        print(result.rows)
except CineDataError as error:
    print(error)
```

Depois da conferência dos dados, a resposta em português pode ser avaliada com o modo padrão da CLI ou com `agent.ask`, que acrescenta a redação para resultados não vazios. Verifique se a resposta preserva valores, unidades e avisos de limitação. Os testes não disparam essa avaliação externa automaticamente.
