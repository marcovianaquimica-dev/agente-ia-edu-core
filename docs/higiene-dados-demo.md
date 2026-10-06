# Higiene de dados do ambiente de demonstração

Auditoria executada em 2026-10-06, antes da apresentação. A ordem seguida foi
**auditar → classificar → documentar → garantir recuperação → limpar apenas o
seguro → validar → testar**. Nada foi apagado antes de estar nesta página.

## 1. Qual banco é este

| | |
|---|---|
| servidor | PostgreSQL 16.15 em contêiner Docker `agente-ia-edu-postgres` (`pgvector/pgvector:pg16`) |
| porta | host 5433 → 5432 do contêiner |
| banco | `agente_ia_edu`, usuário `agenteedu` |
| tamanho | 100 MB |
| migração | `067_guided_practice` |

Este é o banco **de desenvolvimento**: a credencial é a literal de `.env` de
dev, o contêiner é local, e `tests/conftest.py` monta a `DATABASE_URL` a partir
dos mesmos `POSTGRES_*` — ou seja, a suíte roda contra este mesmo Postgres.
Não há indício de produção. Ainda assim, **nenhum registro foi apagado em
definitivo** (ver §5).

## 2. Recuperação garantida antes de qualquer alteração

```
pg_dump --format=custom  →  scratchpad/backup_antes_higiene.dump
exit 0 · 5.484.006 bytes · pg_restore --list mostra 125 TABLE DATA
```

O dump fica **fora do repositório** (diretório de rascunho da sessão) porque
contém dados de pessoas. Não foi e não deve ser commitado.

## 3. Inventário por escola

| escola | pessoas | alunos | turmas | propostas | redações | vínculos |
|---|---|---|---|---|---|---|
| Escola ABC | 48 | 48 | 3 | 2 | 17 | 27 |
| Escola Partner | 1 | 1 | 1 | 9 | 5 | 22 |
| BWalk26 escola | 0 | 0 | 0 | 0 | 0 | 4 |
| ESCOLA TESTE | 0 | 0 | 0 | 0 | 0 | 1 |

## 4. Classificação de cada suspeito

`A` legítimo · `B` demonstração intencional · `C` teste/dev comprovado ·
`D` incerto (preservar)

### Propostas de redação (11 no total)

| título | escola | atribuições | redações | classe | ação |
|---|---|---|---|---|---|
| Desafios para o desenvolvimento da autonomia intelectual… | ABC | 1 | 17 | **A** | preservar |
| Desafios para promover o uso crítico da IA… | ABC | 1 | 0 | **A** | já na lixeira pelo professor — não tocar |
| Desafios para o enfrentamento da invisibilidade do trabalho… | Partner | 1 | 1 | **B** | preservar |
| O valor da leitura na era digital | Partner | 1 | 1 | **B** | preservar |
| Perspectivas acerca do envelhecimento na sociedade… | Partner | 1 | 1 | **B** | preservar |
| Desafios para o combate ao racismo na sociedade brasileira | Partner | 1 | 1 | **B** | preservar |
| Tema livre | Partner | 1 | 1 | **B** | preservar — `is_free_theme = true`, o título **é** a funcionalidade, com enunciado real e uma redação entregue |
| teste | Partner | 1 | 0 | **C** | já na lixeira pelo professor em 2026-09-29 18:33 — não tocar |
| Teste dashboard agente | Partner | 1 | 0 | **C** | já na lixeira pelo professor em 2026-09-29 18:33 — não tocar |
| **Teste** | Partner | 0 | 0 | **C** | mover para a lixeira |
| **aergdfg** | Partner | 0 | 0 | **C** | mover para a lixeira |

### Turmas, pessoas, usuários

| registro | evidência | classe | ação |
|---|---|---|---|
| Turma A (22), Turma B (25), Turma 3ª A (Piloto) (1), 3ª Série A (1) | matrículas reais em `student_enrollments` | A/B | preservar |
| 49 pessoas | único nome com padrão suspeito é `Aluno Teste A` | B | preservar — é a identidade declarada do Piloto Zero do Aluno |
| 23 usuários com nome de pessoa (`ana_sa`, `lucas_lima`, …) | vínculos reais | A/B | preservar |
| `student:alice` | `users` sem **nenhum** vínculo: 0 em `user_school_links`, 0 em `enrollment_transitions` | **D** | **preservar por segurança** — ver §6 |
| `BWalk26 escola`, `ESCOLA TESTE` | zero pessoas/alunos/turmas/propostas, mas 4 e 1 vínculos de usuário | **D** | **preservar por segurança** — ver §6 |

## 5. O que foi limpo, e por que isso é reversível

Apenas os dois registros da classe C que ainda apareciam na lista ativa:

| | `aergdfg` | `Teste` |
|---|---|---|
| id | `c784f203-0cea-4fa2-a96a-229d69554401` | `31e43eb3-54af-419f-bbab-ed32725e2c00` |
| origem | `prof_mendes`, 2026-09-29 18:35 | `prof_mendes`, 2026-09-29 18:34 |
| enunciado | `adfdafv` | `anxvbksajd` |
| status | `DRAFT` | `DRAFT` |
| vínculos | 0 atribuições, 0 materiais, 0 lotes, 0 logs, 0 redações | idem |
| referência em teste | nenhuma (`grep` em `tests/` e `src/`) | nenhuma |

A evidência de que são teste não é "o título parece": é o **enunciado ser
batida de teclado**, serem `DRAFT` nunca atribuídos, estarem sem nenhum
dependente nas quatro tabelas que referenciam `essay_prompts`, e terem sido
criados entre 18:34 e 18:35 do mesmo dia em que o próprio professor mandou
`teste` e `Teste dashboard agente` para a lixeira, às 18:33.

