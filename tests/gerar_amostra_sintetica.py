"""
Gera arquivos FALSOS no mesmo formato (cabeçalho, separador, datas, zip)
dos arquivos da Anatel, para testar o pipeline inteiro sem internet.

Uso:  python tests/gerar_amostra_sintetica.py
"""
import io
import random
import zipfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

random.seed(7)
PASTA = Path(__file__).resolve().parent / "fixtures"

CAB_ATENDIMENTO = ("TIPO_ATENDIMENTO;SERVICO;ASSUNTO;UF;PRESTADORA;DATA_ABERTURA;DATA_RESPOSTA;"
                   "RESPONDIDA;REABERTA;PrazoResposta;ID_SITUACAO;SITUACAO;RESOLVIDA;NOTA_CONSUMIDOR;"
                   "DATA_FINALIZACAO;DETALHAMENTO;ENTIDADEANDAMENTO")
CAB_REGISTRADAS = ("DataExtracao;SOLICITAÇÕES;Ano;Mês;AnoMês;UF;Cidade;CO_MUNICIPIO;CanalEntrada;"
                   "Condição;TipoAtendimento;Serviço;Marca;Assunto;Problema")

# serviço: (peso, código usado na base de registros)
SERVICOS = {"Banda Larga Fixa": (32, "SCM"), "Celular Pós-Pago": (30, "SMP_Pós"),
            "Celular Pré-Pago": (15, "SMP_Pré"), "TV por Assinatura": (13, "SeAC"),
            "Telefone Fixo": (10, "STFC")}
# operadora: (peso, tempo médio de resposta, chance de reabrir, chance de resolver, tendência/ano no tempo)
OPERADORAS = {"OPERADORA A": (30, 6.0, 0.10, 0.62, -0.3), "OPERADORA B": (27, 7.5, 0.14, 0.55, -1.0),
              "OPERADORA C": (22, 5.0, 0.09, 0.66, +0.6), "OPERADORA D": (12, 8.0, 0.17, 0.48, 0.0),
              "OPERADORA E": (5, 6.5, 0.11, 0.58, 0.0), "OPERADORA F": (2.5, 9.0, 0.20, 0.45, 0.0),
              "PROVEDOR LOCAL X": (0.6, 7.0, 0.12, 0.60, 0.0), "PROVEDOR LOCAL Y": (0.3, 7.0, 0.12, 0.60, 0.0)}
ASSUNTOS = [("Cobrança", 30), ("Qualidade, Funcionamento e Reparo", 24), ("Cancelamento", 11),
            ("Atendimento", 9), ("Oferta, Contratação e Plano de Serviço", 8),
            ("Instalação ou Ativação", 6), ("Bloqueio, desbloqueio ou Suspensão", 5),
            ("Portabilidade", 3), ("Crédito Pré-pago", 2), ("Ressarcimento", 2)]
UFS = {"SP": 46, "MG": 21, "RJ": 17, "BA": 15, "PR": 12, "RS": 11, "PE": 10, "CE": 9, "PA": 9,
       "SC": 8, "GO": 7, "MA": 7, "AM": 4, "ES": 4, "PB": 4, "DF": 4, "MT": 4, "RN": 3, "AL": 3,
       "PI": 3, "MS": 3, "SE": 2, "RO": 2, "TO": 2, "AC": 1, "AP": 1, "RR": 1}
CANAIS = [("Usuário web", 42), ("Call center", 33), ("Aplicativo", 21), ("Fale Conosco", 4)]


def escolher(d: dict | list):
    if isinstance(d, dict):
        return random.choices(list(d), weights=[v[0] if isinstance(v, tuple) else v for v in d.values()])[0]
    return random.choices([k for k, _ in d], weights=[w for _, w in d])[0]


