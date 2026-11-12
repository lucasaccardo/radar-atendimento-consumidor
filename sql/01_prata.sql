-- =====================================================================
-- 01 - CAMADA PRATA: dado limpo, tipado e padronizado
-- Entrada: bronze_atendimento (texto puro, igual ao arquivo da Anatel)
-- Saída: prata_atendimento  (1 linha por reclamação, tipada)
--
-- Cada regra de saneamento tem um código (R1, R2...). O mesmo código está
-- em documentacao/REGRAS_DE_SANEAMENTO.md, com o motivo de cada uma.
-- =====================================================================

-- R1: texto sem espaços sobrando; vazio e o texto "Nulo" viraram NULL
CREATE OR REPLACE MACRO limpa_texto(x) AS
    CASE WHEN lower(trim(x)) IN ('', 'nulo', 'null', '-', 'n/a') THEN NULL
         ELSE regexp_replace(trim(x), '\s+', ' ', 'g') END;

-- chave de comparação: minúsculo e sem acento ("Reclamação" = "reclamacao")
CREATE OR REPLACE MACRO chave_texto(x) AS
    lower(strip_accents(limpa_texto(x)));

-- R2: datas aceitam aaaa-mm-dd (padrão da Anatel) e dd/mm/aaaa; o resto vira NULL
CREATE OR REPLACE MACRO para_data(x) AS
    CAST(COALESCE(try_strptime(left(trim(x), 10), '%Y-%m-%d'),
                  try_strptime(left(trim(x), 10), '%d/%m/%Y')) AS DATE);

-- S/N -> verdadeiro/falso
CREATE OR REPLACE MACRO sim_nao(x) AS
    CASE WHEN chave_texto(x) IN ('s', 'sim') THEN TRUE
         WHEN chave_texto(x) IN ('n', 'nao') THEN FALSE END;

CREATE OR REPLACE TABLE prata_atendimento AS
WITH tipado AS (
    SELECT
        limpa_texto(tipo_atendimento)                  AS tipo_atendimento,
        limpa_texto(servico)                           AS servico_original,
        limpa_texto(assunto)                           AS assunto_original,
        upper(limpa_texto(uf))                         AS uf_original,
        upper(limpa_texto(prestadora))                 AS prestadora_original,
        para_data(data_abertura)                       AS data_abertura,
        para_data(data_resposta)                       AS data_resposta,
        para_data(data_finalizacao)                    AS data_finalizacao,
        limpa_texto(data_abertura)                     AS data_abertura_texto,
        sim_nao(respondida)                            AS respondida_original,
        sim_nao(reaberta)                              AS reaberta_original,
        try_cast(trim(tempo_resposta) AS INTEGER)      AS tempo_original,
        limpa_texto(tempo_resposta)                    AS tempo_texto,
        limpa_texto(situacao)                          AS situacao,
        sim_nao(resolvida)                             AS resolvida,
        try_cast(trim(nota_consumidor) AS INTEGER)     AS nota_original,
        limpa_texto(nota_consumidor)                   AS nota_texto
    FROM bronze_atendimento
)
SELECT
    -- Mês de referência = mês de ABERTURA da reclamação
    CAST(date_trunc('month', data_abertura) AS DATE)                           AS data_mes,
    data_abertura,
    data_resposta,
    data_finalizacao,
    tipo_atendimento,
    COALESCE(servico_original, 'Não informado')                                AS servico,
    COALESCE(assunto_original, 'Não informado')                                AS assunto,
    -- R3: UF só vale se existir na lista oficial do IBGE; senão vira 'NI'
    CASE WHEN uf_original IN (SELECT uf FROM ref_estados) THEN uf_original ELSE 'NI' END AS uf,
    COALESCE(prestadora_original, 'NÃO INFORMADO')                             AS prestadora,
    COALESCE(respondida_original, FALSE)                                       AS respondida,
    COALESCE(reaberta_original, FALSE)                                         AS reaberta,
    situacao,
    resolvida,                                     -- NULL = consumidor não avaliou
    (resolvida IS NOT NULL)                                                    AS avaliada,

    -- R4: tempo de resposta fora de 0..365 dias é erro de registro: não entra na média
    CASE WHEN tempo_original BETWEEN 0 AND getvariable('tempo_max') THEN tempo_original END
                                                                               AS tempo_resposta_dias,
    -- R5: SLA regulatório = resposta em até 10 dias corridos (configurável)
    (COALESCE(respondida_original, FALSE)
        AND tempo_original BETWEEN 0 AND getvariable('sla_dias'))              AS respondida_no_sla,
    -- R6: nota só vale de 1 a 5
    CASE WHEN nota_original BETWEEN 1 AND 5 THEN nota_original END             AS nota,

    -- R7: base de análise = tipo "Reclamação", dentro do período e só meses "maduros"
    (chave_texto(tipo_atendimento) = 'reclamacao')                             AS e_reclamacao,
    (data_abertura >= getvariable('inicio')
        AND data_abertura < getvariable('corte') - to_months(getvariable('meses_maturacao')))
                                                                               AS no_periodo,

    -- Marcadores de qualidade (alimentam o relatório; não excluem a linha)
    (data_abertura IS NULL)                                                    AS flag_data_invalida,
    (uf_original IS NULL OR uf_original NOT IN (SELECT uf FROM ref_estados))   AS flag_uf_invalida,
    (tempo_texto IS NOT NULL AND (tempo_original IS NULL
        OR tempo_original NOT BETWEEN 0 AND getvariable('tempo_max')))         AS flag_tempo_invalido,
    (nota_texto IS NOT NULL AND (nota_original IS NULL
        OR nota_original NOT BETWEEN 1 AND 5))                                 AS flag_nota_invalida,
    (COALESCE(respondida_original, FALSE) AND data_resposta IS NULL)           AS flag_respondida_sem_data,
    (data_resposta < data_abertura)                                            AS flag_resposta_antes_abertura,
    -- o campo de tempo bate com a diferença entre as datas?
    (tempo_original IS NOT NULL AND data_resposta IS NOT NULL AND data_abertura IS NOT NULL
        AND tempo_original <> date_diff('day', data_abertura, data_resposta))  AS flag_tempo_inconsistente
