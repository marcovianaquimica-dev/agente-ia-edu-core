# Pedagogical Intelligence Engine — V1

## 1. O problema

O Assessor sabia conteúdos, questões, tentativas, domínio, intervenções e
trajetória. **Não sabia o que compõe um conteúdo.** A decisão acontecia então
no nível errado: `decidir_intervencao` já identificava a pior micro-habilidade
e intervinha no conteúdo inteiro — material do conteúdo, prática do conteúdo.

Quem só tropeça em converter massa em mol recebia uma aula sobre
estequiometria.

E a escolha da micro-habilidade era pelo **menor acerto**. Para um aluno que
vai mal na leitura da fórmula (0/3) e pior ainda no problema completo (0/5),
isso aponta o problema completo — e o sistema ensina a cadeia inteira a quem
não lê o índice do NH₃.

## 2. Arquitetura

```
GRAFO PEDAGÓGICO         o que compõe o conteúdo, e o que vem antes
        ↓
SONDAGEM                 qual habilidade medir, e com que item
        ↓
MODELO DO ALUNO          {habilidade: estado}, sobre o que já se mede
        ↓
MOTOR PEDAGÓGICO         sobre o quê intervir, e qual intervenção
        ↓
ESTRATÉGIA E APOIO       qual abordagem, com quanto apoio
        ↓
EVIDÊNCIA                só L0 conta
```

Cada camada é um módulo, sem estado, sem banco e sem IA. Isso não é purismo:
é o que torna os sete cenários de aceitação testáveis sem subir nada.

## 3. Pedagogical Knowledge Graph

Genérico (`grafo_pedagogico.py`) e **sem assunto nenhum** — há teste lendo o
próprio arquivo e falhando se alguém escrever o nome de uma disciplina,
inclusive na prosa. Metade dos testes do grafo usa um grafo sintético de
equação do primeiro grau.

O mapa do piloto (`grafo_estequiometria.py`):

```
Leitura de fórmulas ──────────┐
                              ├──> Massa molar ──┐
O que é um mol ───────────────────────────────── ├──> Massa ↔ mol ──┐
                                                 │                  ├──> Massa a massa ──┐
Leitura dos coeficientes ──> Proporção ──┬───────┘                  │                    ├──> Problema completo
                                         └──> Mol a mol ────────────────────────────────┘
```

**Quatro dos nove códigos já existiam** em
`pedagogical_classifications.subcontent`, com 20 questões entre eles
(`PROPORCAO_ESTEQUIOMETRICA`, `RELACAO_MASSA_MASSA`, `RELACAO_MASSA_MOL`,
`RELACAO_MOL_MOL`). Entraram com o nome que tinham: renomeá-los desligaria as
20 do mapa.

## 4. Student Knowledge Model

`diagnostico_por_habilidade` já produzia `{habilidade: banda}`. Faltava a
tradução, e ela é conservadora numa direção específica: **amostra insuficiente
não vira lacuna**. Chamar de "precisa de apoio" uma habilidade medida por uma
resposta só mandaria o aluno estudar o que talvez já saiba — e a chance de
acertar no chute é 1 em 5. Faixa intermediária e faixa desconhecida também não
viram.

## 5. Decision Engine

A ordem das perguntas é a própria política:

| pergunta | resposta |
|---|---|
| nada medido? | sondar |
| sem lacuna? | avançar |
| qual lacuna? | **a mais básica da cadeia**, não a pior nota |
| e então? | ensinar → apoio → apoio saindo → sozinho → verificar |
| acabaram as estratégias? | escalar |

## 6. Teaching Strategy Engine

Seis estratégias em escada (DECOMPOSITION, WORKED_EXAMPLE, GUIDED_QUESTIONS,
VISUAL_REPRESENTATION, ANALOGY, PREREQUISITE_REVIEW). Quando acabam,
`proxima_estrategia` devolve `None` — não é erro, é o sinal de escalada.
Devolver uma repetida seria o loop que a camada existe para impedir.

`PREREQUISITE_REVIEW` só entra se a habilidade tiver pré-requisito: mandar
revisar o pré-requisito de quem não tem nenhum é mandar o aluno para lugar
nenhum.

## 7. Scaffolding e fading

```
L3 exemplo resolvido → L2 perguntas guiadas → L1 dica → L0 sozinho
```

Acertar retira um degrau; errar devolve um. Nível desconhecido volta ao mais
apoiado — na dúvida, apoiar mais.

## 8. Evidence Engine

**Só L0 produz evidência.** É um `==`, não uma convenção, e há teste varrendo
os quatro níveis. É a linha que impede o sistema de dar por aprendido quem foi
conduzido até a resposta. Nível desconhecido não produz — fail closed.

