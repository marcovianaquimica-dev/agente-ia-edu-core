# Dashboard SEDUC-PB (v1 simplificada) - Design

## Contexto

A SEDUC-PB pediu um dashboard gerencial para o Simulado de Redação da 3ª série
do Ensino Médio (~50.000 redações, corrigidas pela pipeline de [correção em
massa via Batch API](2026-09-29-correcao-em-massa-rede-estadual-design.md)).
Existe uma proposta técnica completa e ambiciosa (documento de apoio
`resumo_tecnico_dashboard_redacao_seduc_pb_v2.pdf`, fora do repositório, em
`/Users/marcoviana/Library/Mobile Documents/com~apple~CloudDocs/PLATAFORMA DE
REDAÇÃO/PROJETO ESTADO PB/`), com 9 telas e hierarquia completa
Estado→GRE→Município→Escola→Turma→Aluno.

Este documento especifica uma **v1 deliberadamente menor**, pedida
explicitamente pelo usuário para entrega rápida: um projeto pontual, separado
do produto principal, cobrindo só o que entrega valor imediato. A hierarquia
GRE/Município **não existe no schema atual** (que é só por `school_id`) e
fica fora do escopo desta v1 - ela aparece aqui só como contexto da visão
completa, não como requisito.

## Escopo da v1

**Dentro do escopo:**
- 4 telas: Visão Geral, Ranking, Escola, Turma.
- Classificação por faixa de nota (determinística, configurável).
- Ranking de alunos com posição e empate = mesma posição.
- Síntese executiva curta via IA (geral + por escola), cacheada.
- Export periódico (sob demanda) do banco principal para um snapshot
  próprio, isolando o dashboard do banco de produção.
- Login por usuário (sem escopo por perfil - todo usuário autenticado vê
  todos os dados).

**Fora do escopo desta v1** (fica para uma v2, se for pedida depois):
- Hierarquia GRE/Município e as telas correspondentes.
- Classificação qualitativa de pontos fortes/melhoria via IA (tags por
  competência).
- Diagnóstico pedagógico e plano de ação gerencial via IA.
- Central de relatórios / exportação de documentos PDF via template.
- Escopo de acesso por perfil (diretor só vê a própria escola etc).
- Exportação Excel.
- Agendamento automático do export (roda manualmente, sob demanda).

## Arquitetura

Pasta nova e isolada dentro do repositório atual: `dashboards/seduc-pb/`.
Tem dependências, banco e processo próprios - **não importa nada** do
pacote `agente_ia_edu` (`src/`). A única integração com o produto principal
é o export job lendo o Postgres de produção com um usuário só-leitura.

```
dashboards/seduc-pb/
  pyproject.toml              # dependências próprias (FastAPI, Jinja2, etc.)
  export_snapshot.py          # CLI: le o Postgres principal, grava snapshot.db
  app.py                      # FastAPI: serve as 4 telas a partir do snapshot
  auth.py                     # login simples por usuário, sessão via cookie
  ia_synthesis.py             # gera e cacheia a síntese executiva
  templates/                  # Jinja2: visao_geral.html, ranking.html, escola.html, turma.html
  snapshot.db                 # SQLite, gerado pelo export job (gitignored)
  tests/
```

Três componentes:

1. **Export job** (`export_snapshot.py`) - script Python rodado manualmente
   via CLI (`python export_snapshot.py`). Conecta no Postgres principal
   (`DATABASE_URL` de um usuário **só-leitura**, nunca o usuário de
   aplicação), lê os dados necessários, calcula faixa/classificação e
   ranking, e grava um snapshot em SQLite (`snapshot.db`). É idempotente:
   cada execução recria as tabelas derivadas do zero a partir do estado
   atual do Postgres - não há lógica incremental, porque o volume de hoje
   (dezenas de correções) não justifica a complexidade, e mesmo em 50.000
   linhas uma leitura completa + `INSERT` em SQLite roda em segundos.

2. **App web** (`app.py`) - FastAPI + Jinja2. Lê **exclusivamente** do
   `snapshot.db`, nunca do Postgres principal. Serve as 4 telas.

3. **Síntese executiva** (`ia_synthesis.py`) - chamada como parte do export
   (ou separadamente, `python ia_synthesis.py`). Gera um texto curto por
   corte (um geral + um por escola) a partir de dados **já agregados**
   (médias, distribuição por faixa, não o texto da redação) e persiste no
   snapshot junto com um hash dos dados de entrada. Uma nova execução só
   gera uma síntese nova para um corte se o hash mudou.

## Dados e cálculos

### Fonte (Postgres principal, só leitura)

O export job junta:
- `essay_corrections` (status, `final_scores` jsonb - `total` e
  `per_competency.{C1..C5}.points`, `created_at`).
- `essay_submissions` (liga correção a aluno via `student_id`).
- `users` + `persons` (nome do aluno).
- `schools` (nome da escola).
- `classes` / `student_enrollments` (turma do aluno).

Só correções com `status = 'APPROVED'` entram no snapshot - uma correção
ainda em revisão não deve aparecer em métricas gerenciais.

### Classificação por faixa

Tabela de configuração (`dashboards/seduc-pb/faixas.py` ou
`faixas.json`, versionada no próprio dashboard - não no banco principal):

| Faixa | Classificação |
|---|---|
| 0-399 | Muito baixo |
| 400-599 | Baixo |
| 600-799 | Adequado |
| 800-899 | Alto |
| 900-1000 | Muito alto |