A limpeza usou **o mecanismo do próprio produto** — `DELETE
/api/v1/catalog/essay-prompts/{id}`, que chama
`EssayProposalService.soft_delete_prompt` e escreve **só** `deleted_at`. Nada
é apagado em definitivo: a linha permanece no banco, aparece na Lixeira, e
`POST /{id}/restore` devolve o registro dentro de 30 dias. Não houve `DELETE`
em SQL, nem alteração de FK, nem registro órfão.

Para desfazer:

```bash
curl -X POST -H "Authorization: Bearer user:prof_mendes" \
  http://127.0.0.1:8099/api/v1/catalog/essay-prompts/c784f203-0cea-4fa2-a96a-229d69554401/restore
```

## 6. O que foi deixado de fora, de propósito

- **`student:alice`** é resíduo do prefixo `student:` que quebra o
  `UserSchoolLink` — um `users` sem vínculo algum. Remover exigiria `DELETE`
  cru numa tabela de identidade, sem mecanismo de produto e sem reversão. E
  não há ganho: não aparece em nenhuma tela do roteiro. Fica.
- **`BWalk26 escola` e `ESCOLA TESTE`** só apareceriam numa lista de escolas,
  e o catálogo do Portal (`modulos_do_portal.py`) expõe apenas `/redacao` e
  `/student/aluno.html` — `admin.html` não é alcançável pela demonstração.
  Apagar escola é dado institucional sem mecanismo de produto. Ficam.
- **As 16 correções em `PENDING_REVIEW`** da Escola ABC são estado legítimo do
  produto: 17 redações reais entregues, aguardando revisão humana. A fila
  mostrar 16 pendências não é sujeira — é a função. Nada a limpar.

Em todos os três casos a regra aplicada foi a mesma: dado institucional ou de
pessoa vale mais que a estética da demonstração, e na dúvida não se apaga.

## 7. O que a validação encontrou de quebrado — e o que foi corrigido

### Corrigido: `hidden` não escondia os filtros globais

Ao conferir a navegação depois da limpeza, os filtros "🏫 Turma" e "📅 Período"
do cabeçalho **continuavam na tela** na view da Redação — exatamente o que o
bloco anterior tinha fechado como resolvido.

O JS estava certo: `switchView` marcava `g.hidden = true`, e
`RedacaoNav.filtrosGlobaisValem('essay-review')` devolvia `false`. Medido no
navegador: `hidden: true`, `display: "flex"`. O atributo `hidden` só esconde
pela folha do **navegador**, e `.filter-group { display: flex }` a vence. O
teste em Node passava porque testava a decisão, não a tela.

O repositório já tinha tropeçado nisso três vezes e corrigido caso a caso
(`.activity-player-dialog-backdrop[hidden]`, `.activity-result-questions[hidden]`,
`.ss-time-custom[hidden]`). A correção agora vale para o produto inteiro, em
`nucleo.css`:

```css
[hidden] { display: none !important; }
```

O teste novo (`tests/test_hidden_esconde_mesmo.py`) não procura esse texto na
folha: ele **resolve a cascata** para o elemento real, na ordem em que a
página carrega as folhas, e pergunta qual `display` sobra. Antes da correção
devolvia `flex` e ficava vermelho; depois devolve `none`. Medido de novo no
navegador: `display: "none"`, altura 0.

### Pendente: nomes de turma inventados nos `<select>` do cabeçalho

O backend **já** corrigiu isto: `real_classroom_external_ids` substituiu o
antigo placeholder `["TURMA_3A", "TURMA_3B"]`, que — nas palavras do próprio
código — vinha "inventando nomes de turma que não existem". O frontend ainda
carrega o gêmeo desse placeholder, escrito à mão em cinco lugares:

| arquivo | linha | select |
|---|---|---|
| `src/agente_ia_edu/web/teacher.html` | 127 | filtro global de Turma |
| `src/agente_ia_edu/web/teacher.html` | 807 | Turma, no modal "Registrar Nova Aula" |
| `src/agente_ia_edu/web/coordination.html` | 94 | filtro global de Turma |
| `src/agente_ia_edu/web/coordination.html` | 404 | Turma, no formulário de relatório |
| `src/agente_ia_edu/web/coordination.html` | 469 | Turma alvo, na orientação pedagógica |

Para a Escola ABC — cujas turmas reais são "Turma A", "Turma B" e
"Turma 3ª A (Piloto)" — esses dois nomes simplesmente não existem. O endpoint
que devolve as turmas de verdade já está no ar
(`GET /api/v1/teacher/classrooms`) e já é consumido pela própria Redação.

**Não foi corrigido neste bloco**: são cinco pontos em dois módulos, cada um
com a sua fonte de dados, e isto é desenvolvimento de frontend, não higiene de
dados. Fica registrado como P1 com o caminho da correção pronto. Na
apresentação o filtro global **não aparece** na Redação (§7, corrigido); ele
aparece nas views próprias do professor.

### Não é sujeira: as 16 correções pendentes

A fila de Correções da Escola ABC mostra 16 redações aguardando revisão
humana. São 17 entregas reais de alunos reais. A fila cheia é a função do
produto, não um resíduo de teste. Nada a limpar.

## 8. A recuperação foi testada, não assumida

Não bastava o código dizer que restaura. `Teste` foi restaurado e devolvido à
lixeira, contra a API de verdade:

```
POST .../31e43eb3-.../restore   → 200    lista ativa: 5 → 6, "Teste" de volta
DELETE .../31e43eb3-...         → 204    lista ativa: 6 → 5
```

Além disso, a própria Lixeira da Redação mostra os quatro itens com o botão
**Restaurar** e os dias restantes — 30 para os dois movidos hoje, 24 para os
dois que o professor já tinha excluído.
