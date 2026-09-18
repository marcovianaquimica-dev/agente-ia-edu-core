# Simulado Aberto — plataforma de captação de leads

**Data:** 2026-09-18
**Status:** design aprovado, aguardando plano de implementação
**Projeto:** novo repositório independente (fora do `agente-ia-edu-core`)

---

## 1. Contexto e objetivo

Produto de aquisição para a plataforma paga "Química do ENEM": um simulado aberto, público,
que qualquer visitante pode responder sem cadastro prévio na plataforma paga. Em troca de
fazer o simulado, o visitante deixa nome, e-mail e WhatsApp (lead) e recebe uma nota no
estilo ENEM e um feedback pedagógico por assunto — que também funciona como o gancho para a
oferta de matrícula na plataforma paga ao final.

Este produto **não dá acesso** à plataforma Química do ENEM nem ao banco de questões do
`agente-ia-edu-core`. É um sistema deliberadamente separado: repositório próprio, banco de
dados próprio, deploy próprio.

### Fora de escopo (v1)

- Qualquer integração com o `agente-ia-edu-core` (banco de questões, classificação, RAG).
- Múltiplos administradores / gestão de contas de admin.
- Integração com CRM ou ferramenta de e-mail marketing externa (só WhatsApp via Z-API).
- Cálculo de nota estilo TRI (Teoria de Resposta ao Item). A nota é linear, ver §5.

---

## 2. Arquitetura

- **Backend:** Python + FastAPI, seguindo os mesmos padrões de projeto do
  `agente-ia-edu-core` (SQLAlchemy 2.x, Alembic para migrations, Pydantic para schemas) mas
  em código totalmente independente.
- **Frontend:** server-rendido, Jinja2 + JavaScript vanilla, sem build step nem framework
  SPA. Evita a complexidade de um pipeline de build separado no VPS.
- **Banco de dados:** PostgreSQL próprio (container separado do Postgres do core).
- **Deploy:** Docker Compose (app + Postgres) em um Hostinger VPS, repositório hospedado no
  GitHub.
- **Três áreas de rotas:**
  - **Público** — catálogo de simulados, captura de lead, execução do simulado, resultado.
  - **Admin** — login único (credenciais via variável de ambiente), CRUD de simulados e
    questões (com upload de imagem), listagem e exportação de leads.
  - **Integração WhatsApp** — serviço assíncrono que envia o feedback final via Z-API depois
    que uma tentativa é finalizada.

---

## 3. Modelo de dados

| Entidade | Campos principais | Observações |
|---|---|---|
| **Simulado** | título, descrição, `score_min`, `score_max` (padrão 308,6 / 858,7), status (rascunho/publicado) | Cada simulado tem sua própria faixa de nota e seu próprio conjunto de questões. |
| **Questao** | `simulado_id`, enunciado (texto + imagens opcionais), 5 alternativas (texto + imagem opcional cada), gabarito, resolução (texto + imagens), `assunto` (texto interno) | `assunto` nunca é exposto ao aluno — só alimenta o feedback agregado. |
| **Lead** | nome, e-mail (único), telefone WhatsApp | Criado na primeira tentativa; reaproveitado em tentativas futuras pelo mesmo e-mail. |
| **Attempt** (tentativa) | `lead_id`, `simulado_id`, token de sessão (cookie), status (em andamento/finalizada), `started_at`, `finished_at`, `score`, `whatsapp_status` (pendente/enviado/falhou) | Uma linha por tentativa; leads podem ter várias tentativas do mesmo simulado (retomada ilimitada). |
| **Answer** (resposta) | `attempt_id`, `question_id`, alternativa escolhida, `is_correct`, `answered_at` | Gravada a cada resposta — é o que dá o mapeamento de progresso e permite retomar a sessão. |

---

## 4. Fluxo do aluno

1. Abre o catálogo público (`/simulados`) e escolhe um simulado publicado.
2. Preenche nome, e-mail e WhatsApp → cria (ou recupera) o `Lead`, cria uma nova `Attempt`,
   seta um cookie com o token da tentativa, redireciona para a primeira questão.
3. Responde as questões em qualquer ordem; cada resposta é salva imediatamente no banco (não
   só em memória de sessão). Uma barra/mapa mostra quais já foram respondidas.
