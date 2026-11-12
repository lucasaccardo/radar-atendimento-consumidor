"""
ETAPA 1 - EXTRAÇÃO (camada BRONZE)

O que faz:
1. Pergunta ao servidor da Anatel se o arquivo mudou (cabeçalho Last-Modified).
2. Só baixa de novo se mudou (extração incremental: economiza tempo e banda).
3. Baixa população e lista de UFs do IBGE.
4. Registra tudo num MANIFESTO: de onde veio, quando, tamanho e hash SHA-256.

Bronze = o arquivo ORIGINAL, intocado (o .zip da Anatel e o JSON do IBGE).
Se um número da lâmina for questionado, dá para provar de qual arquivo ele
veio, quando foi baixado e que o arquivo não foi alterado (hash).
"""
import hashlib
import json
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

import requests

import config

log = logging.getLogger("extrair")


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _requisitar(metodo: str, url: str, stream: bool = False) -> requests.Response:
    """Requisição com novas tentativas e espera crescente (2s, 4s, 8s...).
    Sites do governo oscilam; uma falha isolada não deve derrubar o pipeline."""
    ultimo_erro = None
    for tentativa in range(1, config.HTTP_TENTATIVAS + 1):
        try:
            resp = requests.request(metodo, url, headers=config.HTTP_HEADERS,
                                    timeout=config.HTTP_TIMEOUT, stream=stream)
            resp.raise_for_status()
            return resp
        except requests.RequestException as erro:
            ultimo_erro = erro
            espera = 2 ** tentativa
            log.warning("Falha (%s/%s) em %s: %s. Nova tentativa em %ss",
                        tentativa, config.HTTP_TENTATIVAS, url, erro, espera)
            time.sleep(espera)
    raise RuntimeError(f"Não foi possível acessar {url}: {ultimo_erro}")


def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def carregar_manifesto(caminho: Path) -> dict:
    if caminho.exists():
        return json.loads(caminho.read_text(encoding="utf-8"))
    return {"fontes": {}, "ibge": {}}


def salvar_manifesto(caminho: Path, manifesto: dict) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Anatel
# ---------------------------------------------------------------------------
def baixar_fonte(nome: str, fonte: dict, p: dict, manifesto: dict) -> None:
    destino = p["bronze"] / f"anatel_{nome}.zip"
    anterior = manifesto["fontes"].get(nome)

    cabecalho = _requisitar("HEAD", fonte["url"]).headers
    versao_remota = {"last_modified": cabecalho.get("Last-Modified"),
                     "bytes": int(cabecalho.get("Content-Length", 0))}
    if (anterior and destino.exists()
            and anterior.get("last_modified") == versao_remota["last_modified"]
            and anterior.get("bytes") == versao_remota["bytes"]):
        log.info("%s: sem mudança desde %s (não baixa de novo)", nome, anterior["last_modified"])
        anterior["verificado_em"] = _agora()
        anterior["novo"] = False
        return

    log.info("%s: baixando %.0f MB ...", nome, versao_remota["bytes"] / 1e6)
    inicio = time.time()
    temporario = destino.with_suffix(".part")
    with _requisitar("GET", fonte["url"], stream=True) as resp, open(temporario, "wb") as f:
        for bloco in resp.iter_content(chunk_size=4 * 1024 * 1024):
            f.write(bloco)
    temporario.replace(destino)   # só substitui o arquivo antigo quando o novo chegou inteiro

    manifesto["fontes"][nome] = {
        "titulo": fonte["titulo"],
        "pagina": fonte["pagina"],
        "url": fonte["url"],
        "arquivo": destino.name,
        "arquivo_interno": fonte["arquivo_interno"],
        "last_modified": versao_remota["last_modified"],
        "bytes": destino.stat().st_size,
        "sha256": sha256(destino),
        "baixado_em": _agora(),
        "verificado_em": _agora(),
        "segundos_download": round(time.time() - inicio, 1),
        "novo": True,
    }
    log.info("%s: ok em %ss", nome, manifesto["fontes"][nome]["segundos_download"])


def extrair_anatel(p: dict, manifesto: dict) -> None:
    p["bronze"].mkdir(parents=True, exist_ok=True)
    manifesto["falhas"] = []
    for nome, fonte in config.FONTES_ANATEL.items():
        try:
            baixar_fonte(nome, fonte, p, manifesto)
        except Exception as erro:  # noqa: BLE001
            ja_tinha = (p["bronze"] / f"anatel_{nome}.zip").exists()
            manifesto["falhas"].append({"fonte": nome, "erro": str(erro)[:300], "em": _agora(),
                                        "usou_versao_anterior": ja_tinha})
            if ja_tinha:
                log.error("%s indisponível (%s). Usando a versão baixada antes.", nome, erro)
            elif fonte["obrigatoria"]:
                raise RuntimeError(f"Fonte obrigatória '{nome}' indisponível e sem cópia local") from erro
            else:
                log.error("%s indisponível e é opcional: seguindo sem ela.", nome)
        salvar_manifesto(p["manifesto"], manifesto)