## 9. Onde a IA decide, e onde não

| | decide |
|---|---|
| **qual micro-habilidade** | Núcleo |
| **qual estratégia** | Núcleo |
| **quanto apoio** | Núcleo |
| **se houve aprendizagem** | Núcleo (evidência + política) |
| **a linguagem de uma explicação** | IA, por `explicacao_do_erro` |
| **variantes de item** | IA, numa fase futura, com validação fail-closed |

**IA NÃO É AUTORIDADE DE MASTERY.** Verificado por ausência de caminho: as
oito camadas do motor não importam provedor, não citam fornecedor e não
alcançam o banco. A sondagem é curada e os gabaritos são refeitos por conta —
um item diagnóstico errado não falha ruidosamente, ele mede errado em silêncio.

## 10. Compatibilidade

```
conteúdo COM grafo  →  primeiro gargalo
conteúdo SEM grafo  →  exatamente o comportamento de antes
```

36 dos 37 conteúdos do catálogo não têm contrato V2 e nenhum muda hoje. Se o
grafo não reconhecer nenhuma das fracas — subconteúdos antigos —, a escolha
volta a ser a de sempre.

## 11. Migrations

Nenhuma. A auditoria achou o que decide o desenho: `domain_content_mastery`
já tem `subcontent_code`, e `catalog_node_prerequisites` já existe. O grafo
não é persistido — um grafo que muda por decisão pedagógica, e não por uso, é
artefato de código.

## 12. O que NÃO foi implementado — e por quê

Esta é a parte que mais importa para quem continuar.

| | estado | motivo |
|---|---|---|
| sondagem servida ao aluno | **não** | os 5 itens existem como dados curados e conferidos, mas não estão publicados no Question Bank como `QuestionVersion`. Sem isso o microdiagnóstico continua montando o lote pelo banco |
| motor dono do passo | **não** | o motor decide o ALVO; o tipo de passo continua com `decidir_intervencao`, que é quem sabe se há material e item guiado. Trocar isso exige material por micro-habilidade, que não existe — e inventá-lo seria inventar conteúdo pedagógico |
| apoio L3→L0 no fluxo vivo | **não** | a escada está implementada e testada; nada no readiness ainda a lê |
| histórico de estratégias | **derivado** | não é persistido; `historico_por_habilidade` deriva do ciclo. Garante não-repetição dentro de um percurso, mas duas sessões podem repetir uma abordagem |
| geração de variantes por IA | **não** | §16 do bloco; a infraestrutura de validação fail-closed existe em `diagnostic_bank`, a ligação não |
| spacing | **não** | documentado como extensão |

## 13. Dívidas

**P0** — a sondagem por micro-habilidade não chega ao aluno. É o que falta
para o cenário de aceitação ser demonstrável no produto.

**P1** — persistir o histórico de estratégias por habilidade; material
pedagógico por micro-habilidade (hoje o material é do conteúdo).

**P2** — spacing; geração de variantes; grafo editável sem deploy.

## 14. Próximo passo recomendado

Publicar os cinco itens de sondagem no Question Bank, classificados com o
`subcontent` da micro-habilidade que medem. É seeding de dados, não código
novo, e destrava tudo o que está acima: o microdiagnóstico passa a medir por
habilidade com o motor de seleção que já existe, a correção grava evidência
por `subcontent`, e o alvo do assessor passa a ser calculado sobre medida
real em vez de herdada.

## 15. Dados de QA criados neste bloco

Para a verificação no navegador foi criado **um** aluno, separado do que fica
reservado ao teste manual:

| | |
|---|---|
| nome | Aluno Teste Motor |
| identificador | `aluno_teste_motor` |
| código | `PILOTO-0003` |
| escola / turma | Escola ABC · Turma 3ª A (Piloto) |
| marca | `user_school_links.metadata ->> 'qa_purpose' = 'ENGINE_BROWSER_CHECK'` |

Ele acumulou 29 tentativas e 29 resultados ao tentar levar a rota até
Estequiometria. **Isso é histórico de teste, não de aluno** — e precisa sair
antes da implantação real.

Como encontrá-lo, sem adivinhar: pela marca acima, pelo `external_id` nas três
tabelas de identidade (`persons`, `students`, `users`), ou pelo
`student_code`. A ordem de remoção segue as FKs — o histórico primeiro, pelos
mesmos filtros de `scripts/reset_piloto_zero.py` trocando o identificador,
depois `user_school_links` → `student_enrollments` → `students` → `users` →
`persons`.

`aluno_teste_jornada` (`PILOTO-0002`, `qa_purpose = E2E_MANUAL_JOURNEY`)
continua sem histórico nenhum e não foi tocado em momento algum deste bloco.
