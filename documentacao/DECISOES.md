# Decisões de Arquitetura (ADR)

Estrutura: **contexto → decisão → alternativas descartadas → impacto**. Trata-se do modelo de documentação (ADR, *Architecture Decision Record*) que permite que outra pessoa entenda **porque** o projeto foi implementado desta forma, e não apenas **como**.

---

## D1. Fonte de dados: Anatel em vez de Consumidor.gov.br

**Contexto.** O planejamento inicial previa o uso do Consumidor.gov.br (reclamações de todas as áreas). No dia 12/11/2026, durante o desenvolvimento:
- o domínio `dados.mj.gov.br` parou de responder no DNS (todos os links do dados.gov.br para estes arquivos CSV passaram a apresentar "não foi possível resolver o host");
- a página "Dados Abertos" do próprio portal Consumidor.gov.br ficou em estado de carregamento permanente (erro 504 no servidor).

**Decisão.** Utilizar a base de dados **"Consumidor – Indicadores de Qualidade de Atendimento" da Anatel**: oficial, atualizada mensalmente, com a mesma estrutura (uma linha por reclamação, com resposta, resolução e nota) e com uma coluna extra, a **reabertura**.

**Alternativas descartadas.**
- *Esperar que a fonte de dados volte a ficar online:* sem estimativa de retorno, e o prazo de entrega era de 1 dia.
- *Usar uma cópia da base de dados em site de terceiros:* não cumpre o requisito de "base pública do governo" e perde a rastreabilidade.
- *Ranking do Banco Central:* trimestral e já consolidado por instituição; não permitiria a análise por estado, assunto e tempo de resposta.

**Impacto.** O foco foi direcionado para **telecom**, um dos maiores setores de atendimento terceirizado. A arquitetura não sofreu alteração: apenas a extração e as regras de negócio foram modificadas. O catálogo antigo foi arquivado em `documentacao/historico/` para fins de registro.

---

## D2. SQL no DuckDB como motor de transformação

**Decisão.** Todas as regras de negócio estão em arquivos `.sql`. O Python apenas baixa arquivos, renomeia as colunas e executa os arquivos SQL em ordem.

- O SQL é a linguagem comum da área de dados: qualquer analista consegue revisar a regra de negócio.
- O DuckDB lê arquivos CSV diretamente, processa milhões de linhas em segundos em um notebook e **não necessita de servidor**: quem clonar o repositório pode rodar o projeto com um `pip install`.
- O SQL é padrão: para migrar para MySQL, Athena, Snowflake ou BigQuery basta ajustar algumas funções.

**Alternativas descartadas.** *Pandas:* usa muita memória com 12 milhões de linhas e espalha a regra de negócio em código Python. *MySQL local:* exige instalar e gerenciar servidor, o que torna difícil reproduzir o projeto.

---

## D3. Camadas bronze / prata / ouro

**Decisão.** Três camadas, cada uma com uma responsabilidade.

| Camada | Garante |
|---|---|
| Bronze | Rastreabilidade: o arquivo original, intocado, com hash SHA-256 no manifesto |
| Prata | Qualidade: tipos de dados corretos, rótulos padronizados, marcas de problema |
| Ouro | Performance e consistência: cubos prontos, iguais para lâmina e Power BI |

**Impacto.** Se uma regra de negócio mudar, reprocessa-se a prata a partir da bronze sem baixar nada de novo (`--sem-download`).

---

## D4. A camada ouro guarda somas e contagens, nunca médias

**Problema que evita (média de médias).**
SP tem 1.000 reclamações com tempo médio de resposta de 5 dias; RR tem 10 com 15 dias.
- Média das médias: (5 + 15) / 2 = **10 dias** ❌
- Média ponderada: (5×1.000 + 15×10) / 1.010 = **5,1 dias** ✅

**Decisão.** O cubo guarda `soma_tempo_resposta` e `qtd_tempo_resposta`. A média é calculada **em tempo real**, para o filtro que estiver selecionado. O mesmo acontece para %, nota e taxa por habitante.

---

## D5. Comparação ano a ano sempre no mesmo período

**Problema.** Em novembro de 2026, os dados de 2026 vão até setembro. Comparar jan–set/2026 com o ano de 2025 inteiro mostraria uma "queda" falsa de 25%.

**Decisão.** A comparação usa exatamente os mesmos meses do ano anterior (jan–set/2026 x jan–set/2025). Se o ano anterior não tiver estes meses (2024 na série), a lâmina mostra "sem ano anterior para comparar" em vez de inventar um número.

---

## D6. Maturação: os meses mais recentes ficam de fora

**Problema.** A Anatel gera o arquivo no início do mês. Uma reclamação aberta em 28/10 ainda pode estar sem resposta (a operadora tem 10 dias) ou sem avaliação (o consumidor tem mais 10). Outubro estaria "incompleto" e pareceria melhor ou pior do que é.