Calculada no export, a partir de `final_scores.total`. Mudar os cortes é
editar esse arquivo e rodar o export de novo - não precisa de migration.

### Ranking

Calculado no export: ordena por `nota_final` descendente, atribui posição
com empate = mesma posição (padrão recomendado no documento de referência -
duas notas iguais ocupam a mesma posição, e a próxima posição pula o número
correspondente de alunos empatados). Guardado já pronto no snapshot - a
tela não recalcula a cada request.

## Esquema do snapshot (SQLite)

```sql
CREATE TABLE redacoes (
    id_redacao TEXT PRIMARY KEY,
    id_aluno TEXT NOT NULL,
    nome_aluno TEXT NOT NULL,
    escola_id TEXT NOT NULL,
    escola_nome TEXT NOT NULL,
    turma_id TEXT,
    turma_nome TEXT,
    nota_final INTEGER NOT NULL,
    c1 INTEGER NOT NULL,
    c2 INTEGER NOT NULL,
    c3 INTEGER NOT NULL,
    c4 INTEGER NOT NULL,
    c5 INTEGER NOT NULL,
    faixa_classificacao TEXT NOT NULL,
    posicao_geral INTEGER NOT NULL,
    data_correcao TEXT NOT NULL
);

CREATE TABLE sinteses (
    corte TEXT PRIMARY KEY,         -- 'geral' ou 'escola:<escola_id>'
    texto TEXT NOT NULL,
    hash_dados TEXT NOT NULL,
    gerado_em TEXT NOT NULL
);

CREATE TABLE usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    login TEXT UNIQUE NOT NULL,
    senha_hash TEXT NOT NULL
);
```

`redacoes` é inteiramente recriada a cada export (`DROP` + `CREATE` +
`INSERT`, dentro de uma transação). `sinteses` e `usuarios` persistem entre
exports (a síntese só regenera se o hash mudar; usuários são cadastrados
manualmente, não vêm do export).

## Telas

- **Visão Geral** (`/`): total de redações corrigidas, média, mediana,
  desvio-padrão, distribuição por faixa (contagem/%), síntese executiva
  geral.
- **Ranking** (`/ranking`): tabela com posição, aluno, escola, turma, nota,
  classificação, C1-C5. Filtrável por escola e turma. Ordenável por nota ou
  por qualquer competência.
- **Escola** (`/escola/{escola_id}`): mesmas métricas da Visão Geral,
  recortadas para a escola, mais síntese executiva da escola e lista de
  turmas com média por turma.
- **Turma** (`/turma/{turma_id}`): lista de alunos da turma com nota,
  C1-C5 e classificação, ordenável.

## Síntese executiva (IA)

Entrada: só dados agregados já calculados (médias, distribuição por faixa,
contagens) - nunca o texto da redação ou da devolutiva completa, seguindo o
princípio já estabelecido no documento de referência ("IA não recorrige o
que o motor já corrigiu"). Saída: texto curto (2-4 frases). Cacheada por
corte com hash dos dados de entrada - reabrir a tela ou rodar o export sem
mudança de dados não gera chamada nova.

## Autenticação

Tabela `usuarios` própria no snapshot (login + hash de senha, bcrypt).
Cadastro de usuário é manual (script simples ou inserção direta - não há
tela de admin nesta v1). Sessão via cookie assinado. Sem escopo por perfil:
qualquer usuário autenticado vê todos os dados do snapshot. Não reaproveita
o sistema de identidade do produto principal (`TestExternalIdentityProvider`
etc.) - é deliberadamente mais simples.

## Stack técnico

Python (reaproveita familiaridade do time), FastAPI + Jinja2 para o app
web, SQLite para o snapshot (zero infraestrutura extra, adequado ao volume
e à ausência de escrita concorrente de múltiplos usuários), `psycopg` só no
export job para ler do Postgres principal. Dependências isoladas em
`dashboards/seduc-pb/pyproject.toml`, ambiente virtual próprio.

## Testes

`pytest` dentro de `dashboards/seduc-pb/tests/`:
- Export job: dado um Postgres de teste com dados sintéticos, o snapshot
  gerado tem as linhas e colunas esperadas.
- Classificação por faixa: cada corte mapeia para o rótulo certo,
  incluindo os limites exatos (399/400, 899/900 etc.).
- Ranking: cálculo de posição com e sem empates.
- Síntese: cache não regenera quando o hash não muda; regenera quando muda.
- Auth: login válido cria sessão; login inválido é rejeitado.

## Riscos e decisões em aberto

1. **Dados reais ainda limitados**: hoje existem poucas dezenas de
   correções reais aprovadas (a correção em massa de 50 mil ainda não
   rodou em volume - calibração de recorte de folha física pendente,
   documentada em outro projeto). A v1 deste dashboard funciona com os
   dados que existem hoje; crescer para 50 mil é só rodar o export de novo
   (sem mudança de código esperada, dado que SQLite comporta essa escala
   tranquilamente).
2. **Hierarquia GRE/Município**: deliberadamente fora de escopo. Se vier a
   ser pedida depois, precisa de uma fonte de dados nova (a SEDUC teria que
   fornecer o mapeamento escola→município→GRE, que não existe em nenhum
   sistema hoje).
3. **Cadastro de usuário manual**: aceitável para a v1 pontual (poucos
   usuários esperados); uma tela de admin fica para depois, se o número de
   usuários crescer.