4. Se fechar a aba e voltar no mesmo navegador, o cookie identifica a `Attempt` em aberto e o
   aluno retoma exatamente de onde parou.
5. Ao responder a última questão pendente, aparece um modal de confirmação: "Você respondeu
   todas as questões. Deseja finalizar o simulado?" — o aluno ainda pode voltar e revisar
   respostas antes de confirmar.
6. Ao confirmar: a `Attempt` é marcada como finalizada, o score é calculado (§5), o feedback
   por assunto é gerado (§5), e a página de resultado é exibida com uma oferta de matrícula
   na plataforma Química do ENEM.
7. Em paralelo, sem bloquear a resposta HTTP da finalização, o backend dispara o mesmo
   resultado/feedback por WhatsApp via Z-API (§6).
8. Uma `Attempt` finalizada não aceita mais respostas. Se o aluno acessar de novo (mesmo
   cookie), cai direto na tela de resultado dessa tentativa.
9. Nada impede o mesmo e-mail de iniciar uma **nova** tentativa do mesmo simulado a qualquer
   momento (retomada/repetição ilimitada).

---

## 5. Pontuação e feedback

**Score** (por `Simulado`, usando seus `score_min`/`score_max` e N = número de questões):

```
delta = score_max - score_min
score = score_min + (acertos / N) * delta
```

Ex.: química ENEM com `score_min = 308.6`, `score_max = 858.7` → cada acerto vale
`delta / N` pontos a partir do piso de 308,6.

**Feedback por assunto:**

1. Agrupa as `Answer` da tentativa pelo campo interno `assunto` da `Questao`.
2. Calcula percentual de acerto por assunto.
3. Classifica em faixas (ex.: forte / mediano / atenção) e gera texto do tipo "Você foi bem
   em Estequiometria (4/4) e precisa reforçar Eletroquímica (1/5)".
4. O mesmo texto é reaproveitado na tela de resultado e na mensagem de WhatsApp.

---

## 6. Admin e cadastro de conteúdo

- Login único via usuário/senha em variável de ambiente — sem tela de cadastro de outros
  admins no v1.
- **Simulados:** criar/editar/publicar/despublicar; `score_min`/`score_max` pré-preenchidos
  com 308,6/858,7, editáveis por simulado.
- **Questões** (dentro de um simulado): criar/editar/reordenar; upload de imagem para
  enunciado, cada alternativa e resolução; campo de assunto interno.
- **Leads:** listagem com filtro por simulado, exportação em CSV.
- Imagens salvas em volume Docker no próprio VPS e servidas estaticamente — sem S3/CDN
  externo no v1.

---

## 7. Integração WhatsApp (Z-API)

- Ao finalizar uma `Attempt`, um job assíncrono monta a mensagem de resultado + feedback e
  chama a API do Z-API para o telefone do `Lead`.
- Falha de envio (Z-API fora do ar, número inválido etc.) é logada e registrada em
  `Attempt.whatsapp_status = "falhou"`; **não** afeta o aluno, que já viu o resultado na tela.
- O admin pode ver tentativas com envio pendente/falho e reenviar manualmente (ação simples
  no painel admin, sem fila/retry automático no v1).
- Credenciais do Z-API (instance id, token) ficam em variável de ambiente.

---

## 8. Testes

- Fórmula de pontuação (`score_min`/`score_max`/N variados, incluindo bordas: 0 acertos, 100%
  acertos).
- Fluxo completo de tentativa: iniciar → responder → fechar/retomar via cookie → finalizar →
  bloquear respostas após finalizar → nova tentativa do mesmo lead permitida.
- Geração do feedback por assunto (agrupamento e classificação de faixas).
- CRUD do admin (simulados, questões, upload de imagem, leads/exportação CSV).
- Envio ao Z-API mockado (sucesso e falha, sem afetar o fluxo do aluno).

---

## 9. Deploy

- Docker Compose (app + Postgres) no Hostinger VPS, com script/README de setup inspirado no
  padrão do `docker-compose.yml` do `agente-ia-edu-core` (referência de padrão, código
  independente).
- Repositório novo e público/privado no GitHub, conforme preferência do usuário no momento da
  criação.