def linha_atendimento(dia: date) -> tuple[list[str], dict]:
    srv = escolher(SERVICOS)
    op = escolher(OPERADORAS)
    _, tempo_medio, p_reabrir, p_resolver, tendencia = OPERADORAS[op]
    tempo_medio += tendencia * (dia.year - 2024 + dia.month / 12)
    tipo = random.choices(["Reclamação", "Pedido de Informação", "Denúncia"], weights=[96, 3, 1])[0]
    tempo = max(0, int(random.gauss(tempo_medio, 3)))
    respondida = random.random() < 0.985
    reaberta = random.random() < p_reabrir
    avaliou = random.random() < 0.42
    resolvida = (random.random() < p_resolver) if avaliou else None
    nota = (random.choices([1, 2, 3, 4, 5], weights=[4, 5, 12, 25, 54] if resolvida else [58, 18, 13, 7, 4])[0]
            if avaliou else None)
    resposta = dia + timedelta(days=tempo)
    fim = resposta + timedelta(days=random.randint(0, 11))
    uf = escolher(UFS)
    campos = [tipo, srv, escolher(ASSUNTOS), uf, op, dia.isoformat(),
              resposta.isoformat() if respondida else "Nulo", "S" if respondida else "N",
              "S" if reaberta else "N", str(tempo) if respondida else "Nulo",
              "7" if avaliou else "6", "Finalizada com avaliação" if avaliou else "Finalizada sem avaliação",
              ("S" if resolvida else "N") if avaliou else "Nulo", str(nota) if nota else "Nulo",
              fim.isoformat(), "Detalhamento genérico", "ANATEL"]
    # --- sujeira proposital ---
    r = random.random()
    if r < 0.002:
        campos[5] = "2025-02-31"           # data inválida
    elif r < 0.005:
        campos[3] = random.choice(["XX", ""])  # UF inválida
    elif r < 0.007 and respondida:
        campos[9] = random.choice(["-2", "999"])  # tempo impossível
    elif r < 0.0075 and avaliou:
        campos[13] = "9"                    # nota fora da escala
    elif r < 0.009:
        campos[4] = "  " + op.lower() + " "   # caixa e espaços diferentes
    info = {"tipo": tipo, "srv": SERVICOS[srv][1], "op": op, "uf": uf, "dia": dia, "reaberta": reaberta}
    return campos, info


def zipar(destino: Path, arquivos: dict[str, str]) -> None:
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        for nome, texto in arquivos.items():
            z.writestr(nome, texto.encode("utf-8"))


def main() -> None:
    PASTA.mkdir(parents=True, exist_ok=True)
    linhas, contagem = [], Counter()
    dia = date(2023, 6, 1)
    while dia <= date(2026, 11, 10):
        fator = 1.15 if dia.month in (1, 11, 12) else 1.0   # pico de fim/início de ano
        for _ in range(int(random.gauss(80 * fator, 8))):
            campos, info = linha_atendimento(dia)
            linhas.append(";".join(campos))
            canal = escolher(CANAIS)
            chave = (info["dia"].strftime("%Y-%m"), info["uf"], canal, info["tipo"], info["srv"], info["op"])
            # igual à base real: cada linha é OU um registro novo OU uma reabertura (ver D17)
            contagem[(*chave, "Reaberta" if info["reaberta"] else "Nova")] += 1
        dia += timedelta(days=1)

    atendimento = "\n".join([CAB_ATENDIMENTO, *linhas]) + "\n"
    zipar(PASTA / "anatel_atendimento.zip", {"AnatelConsumidorReclamacoesCSV.csv": atendimento})

    reg = [CAB_REGISTRADAS]
    for (anomes, uf, canal, tipo, srv, op, cond), n in sorted(contagem.items()):
        n = max(1, round(n * random.uniform(0.97, 1.03)))  # bases diferentes nunca batem 100%
        ano, mes = anomes.split("-")
        reg.append(";".join(["2026-11-10", str(n), ano, str(int(mes)), anomes, uf, f"Capital {uf}", "0",
                             canal, cond, tipo, srv, op, "Assunto", "Problema"]))
    # igual ao zip real: reclamacoes.csv é o histórico antigo; os dados recentes ficam no "contexto",
    # que tem uma coluna a mais (Linha)
    contexto = "\n".join([reg[0] + ";Linha"] + [f"{l};{i}" for i, l in enumerate(reg[1:], 1)]) + "\n"
    zipar(PASTA / "anatel_registradas.zip", {"reclamacoes.csv": CAB_REGISTRADAS + "\n",
                                             "reclamacoes_contexto.csv": contexto})
    print(f"{len(linhas):,} reclamações sintéticas e {len(reg) - 1:,} linhas agregadas em {PASTA}")


if __name__ == "__main__":
    main()
