# Edu · Assessor Pedagógico — progresso

Documento de progresso do bloco **PROMPT MESTRE — EDU · ASSESSOR PEDAGÓGICO**
(§24 da especificação). Atualizado em **2026-10-08**.

Branch: `fase6/vetorial`. Nenhuma tabela nova, nenhuma migration, nenhum dado
real tocado.

---

## 1. Plano aprovado, e onde ele está

| # | Item | Seção | Estado | Commit |
|---|------|-------|--------|--------|
| 1 | Concisão contextual da resposta | §4 | **feito** | `b705605` |
| 2 | Os sete estados de aprendizagem; a conversa pode acabar | §6 | **feito** | `c10eed7` |
| 3 | Consolidação e retenção | §18 / §12 | **feito** | `ce6dbde` |
| — | Rótulo "Consolidado" reservado à consolidação de verdade | §12 | **feito** | `20d8f3a` |
| 4 | Relatório de apoio à aprendizagem, sem encaminhamento | §17 | **feito** | `0e3014b` |
| 5 | Níveis de autonomia 1–4 | §14 | **feito** | `7ec3739` |
| 6 | Contexto de exploração livre | §9 / §15 | **feito** | `06c952c` |
| — | YouTube: integração e o que falta para ligar | §8 | **parcial, registrado** | `25290ca` |
| — | Correções do QA no navegador | §17 / §9 | **feito** | `47ccde9` |

---

## 2. O que foi implementado, por seção

### §17 — Relatório de apoio à aprendizagem

Gerar, ver e baixar em PDF, de dentro de "Meu progresso":

```
GET /api/v1/student/learning-support-report
GET /api/v1/student/learning-support-report.pdf
```

- Seis seções na ordem do §17, começando pelo que o aluno **já faz**.
- Seção sem registro **diz que não há**, em vez de ser preenchida.
- Nenhum percentual, nenhum jargão, nenhuma palavra de laudo.
- "Fez sozinho" e "conseguiu com apoio" são duas listas, e a separação é
  estrutural (duas tabelas), não um `if`.
- **Não há envio, encaminhamento, compartilhamento nem notificação.** Nem
  botão. A proibição é **varrida** em quatro lugares: rotas registradas do
  app, fonte do serviço, fonte da tela e o vocabulário de ações do frontend.
  Provado por mutação: uma rota `POST .../send-to-teacher` acrescentada de
  propósito deixou três asserções vermelhas.
- A rota **não aceita identificador de aluno nenhum** — pedir o relatório de
  outra pessoa é inexprimível.
- As rotinas legítimas do portal do professor continuam de pé, com teste
  exigindo isso (§17 manda não eliminá-las).

### §14 — Níveis de autonomia

`services/niveis_de_autonomia.py`. Quatro níveis (automático, transparência,
confirmação, autoridade), três naturezas de plano (institucional, pessoal
confirmado, recomendação).

- **Fechado por falha**: ação que ninguém classificou cai no nível 4.
- "O Edu não poderá alterar silenciosamente compromissos escolares" é medido
  de ponta a ponta: o aluno percorre o ciclo e a tarefa da escola sai de lá
  com o mesmo prazo, alvo, status e metadados.
- Guarda nova no serviço: um chamador de papel `STUDENT` não cria atividade
  com origem institucional. A rota nem aceita o campo; a guarda é para o
  próximo caminho de escrita.
- A criação de prática devolve `autonomia: {acao, nivel, exige, plano,
  descricao}` — transparência como contrato.

### §9 / §15 — Exploração livre

`services/percurso.py` e prompt `assessor-conversa-v4`.

- Três percursos: planejado, exploração, apoio.
- A v3 mandava "traga de volta ao ponto" para o que fugisse do estudo, e a
  leitura literal recusava curiosidade de **outra disciplina**. A v4 separa
  as duas coisas e responde à curiosidade, sem exigir pré-requisito.
- **Quem declara que é outro assunto é o aluno** (um controle na conversa);
  quem classifica é o servidor.
- A volta ao ponto anterior é **nomeada** e leva à atividade de onde ele saiu.
- Explorar não move passo, não grava evidência, não altera compromisso e não
  apaga o estado da atividade — medido com posição, respostas, contagens das
  duas tabelas de evidência e o próximo passo antes e depois.

### §8 — YouTube

