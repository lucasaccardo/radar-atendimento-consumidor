-- =====================================================================
-- 02 - DIMENSÕES 
-- Dimensão = tabela de "quem / onde / quando / o quê"
-- =====================================================================

-- Base de análise (R7) materializada uma vez, para todo o resto usar
CREATE OR REPLACE TABLE base_analise AS
SELECT * FROM prata_atendimento WHERE e_reclamacao AND no_periodo;

-- Calendário mensal contínuo (sem buracos, mesmo se um mês faltar na fonte)
CREATE OR REPLACE TABLE dim_calendario AS
WITH meses AS (
    SELECT CAST(unnest(generate_series(
        getvariable('inicio'),
        getvariable('corte') - to_months(getvariable('meses_maturacao') + 1),
        INTERVAL 1 MONTH)) AS DATE) AS data_mes
)
SELECT
    data_mes,
    year(data_mes)                                   AS ano,
    month(data_mes)                                  AS mes,
    quarter(data_mes)                                AS trimestre,
    ['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'][month(data_mes)] AS nome_mes,
    ['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'][month(data_mes)]
        || '/' || right(CAST(year(data_mes) AS VARCHAR), 2)                    AS rotulo_mes,
    strftime(data_mes, '%Y-%m')                      AS ano_mes,
    data_mes IN (SELECT DISTINCT data_mes FROM base_analise)                   AS tem_dados
FROM meses
ORDER BY data_mes;

-- UF com região (fonte: IBGE) + linha "NI" para UF inválida/ausente
CREATE OR REPLACE TABLE dim_uf AS
SELECT uf, nome_uf, regiao_sigla, regiao, cod_ibge FROM ref_estados
UNION ALL
SELECT 'NI', 'Não informado', 'NI', 'Não informado', NULL;

CREATE OR REPLACE TABLE dim_servico AS
SELECT dense_rank() OVER (ORDER BY servico) AS id_servico, servico
FROM (SELECT DISTINCT servico FROM base_analise);

CREATE OR REPLACE TABLE dim_assunto AS
SELECT dense_rank() OVER (ORDER BY assunto) AS id_assunto, assunto
FROM (SELECT DISTINCT assunto FROM base_analise);

-- R11: sucessão de marca. Quando a mesma base de clientes muda de nome, a série
-- histórica "quebra": a marca antiga despenca e a nova dispara, sem mudança real.
-- Caso real: a Oi Fibra foi vendida à V.tal (fev/2025) e virou Nio; os canais da
-- Oi Fibra conviveram com os da Nio até ~ago/2025. Para comparar ano contra ano,
-- as duas marcas são analisadas juntas. A coluna "prestadora" mantém o nome original.
CREATE OR REPLACE TABLE ref_sucessao_marca AS
SELECT * FROM (VALUES
    ('OI',  'OI / NIO', 'Oi Fibra vendida à V.tal em fev/2025 e renomeada Nio'),
    ('NIO', 'OI / NIO', 'Nio = antiga Oi Fibra (base de ~4 milhões de clientes)')
) AS t(prestadora, grupo_marca, motivo);

-- R9: cauda longa. Grupo com menos de 0,5% do volume vira "OUTRAS".
-- Mantém a lâmina legível e evita ranking com amostra pequena.
CREATE OR REPLACE TABLE dim_operadora AS
WITH marcas AS (
    SELECT b.prestadora, COALESCE(s.grupo_marca, b.prestadora) AS grupo_marca, count(*) AS volume_periodo
    FROM base_analise b
    LEFT JOIN ref_sucessao_marca s USING (prestadora)
    GROUP BY ALL
),
grupos AS (
    SELECT grupo_marca, sum(volume_periodo) / sum(sum(volume_periodo)) OVER () AS participacao
    FROM marcas GROUP BY grupo_marca
)
SELECT
    dense_rank() OVER (ORDER BY m.prestadora)                                  AS id_operadora,
    m.prestadora,
    m.grupo_marca,
    m.volume_periodo,
    round(g.participacao, 5)                                                   AS participacao_grupo,
    CASE WHEN g.participacao >= getvariable('min_participacao') THEN m.grupo_marca
         ELSE 'OUTRAS' END                                                     AS operadora_exibicao
FROM marcas m
JOIN grupos g USING (grupo_marca);

-- População por UF e ano.
-- R10: se o IBGE ainda não publicou a estimativa de um ano, usamos a do ano
-- anterior mais próximo (a variação anual de população é < 1%).
CREATE OR REPLACE TABLE ref_populacao AS
WITH anos AS (SELECT DISTINCT ano FROM dim_calendario),
pop AS (
    SELECT e.uf, p.ano, p.populacao
    FROM ref_populacao_bruta p JOIN ref_estados e USING (cod_ibge)
)
SELECT
    e.uf,
    a.ano,
    (SELECT p.populacao FROM pop p WHERE p.uf = e.uf AND p.ano <= a.ano
     ORDER BY p.ano DESC LIMIT 1)                                         AS populacao,
    (SELECT max(p.ano) FROM pop p WHERE p.uf = e.uf AND p.ano <= a.ano)   AS ano_estimativa_usada
FROM ref_estados e CROSS JOIN anos a;
