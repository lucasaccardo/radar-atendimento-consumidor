"""
ETAPA 4 - PUBLICAÇÃO

Gera duas saídas a partir da camada ouro:
1. docs/data/*.json  -> lida pela lâmina HTML 
2. data/ouro/powerbi/*.parquet e *.csv -> lida pelo Power BI

Formato do JSON da lâmina: "colunar compacto". Em vez de repetir o nome de
cada campo em cada linha ({"uf":"SP","rec":10}, {"uf":"RJ","rec":7}...),
mandei a lista de colunas uma vez e as linhas como listas de números.
Textos repetidos (UF, serviço, operadora) viram índices de um dicionário.
Resultado: arquivo várias vezes menor e lâmina abrindo rápido.
"""
import json
import logging
from datetime import datetime

import config

log = logging.getLogger("exportar")

MEDIDAS = ["reclamacoes", "respondidas", "respondidas_no_sla", "soma_tempo_resposta",
           "qtd_tempo_resposta", "reabertas", "avaliadas", "resolvidas", "soma_nota", "qtd_nota"]


def _lista(con, sql: str) -> list:
    return [r[0] for r in con.execute(sql).fetchall()]


def gerar_json_lamina(con, manifesto: dict, modo_amostra: bool, tem_registradas: bool) -> dict:
    meses = _lista(con, "SELECT ano_mes FROM dim_calendario ORDER BY data_mes")
    ufs = [list(r) for r in con.execute(
        "SELECT uf, nome_uf, regiao FROM dim_uf ORDER BY uf = 'NI', uf").fetchall()]
    servicos = _lista(con, "SELECT servico FROM ouro_cubo GROUP BY 1 ORDER BY sum(reclamacoes) DESC")
    # operadoras por volume, com OUTRAS sempre no fim
    operadoras = _lista(con, """SELECT operadora FROM ouro_cubo GROUP BY 1
                                ORDER BY operadora = 'OUTRAS', sum(reclamacoes) DESC""")
    assuntos = _lista(con, "SELECT assunto FROM ouro_cubo_assunto GROUP BY 1 ORDER BY sum(reclamacoes) DESC")
    canais = _lista(con, "SELECT canal_entrada FROM ouro_cubo_canal GROUP BY 1 ORDER BY sum(solicitacoes) DESC")

    i_mes = {m: i for i, m in enumerate(meses)}
    i_uf = {u[0]: i for i, u in enumerate(ufs)}
    i_srv = {s: i for i, s in enumerate(servicos)}
    i_op = {o: i for i, o in enumerate(operadoras)}
    i_ass = {a: i for i, a in enumerate(assuntos)}
    i_can = {c: i for i, c in enumerate(canais)}

    cubo = [[i_mes[m], i_uf[u], i_srv[s], i_op[o], *v] for m, u, s, o, *v in con.execute(f"""
        SELECT strftime(data_mes, '%Y-%m'), uf, servico, operadora, {", ".join(MEDIDAS)}
        FROM ouro_cubo ORDER BY 1, 2, 3, 4""").fetchall()]
    cubo_assunto = [[i_mes[m], i_srv[s], i_op[o], i_ass[a], *v] for m, s, o, a, *v in con.execute("""
        SELECT strftime(data_mes, '%Y-%m'), servico, operadora, assunto,
               reclamacoes, reabertas, avaliadas, resolvidas
        FROM ouro_cubo_assunto ORDER BY 1, 2, 3, 4""").fetchall()]
    cubo_canal = [[i_mes[m], i_srv[s], i_op[o], i_can[c], n] for m, s, o, c, n in con.execute("""
        SELECT strftime(data_mes, '%Y-%m'), servico, operadora, canal_entrada, sum(solicitacoes)
        FROM ouro_cubo_canal
        WHERE servico IN (SELECT servico FROM ouro_cubo) GROUP BY ALL ORDER BY 1""").fetchall()
        if s in i_srv and o in i_op and m in i_mes]

    populacao = {}
    for uf, ano, pop in con.execute("SELECT uf, ano, populacao FROM ref_populacao").fetchall():
        populacao.setdefault(str(ano), {})[uf] = pop

    fonte = manifesto["fontes"]["atendimento"]
    nota_ibge = (" (cópia de referência: a API do IBGE não respondeu nesta execução)"
                 if manifesto.get("ibge", {}).get("populacao", {}).get("origem") == "copia_referencia"
                 else "")
    return {
        "meta": {
            "titulo": "Radar de Atendimento em Telecom",
            "origem": "amostra_sintetica" if modo_amostra else "dados_reais",
            "gerado_em": datetime.now().isoformat(timespec="seconds"),
            "arquivo_anatel_atualizado_em": fonte.get("last_modified"),
            "competencia_inicio": meses[0],
            "competencia_fim": meses[-1],
            "meses_maturacao": config.MESES_MATURACAO,
            "sla_dias": config.SLA_RESPOSTA_DIAS,
            "min_participacao_operadora": config.MIN_PARTICIPACAO_OPERADORA,
            "tem_canal": tem_registradas and bool(cubo_canal),
            "fontes": [
                {"nome": f["titulo"], "url": f["pagina"]} for f in config.FONTES_ANATEL.values()
            ] + [
                {"nome": "IBGE - População residente estimada (tabela 6579)" + nota_ibge,
                 "url": "https://sidra.ibge.gov.br/tabela/6579"},
                {"nome": "IBGE - Localidades (UF e região)",
                 "url": "https://servicodados.ibge.gov.br/api/docs/localidades"},
            ],
        },
        "dimensoes": {"meses": meses, "ufs": ufs, "servicos": servicos, "operadoras": operadoras,
                      "assuntos": assuntos, "canais": canais},
        "medidas": MEDIDAS,
        "cubo": {"colunas": ["mes", "uf", "servico", "operadora", *MEDIDAS], "linhas": cubo},
        "cubo_assunto": {"colunas": ["mes", "servico", "operadora", "assunto", "reclamacoes",
                                     "reabertas", "avaliadas", "resolvidas"], "linhas": cubo_assunto},
        "cubo_canal": {"colunas": ["mes", "servico", "operadora", "canal", "solicitacoes"],
                       "linhas": cubo_canal},
        "populacao": populacao,
    }


