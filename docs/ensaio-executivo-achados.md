# Ensaio executivo — achados

Auditoria crítica feita tela a tela, no navegador, contra o banco de
desenvolvimento, na perspectiva de quem assiste à apresentação: diretor,
coordenador, mantenedor.

O objetivo não foi provar que o sistema está bom. Foi encontrar o que
diminuiria o impacto ou a confiança durante a apresentação.

Companheiro de `demo-nucleo-edu-360.md`, que é o roteiro. Este é o diagnóstico.

---

## Corrigido durante o ensaio

Cinco achados eram pequenos e seguros o bastante para corrigir na hora. Todos
têm teste e foram verificados no navegador.

| ID | Tela | Problema | Commit |
|---|---|---|---|
| C1 | Redação (aluno) | Tema jogado na lixeira pelo professor continuava aparecendo para o aluno | `a41d761` |
| C2 | Devolutiva de redação | `OCR_DUVIDOSO`, `TIPO_TEXTUAL`, `ORTOGRAFIA` — códigos internos do motor projetados na tela | `63d47b8` |
| C3 | Portal e Assessor | `Olá, aluno_teste_a` — identificador técnico no lugar do nome, que estava no banco o tempo todo | `918e362` |
| C4 | Prática guiada | Depois de pedir a dica, a tela voltava a dizer "Tente primeiro por conta própria", contradizendo o que acabara de acontecer | `c71095e` |
| C5 | Toda a web | O navegador reusava arquivos antigos sem perguntar: três correções foram dadas como "não funcionou" quando já estavam no disco | `ed1704e` |

C5 merece um parágrafo. Ele aconteceu **três vezes** no mesmo ensaio, duas
delas comendo tempo de diagnóstico. Numa apresentação, o custo não é tempo: é
o telão mostrando o build de ontem com o bug que foi corrigido de manhã, sem
nenhuma forma de perceber isso no meio da sala. A correção manda
`cache-control: no-cache` em todo estático e em todas as 12 páginas. **Ela não
desfaz o que o navegador já guardou antes** — por isso a recarga forçada
continua obrigatória no checklist da véspera.

---

## Em aberto — P0

Bloqueiam ou ferem a apresentação. Nenhum foi corrigido aqui: os três tocam
dados de demonstração ou autenticação, e nenhum dos dois é território para
correção autônoma.

### P0-1 — Cabeçalho do Dashboard do Professor expõe o encanamento

| | |
|---|---|
| **TELA** | `/teacher` |
| **PROBLEMA** | O cabeçalho tem dois campos de texto de desenvolvimento, preenchidos com `user:prof_mendes` e `6f26cd3c-63d5-4509-a041-13714f75e53e` |
| **EVIDÊNCIA** | `teacher.html:96-102` — dois `<input type="text">` com `value` fixo no HTML. Confirmado na tela, projetado a 1366×768 |
| **IMPACTO** | Um UUID cru rotulado "Escola" na frente de um mantenedor diz "isto é uma ferramenta interna", não "isto é um produto" |
| **CORREÇÃO** | Resolver escola e pessoa pelo contexto autenticado, como `/api/v1/portal/overview` já faz, e tirar os campos da tela |
| **RISCO** | Médio. Todo o JS do dashboard lê esses dois inputs para montar cada requisição; removê-los sem religar a origem quebra a tela inteira |
| **TEMPO** | 4h com teste |

### P0-2 — Lixo no banco de demonstração visível ao professor

| | |
|---|---|
| **TELA** | Lista de temas de redação (professor) |
| **PROBLEMA** | Existem temas chamados `aergdfg` e `Teste`, sem `deleted_at` |
| **EVIDÊNCIA** | Consulta a `essay_prompts`: `aergdfg` (0 turmas), `Teste` (0 turmas), além de `teste`, `Teste dashboard agente` e um tema de IA já na lixeira |
| **IMPACTO** | `aergdfg` projetado destrói a credibilidade da tela inteira em um segundo |
| **CORREÇÃO** | Marcar `deleted_at` nos temas de teste. É reversível — a lixeira tem retenção, não é exclusão |
| **RISCO** | Baixo tecnicamente, mas **mexe em dado**: precisa de confirmação de quem é dono do banco antes de rodar |
| **TEMPO** | 10 min, depois da confirmação |