Busca real implementada e testada **menos a chamada HTTP**: consulta, duas
chamadas da API v3, validação e descarte, com respostas reais injetadas.
Dado pessoal na consulta é **recusado**. Sem credencial o provedor **levanta**
em vez de devolver `[]` — "não procurei" deixou de parecer "não achei nada".

**Falta a credencial, e a tela do aluno não existe.** Ver
[`youtube-o-que-falta-para-ligar.md`](youtube-o-que-falta-para-ligar.md).

---

## 3. Arquivos e componentes

**Novos serviços:** `relatorio_de_apoio.py`, `relatorio_de_apoio_do_aluno.py`,
`niveis_de_autonomia.py`, `percurso.py`, `youtube_busca.py`,
`consolidacao.py`, `consolidacao_do_aluno.py`, `concisao.py`,
`estados_de_aprendizagem.py`.

**Prompts versionados:** `assessor_prompts/v3.py` (§4), `v4.py` (§9). As
versões anteriores continuam no registro e chamáveis.

**Modificados:** `api/routes/student.py` (duas rotas do relatório, percurso na
conversa, tradução do progresso), `services/report_render.py` (um motor de
PDF, não dois; transliteração do que a fonte não desenha),
`services/student_progress.py`, `services/adaptive_practice.py`,
`services/conversa_do_assessor.py`, `services/video_discovery.py`.

**Frontend:** `web/aluno-relatorio.js` (novo, vocabulário de ações fechado),
`web/aluno-conversa.js`, `web/aluno.js`, `web/aluno.html`, `web/aluno.css`.

---

## 4. Testes

**Novos:** `test_relatorio_de_apoio.py`, `test_relatorio_de_apoio_http.py`,
`test_niveis_de_autonomia.py`, `test_percurso_de_exploracao.py`,
`test_youtube_busca_real.py`, `test_aluno_relatorio_frontend.js` (node, ligado
à suíte por `COMPORTAMENTAIS`).

O resultado da suíte completa e os números do QA no navegador estão no
relatório da etapa, entregue ao dono do produto.

---

## 5. Problemas encontrados

Sete defeitos reais, todos corrigidos e registrados nos commits:

1. **A varredura do §17 não varria nada.** Nesta versão do FastAPI
   `app.routes` não é plano; varrer o primeiro nível achava 64 entradas e
   zero rotas. A proibição mais importante da especificação estaria verde
   provando nada.
2. **`/student/progress` vazava código curricular e número ao aluno.** O
   campo `habilidades` saía cru, com `acerto: 1.0`. Pego por
   `test_aluno_ciclo_pedro`, que eu não havia rodado.
3. **Um teste meu passava comparando `None` com `None`** — três chaves que o
   `ActivityPlayerState` não tem.
4. **Um teste antigo passou a chamar a internet** depois de o stub do YouTube
   virar cliente de verdade.
5. **O PDF engolia a pontuação:** travessão, aspas curvas e reticências
   viravam "·" em silêncio, no papel que o aluno leva a um adulto.
6. **O relatório virou parede:** sete frases idênticas numa seção, com
   histórico real.
7. **A volta prometia a atividade e despachava o próximo passo** — que, para
   o aluno de QA, era uma escalação ao professor.

---

## 6. Pendências

- **Credencial do YouTube** e **tela de vídeo do aluno** (§8.6–8.12). Nenhuma
  das duas é trabalho de integração; estão registradas com as regras já
  mapeadas.
- **Recusa de recomendação persistida** (§14). Recusa é fato não derivável e
  exigiria uma linha gravada. Hoje não há planejador pessoal nem insistência —
  o próximo passo é um convite na tela, sem repetição — então não há o que
  respeitar ainda. Registrado no cabeçalho de `niveis_de_autonomia.py`.
- **Nome humano de micro-habilidade.** Não existe cadastro; o relatório
  formata o próprio código (`MASSA_MOLAR` → "Massa molar") em vez de inventar
  um nome.
- **Mapa visual de aprendizagem** (§12) e **integração multidisciplinar**
  (§11) não foram escopo deste bloco.

---

## 7. Próxima etapa

Decisão do dono do produto. As candidatas, em ordem de valor pedagógico:

1. A tela de vídeo do aluno, quando houver credencial e curadoria.
2. O cadastro de nome de micro-habilidade — hoje o aluno lê o código
   formatado.
3. O mapa visual do §12, agora que consolidação e retenção existem de verdade
   como estado.
