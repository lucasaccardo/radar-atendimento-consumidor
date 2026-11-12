# Dicionário de dados

## 1. Fontes (como chegam)

### 1.1 Anatel: Indicadores de Qualidade de Atendimento

O arquivo `indicadores_qualidade_atendimento.zip` contém o arquivo `AnatelConsumidorReclamacoesCSV.csv`. Os campos são separados por ponto e vírgula, codificados em UTF‑8, e cada linha representa uma reclamação. Os dados começam em novembro de 2019. Quando descompactado, o arquivo ocupa cerca de 2,4 GB e tem aproximadamente 12,2 milhões de linhas.

> **Atenção:** toda vez que uma reclamação é reaberta, o sistema grava uma nova linha, marcada com `REABERTA = S`. Essa regra foi verificada usando a base de registros da Anatel (consulte D17 em `DECISOES.md`).

| Coluna original | Nome no banco | Tipo | Descrição | Exemplo |
|---|---|---|---|---|
| TIPO_ATENDIMENTO | tipo_atendimento | texto | Natureza da solicitação | Reclamação |
| SERVICO | servico | texto | Serviço reclamado | Celular Pós-Pago |
| ASSUNTO | assunto | texto | Tema da reclamação | Cobrança |
| UF | uf | texto(2) | Estado do consumidor | SP |
| PRESTADORA | prestadora | texto | Operadora reclamada | VIVO |
| DATA_ABERTURA | data_abertura | data | Registro na Anatel (aaaa-mm-dd) | 2026-03-14 |
| DATA_RESPOSTA | data_resposta | data | Resposta da operadora | 2026-03-19 |
| RESPONDIDA | respondida | S/N | Operadora respondeu? | S |
| REABERTA | reaberta | S/N | Consumidor reabriu a reclamação? | N |
| PrazoResposta | tempo_resposta | inteiro | **Dias até a resposta** (apesar do nome) | 5 |
| ID_SITUACAO | id_situacao | inteiro | Código da situação | 7 |
| SITUACAO | situacao | texto | Situação final | Finalizada com avaliação |
| RESOLVIDA | resolvida | S/N/Nulo | Avaliação do consumidor (Nulo = não avaliou) | S |
| NOTA_CONSUMIDOR | nota_consumidor | 1–5/Nulo | Satisfação com o atendimento | 4 |
| DATA_FINALIZACAO | data_finalizacao | data | Encerramento | 2026-03-30 |
| DETALHAMENTO | detalhamento | texto | Descrição do andamento | — |
| ENTIDADEANDAMENTO | entidade_andamento | texto | Quem deu o último andamento | ANATEL |

A Anatel escreve a palavra **Nulo** em campos vazios. A regra R1 transforma essa palavra em um valor nulo (NULL).

### 1.2 Anatel: Reclamações Registradas

O arquivo `consumidor_reclamacoes.zip` utiliza separador ponto e vírgula, codificação UTF‑8 e já está agregado, ou seja, cada linha contém um número total. Dentro do zip há dois arquivos:

- `reclamacoes.csv`: arquivo histórico, que cobre de janeiro de 2015 até maio de 2020, contendo cerca de 16 milhões de linhas. Este arquivo não será usado, pois a análise inicia em 2024.
- `reclamacoes_contexto.csv`: arquivo de dados recentes, que possui uma coluna extra chamada `Linha`. Este é o arquivo que será usado.

> Esse fato não está documentado em nenhum lugar da Anatel; ele foi descoberto ao consultar os valores mínimo e máximo de `anomes` na primeira carga (consulte D15 em `DECISOES.md`).

| Coluna original | Nome no banco | Descrição |
|---|---|---|
| DataExtracao | data_extracao | Data em que a Anatel gerou o arquivo |
| SOLICITAÇÕES | solicitacoes | Quantidade de solicitações da linha |
| AnoMês | anomes | Mês de registro (aaaa-mm) |
| UF, Cidade, CO_MUNICIPIO | uf, cidade, co_municipio | Localização |
| CanalEntrada | canal_entrada | Por onde o consumidor registrou (Usuário web, Call center, Aplicativo...) |
| Condição | condicao | Nova, Reaberta ou Reencaminhada |
| TipoAtendimento | tipo_atendimento | Reclamação, Pedido de Informação... |
| Serviço | servico | Código do serviço (SMP_Pós, SCM, SeAC, STFC...) → traduzido pela regra R8 |
| Marca | marca | Marca da operadora |
| Assunto, Problema | assunto, problema | Tema e detalhe |