# ---------------------------------------------------------------------------
# IBGE
# ---------------------------------------------------------------------------
def extrair_ibge(p: dict, manifesto: dict) -> None:
    ano_inicio = int(config.COMPETENCIA_INICIO[:4])
    anos = [str(a) for a in range(ano_inicio, datetime.now().year + 1)]
    alvos = {"populacao": config.IBGE_POPULACAO.format(anos="|".join(anos)),
             "estados": config.IBGE_ESTADOS}
    for nome, url in alvos.items():
        destino = p["bronze"] / f"ibge_{nome}.json"
        copia_referencia = p["referencia"] / f"ibge_{nome}.json"
        try:
            conteudo = _requisitar("GET", url).content
            json.loads(conteudo)  # garante que veio um JSON válido antes de sobrescrever
            destino.write_bytes(conteudo)
            # guarda a resposta no repositório: é ela que a automação usa se a API não responder
            copia_referencia.parent.mkdir(parents=True, exist_ok=True)
            copia_referencia.write_bytes(conteudo)
            manifesto["ibge"][nome] = {"url": url, "origem": "api", "baixado_em": _agora(),
                                       "bytes": len(conteudo), "sha256": sha256(destino)}
            log.info("IBGE %s: ok", nome)
        except Exception as erro:  # noqa: BLE001
            manifesto.setdefault("falhas", []).append(
                {"fonte": f"ibge_{nome}", "erro": str(erro)[:300], "em": _agora(),
                 "usou_versao_anterior": destino.exists() or copia_referencia.exists()})
            if destino.exists():
                log.warning("IBGE %s indisponível: mantendo a versão de %s",
                            nome, manifesto["ibge"].get(nome, {}).get("baixado_em"))
            elif copia_referencia.exists():
                # D18: a API do IBGE não responde aos servidores do GitHub (fora do Brasil)
                shutil.copy(copia_referencia, destino)
                manifesto["ibge"][nome] = {"url": url, "origem": "copia_referencia",
                                           "baixado_em": _agora(), "sha256": sha256(destino)}
                log.warning("IBGE %s indisponível: usando a cópia de referência do repositório", nome)
            else:
                raise


# ---------------------------------------------------------------------------
# Modo amostra (teste offline com arquivos sintéticos no MESMO formato)
# ---------------------------------------------------------------------------
def preparar_amostra(p: dict) -> dict:
    p["bronze"].mkdir(parents=True, exist_ok=True)
    manifesto = {"fontes": {}, "ibge": {}, "falhas": [], "modo": "amostra_sintetica"}
    for nome, fonte in config.FONTES_ANATEL.items():
        origem = p["fixtures"] / f"anatel_{nome}.zip"
        destino = p["bronze"] / origem.name
        shutil.copy(origem, destino)
        manifesto["fontes"][nome] = {
            "titulo": fonte["titulo"] + " [AMOSTRA SINTÉTICA]", "pagina": fonte["pagina"],
            "url": origem.as_uri(), "arquivo": destino.name,
            "arquivo_interno": fonte["arquivo_interno"],
            "last_modified": "amostra", "bytes": destino.stat().st_size,
            "sha256": sha256(destino), "baixado_em": _agora(), "verificado_em": _agora(),
            "novo": True,
        }
    for nome in ("populacao", "estados"):
        shutil.copy(p["fixtures"] / f"ibge_{nome}.json", p["bronze"] / f"ibge_{nome}.json")
        manifesto["ibge"][nome] = {"url": "snapshot real do IBGE (12/11/2026)", "baixado_em": _agora()}
    salvar_manifesto(p["manifesto"], manifesto)
    return manifesto


def adotar_arquivos_locais(pasta_local: Path, p: dict, manifesto: dict) -> None:
    """Se você já baixou os .zip pelo navegador, o pipeline usa esses arquivos
    em vez de baixar de novo. O manifesto registra que vieram de cópia local."""
    p["bronze"].mkdir(parents=True, exist_ok=True)
    for nome, fonte in config.FONTES_ANATEL.items():
        nome_remoto = fonte["url"].rsplit("/", 1)[-1]
        origem = pasta_local.expanduser() / nome_remoto
        if not origem.exists():
            log.warning("%s não encontrado em %s", nome_remoto, pasta_local)
            continue
        destino = p["bronze"] / f"anatel_{nome}.zip"
        shutil.copy2(origem, destino)
        try:
            last_modified = _requisitar("HEAD", fonte["url"]).headers.get("Last-Modified")
        except Exception:  # noqa: BLE001 - sem internet: usa a data do arquivo local
            last_modified = datetime.fromtimestamp(origem.stat().st_mtime).strftime(
                "%a, %d %b %Y %H:%M:%S GMT")
        manifesto["fontes"][nome] = {
            "titulo": fonte["titulo"], "pagina": fonte["pagina"], "url": fonte["url"],
            "arquivo": destino.name, "arquivo_interno": fonte["arquivo_interno"],
            "last_modified": last_modified, "bytes": destino.stat().st_size,
            "sha256": sha256(destino), "baixado_em": _agora(), "verificado_em": _agora(),
            "origem": f"cópia local de {origem}", "novo": True,
        }
        log.info("%s: usando arquivo local %s (%.0f MB)", nome, origem, destino.stat().st_size / 1e6)


def executar(modo_amostra: bool = False, pasta_local: Path | None = None) -> dict:
    p = config.pastas(modo_amostra)
    if modo_amostra:
        return preparar_amostra(p)
    manifesto = carregar_manifesto(p["manifesto"])
    if pasta_local:
        adotar_arquivos_locais(pasta_local, p, manifesto)
    extrair_anatel(p, manifesto)
    extrair_ibge(p, manifesto)
    salvar_manifesto(p["manifesto"], manifesto)
    return manifesto
