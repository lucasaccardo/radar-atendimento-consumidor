"""
ETAPA 3 - QUALIDADE DE DADOS 

Cada checagem devolve OK, ALERTA, ERRO ou INFO.
- ALERTA: publica, mas mostra o aviso na lâmina (transparência).
- ERRO:   o pipeline NÃO publica. Melhor ficar com o dado do mês passado
          do que publicar um número errado para a diretoria.
- INFO:   não é problema, é contexto para quem lê (ex.: o que ficou fora da base).
"""
import logging
from datetime import datetime
from statistics import median

import config

log = logging.getLogger("qualidade")


def _status(pct: float, chave: str) -> str:
    alerta, erro = config.LIMITES_QUALIDADE[chave]
    if pct >= erro:
        return "ERRO"
    if pct >= alerta:
        return "ALERTA"
    return "OK"


def executar(con, reconciliacao: list[dict], manifesto: dict, tem_registradas: bool) -> dict:
    um = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    checks = []

    def registrar(chave, titulo, regra, qtd, base, status=None, detalhe=None):
        pct = round(100.0 * qtd / base, 3) if base else 0.0
        checks.append({"id": chave, "titulo": titulo, "regra": regra, "quantidade": int(qtd),
                       "percentual": pct, "status": status or _status(pct, chave), "detalhe": detalhe})

    linhas_prata = um("SELECT count(*) FROM prata_atendimento")
    linhas_base = um("SELECT count(*) FROM base_analise")

    # 1. Reconciliação: tudo que estava no arquivo chegou no banco?
    for r in reconciliacao:
        registrar("diferenca_reconciliacao", f"Reconciliação arquivo x banco ({r['fonte']})",
                  "Linhas no arquivo = linhas carregadas", abs(r["diferenca"]), r["linhas_no_arquivo"],
                  detalhe=[f"{r['linhas_no_arquivo']:,} no arquivo; {r['linhas_carregadas']:,} carregadas"
                           .replace(",", ".")])

    # 2. Linhas idênticas: NÃO removemos (a base não tem ID; podem ser reclamações diferentes)
    distintas = um("""SELECT count(DISTINCT hash(data_abertura, data_resposta, data_finalizacao, uf,
                      prestadora, servico, assunto, respondida, reaberta, tempo_resposta_dias,
                      resolvida, nota, situacao)) FROM base_analise""")
    registrar("linhas_identicas", "Linhas idênticas (mantidas)",
              "Sem ID na fonte: não dá para afirmar que são duplicadas",
              linhas_base - distintas, linhas_base, status="INFO")

    # 3. Datas inválidas (sobre tudo o que chegou)
    registrar("datas_invalidas", "Data de abertura inválida", "R2",
              um("SELECT count(*) FROM prata_atendimento WHERE flag_data_invalida"), linhas_prata)

    # 4..8. Marcadores calculados na prata, medidos dentro da base de análise
    marcadores = [
        ("uf_invalida", "flag_uf_invalida", "UF ausente ou inválida", "R3"),
        ("tempo_resposta_invalido", "flag_tempo_invalido", "Tempo de resposta inválido", "R4"),
        ("nota_invalida", "flag_nota_invalida", "Nota fora de 1 a 5", "R6"),
        ("respondida_sem_data", "flag_respondida_sem_data", "Respondida sem data de resposta", "Coerência"),
        ("resposta_antes_abertura", "flag_resposta_antes_abertura", "Resposta antes da abertura", "Coerência"),
    ]
    contagens = con.execute(
        "SELECT " + ", ".join(f"count(*) FILTER (WHERE {col})" for _, col, _, _ in marcadores)
        + " FROM base_analise").fetchone()
    for (chave, _, titulo, regra), qtd in zip(marcadores, contagens):
        registrar(chave, titulo, regra, qtd, linhas_base)

    registrar("tempo_inconsistente", "Tempo de resposta ≠ diferença entre as datas",
              "Informativo: usamos o campo oficial de tempo",
              um("SELECT count(*) FROM base_analise WHERE flag_tempo_inconsistente"), linhas_base,
              status="INFO")

    # 9. Meses faltando na série
    buracos = [r[0] for r in con.execute(
        "SELECT ano_mes FROM dim_calendario WHERE NOT tem_dados ORDER BY 1").fetchall()]
    checks.append({"id": "meses_faltando", "titulo": "Meses sem dados na série",
                   "regra": "Série mensal completa", "quantidade": len(buracos), "percentual": None,
                   "status": "ALERTA" if buracos else "OK", "detalhe": buracos or None})

    # 10. Volume anômalo (queda/alta brusca costuma ser arquivo incompleto)
    volumes = dict(con.execute(
        "SELECT strftime(data_mes, '%Y-%m'), count(*) FROM base_analise GROUP BY 1 ORDER BY 1").fetchall())
    mediana = median(volumes.values()) if volumes else 0
    fora = {m: v for m, v in volumes.items()
            if mediana and abs(v / mediana - 1) > config.LIMITE_VARIACAO_VOLUME}
    checks.append({
        "id": "volume_anomalo", "titulo": "Volume mensal fora do padrão",
        "regra": f"±{int(config.LIMITE_VARIACAO_VOLUME * 100)}% da mediana mensal ({int(mediana):,})".replace(",", "."),
        "quantidade": len(fora), "percentual": None, "status": "ALERTA" if fora else "OK",
        "detalhe": [f"{m}: {v:,} ({v / mediana - 1:+.0%})".replace(",", ".") for m, v in fora.items()] or None})

    # 11. Toda UF/ano tem população? (senão a taxa por 100 mil sai errada)
    sem_pop = con.execute("""
        SELECT DISTINCT f.uf || '/' || c.ano FROM ouro_cubo f JOIN dim_calendario c USING (data_mes)
        LEFT JOIN ref_populacao p ON p.uf = f.uf AND p.ano = c.ano
        WHERE f.uf <> 'NI' AND p.populacao IS NULL""").fetchall()
    checks.append({"id": "cobertura_populacao", "titulo": "UF/ano sem população do IBGE", "regra": "R10",
                   "quantidade": len(sem_pop), "percentual": None,
                   "status": "ERRO" if sem_pop else "OK", "detalhe": [s[0] for s in sem_pop] or None})

    # 12. Conferência entre as duas bases da Anatel
    if tem_registradas:
        conf = con.execute("""SELECT strftime(data_mes, '%Y-%m'), diferenca_pct FROM ouro_conferencia_fontes
                              WHERE diferenca_pct IS NOT NULL ORDER BY abs(diferenca_pct) DESC""").fetchall()
        media_abs = sum(abs(d) for _, d in conf) / len(conf) if conf else 0
        checks.append({
            "id": "divergencia_entre_fontes", "titulo": "Volume: base de atendimento x base de registros (novas + reabertas)",
            "regra": "Média da diferença mensal absoluta", "quantidade": len(conf),
            "percentual": round(media_abs, 2), "status": _status(media_abs, "divergencia_entre_fontes"),
            "detalhe": [f"{m}: {d:+.1f}%" for m, d in conf[:3]] or None})

    # 13. O que ficou fora da base de análise (informativo)
    fora_base = con.execute("""
        SELECT count(*) FILTER (WHERE NOT e_reclamacao),
               count(*) FILTER (WHERE e_reclamacao AND data_abertura < getvariable('inicio')),
               count(*) FILTER (WHERE e_reclamacao AND data_abertura >= getvariable('corte')
                                                      - to_months(getvariable('meses_maturacao')))
        FROM prata_atendimento""").fetchone()
    registrar("fora_da_base", "Fora da base de análise", "R7", linhas_prata - linhas_base, linhas_prata,
              status="INFO", detalhe=[f"outros tipos de atendimento: {fora_base[0]:,}".replace(",", "."),
                                      f"antes de {config.COMPETENCIA_INICIO}: {fora_base[1]:,}".replace(",", "."),
                                      f"meses ainda em maturação: {fora_base[2]:,}".replace(",", ".")])

    # 14. Contrato de dados: se chegamos aqui, todos os arquivos tinham as colunas obrigatórias
    checks.append({"id": "contrato_dados", "titulo": "Colunas obrigatórias presentes",
                   "regra": "Layout esperado da Anatel", "quantidade": len(reconciliacao),
                   "percentual": None, "status": "OK", "detalhe": None})

    resumo = {s: sum(1 for c in checks if c["status"] == s) for s in ("OK", "ALERTA", "ERRO", "INFO")}
    for c in checks:
        nivel = {"ERRO": logging.ERROR, "ALERTA": logging.WARNING}.get(c["status"], logging.INFO)
        pct = f" ({c['percentual']}%)" if c["percentual"] is not None else ""
        log.log(nivel, "  [%s] %s: %s%s", c["status"], c["titulo"], f"{c['quantidade']:,}", pct)

    return {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "totais": {"linhas_prata": linhas_prata, "linhas_base_analise": linhas_base,
                   "linhas_nos_arquivos": {r["fonte"]: r["linhas_no_arquivo"] for r in reconciliacao}},
        "resumo": resumo,
        "aprovado": resumo["ERRO"] == 0,
        "checks": checks,
        "reconciliacao": reconciliacao,
        "meses_faltando": buracos,
    }
