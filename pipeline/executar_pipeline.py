"""
ORQUESTRADOR - roda as 4 etapas em ordem e registra a execução.

Pasta do Projeto:
    python pipeline/executar_pipeline.py              # dados reais (baixa o que faltar)
    python pipeline/executar_pipeline.py --amostra    # teste offline com dados sintéticos
    python pipeline/executar_pipeline.py --sem-download  # reprocessa sem baixar nada
    python pipeline/executar_pipeline.py --pasta-local ~/Downloads  # usa os .zip já baixados

Fluxo:
    1. extrair     -> bronze (arquivos originais + manifesto)
    2. transformar -> prata, dimensões, fato e ouro (SQL no DuckDB)
    3. qualidade   -> relatório; se tiver ERRO, para aqui e não publica
    4. exportar    -> JSON da lâmina + Parquet/CSV do Power BI
"""
import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import config
import exportar
import extrair
import qualidade
import transformar


def configurar_log(pasta_logs) -> str:
    # o terminal do Windows não usa UTF-8; sem isso, símbolos como "≠" quebram o log
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")
    pasta_logs.mkdir(parents=True, exist_ok=True)
    arquivo = pasta_logs / f"pipeline_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)-11s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(arquivo, encoding="utf-8")],
    )
    return str(arquivo)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pipeline do Radar de Atendimento em Telecom")
    parser.add_argument("--amostra", action="store_true", help="usa dados sintéticos de teste")
    parser.add_argument("--sem-download", action="store_true", help="não baixa nada, só reprocessa")
    parser.add_argument("--forcar", action="store_true", help="publica mesmo com ERRO de qualidade")
    parser.add_argument("--pasta-local", type=Path, default=None,
                        help="pasta onde você já baixou os .zip da Anatel (ex.: ~/Downloads)")
    args = parser.parse_args()

    p = config.pastas(args.amostra)
    arquivo_log = configurar_log(p["logs"])
    log = logging.getLogger("pipeline")
    inicio = time.time()
    execucao = {"inicio": datetime.now().isoformat(timespec="seconds"),
                "modo": "amostra" if args.amostra else "real", "status": "falhou"}
    etapas = {}

    try:
        t = time.time()
        log.info("=== 1/4 EXTRAÇÃO ===")
        if args.sem_download and not args.amostra:
            manifesto = extrair.carregar_manifesto(p["manifesto"])
            if not manifesto.get("fontes"):
                raise RuntimeError("Ainda não há dados baixados. Rode primeiro sem --sem-download "
                                   "(ou com --pasta-local ~/Downloads).")
        else:
            manifesto = extrair.executar(args.amostra, args.pasta_local)
        etapas["extracao_s"] = round(time.time() - t, 1)

        t = time.time()
        log.info("=== 2/4 TRANSFORMAÇÃO ===")
        con, reconciliacao, tem_registradas = transformar.executar(manifesto, args.amostra)
        etapas["transformacao_s"] = round(time.time() - t, 1)

        t = time.time()
        log.info("=== 3/4 QUALIDADE ===")
        relatorio = qualidade.executar(con, reconciliacao, manifesto, tem_registradas)
        etapas["qualidade_s"] = round(time.time() - t, 1)
        if not relatorio["aprovado"] and not args.forcar:
            raise RuntimeError("Reprovado na checagem de qualidade (há itens com ERRO). "
                               "Nada foi publicado. Veja o log e corrija a causa, "
                               "ou rode com --forcar se tiver certeza.")

        t = time.time()
        log.info("=== 4/4 PUBLICAÇÃO ===")
        publicado = exportar.executar(con, relatorio, manifesto, args.amostra, tem_registradas)
        etapas["publicacao_s"] = round(time.time() - t, 1)
        con.close()

        execucao.update({
            "status": "sucesso" if relatorio["resumo"]["ALERTA"] == 0 else "sucesso_com_alertas",
            "competencia_mais_recente": publicado["competencia_fim"],
            "fontes_atualizadas": [n for n, f in manifesto["fontes"].items() if f.get("novo")],
            "falhas_download": manifesto.get("falhas", []),
            "linhas_base_analise": relatorio["totais"]["linhas_base_analise"],
            "checagens": relatorio["resumo"],
            "meses_faltando": relatorio["meses_faltando"],
        })
        return_code = 0
    except Exception as erro:  # noqa: BLE001
        log.exception("Pipeline interrompido: %s", erro)
        execucao["erro"] = str(erro)[:500]
        return_code = 1
    finally:
        execucao["fim"] = datetime.now().isoformat(timespec="seconds")
        execucao["duracao_s"] = round(time.time() - inicio, 1)
        execucao["etapas"] = etapas
        execucao["log"] = arquivo_log.split("/")[-1].split("\\")[-1]
        exportar.registrar_execucao(p["lamina_dados"], execucao)
        log.info("Execução %s em %ss", execucao["status"].upper(), execucao["duracao_s"])
    return return_code


if __name__ == "__main__":
    sys.exit(main())