FROM tipado;


-- ---------------------------------------------------------------------
-- Fonte complementar: solicitações registradas - Agregada por mês
-- ---------------------------------------------------------------------
-- R8: "de-para" de serviço. As duas bases da Anatel usam nomes diferentes
-- para o mesmo serviço. Sem isso, não dá para cruzar uma com a outra.
CREATE OR REPLACE TABLE ref_depara_servico AS
SELECT * FROM (VALUES
    ('smp_pos',  'Celular Pós-Pago'),
    ('smp_pre',  'Celular Pré-Pago'),
    ('scm',      'Banda Larga Fixa'),
    ('seac',     'TV por Assinatura'),
    ('stfc',     'Telefone Fixo')
) AS t(codigo_registradas, servico);

CREATE OR REPLACE TABLE prata_registradas AS
SELECT
    CAST(strptime(left(trim(anomes), 7) || '-01', '%Y-%m-%d') AS DATE)       AS data_mes,
    CASE WHEN upper(limpa_texto(uf)) IN (SELECT uf FROM ref_estados)
         THEN upper(limpa_texto(uf)) ELSE 'NI' END                           AS uf,
    COALESCE(d.servico, 'Outros')                                            AS servico,
    upper(COALESCE(limpa_texto(marca), 'NÃO INFORMADO'))                     AS prestadora,
    COALESCE(limpa_texto(canal_entrada), 'Não informado')                    AS canal_entrada,
    limpa_texto(condicao)                                                    AS condicao,
    limpa_texto(tipo_atendimento)                                            AS tipo_atendimento,
    COALESCE(try_cast(trim(solicitacoes) AS INTEGER), 0)                     AS solicitacoes
FROM bronze_registradas b
LEFT JOIN ref_depara_servico d
       ON d.codigo_registradas = regexp_replace(chave_texto(b.servico), '[^a-z]+', '_', 'g')
WHERE chave_texto(b.tipo_atendimento) = 'reclamacao'
  AND try_strptime(left(trim(anomes), 7) || '-01', '%Y-%m-%d') >= getvariable('inicio')
  AND try_strptime(left(trim(anomes), 7) || '-01', '%Y-%m-%d')
        < getvariable('corte') - to_months(getvariable('meses_maturacao'));