def exportar_powerbi(con, pasta, pasta_csv) -> list[str]:
    """Modelo estrela em Parquet (rápido, tipado, pequeno: vai para o GitHub)
    e em CSV (abre em qualquer lugar, mas é grande: fica só na sua máquina)."""
    pasta.mkdir(parents=True, exist_ok=True)
    pasta_csv.mkdir(parents=True, exist_ok=True)
    tabelas = {
        "fato_atendimento_mensal": "SELECT * FROM ouro_fato_mensal",
        "fato_canal_mensal": "SELECT * FROM ouro_cubo_canal",
        "dim_calendario": "SELECT * FROM dim_calendario",
        "dim_uf": "SELECT * FROM dim_uf",
        "dim_servico": "SELECT * FROM dim_servico",
        "dim_operadora": "SELECT * FROM dim_operadora",
        "dim_assunto": "SELECT * FROM dim_assunto",
        "dim_populacao": "SELECT * FROM ref_populacao",
    }
    for nome, sql in tabelas.items():
        destino = str(pasta / nome).replace("\\", "/")
        destino_csv = str(pasta_csv / nome).replace("\\", "/")
        con.execute(f"COPY ({sql}) TO '{destino}.parquet' (FORMAT parquet, COMPRESSION zstd)")
        con.execute(f"COPY ({sql}) TO '{destino_csv}.csv' (HEADER, DELIMITER ';')")
    return list(tabelas)


def registrar_execucao(pasta, execucao: dict) -> list:
    """Histórico das últimas 24 execuções: é o 'controle de atualizações' da lâmina."""
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / "execucoes.json"
    historico = json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else []
    historico = [execucao, *historico][:24]
    arquivo.write_text(json.dumps(historico, ensure_ascii=False, indent=1), encoding="utf-8")
    gerar_bundle_js(pasta)
    return historico


def gerar_bundle_js(pasta) -> None:
    """Junta os 3 JSON num dados.js. Motivo: navegador bloqueia fetch() de JSON
    quando o HTML é aberto direto do disco (file://). Um <script src> funciona
    sempre: no GitHub Pages e com duplo clique no arquivo."""
    partes = []
    for variavel, nome in (("RADAR", "radar.json"), ("QUALIDADE", "qualidade.json"),
                           ("EXECUCOES", "execucoes.json")):
        arquivo = pasta / nome
        conteudo = arquivo.read_text(encoding="utf-8") if arquivo.exists() else "null"
        partes.append(f"window.{variavel} = {conteudo};")
    (pasta / "dados.js").write_text("\n".join(partes), encoding="utf-8")


def executar(con, qualidade: dict, manifesto: dict, modo_amostra: bool, tem_registradas: bool) -> dict:
    p = config.pastas(modo_amostra)
    p["lamina_dados"].mkdir(parents=True, exist_ok=True)

    dados = gerar_json_lamina(con, manifesto, modo_amostra, tem_registradas)
    destino = p["lamina_dados"] / "radar.json"
    destino.write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (p["lamina_dados"] / "qualidade.json").write_text(
        json.dumps(qualidade, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log.info("Lâmina: %s (%.0f KB)", destino.name, destino.stat().st_size / 1024)

    gerados = exportar_powerbi(con, p["ouro_bi"], p["ouro_csv"])
    log.info("Power BI: %s tabelas em %s", len(gerados), p["ouro_bi"])
    return {"competencia_fim": dados["meta"]["competencia_fim"], "kb_json": round(destino.stat().st_size / 1024)}