**Decisão.** Ficam fora o mês da extração e mais 1 mês (`MESES_MATURACAO = 1` no `config.py`). Com o arquivo de 12/11/2026, a série vai até setembro/2026. O número de reclamações excluídas por maturação aparece no relatório de qualidade.

---

## D7. Linhas idênticas são mantidas

**Problema.** A base não tem número de protocolo nem dado pessoal. Duas pessoas diferentes que reclamaram da mesma operadora, no mesmo dia, do mesmo assunto, na mesma UF, com o mesmo desfecho, geram **linhas idênticas**.

**Decisão.** Não remover. "Duplicada" pressupõe saber que é o mesmo evento, e sem chave isso não é possível. O volume de linhas idênticas é medido e mostrado como INFO no relatório de qualidade.

---

## D8. Taxa de reabertura como termômetro de FCR

**Contexto.** FCR (*First Contact Resolution*, resolução no primeiro contato) é um dos principais KPIs de central de atendimento, mas não existe na base pública.

**Decisão.** Usar a **taxa de reabertura** (reclamações reabertas ÷ total) como equivalente. Se o consumidor reabre, a primeira resposta não resolveu. Na lâmina, ela é o eixo vertical da matriz de operadoras e a base da priorização em "Onde agir primeiro".

---

## D9. "Resolvidas" só entre as avaliadas

**Contexto.** Pela regra da Anatel, se o consumidor não avalia em 10 dias, a reclamação é classificada automaticamente como resolvida.

**Decisão.** % resolvidas = resolvidas ÷ **avaliadas**. Contar as não avaliadas como resolvidas inflaria o indicador e esconderia o problema.

---

## D10. Priorização por impacto, não só por taxa

**Decisão.** O cartão "Onde agir primeiro" escolhe o assunto com mais **reaberturas em excesso**: `reclamações × (reabertura do assunto − reabertura média)`. Um assunto com 30% de reabertura e 100 casos pesa menos do que um com 14% e 50.000 casos. É assim que se prioriza um plano de ação numa operação: pelo tamanho do ganho.

---

## D11. Lâmina estática, dados pré-agregados

**Decisão.** A lâmina é um único HTML no GitHub Pages, sem servidor nem banco. Os dados vão num arquivo compacto (formato colunar com dicionários), gerado pelo pipeline.

**Trade-off assumido.** Os cubos de *assunto* e de *canal de entrada* não abrem por UF (ficariam grandes demais para o navegador). Quando há filtro de UF, a lâmina avisa "visão Brasil". O Power BI, que não tem essa limitação, tem todos os cruzamentos.

**Por que não usar biblioteca de gráficos (Chart.js, D3)?** Os gráficos são SVG escritos à mão: a lâmina abre offline, não depende de CDN externo e cada detalhe visual está sob controle.

---

## D12. Qualidade como portão de publicação

**Decisão.** Cada checagem devolve OK, ALERTA, ERRO ou INFO. Com qualquer ERRO, o pipeline **não publica**: a lâmina continua com os dados do mês anterior e o GitHub manda e-mail avisando. Para a diretoria, um número desatualizado e sinalizado é melhor do que um número errado.

Quem decide se um problema é ALERTA ou ERRO são os limites em `config.py` (`LIMITES_QUALIDADE`), não o código.

---

## D13. Atualização automática pelo GitHub Actions

**Decisão.** O workflow `.github/workflows/atualizar-dados.yml` roda todo dia 15: testa o pipeline com a amostra sintética, roda com os dados reais e só publica se algo mudou. Cada execução fica registrada no "Histórico de atualizações" da lâmina (é o **controle de atualizações de relatórios** pedido na vaga).

A extração é **incremental**: o pipeline pergunta à Anatel se o arquivo mudou (cabeçalho `Last-Modified`) antes de baixar 460 MB.

---

## D14. Testes com amostra sintética

**Decisão.** `tests/gerar_amostra_sintetica.py` gera arquivos falsos no **mesmo formato** da Anatel (cabeçalho, separador, datas, zip), com sujeira proposital (data 31/02, UF "XX", nota 9, tempo negativo). O pipeline roda neles em segundos, sem internet.

**Por quê.** Prova que as regras de saneamento funcionam, permite testar mudanças antes de processar 12 milhões de linhas e roda antes de cada atualização automática. As operadoras da amostra são fictícias e a lâmina mostra uma faixa amarela "DADOS SINTÉTICOS" quando lê esses dados.

---

## D15. Inspecionar a fonte antes de confiar nela

**Contexto.** Na primeira carga real, a checagem "Volume: base de atendimento x base de registros" não encontrou nenhum mês para comparar. Uma consulta simples na bronze mostrou o motivo:

