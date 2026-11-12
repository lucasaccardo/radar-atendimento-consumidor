"""
Configuração central do pipeline.

- Centraliza período, URLs e limites de qualidade em UM lugar,
  sem mexer na lógica. Isso reduz erro e facilita a passagem de conhecimento.
"""
from pathlib import Path

# ---------------------------------------------------------------------------
# Pastas
# ---------------------------------------------------------------------------
RAIZ = Path(__file__).resolve().parent.parent


def pastas(modo_amostra: bool = False) -> dict:
    """Devolve todas as pastas usadas. No modo amostra tudo vai para data_amostra/,
    para que dado sintético NUNCA se misture com dado real."""
    dados = RAIZ / ("data_amostra" if modo_amostra else "data")
    return {
        "dados": dados,
        "bronze": dados / "bronze",
        "temporario": dados / "tmp",
        "manifesto": dados / "bronze" / "manifesto.json",
        "banco": dados / "radar.duckdb",
        "logs": dados / "logs",
        # Parquet do Power BI fica versionado no repositório (quem clonar consegue abrir o .pbix)
        "ouro_bi": dados / "ouro" / "powerbi" if modo_amostra else RAIZ / "powerbi" / "dados",
        "ouro_csv": dados / "ouro" / "csv",
        # A lâmina lê daqui. A amostra vai para uma pasta separada.
        "lamina_dados": RAIZ / "docs" / ("data_amostra" if modo_amostra else "data"),
        "sql": RAIZ / "sql",
        "fixtures": RAIZ / "tests" / "fixtures",
        # Última resposta válida do IBGE, versionada no Git (ver D18)
        "referencia": RAIZ / "dados_referencia",
    }


# ---------------------------------------------------------------------------
# Período analisado
# ---------------------------------------------------------------------------
# 2024-01 em diante permite duas comparações ano contra ano:
# 2025 x 2024 (ano cheio) e 2026 x 2025 (mesmo período).
COMPETENCIA_INICIO = "2024-01"

# ---------------------------------------------------------------------------
# Fontes (todas públicas, sem cadastro)
# ---------------------------------------------------------------------------
FONTES_ANATEL = {
    # PRINCIPAL: 1 linha por reclamação, com resposta, reabertura, resolução e nota
    "atendimento": {
        "titulo": "Anatel - Consumidor: Indicadores de Qualidade de Atendimento",
        "pagina": "https://dados.gov.br/dados/conjuntos-dados/consumidor---indicadores-de-qualidade-de-atendimento",
        "url": "https://www.anatel.gov.br/dadosabertos/paineis_de_dados/consumidor/indicadores_qualidade_atendimento.zip",
        "arquivo_interno": "AnatelConsumidorReclamacoesCSV.csv",
        "obrigatoria": True,
    },
    # COMPLEMENTAR: volume de solicitações por canal de entrada (agregado por mês)
    "registradas": {
        "titulo": "Anatel - Consumidor: Reclamações Registradas",
        "pagina": "https://dados.gov.br/dados/conjuntos-dados/solicitacoesregistradasnaanatel",
        "url": "https://www.anatel.gov.br/dadosabertos/paineis_de_dados/consumidor/consumidor_reclamacoes.zip",
        # o zip traz 2 arquivos: reclamacoes.csv é o histórico (jan/2015 a mai/2020);
        # os dados recentes ficam em reclamacoes_contexto.csv (descoberto inspecionando a fonte)
        "arquivo_interno": "reclamacoes_contexto.csv",
        "obrigatoria": False,
    },
}
# IBGE - população residente estimada por UF (tabela 6579, variável 9324)
IBGE_POPULACAO = (
    "https://servicodados.ibge.gov.br/api/v3/agregados/6579/periodos/{anos}"
    "/variaveis/9324?localidades=N3[all]"
)
# IBGE - lista de UFs com sigla e região
IBGE_ESTADOS = "https://servicodados.ibge.gov.br/api/v1/localidades/estados"

# (conexão, leitura) em segundos. Se o servidor nem atende em 30 s, desiste logo;
# a leitura fica longa porque os arquivos da Anatel têm centenas de MB.
HTTP_TIMEOUT = (30, 300)
HTTP_TENTATIVAS = 4         # novas tentativas com espera crescente
HTTP_HEADERS = {"User-Agent": "radar-atendimento-consumidor/1.0 (projeto de portfolio)"}

# ---------------------------------------------------------------------------
# Contrato de dados: colunas que PRECISAM existir. Se a Anatel mudar o
# layout, o pipeline para com erro claro em vez de publicar número errado.
# (nomes já normalizados: minúsculo, sem acento, com _)
# ---------------------------------------------------------------------------
COLUNAS_OBRIGATORIAS = {
    "atendimento": ["tipo_atendimento", "servico", "assunto", "uf", "prestadora",
                    "data_abertura", "data_resposta", "respondida", "reaberta",
                    "tempo_resposta", "situacao", "resolvida", "nota_consumidor",
                    "data_finalizacao"],
    "registradas": ["solicitacoes", "anomes", "uf", "canal_entrada", "condicao",
                    "tipo_atendimento", "servico", "marca"],
}

# Nome normalizado -> nome usado no banco
APELIDOS_COLUNAS = {
    "prazoresposta": "tempo_resposta",      # na prática é "dias até a resposta"
    "entidadeandamento": "entidade_andamento",
    "id_situacao": "id_situacao",
    "tipoatendimento": "tipo_atendimento",
    "canalentrada": "canal_entrada",
    "dataextracao": "data_extracao",
}

# ---------------------------------------------------------------------------
# Regras de negócio
# ---------------------------------------------------------------------------
SLA_RESPOSTA_DIAS = 10           # prazo regulatório: 10 dias corridos (gov.br/anatel)
# Maturação: reclamação aberta no fim do mês ainda pode estar em andamento
# quando a Anatel gera o arquivo. Excluímos o mês da extração e mais N meses
# anteriores para não comparar mês "incompleto" com mês "fechado".
MESES_MATURACAO = 1
TEMPO_RESPOSTA_MAX_DIAS = 365    # acima disso tratamos como erro de registro
MIN_PARTICIPACAO_OPERADORA = 0.005  # operadora com < 0,5% do volume vira "Outras"

# ---------------------------------------------------------------------------
# Limites do relatório de qualidade: (alerta, erro) em % das linhas
# ---------------------------------------------------------------------------
LIMITES_QUALIDADE = {
    "diferenca_reconciliacao": (0.1, 1.0),
    "duplicados": (0.5, 5.0),
    "datas_invalidas": (0.5, 5.0),
    "uf_invalida": (1.0, 10.0),
    "tempo_resposta_invalido": (1.0, 10.0),
    "nota_invalida": (0.1, 5.0),
    "respondida_sem_data": (1.0, 10.0),
    "resposta_antes_abertura": (0.1, 2.0),
    # nunca bloqueia: são bases com regras de contagem diferentes; serve de alerta
    "divergencia_entre_fontes": (10.0, 1000.0),
}
LIMITE_VARIACAO_VOLUME = 0.40    # mês com volume 40% fora da mediana gera alerta