### P0-3 — A fila de correções é uma parede de falhas

| | |
|---|---|
| **TELA** | Fila de correções de redação (professor/coordenação) |
| **PROBLEMA** | 18 das 23 correções estão em `NEEDS_REVIEW` |
| **EVIDÊNCIA** | `failure_reason = AllProvidersFailedError: All providers failed: openai: ProviderTimeoutError` em 18 linhas, todas do tema "Desafios para o desenvolvimento da autonomia intelectual" |
| **IMPACTO** | 78% de falha visível. A pergunta seguinte é "isso falha sempre?" e não há resposta boa no meio da apresentação |
| **CORREÇÃO** | Duas opções honestas: (a) reprocessar as 18 com o provedor funcionando, (b) não mostrar a fila. O roteiro adotou (b) |
| **RISCO** | (a) custa chamadas de API e tempo de execução; (b) custa nada |
| **TEMPO** | (a) 2h incluindo verificação; (b) zero |

---

## Em aberto — P1

Não quebram, mas custam impacto.

### P1-1 — Duas marcas no mesmo produto

| | |
|---|---|
| **TELA** | `/teacher`, `/coordination`, `/reception`, `/admin`, `/entrada`, `/question-bank` |
| **PROBLEMA** | A lateral e o título dizem **AGENTE IA EDU**; o Portal diz **Núcleo Edu 360** e chama aquele módulo de **Assessor Pedagógico** |
| **EVIDÊNCIA** | 10 ocorrências de "AGENTE IA EDU" em `src/agente_ia_edu/web/*.html`; visto na barra lateral do dashboard |
| **IMPACTO** | Mostrar Portal e professor na mesma sessão exibe dois produtos. A classificação honesta é **inconsistência de produto**, não dívida visual |
| **CORREÇÃO** | Definir a nomenclatura (plataforma × módulo) e aplicar. Não foi feito aqui porque **nomear o produto é decisão de quem o constrói**, não de quem audita |
| **RISCO** | Baixo tecnicamente; alto se a decisão for tomada errada e tiver de ser desfeita |
| **TEMPO** | 2h depois da decisão |

### P1-2 — As telas ficam pequenas no telão

| | |
|---|---|
| **TELA** | `/portal` e `/student/aluno.html` |
| **PROBLEMA** | Largura máxima fixa (1120px no Portal, 760px no aluno) independente da tela |
| **EVIDÊNCIA** | Medido: a 1920×1080 o Portal ocupa 55% da largura e sobram 342px abaixo; o aluno ocupa 40% e sobram 554px. A 1366×768: 82% e 56%. A 1280×720 (= 1920 com zoom 150%): 88% e 59% |
| **IMPACTO** | Projetada sem zoom, a tela parece um protótipo pequeno no meio de um telão |
| **CORREÇÃO** | Mitigação imediata: **zoom do navegador em 125%** (já no checklist). Correção real: tipografia e grade que escalem acima de 1440px |
| **RISCO** | Baixo. Mas alargar a caixa de texto sem mexer na tipografia piora a leitura — a linha mais longa do Portal já tem 141 caracteres, quase o dobro do confortável |
| **TEMPO** | Mitigação: zero. Correção: 4h |

### P1-3 — A Redação é outro aluno

| | |
|---|---|
| **TELA** | `/redacao` |
| **PROBLEMA** | A identidade é fixa no código (`student:alice`), enquanto Portal e Assessor usam a identidade escolhida (`aluno_teste_a`) |
| **EVIDÊNCIA** | `essay.js:48` — `const DEFAULT_STUDENT_ID = 'student:alice'` |
| **IMPACTO** | Nada na tela denuncia isso (a Redação não mostra nome). O risco é o apresentador dizer "o mesmo aluno" e não poder sustentar |
| **CORREÇÃO** | Ler a mesma identidade do Portal **e** semear redações corrigidas para o Aluno Teste A. Só a primeira metade deixaria a tela vazia — pior |
| **RISCO** | Médio: envolve gerar correções reais |
| **TEMPO** | 1 dia |

