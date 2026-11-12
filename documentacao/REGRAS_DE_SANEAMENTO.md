# Regras de saneamento

Cada regra tem um código. O mesmo código aparece como comentário no SQL (`sql/01_prata.sql` e `sql/02_dimensoes.sql`) e no relatório de qualidade da lâmina. Assim, dá para ir da lâmina até a linha de código que gerou o número.

**Princípio geral:** uma regra de saneamento **não apaga** a linha. Ela marca o problema (`flag_...`), troca o valor inválido por "não informado" ou o tira do cálculo afetado, e o relatório de qualidade conta quantas linhas foram afetadas. Apagar esconde o problema; marcar o deixa visível.

| Código | Regra | Por quê | Onde aparece na qualidade |
|---|---|---|---|
| **R1** | Texto: remove espaços sobrando; vazio e o texto `"Nulo"` viram NULL | A Anatel escreve a palavra "Nulo" em vez de deixar o campo vazio. Sem isso, "Nulo" viraria uma categoria | — |
| **R2** | Datas: aceita `aaaa-mm-dd` (padrão Anatel) e `dd/mm/aaaa`; o resto vira NULL | Formatos já mudaram entre anos em bases do governo. Data impossível (ex.: 31/02) não pode quebrar o processo | "Data de abertura inválida" |
| **R3** | UF só vale se existir na lista oficial do IBGE; senão vira `NI` | Evita "estado XX" no mapa e garante o cruzamento com a população | "UF ausente ou inválida" |
| **R4** | Tempo de resposta fora de 0 a 365 dias não entra na média | Valor negativo ou de anos é erro de registro e distorce a média | "Tempo de resposta inválido" |
| **R5** | "No prazo" = respondida em até **10 dias corridos** | É o prazo que a Anatel dá às operadoras (fonte: gov.br/anatel). Fica no `config.py` (`SLA_RESPOSTA_DIAS`) | — |
| **R6** | Nota só vale de 1 a 5 | A escala oficial é 1 a 5; fora disso não é nota | "Nota fora de 1 a 5" |
| **R7** | Base de análise = tipo "Reclamação", a partir de 2024-01, só meses **maduros** | Pedidos de informação e denúncias têm outra natureza. Mês recente ainda tem reclamações em andamento (ver "maturação" em DECISOES.md) | "Fora da base de análise" (INFO, com o motivo) |
| **R8** | "De-para" de serviço entre as duas bases da Anatel (`SMP_Pós` → `Celular Pós-Pago` etc.) | As duas bases usam códigos diferentes para o mesmo serviço. Sem isso não dá para cruzar | "Volume: base de atendimento x base de registros" |
| **R9** | Operadora com menos de 0,5% do volume vira `OUTRAS` | Cauda longa com centenas de provedores pequenos poluiria a leitura e geraria ranking com amostra pequena | — |
| **R11** | **Sucessão de marca:** OI e NIO são analisadas juntas como `OI / NIO` | A Oi Fibra foi vendida à V.tal em fev/2025 e virou Nio. Sem a regra, a OI "cai 94%" e a NIO "sobe 312%" sem mudança real no atendimento. A tabela `ref_sucessao_marca` guarda o motivo de cada sucessão | — |
| **R10** | Se o IBGE ainda não publicou a população de um ano, usa a estimativa mais recente anterior | A população varia menos de 1% ao ano; deixar sem população quebraria a taxa por habitante | "UF/ano sem população do IBGE" |

## O que **não** é feito, de propósito

| Não fazemos | Motivo |
|---|---|
| Remover linhas duplicadas | A base não tem ID nem dados pessoais. Duas reclamações diferentes (mesmo dia, operadora, assunto e UF) podem ser idênticas linha a linha. Remover apagaria reclamações legítimas. Medimos e mostramos ("Linhas idênticas (mantidas)") |
| Recalcular o tempo de resposta pelas datas | Usamos o campo oficial da Anatel. Quando ele diverge da diferença entre as datas, isso é contado como informação ("Tempo de resposta ≠ diferença entre as datas") |
| Considerar "não avaliada" como "resolvida" | A Anatel classifica automaticamente como resolvida quem não avalia em 10 dias. Isso inflaria a resolutividade. Usamos só as avaliadas |
