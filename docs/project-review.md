# Revisão de aderência e qualidade

Revisão realizada em **05/10/2026**, comparando o código com o PDF **Atividade GenAI — RocketLab 26.2 Perfis vdev**, de duas páginas. O enunciado foi usado como referência de requisitos. A revisão não modifica o prazo ou as condições de entrega da atividade.

O núcleo solicitado está implementado: perguntas em linguagem natural, geração de SQL, leitura do banco fornecido e apresentação dos resultados em uma aplicação Python versionada. A organização e as proteções do banco foram verificadas. O usuário confirmou sucesso em todos os cenários do roteiro manual descrito abaixo. Na amostra instrumentada desta revisão, duas perguntas concluíram e uma falhou. Essas evidências validam os cenários exercitados; não estabelecem uma taxa de acerto para todas as perguntas possíveis.

## Requisitos do enunciado

| Item | Implementação e evidência | Avaliação |
| --- | --- | --- |
| Consultas em linguagem natural sobre a camada Gold | `CineDataAgent.query` e `ask` em `src/cinedata/agent.py`; pergunta e esquema enviados ao modelo | Implementado; acerto semântico depende do modelo |
| Consultas de leitura e dados atuais | `database.py` abre uma conexão somente leitura a cada execução; o cache guarda apenas SQL | Implementado e testado, inclusive após mudança de dados em banco temporário |
| Python e entregável executável | Pacote em `src/cinedata`, `main.py`, CLI e dependências no `pyproject.toml` | Implementado |
| Modelo e framework à escolha | OpenRouter configurável e orquestração própria em Python | Escolha documentada; nenhum framework específico é exigido pelo PDF |
| Sugestão de modelo com tool calling | A aplicação controla geração textual, validação e execução do SQL | Tool calling é uma sugestão no PDF; a implementação usa um fluxo explícito |
| Banco `cinerocket.db` e dez tabelas dimensionais | SQLite via biblioteca padrão; esquema, PKs e FKs obtidos por introspecção | As dez tabelas de dados foram encontradas; `alembic_version` fica fora das consultas do agente |
| GitHub e README para execução | Repositório configurado, histórico com 22 commits nesta revisão, instalação e configuração documentadas | Implementado |
| Planejamento das chamadas ao modelo | Sem chamadas na inicialização; correção limitada; contexto de resultados limitado; contagens locais e cache de SQL | Implementado; a amostra real desta revisão consumiu cinco chamadas |
| Interface de chat | CLI interativa e modo de pergunta única | Suficiente; o PDF dispensa interface de chat |

Interface web, gráficos, memória de conversa, fallback, busca semântica e conexão ao Databricks aparecem como possibilidades adicionais no enunciado. Sua ausência não impede a entrega principal. Guardrails, avaliação e cache de SQL já foram incluídos como melhorias.

## Cobertura dos exemplos de análise

O arquivo `tests/evaluation_questions.json` reúne consultas de referência para conferir os resultados. Essas referências não substituem o SQL gerado pelo agente.

| Exemplo do PDF | Caso de referência |
| --- | --- |
| Dez filmes com maior receita em R$ | `top_receita_brl` |
| Lucro médio por gênero | `lucro_medio_genero` |
| Filmes com maior margem de lucro | `top_margem` |
| Cinco filmes mais populares | `top_popularidade` |
| Divergência entre TMDB e IMDb | `divergencia_tmdb_imdb` |
| Média IMDb por ano | `imdb_por_ano` |
| Ator com mais filmes nos últimos cinco anos | `ator_ultimos_cinco_anos` |
| Diretores com maior média e mínimo de cinco filmes | `diretores_nota_minimo_cinco` |
| Dupla ator–diretor mais frequente | `dupla_ator_diretor` |
| Quantidade de filmes por gênero | `filmes_por_genero` |
| Produtora com maior lucro | `produtora_maior_lucro` |
| Gênero com maior margem média | `genero_maior_margem` |
| Filmes mais avaliados pelos usuários | `mais_avaliados_usuarios` |
| Divergência entre usuários e IMDb | `divergencia_usuarios_imdb` |

O suporte a BRL já existia no esquema e nas orientações do agente, mas faltava uma referência específica para o exemplo em R$. Esta revisão acrescentou `top_receita_brl` e um teste com valores e ordenação diferentes de USD, para detectar mistura de moedas. O conjunto passou de 21 para 22 casos. As definições de margem, moeda padrão, notas, nulos e período temporal estão em [evaluation.md](evaluation.md) e [database.md](database.md).

## Verificações locais

