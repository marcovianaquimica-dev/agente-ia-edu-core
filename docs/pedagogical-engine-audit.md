# Auditoria antes do Pedagogical Intelligence Engine V1

Feita em 2026-10-06, a partir do código e do banco de desenvolvimento — não
de memória. O objetivo era responder uma pergunta antes de escrever qualquer
linha: **quanto da arquitetura alvo já existe?**

A resposta é: mais do que parece, e em lugares que não se anunciam como
"motor pedagógico".

## O que já existe, e que não deve ser duplicado

| conceito da arquitetura alvo | o que já existe | onde |
|---|---|---|
| grafo de pré-requisitos | `catalog_node_prerequisites`, genérico, com `ck_..._not_self` e unicidade | tabela |
| micro-habilidade por questão | `pedagogical_classifications.subcontent` | tabela |
| domínio **por micro-habilidade** | `domain_content_mastery.subcontent_code` | tabela |
| evidência | `learning_history` + `activity_result_items` | tabelas |
| diagnóstico por habilidade | `diagnostico_por_habilidade.py` | serviço |
| política de cortes | `PerformanceThresholdPolicy` | serviço |
| trajetória (recuperação) | `trajetoria_do_aluno.py` | serviço |
| máquina de decisão | `assessor_pedagogico._acao` | serviço |
| estratégias de explicação | `explicacao_do_erro.ESTRATEGIAS` (6, em escada) | serviço |
| prática guiada com níveis de ajuda | `itens_guiados.py` (4 níveis) + `pratica_guiada.py` | serviço |
| IA substituível | `TextGenerationProvider`, `ProviderRouter`, prompts versionados | pacote |
| fail-closed de item gerado | `diagnostic_bank.conferir_quimica` (verificação determinística) | serviço |

**A descoberta que define o desenho:** `domain_content_mastery` **já tem**
`subcontent_code`. O modelo do aluno por micro-habilidade é representável sem
tabela nova. Isso elimina a migration que esta arquitetura pareceria exigir.

## Os gaps reais

1. **O grafo de micro-habilidades não existe.** `catalog_node_prerequisites`
   liga CONTEÚDOS (`STOICHIOMETRY ← BALANCING`), não micro-habilidades. Os
   subconteúdos de Estequiometria vivem como strings livres nas
   classificações (`RELACAO_MASSA_MOL`, `PROPORCAO_ESTEQUIOMETRICA`…), sem
   relação entre si.
2. **A decisão é por CONTEÚDO.** `decidir_intervencao` identifica a pior
   micro-habilidade (`habilidade_que_trava`) mas intervém no conteúdo
   inteiro: o material é do conteúdo, a prática é do conteúdo.
3. **A sondagem não sonda habilidade.** Depois do bloco anterior ela começa
   pelas questões marcadas como fáceis — melhor que sortear por número, mas
   ainda não é "qual micro-habilidade estou medindo agora".
4. **Não há níveis de apoio formalizados.** A guiada tem 4 níveis de ajuda
   DENTRO de um item; não há L3→L2→L1→L0 atravessando o ciclo.
5. **Evidência não distingue assistido de autônomo.** `guided_practice_items`
   não escreve mastery (correto), mas a prática comum escreve sem registrar
   com quanto apoio foi feita.

## Decisões tomadas, e por quê

**O grafo é configuração versionada em código, não tabela.** O bloco pede
V1 e pede evitar migration. Um grafo de 9 micro-habilidades que muda por
decisão pedagógica — e não por uso — é artefato de código, como
`itens_guiados.py` e `conteudo_balanceamento.py` já são. Vira tabela quando
alguém precisar editá-lo sem deploy, e não antes.

**A micro-habilidade reusa `subcontent_code`.** O código canônico é o mesmo
string que as classificações já usam; o rótulo em português fica no grafo. Com
isso `domain_content_mastery` guarda o modelo do aluno por habilidade sem
nenhuma coluna nova.

**A sondagem tem itens curados.** O bloco é explícito: a IA não pode ser
requisito para o diagnóstico funcionar. Os cinco itens do piloto entram como
dados do domínio, com gabarito verificável.

**O legado continua.** Conteúdo sem contrato pedagógico V2 segue pelo caminho
atual, sem mudança de comportamento. O motor novo só assume onde há grafo.

## Risco de migration

Zero, pelo caminho escolhido. As quatro tabelas que o motor lê
(`domain_content_mastery`, `pedagogical_classifications`, `learning_history`,
`activity_result_items`) já têm as colunas necessárias, e o grafo não é
persistido.

## Estratégia incremental

```
1. grafo genérico            + grafo sintético não-Química (prova de independência)
2. grafo de Estequiometria   os 9 nós de §5
3. sondagem por habilidade   5 itens curados
4. mapa do aluno             por micro-habilidade, sobre o que já se mede
5. primeiro gargalo          travessia de pré-requisitos
6. estratégias               escolha com histórico, nunca repetindo
7. apoio e retirada          L3→L0
8. evidência                 assistido ≠ autônomo
9. ligação no readiness      com fallback legado
```
