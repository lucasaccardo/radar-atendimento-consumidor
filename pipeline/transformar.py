"""
ETAPA 2 - TRANSFORMAÇÃO (BRONZE -> PRATA -> OURO)

O Python aqui só faz o "encanamento": abre o .zip, padroniza o nome das
colunas e manda o DuckDB executar os arquivos .sql da pasta sql/ em ordem.
TODA a regra de negócio está em SQL, que é a linguagem que a área de dados lê.

DuckDB:
- Roda SQL padrão direto em arquivos CSV, sem instalar servidor.
- Processa milhões de linhas em segundos num notebook comum.
- O mesmo SQL migraria para MySQL, Athena ou Snowflake com poucos ajustes.
"""
import json
import logging
import re
import unicodedata
import zipfile
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path

import duckdb

import config

log = logging.getLogger("transformar")


# ---------------------------------------------------------------------------
# Padronização do cabeçalho
# ---------------------------------------------------------------------------
def normalizar_nome(coluna: str) -> str:
    """'SOLICITAÇÕES' -> 'solicitacoes' | 'PrazoResposta' -> 'tempo_resposta'."""
    sem_acento = unicodedata.normalize("NFKD", coluna).encode("ascii", "ignore").decode()
    limpo = re.sub(r"[^a-z0-9]+", "_", sem_acento.lower()).strip("_")
    return config.APELIDOS_COLUNAS.get(limpo, limpo)


def detectar_codificacao(caminho: Path) -> str:
    """Testa UTF-8 numa amostra grande; se quebrar, é Latin-1 (padrão antigo do governo)."""
    with open(caminho, "rb") as f:
        amostra = f.read(4_000_000)
    try:
        amostra.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError as erro:
        # o corte da amostra pode ter partido um caractere de 2 bytes no fim
        return "utf-8" if erro.start >= len(amostra) - 3 else "latin-1"


def ler_cabecalho(caminho: Path, codificacao: str) -> list[str]:
    with open(caminho, "r", encoding=codificacao, errors="replace") as f:
        primeira = f.readline().lstrip("﻿").rstrip("\r\n")
    return [c.strip().strip('"').strip() for c in primeira.split(";")]