### 1.3 IBGE

| Fonte | Campos usados |
|---|---|
| Tabela 6579, variável 9324 (população residente estimada) | código da UF, ano, população |
| API de localidades (estados) | código IBGE, sigla, nome, região |

## 2. Modelo (o que o pipeline produz)

### 2.1 Prata

`prata_atendimento` é uma tabela que contém uma linha por reclamação, já tipada. Além das colunas listadas acima (que já foram limpas), ela inclui:

| Coluna | Descrição |
|---|---|
| data_mes | Primeiro dia do mês de abertura (mês de referência) |
| avaliada | O consumidor avaliou (resolvida não é NULL) |
| tempo_resposta_dias | Tempo válido (0 a 365) ou NULL (R4) |
| respondida_no_sla | Respondida em até 10 dias (R5) |
| nota | Nota válida (1 a 5) ou NULL (R6) |
| e_reclamacao, no_periodo | Critérios da base de análise (R7) |
| flag_* | Marcadores de qualidade (não excluem a linha) |

### 2.2 Dimensões
| Tabela | Chave | Colunas |
|---|---|---|
| dim_calendario | data_mes | ano, mes, trimestre, nome_mes, rotulo_mes, ano_mes, tem_dados |
| dim_uf | uf | nome_uf, regiao_sigla, regiao, cod_ibge (inclui `NI` = não informado) |
| dim_servico | id_servico | servico |
| dim_operadora | id_operadora | prestadora, volume_periodo, participacao, operadora_exibicao (R9) |
| dim_assunto | id_assunto | assunto |
| dim_populacao (ref_populacao) | uf + ano | populacao, ano_estimativa_usada (R10) |

### 2.3 Fato
O fato `fato_atendimento` contém uma linha por reclamação da base de análise, apenas com chaves e medidas.  
O fato `ouro_fato_mensal`, usado pelo Power BI, tem grão **mês × UF × serviço × operadora × assunto**.

| Coluna | Tipo | Descrição |
|---|---|---|
| reclamacoes | inteiro | Quantidade de reclamações |
| respondidas | inteiro | Respondidas pela operadora |
| respondidas_no_sla | inteiro | Respondidas em até 10 dias |
| soma_tempo_resposta | inteiro | Soma dos dias até a resposta |
| qtd_tempo_resposta | inteiro | Reclamações com tempo válido (denominador do tempo médio) |
| reabertas | inteiro | Reabertas pelo consumidor |
| avaliadas | inteiro | Avaliadas pelo consumidor |
| resolvidas | inteiro | Avaliadas como resolvidas |
| soma_nota | inteiro | Soma das notas |
| qtd_nota | inteiro | Quantidade de notas válidas |

Por que somas e contagens? Consulte D4 em `DECISOES.md`. Qualquer média será recalculada corretamente para qualquer filtro.

### 2.4 Cubos da lâmina
| Tabela | Grão | Uso |
|---|---|---|
| ouro_cubo | mês × UF × serviço × operadora | KPIs, evolução, mapa, matriz, tabela |
| ouro_cubo_assunto | mês × serviço × operadora × assunto | “O que gera reclamação” |
| ouro_cubo_canal | mês × UF × serviço × operadora × canal | “Por onde o consumidor reclama” |
| ouro_conferencia_fontes | mês | Conferência de volume entre as duas bases da Anatel |
| vw_kpis_brasil_mensal | mês | View para conferir os números com SQL puro |

### 2.5 Arquivos gerados
| Arquivo | Conteúdo |
|---|---|
| `data/bronze/manifesto.json` | Origem, data, tamanho e hash SHA-256 de cada arquivo baixado |
| `data/logs/pipeline_*.log` | Log completo de cada execução |
| `docs/data/radar.json` | Cubos da lâmina (formato colunar compacto) |
| `docs/data/qualidade.json` | Relatório de qualidade da última execução |
| `docs/data/execucoes.json` | Histórico das últimas 24 execuções |
| `docs/data/dados.js` | Os três JSON juntos, para a lâmina abrir sem servidor |
| `powerbi/dados/*.parquet` | Modelo estrela para o Power BI |