### P1-4 — A home chama de nova uma prática já começada

| | |
|---|---|
| **TELA** | `/student/aluno.html` |
| **PROBLEMA** | Com a prática guiada no meio (2 tentativas, 2 ajudas), a home mostra "Tentar com ajuda" e `next_step.state = NOT_STARTED` |
| **EVIDÊNCIA** | Medido na API: `next_step.state = "NOT_STARTED"`, `cta = "Tentar com ajuda"`, enquanto a tela interna mostrava "Ajuda: 2 de 4" |
| **IMPACTO** | Baixo na apresentação (o reset zera isso), real no uso |
| **CORREÇÃO** | A prontidão precisa ler `guided_practice_items` e derivar estado e CTA, como já faz para a prática comum via `resume_assignment_id` |
| **RISCO** | Baixo, mas toca o contrato de prontidão, que tem muitos testes |
| **TEMPO** | 2h com teste |

### P1-5 — Nomes de conteúdo sem acento

| | |
|---|---|
| **TELA** | `/teacher` (e qualquer lugar que mostre nome de conteúdo) |
| **PROBLEMA** | "Solucoes", "Funcoes", "Cinematica" |
| **EVIDÊNCIA** | Visto no dashboard, em "Pontos de Melhoria Prioritários" e em "Conteúdos Ensinados" |
| **IMPACTO** | Numa tela de escola brasileira, erro de português visível |
| **CORREÇÃO** | É **dado** (nome de conteúdo no banco), não código. Corrigir as linhas |
| **RISCO** | Baixo, mas mexe em dado: confirmar antes |
| **TEMPO** | 30 min |

### P1-6 — Números em formato errado para pt-BR

| | |
|---|---|
| **TELA** | `/teacher` |
| **PROBLEMA** | "51.9%", "30.6%", "47.2%" — ponto decimal em vez de vírgula |
| **EVIDÊNCIA** | Visto nos cards de média e nos pontos de melhoria |
| **IMPACTO** | Pequeno isolado; somado ao resto do cabeçalho, reforça "ferramenta interna" |
| **CORREÇÃO** | Formatação com `toLocaleString('pt-BR')` no JS do dashboard |
| **RISCO** | Baixo |
| **TEMPO** | 1h com teste |

### P1-7 — Linhas longas demais

| | |
|---|---|
| **TELA** | `/portal` |
| **PROBLEMA** | A linha mais longa tem ~141 caracteres a 1920px (confortável: 45 a 85) |
| **EVIDÊNCIA** | Medido: "Estes módulos fazem parte do ecossistema…" ocupa a largura inteira do palco |
| **IMPACTO** | Leitura cansativa a quem está longe do telão |
| **CORREÇÃO** | `max-width` em caracteres (`65ch`) nos parágrafos de apoio |
| **RISCO** | Baixo. É a correção que deve vir **junto** com P1-2, nunca depois |
| **TEMPO** | 1h |

### P1-8 — Os primeiros 10 segundos dizem ONDE, não O QUÊ

| | |
|---|---|
| **TELA** | `/portal` |
| **PROBLEMA** | O título é "Núcleo Edu 360" e a chamada é "O que vamos acompanhar hoje?" — uma pergunta ao usuário, não uma afirmação do que a plataforma faz |
| **EVIDÊNCIA** | Texto da tela, lido na íntegra. Em 10 segundos de silêncio um diretor sabe onde está e não sabe o que é |
| **IMPACTO** | O momento mais caro da apresentação depende inteiramente da fala do apresentador |
| **CORREÇÃO** | Uma linha abaixo do título dizendo o que a plataforma resolve. Duas formas possíveis: *"Diagnóstico, ensino e acompanhamento da aprendizagem, em um só lugar."* ou *"A plataforma que descobre onde o aluno trava e ensina o que falta."* |
| **RISCO** | Técnico: nenhum, é uma linha de copy. Real: **posicionar o produto é decisão de quem o constrói**, não de quem audita. Por isso não foi aplicada |
| **TEMPO** | 10 min depois da decisão |

