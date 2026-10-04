# Observações sobre o banco fornecido

Estas observações foram obtidas por consultas de leitura ao `cinerocket.db`, extraído do arquivo `cinerocket (1).db` presente no ZIP da atividade. Elas descrevem esse arquivo; contagens e faixas podem mudar se outra versão do banco for utilizada.

## Tabelas e contagens

| Tabela | Registros | Papel |
| --- | ---: | --- |
| `dim_movies` | 95.645 | Catálogo e atributos dos filmes |
| `fact_movies_performance` | 95.645 | Finanças, popularidade e indicadores TMDB/IMDb |
| `dim_genres` | 19 | Gêneros |
| `dim_people` | 424.656 | Pessoas e seu tipo de participação |
| `dim_companies` | 45.941 | Produtoras |
| `dim_reviews` | 40.267 | Quantidade e nota média de avaliações de usuários por filme |
| `movie_reviews` | 43.666 | Avaliações individuais, com nota e texto |
| `bridge_movie_genre` | 121.521 | Ligações entre filmes e gêneros |
| `bridge_movie_person` | 745.450 | Ligações entre filmes e pessoas |
| `bridge_movie_company` | 116.326 | Ligações entre filmes e produtoras |
| `alembic_version` | 1 | Controle de migração; excluído do contexto do modelo |

O esquema contém 48 colunas e nove restrições FK declaradas. Ao excluir `alembic_version`, o contexto para o modelo contém dez tabelas e 47 colunas.

## Relacionamentos

- `dim_movies.sk_movie_id` é a PK dos filmes.
- As tabelas `bridge_movie_*` possuem PK composta e referenciam filmes e a dimensão correspondente por suas chaves substitutas.
- `bridge_movie_person` referencia `dim_people.sk_person_id`; o papel da pessoa está em `dim_people.tipo_pessoa`.
- `fact_movies_performance`, `dim_reviews` e `movie_reviews` referenciam os filmes por `sk_movie_id`.
- `dim_reviews` e `movie_reviews` têm granularidades diferentes: indicadores agregados e avaliações individuais. Essa diferença precisa ser considerada antes de realizar JOINs e agregações.

## Valores observados

Os valores reais de `dim_people.tipo_pessoa` são:

| Tipo | Registros |
| --- | ---: |
| `Ator` | 273.400 |
| `Diretor` | 65.200 |
| `Roteirista` | 86.056 |

`movie_reviews.rating` e `dim_reviews.nota_media_usuarios` apresentaram mínimo 0 e máximo 10. Essa é a faixa observada nos dados, sem presumir uma restrição que não foi extraída do esquema. Não se deve aplicar automaticamente uma escala de 1 a 5 às avaliações desse banco.

As finanças possuem campos distintos em USD e BRL, identificados pelos sufixos `_usd` e `_brl`. Uma consulta deve usar a mesma moeda para receita e orçamento. Amostras limitadas não demonstram a qualidade de todos os dados nem substituem a definição dos critérios de cada análise.

Uma verificação de leitura sobre `fact_movies_performance` encontrou 92.272 receitas USD nulas e 87.719 orçamentos USD nulos; não encontrou valores zero nesses dois campos. Nas 1.630 linhas com receita, orçamento e lucro USD informados, não houve diferença maior que USD 0,01 entre `lucro_usd` e `receita_usd - orcamento_usd`. Esses resultados sustentam a fórmula de lucro adotada e o cuidado de preservar ausências como `NULL`.

## Critérios do prompt

Quando a pergunta não especifica moeda, as consultas usam USD. A margem padrão é o retorno percentual sobre orçamento: `100.0 * (Receita - Orçamento) / NULLIF(Orçamento, 0)`, usando orçamento positivo e receita não nula. Receita informada é receita não nula; lucro também exige orçamento não nulo. Pedidos explícitos em BRL usam os campos BRL sem misturar moedas.

Para “últimos N anos” sem intervalo explícito, o prompt usa uma janela móvel da data de referência menos N anos até a data de referência, incluindo as extremidades. Lançamentos futuros são excluídos dessa janela. A data pode ser fornecida pela aplicação para reproduzir uma avaliação; sem argumento, utiliza-se a data local atual.

## Reprodução da inspeção

Após instalar as dependências, na raiz do projeto:

```bash
python scripts/inspect_database.py
```

As amostras padrão contêm até três linhas por tabela. Textos longos são truncados e BLOBs são substituídos por marcadores de tamanho. A inspeção mantém o arquivo SQLite em modo somente leitura.