def descompactar(zip_path: Path, membro: str, destino: Path) -> int:
    """Extrai o CSV de dentro do zip contando as linhas no caminho (para reconciliar depois)."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    quebras, ultimo = 0, b""
    with zipfile.ZipFile(zip_path) as z:
        nome = next((n for n in z.namelist() if n.lower() == membro.lower()), None)
        if nome is None:
            raise ValueError(f"{zip_path.name} não contém {membro}. Conteúdo: {z.namelist()}")
        with z.open(nome) as origem, open(destino, "wb") as saida:
            for bloco in iter(lambda: origem.read(8 * 1024 * 1024), b""):
                quebras += bloco.count(b"\n")
                ultimo = bloco[-1:]
                saida.write(bloco)
    linhas = quebras + (1 if ultimo not in (b"\n", b"") else 0)
    return max(linhas - 1, 0)  # sem o cabeçalho


# ---------------------------------------------------------------------------
# Carga da BRONZE no DuckDB
# ---------------------------------------------------------------------------
def carregar_fonte(con, nome: str, reg: dict, p: dict) -> dict:
    """Lê o CSV como texto puro (all_varchar). Nenhuma regra é aplicada aqui:
    a tabela bronze é fiel ao arquivo original."""
    zip_path = p["bronze"] / reg["arquivo"]
    membro = config.FONTES_ANATEL[nome]["arquivo_interno"]   # a configuração atual manda
    csv_tmp = p["temporario"] / membro
    log.info("  descompactando %s (%s) ...", reg["arquivo"], membro)
    linhas_arquivo = descompactar(zip_path, membro, csv_tmp)

    codificacao = detectar_codificacao(csv_tmp)
    originais = ler_cabecalho(csv_tmp, codificacao)
    nomes = [normalizar_nome(c) for c in originais]
    faltando = [c for c in config.COLUNAS_OBRIGATORIAS[nome] if c not in nomes]
    if faltando:
        raise ValueError(f"Contrato de dados quebrado em {nome}: faltam {faltando}. "
                         f"Cabeçalho recebido: {originais}")

    lista = "[" + ", ".join(f"'{n}'" for n in nomes) + "]"
    caminho_sql = str(csv_tmp).replace("\\", "/").replace("'", "''")
    con.execute(f"""
        CREATE OR REPLACE TABLE bronze_{nome} AS
        SELECT * FROM read_csv('{caminho_sql}',
            delim=';', header=true, names={lista}, all_varchar=true,
            encoding='{codificacao}', quote='"', escape='"',
            ignore_errors=true, null_padding=true, max_line_size=10000000)
    """)
    carregadas = con.execute(f"SELECT count(*) FROM bronze_{nome}").fetchone()[0]
    try:
        csv_tmp.unlink()  # o original continua guardado no .zip da bronze
    except OSError:
        log.warning("Não consegui apagar %s agora (Windows ainda usando o arquivo). Pode apagar depois.", csv_tmp)
    log.info("  bronze_%s: %s linhas (%s)", nome, f"{carregadas:,}", codificacao)
    return {"fonte": nome, "arquivo": reg["arquivo"], "codificacao": codificacao,
            "colunas_originais": originais, "linhas_no_arquivo": linhas_arquivo,
            "linhas_carregadas": carregadas, "diferenca": linhas_arquivo - carregadas}


def carregar_ibge(con, p: dict) -> None:
    """Transforma os JSON do IBGE em duas tabelas de referência."""
    estados = json.loads((p["bronze"] / "ibge_estados.json").read_text(encoding="utf-8"))
    con.execute("CREATE OR REPLACE TABLE ref_estados (cod_ibge INTEGER, uf VARCHAR, nome_uf VARCHAR, "
                "regiao_sigla VARCHAR, regiao VARCHAR)")
    con.executemany("INSERT INTO ref_estados VALUES (?, ?, ?, ?, ?)", [
        (e["id"], e["sigla"], e["nome"], e["regiao"]["sigla"], e["regiao"]["nome"]) for e in estados])

    populacao = json.loads((p["bronze"] / "ibge_populacao.json").read_text(encoding="utf-8"))
    linhas = []
    for serie in populacao[0]["resultados"][0]["series"]:
        cod = int(serie["localidade"]["id"])
        for ano, valor in serie["serie"].items():
            if valor not in (None, "", "-", "...", "X"):
                linhas.append((cod, int(ano), int(valor)))
    con.execute("CREATE OR REPLACE TABLE ref_populacao_bruta (cod_ibge INTEGER, ano INTEGER, populacao BIGINT)")
    con.executemany("INSERT INTO ref_populacao_bruta VALUES (?, ?, ?)", linhas)


def mes_de_corte(manifesto: dict) -> date:
    """Primeiro dia do mês da extração. Meses a partir dele (e os de maturação)
    ficam fora: ainda têm reclamações em andamento."""
    lm = manifesto["fontes"]["atendimento"].get("last_modified")
    try:
        d = parsedate_to_datetime(lm).date()
    except (TypeError, ValueError):
        d = date.today() if lm != "amostra" else date(2026, 11, 12)
    return date(d.year, d.month, 1)


def executar(manifesto: dict, modo_amostra: bool = False):
    p = config.pastas(modo_amostra)
    p["banco"].parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(p["banco"]))

    log.info("Carregando bronze...")
    reconciliacao = []
    for nome, reg in manifesto["fontes"].items():
        if (p["bronze"] / reg["arquivo"]).exists():
            reconciliacao.append(carregar_fonte(con, nome, reg, p))
    tem_registradas = any(r["fonte"] == "registradas" for r in reconciliacao)
    if not tem_registradas:
        # tabela vazia com as colunas esperadas: o SQL roda igual, só sem dados de canal
        cols = ", ".join(f"{c} VARCHAR" for c in config.COLUNAS_OBRIGATORIAS["registradas"])
        con.execute(f"CREATE OR REPLACE TABLE bronze_registradas ({cols})")
    carregar_ibge(con, p)

    # Parâmetros de negócio que o SQL lê com getvariable(): ficam no config.py
    corte = mes_de_corte(manifesto)
    con.execute(f"SET VARIABLE inicio = DATE '{config.COMPETENCIA_INICIO}-01'")
    con.execute(f"SET VARIABLE corte = DATE '{corte.isoformat()}'")
    con.execute(f"SET VARIABLE meses_maturacao = {int(config.MESES_MATURACAO)}")
    con.execute(f"SET VARIABLE sla_dias = {int(config.SLA_RESPOSTA_DIAS)}")
    con.execute(f"SET VARIABLE tempo_max = {int(config.TEMPO_RESPOSTA_MAX_DIAS)}")
    con.execute(f"SET VARIABLE min_participacao = {float(config.MIN_PARTICIPACAO_OPERADORA)}")

    log.info("Executando SQL (prata, dimensões, fato, ouro)...")
    for arquivo in sorted(p["sql"].glob("*.sql")):
        log.info("  %s", arquivo.name)
        con.execute(arquivo.read_text(encoding="utf-8"))
    return con, reconciliacao, tem_registradas