- **636 testes passaram**, usando bancos temporários e respostas/HTTP simulados.
- **22 consultas de referência executaram** no banco fornecido, sem falhas ou resultados parciais; tamanho e data de modificação permaneceram iguais.
- `pip check` não encontrou dependências quebradas no ambiente instalado.
- `DROP TABLE`, `DELETE` e uma consulta seguida de `DROP` foram rejeitados pelo executor protegido.
- `.env` não está versionado; a verificação dos arquivos de texto versionados não encontrou uma chave OpenRouter real.

A separação entre configuração, transporte, prompts, validação, execução e apresentação facilita manutenção e testes. As defesas de leitura incluem validação textual, autorizador SQLite, abertura somente leitura e limites de tempo e tamanho. A correção automática só ocorre uma vez e passa pelas mesmas verificações. Quando a redação falha, os resultados SQL continuam disponíveis.

O ambiente verificado é Windows, Python 3.12.10 e SQLite 3.49.1. Esta revisão não comprova execução em Linux/macOS nem em uma instalação limpa de todas as versões permitidas de Python.

## Amostra com o modelo real

Foram usadas três perguntas distintas, uma tentativa por pergunta, com `OPENROUTER_MODEL=openrouter/free`, `CineDataAgent.ask` e prazo SQLite de 30 s. As credenciais foram lidas da configuração local. Não houve repetição para selecionar apenas respostas bem-sucedidas. Os tempos incluem geração, eventual correção, execução e redação; não incluem a criação inicial do cliente e a introspecção.

| Pergunta | Tempo total | Resultado observado |
| --- | --- | --- |
| Quantos filmes existem no catálogo? | 2,766 s | 95.645; contagem conferida no banco, resposta local |
| Dez filmes com maior receita em R$ | 34,656 s | Sucesso após uma correção SQL; dez títulos e valores coincidiram com a referência; resposta em português preservou os valores em reais |
| Filmes por gênero, incluindo gêneros sem filmes | 3,953 s | `QueryBlockedError`: saída não aceita como uma consulta `SELECT`/`WITH`; SQL não executado |

O ranking gerado não incluiu o desempate por identificador orientado no prompt. Isso não alterou os resultados desta amostra, mas exige atenção quando houver empates. A saída bruta da pergunta bloqueada não foi preservada; o erro identifica a etapa de validação e não permite atribuir uma causa mais específica ao comportamento do modelo.

O arquivo SQLite permaneceu com o mesmo tamanho e data de modificação depois das chamadas reais. A consulta de referência de gêneros executou separadamente e retornou 19 grupos; portanto, a falha observada na amostra não demonstra ausência dos dados ou do relacionamento necessário.

**Não há uma média representativa de espera nem uma taxa de acerto estimada.** Três tentativas, com consultas diferentes e uma falha, são apenas evidências pontuais. Uma pergunta pode precisar de geração, correção e redação; o prazo de uma operação de rede não é um limite total para essas etapas.

## Testes manuais confirmados pelo usuário

Após receber o roteiro, o usuário informou que todos os cenários funcionaram em sua execução:

- Contagem de 95.645 filmes e repetição da pergunta na mesma sessão.
- Ranking dos cinco filmes com maior receita em USD.
- Quantidade de filmes por gênero.
- Filtro de lançamento em 2020 com ranking de popularidade.
- Busca por título inexistente, com resultado vazio.
- Pedido de exclusão dos filmes sem alteração da contagem do catálogo.
- Pedido de dez filmes com `--max-rows 3`, exibindo três linhas e o aviso de resultado parcial.

Este registro se baseia na confirmação do usuário. Não foram fornecidos tempos, SQL ou saídas completas dessas execuções, portanto não entram nas medições de latência. O sucesso relatado amplia a validação funcional; a falha observada na amostra instrumentada permanece como evidência de que o comportamento do modelo pode variar.

## Limitações e preparação da demonstração

1. **Validar a qualidade do modelo nas outras categorias.** As referências cobrem os exemplos, mas o modelo real não foi avaliado em todos eles. Compare SQL, valores, filtros, unidades e resposta final conforme [evaluation.md](evaluation.md). A amostra real já mostra que uma pergunta válida pode falhar.
2. **Usar prazo apropriado para análises extensas.** Nesta execução, a referência de ator levou 14,2 s e a de dupla ator–diretor, 9,3 s. O padrão de 5 s da CLI pode interromper essas consultas. Para demonstrá-las, execute `python main.py --debug --query-timeout 30`. Esses tempos são locais e variam conforme o ambiente e o SQL gerado.
3. **Conferir critérios de negócio.** Guardrails não garantem que uma consulta de leitura represente corretamente a pergunta. Margem, datas, moedas, quantidade de avaliações e tratamento de empates precisam ser revisados nos dados retornados.

A revisão não encontrou um componente principal ausente. A prioridade antes da apresentação é conferir mais respostas reais com as referências e conhecer as limitações do modelo escolhido; a quantidade de testes automatizados, por si só, não comprova essa qualidade.