```sql
SELECT min(anomes), max(anomes), count(*) FROM bronze_registradas;
-- 2015-01 | 2020-05 | 15.952.407
```

O arquivo `reclamacoes.csv` é só o **histórico**. Os dados recentes estão no outro arquivo do mesmo zip, `reclamacoes_contexto.csv`.

**Decisão.** Ler `reclamacoes_contexto.csv` (configurável em `config.py`).

**Lição.** A checagem de qualidade não "deu erro", mas **um zero onde se esperava um número** é um sinal. Sem a conferência entre fontes, o painel de canal de entrada teria sumido da lâmina sem ninguém perceber.

---

## D16. Sucessão de marca (OI → NIO)

**Contexto.** Na primeira versão com dados reais, o resumo executivo dizia que "a maior contribuição para a alta veio da NIO (+312%)". Na mesma tabela, a OI caía 94%. Não era piora de atendimento: em fevereiro de 2025 a Oi Fibra foi vendida à V.tal e passou a se chamar Nio, levando ~4 milhões de clientes. Os canais antigos conviveram com os novos até ~agosto/2025.

**Decisão.** Tabela `ref_sucessao_marca` (regra R11) agrupando OI e NIO como `OI / NIO` para análise. O nome original continua na coluna `prestadora`, para quem quiser abrir.

**Resultado.** OI + NIO: 82.910 → 88.702 reclamações (jan–set, +7,0%), em vez de "+312%". O maior motor real da alta passou a aparecer corretamente: VIVO, +56 mil reclamações (+21,7%).

**Lição.** Um texto gerado automaticamente só é tão bom quanto o modelo por trás dele. Antes de publicar um "insight", é preciso perguntar: isso é mudança no mundo real ou mudança no cadastro?

---

## D17. A divergência "estável" entre as bases era definição, não erro

**Contexto.** A conferência entre as duas bases da Anatel deu divergência média de 11,2%, e entre 9,8% e 12,2% em **todos** os 33 meses. Erro de carga oscila; diferença constante sugere **regra de contagem diferente**. A taxa de reabertura também ficava em torno de 12%.

**Teste (jan/2024).**

| Base | Contagem |
|---|---|
| Registros: condição "Nova" | 95.590 |
| Registros: condição "Reaberta" | 13.407 |
| Registros: Nova + Reaberta | **108.997** |
| Atendimento: total de linhas | **106.635** (−2,2%) |
| Atendimento: linhas com REABERTA = S | 13.024 (vs 13.407) |

**Conclusão.** A base de atendimento registra **cada reabertura como uma linha nova**. A conferência passou a comparar com "Nova + Reaberta", e a divergência caiu para a faixa de ~2%.

**Consequências.**
- "Reclamações" = registros, incluindo reaberturas. A definição na lâmina foi ajustada.
- "Taxa de reabertura" = registros que são reaberturas ÷ total de registros.

**Lição.** Uma checagem de qualidade não serve só para bloquear: ela ensina como a fonte funciona. Testar a hipótese com uma consulta de um mês levou dois minutos e mudou a definição de dois indicadores.

---

## D18. A automação falhou no primeiro teste: o IBGE não responde fora do Brasil

**Contexto.** A primeira execução da atualização automática no GitHub Actions falhou depois de quase 10 minutos. O log mostrou que a Anatel foi baixada normalmente, mas a API do IBGE (`servicodados.ibge.gov.br`) não aceitou conexão em nenhuma das 4 tentativas. Os servidores do GitHub ficam fora do Brasil; na máquina local, a mesma chamada responde em segundos.

**Decisão.**
- Cada vez que a API responde, a resposta é gravada em `dados_referencia/` e versionada no Git.
- Se a API não responder, o pipeline usa essa cópia, registra a falha no histórico de execuções e a lista de fontes da lâmina avisa.
- O tempo de espera passou a ser separado: 30 s para conectar e 300 s para ler. Um servidor que não atende é descartado em segundos, e não em minutos.

**Alternativas descartadas.**
- *Desligar a automação:* resolveria o X vermelho, mas abriria mão da atualização mensal da Anatel, que funciona.
- *Rodar a automação num servidor no Brasil:* custo e manutenção desproporcionais para um dado que muda uma vez por ano.

**Impacto.** Nenhum nos números: o IBGE publica a estimativa de população uma vez por ano, então a cópia é a mesma informação que a API devolveria.

**Verificação.** A execução no GitHub (com a cópia) e a execução local (com a API) geraram os mesmos números, linha por linha: 162.634 linhas na fato, 81 linhas de população e 67.667 linhas de canal, sem nenhuma diferença.

**Lição.** Testar a automação no ambiente real é a única forma de descobrir esse tipo de problema. Ler o log até a linha do erro levou um minuto e mostrou exatamente qual fonte falhou.
