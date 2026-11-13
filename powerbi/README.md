# Power BI

| Arquivo | O que é |
|---|---|
| `radar_atendimento.pbix` | Relatório pronto para abrir, com os dados embutidos |
| `radar_atendimento.pbip` + pastas `.Report` e `.SemanticModel` | O mesmo relatório como código-fonte (PBIR + TMDL): cada visual e cada medida é um arquivo de texto, versionado no Git |
| `medidas.tmdl` | As 43 medidas DAX como código (aplicadas pela exibição TMDL do Power BI Desktop) |
| `tema_radar.json` | Tema do relatório, com as mesmas cores da lâmina HTML |
| `dados/*.parquet` | Modelo estrela gerado pelo pipeline (camada ouro) |

Os Parquet são regerados a cada execução do pipeline (`python pipeline/executar_pipeline.py`) e pela atualização automática mensal. Para atualizar o `.pbix`, abra-o e clique em **Atualizar**.

O modelo lê os Parquet de `powerbi/dados` por caminho absoluto. Em outro computador, ajuste a origem em **Transformar dados → Configurações da fonte de dados** antes de atualizar.

## Página Radar

Mesma ordem de leitura da lâmina HTML: o que mudou (7 KPIs com variação colorida) → quando (evolução mensal) → por onde (canais) → onde (por 100 mil hab. por UF) → quem (operadoras: tempo x reabertura) → por quê (volume e reabertura por assunto) → detalhe (tabela de medidas). A página **Conferência** fica oculta no modo de leitura e compara os números com a lâmina.

## Modelo

```mermaid
erDiagram
    dim_calendario ||--o{ fato_atendimento_mensal : data_mes
    dim_uf ||--o{ fato_atendimento_mensal : uf
    dim_servico ||--o{ fato_atendimento_mensal : id_servico
    dim_operadora ||--o{ fato_atendimento_mensal : id_operadora
    dim_assunto ||--o{ fato_atendimento_mensal : id_assunto
    dim_calendario ||--o{ fato_canal_mensal : data_mes
    dim_uf ||--o{ fato_canal_mensal : uf
    dim_servico ||--o{ fato_canal_mensal : servico
```

`dim_populacao` (chave UF + ano) não tem relacionamento: é consultada pela medida `População` com `LOOKUPVALUE`.

`fato_canal_mensal` vem de outra base da Anatel, já agrupada por grupo de operadora. Como esse grupo não é único na `dim_operadora`, não existe relacionamento físico: a medida `Solicitações por canal` aplica o filtro de operadora com `TREATAS` (relacionamento virtual).
