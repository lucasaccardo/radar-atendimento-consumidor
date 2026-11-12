-- =====================================================================
-- 03 - FATO: uma linha por reclamação, só com chaves e medidas
-- =====================================================================
CREATE OR REPLACE TABLE fato_atendimento AS
SELECT
    b.data_mes,
    b.uf,
    s.id_servico,
    o.id_operadora,
    a.id_assunto,
    b.respondida,
    b.respondida_no_sla,
    b.reaberta,
    b.avaliada,
    COALESCE(b.resolvida, FALSE)  AS resolvida,
    b.tempo_resposta_dias,
    b.nota
FROM base_analise b
JOIN dim_servico   s USING (servico)
JOIN dim_operadora o USING (prestadora)
JOIN dim_assunto   a USING (assunto);