### P1-9 — Quem domina a base cai num segundo diagnóstico sem aviso

| | |
|---|---|
| **TELA** | Assessor, caminho A (aluno já domina o pré-requisito) |
| **PROBLEMA** | Depois de "Muito bem! Você demonstrou um bom domínio…", o botão **Continuar** abre um novo diagnóstico — agora do conteúdo principal — com o mesmo cabeçalho ("VAMOS VER ONDE VOCÊ ESTÁ") e a trilha ainda em "Diagnóstico (etapa atual)" |
| **EVIDÊNCIA** | Percorrido no navegador: 3 acertos → tela de parabéns → Continuar → "Pergunta 1 de 3" sobre estequiometria |
| **IMPACTO** | A decisão do sistema está certa; a tela não a explica. Para quem assiste, parece que o diagnóstico recomeçou |
| **CORREÇÃO** | Uma tela de transição dizendo que a base está firme e que agora o conteúdo principal será medido |
| **RISCO** | Baixo — é uma tela de passagem, não muda decisão |
| **TEMPO** | 2h com teste |

---

## Em aberto — P2

Registrados, **não** para serem implementados agora.

| ID | Tela | Problema | Observação |
|---|---|---|---|
| P2-1 | Diagnóstico | Equações escritas com coeficiente 1 explícito (`1 H₂ + 1 O₂ → 1 H₂O`) | Fora da convenção química. Um professor na plateia nota |
| P2-2 | Assessor (home) | Ícones de foto, arquivo e voz presentes e inativos | A própria tela avisa. Risco só se alguém clicar |
| P2-3 | `/teacher` | "Nenhum ponto forte identificado para esta seleção" ocupando um painel inteiro | Vazio negativo em posição de destaque |
| P2-4 | Toda a web | Linguagem visual divergente: Portal sóbrio, dashboard carregado de emojis | Sintoma de P1-1, não causa |
| P2-5 | Diagnóstico | As perguntas 1 e 3 são quase a mesma: ambas pedem "qual equação está balanceada" e a resposta das duas é `2 H₂ + O₂ → 2 H₂O` | Três perguntas, duas quase iguais, enfraquece a ideia de diagnóstico |

---

## Mapa de 6 dias

Ordenado por impacto na apresentação, não por dificuldade.

| Dia | O quê | Por que nessa ordem |
|---|---|---|
| **1** | Decidir a nomenclatura (P1-1) e aplicar. Limpar os temas de teste (P0-2) mediante confirmação | Nomenclatura destrava tudo que é visual; a limpeza é a correção de maior impacto por minuto gasto |
| **2** | Cabeçalho do Dashboard do Professor (P0-1) + formatação pt-BR (P1-6) | Juntos transformam a tela de "interna" em "produto" e abrem a possibilidade de mostrá-la |
| **3** | Nomes de conteúdo (P1-5) + decidir sobre as 18 correções falhas (P0-3) | Ambas são dado; fazer no mesmo dia que o banco está sendo tocado |
| **4** | Projeção: tipografia e grade acima de 1440px (P1-2) **com** largura de linha (P1-7) | Só depois que as telas estiverem certas vale ajustar como elas escalam |
| **5** | Prática guiada na prontidão (P1-4). Se sobrar tempo, identidade da Redação (P1-3) | Correções de produto, não de apresentação — ficam por último de propósito |
| **6** | **Nada novo.** Suíte completa, ensaio cronometrado duas vezes, capturas de tela de reserva | Um dia inteiro sem código antes de apresentar não é luxo: é o que separa um ensaio de uma aposta |

Se um dia estourar, o que sai é o dia 5. O que **não** sai é o dia 6.
