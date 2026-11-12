-- =====================================================================
-- 04 - CAMADA OURO: tabelas prontas para consumo (lâmina e Power BI)
--
-- DECISÃO IMPORTANTE: guardamos SOMAS e CONTAGENS, nunca médias prontas.
-- Motivo: "média de médias" dá resultado errado. Exemplo:
--   SP: 1000 reclamações, tempo médio 5 dias | RR: 10 reclamações, 15 dias
--   Média das médias = 10 dias (ERRADO)
--   Média ponderada  = (5*1000 + 15*10) / 1010 = 5,1 dias (CERTO)
-- Com soma e contagem, qualquer filtro recalcula a média certa:
--   tempo médio = soma_tempo_resposta / qtd_tempo_resposta
-- =====================================================================

-- soma(): SUM devolve um inteiro de 128 bits; convertemos para BIGINT,
-- que é o tipo que o Power BI lê sem problema no Parquet.
CREATE OR REPLACE MACRO soma(x) AS CAST(sum(x) AS BIGINT);

-- Grão mais fino da ouro: mês x UF x serviço x operadora x assunto.
-- É a fato do Power BI e a origem de todos os cubos da lâmina.
CREATE OR REPLACE TABLE ouro_fato_mensal AS
SELECT
    data_mes, uf, id_servico, id_operadora, id_assunto,
    count(*)                                                    AS reclamacoes,
    count(*) FILTER (WHERE respondida)                          AS respondidas,
    count(*) FILTER (WHERE respondida_no_sla)                   AS respondidas_no_sla,
    CAST(COALESCE(sum(tempo_resposta_dias), 0) AS BIGINT)       AS soma_tempo_resposta,
    count(tempo_resposta_dias)                                  AS qtd_tempo_resposta,
    count(*) FILTER (WHERE reaberta)                            AS reabertas,
    count(*) FILTER (WHERE avaliada)                            AS avaliadas,
    count(*) FILTER (WHERE resolvida)                           AS resolvidas,
    CAST(COALESCE(sum(nota), 0) AS BIGINT)                      AS soma_nota,
    count(nota)                                                 AS qtd_nota
FROM fato_atendimento
GROUP BY ALL;

-- Cubo principal da lâmina: mês x UF x serviço x operadora (cauda longa agrupada)
CREATE OR REPLACE TABLE ouro_cubo AS
SELECT f.data_mes, f.uf, s.servico, o.operadora_exibicao AS operadora,
       soma(reclamacoes) AS reclamacoes, soma(respondidas) AS respondidas,
       soma(respondidas_no_sla) AS respondidas_no_sla,
       soma(soma_tempo_resposta) AS soma_tempo_resposta, soma(qtd_tempo_resposta) AS qtd_tempo_resposta,
       soma(reabertas) AS reabertas, soma(avaliadas) AS avaliadas, soma(resolvidas) AS resolvidas,
       soma(soma_nota) AS soma_nota, soma(qtd_nota) AS qtd_nota
FROM ouro_fato_mensal f
JOIN dim_servico s USING (id_servico)
JOIN dim_operadora o USING (id_operadora)
GROUP BY ALL;

-- Cubo de assuntos: mês x serviço x operadora x assunto (visão Brasil)
CREATE OR REPLACE TABLE ouro_cubo_assunto AS
SELECT f.data_mes, s.servico, o.operadora_exibicao AS operadora, a.assunto,
       soma(reclamacoes) AS reclamacoes, soma(reabertas) AS reabertas,
       soma(avaliadas) AS avaliadas, soma(resolvidas) AS resolvidas
FROM ouro_fato_mensal f
JOIN dim_servico s USING (id_servico)
JOIN dim_operadora o USING (id_operadora)
JOIN dim_assunto a USING (id_assunto)
GROUP BY ALL;

-- Cubo de canal de entrada (fonte complementar): por onde o consumidor reclamou
CREATE OR REPLACE TABLE ouro_cubo_canal AS
SELECT r.data_mes, r.uf, r.servico,
       COALESCE(o.operadora_exibicao, 'OUTRAS') AS operadora,
       r.canal_entrada,
       soma(r.solicitacoes) AS solicitacoes
FROM prata_registradas r
LEFT JOIN dim_operadora o USING (prestadora)
GROUP BY ALL;

-- Conferência entre fontes: as duas bases da Anatel contam o mesmo volume?
-- A base de atendimento registra cada REABERTURA como uma linha nova (descoberto
-- na validação: ver D17). Por isso compara com registros "Nova" + "Reaberta".
CREATE OR REPLACE TABLE ouro_conferencia_fontes AS
WITH a AS (SELECT data_mes, soma(reclamacoes) AS atendimento FROM ouro_cubo GROUP BY 1),
     r AS (SELECT data_mes, soma(solicitacoes) AS registradas
           FROM prata_registradas
           WHERE lower(strip_accents(condicao)) IN ('nova', 'reaberta') GROUP BY 1)
SELECT a.data_mes, a.atendimento, r.registradas,
       round(100.0 * (a.atendimento - r.registradas) / NULLIF(r.registradas, 0), 2) AS diferenca_pct
FROM a LEFT JOIN r USING (data_mes)
ORDER BY 1;

-- View de conferência: KPIs do Brasil por mês, já calculados.
-- Serve para bater os números da lâmina e do Power BI com SQL puro.
CREATE OR REPLACE VIEW vw_kpis_brasil_mensal AS
SELECT
    c.ano_mes,
    sum(o.reclamacoes)                                                          AS reclamacoes,
    round(sum(o.soma_tempo_resposta) / NULLIF(sum(o.qtd_tempo_resposta), 0), 2) AS tempo_medio_resposta_dias,
    round(100.0 * sum(o.respondidas_no_sla) / NULLIF(sum(o.reclamacoes), 0), 1) AS pct_no_sla,
    round(100.0 * sum(o.reabertas)   / NULLIF(sum(o.reclamacoes), 0), 1)        AS pct_reabertas,
    round(100.0 * sum(o.resolvidas)  / NULLIF(sum(o.avaliadas), 0), 1)          AS pct_resolvidas,
    round(sum(o.soma_nota)           / NULLIF(sum(o.qtd_nota), 0), 2)           AS nota_media
FROM ouro_cubo o
JOIN dim_calendario c USING (data_mes)
GROUP BY c.ano_mes
ORDER BY c.ano_mes;
