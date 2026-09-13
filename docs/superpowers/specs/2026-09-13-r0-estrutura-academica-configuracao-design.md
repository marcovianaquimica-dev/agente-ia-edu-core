# R0 — Estrutura acadêmica e configuração da instituição

**Data:** 2026-09-13
**Status:** design aprovado, aguardando plano de implementação
**Sub-projeto:** R0 da PLATAFORMA REDAÇÃO (módulo `REDACAO_IA`)
**Antecessor:** R1 — régua ENEM versionada e contrato do motor (em PR)

---

## 1. Contexto

R1 entregou a régua e o contrato. R0 entrega o chão em que a plataforma pisa: quem é aluno,
em que turma, de qual ano letivo, sob quais regras a escola opera, e com qual marca a
devolutiva sai.

Diferente de R1, **R0 não é greenfield.** Ele substitui andaime que 12+ arquivos já consomem,
incluindo o serviço de autorização. O custo real não são as tabelas novas; é migrar
consumidores sem abrir uma janela em que a autorização fique ambígua.

### Dois achados que moldaram o design

**O código já antecipava esta tarefa.** `services/authorization.py` declara em docstring que
mantém "uma camada de compatibilidade em torno do estilo `external_user_id` já existente,
enquanto fornece um objeto de contexto mais explícito para futura autenticação SaaS
multi-tenant". R0 é esse futuro.

**Existe andaime com turma fixa no código.** Em `services/teacher_portal.py`, linhas 162 e
179, quando não há dados o serviço devolve `["TURMA_3A", "TURMA_3B"]` — strings literais. É
uma função de **autorização** devolvendo turmas inventadas. Hoje protege um ambiente de
desenvolvimento; num tenant real sem dados, decide quem vê o quê.

---

## 2. Escopo

### Entrega

1. A estrutura acadêmica como entidades reais: pessoa, usuário, ano letivo, unidade,
   segmento, série, turma, aluno, matrícula e transição de matrícula.
2. A configuração da instituição: modo de correção, política de validação, identidade visual
   versionada.
3. O portão de disciplina, completando um mecanismo que já existe e não restringe nada.
4. A migração dos consumidores atuais, em ordem definida, terminando na autorização.

### Não entrega — deliberadamente

- **Nenhuma credencial.** O core não guarda senha, token ou segredo. Ver §3.1.
- **Nenhuma tela.** R0 é modelo, serviço e migração.
- Nenhum fluxo de aprovação de correção — isso é R3. R0 guarda o **padrão e o permitido**; a
  escolha por turma acontece lá.

---

## 3. Decisões do brainstorming

### 3.1 Um princípio arquitetural foi revogado, e o registro importa

`identity.py` declara, em docstring, que o módulo "evita intencionalmente qualquer tabela
local de usuários", que "a plataforma hospedeira é a fonte de verdade para autenticação e
credenciais", e que o core "apenas recebe um contexto de identidade verificado contendo
identificadores externos estáveis para o aluno, o professor, a instituição, a unidade, a
série e a turma".

**A segunda metade dessa frase deixa de valer.** O core passa a ser dono de unidade,
segmento, série, turma e matrícula.

**A primeira metade continua valendo integralmente.** O core não guarda credencial de espécie
alguma. Ter `Person` e `StudentEnrollment` não exige guardar senha, e a spec da REDAÇÃO §21
separa "pessoa, usuário, aluno e matrícula" exatamente assim: um `User` que aponta para uma
identidade externa não viola nada.

**Requisito de implementação:** a docstring de `identity.py` deve ser atualizada dizendo o que
mudou, quando e por quê. Princípio revogado é diferente de princípio esquecido, e quem abrir
aquele arquivo daqui a um ano precisa saber qual dos dois aconteceu.

**Por que a revogação:** a REDAÇÃO precisa de turma real para dashboard por turma (§11),
escopo de professor validado no servidor (§11), identificação de aluno em lote (§14) e
histórico por matrícula com transição entre anos (§13, §22). Depender de strings vindas de um
hospedeiro para decidir autorização é frágil de um jeito que não aparece em teste.

### 3.2 Migração aditiva com ponte de `external_id`

As entidades novas são a verdade. Cada uma ganha uma coluna `external_id` opcional que
preserva o identificador antigo. O código atual continua funcionando, e os consumidores migram
um a um. A alternativa — corte seco — mudaria 12+ arquivos no mesmo commit, incluindo
autorização, e um erro ali não quebra: mostra dado errado para a pessoa errada.

### 3.3 O portão de disciplina completa um mecanismo existente

`pedagogical_universes` já tem a forma exata do que a restrição por disciplina precisa:
`owner_type` aceita `SCHOOL` com `owner_external_id`, e `pedagogical_universe_catalog_scopes`
tem `scope_kind IN ('AREA', 'DISCIPLINE', 'CONTENT')`.

Mas é consumido por apenas dois serviços — `initial_diagnostic` e `domain_map` — e **não** por
autorização, banco de questões ou busca de estudo. **O mecanismo existe e não restringe nada.**
R0 o completa, em vez de criar um segundo conceito de escopo que um dia discordaria do
primeiro.

### 3.4 Dois controles, não três

O brainstorming considerou três modos de instituição. Sobraram dois, porque o terceiro
resolvia com modo aquilo que a validação resolve melhor:

- `correction_mode` diz **se existe nota**: `FORMATIVO` não produz nota nenhuma, `AVALIATIVO`
  produz.
- A política de validação diz **quem aprova**, e só existe no avaliativo.

### 3.5 A configuração mora em colunas tipadas, não em JSON livre

R1 provou o custo da alternativa, e provou caro: a revisão final encontrou que `provenance`
tinha CHECK em uma coluna de três, e que validar no carregador não bastava — porque o seed
gravava por hardcode e a constraint nunca disparava. A lição não foi "valide melhor no código";
foi **o que tem regra, o banco recusa**.

Aqui a aposta é maior que na régua. Uma instituição com modo gravado errado não é um dado feio:
é uma escola operando em avaliativo achando que está em formativo, no exato controle que existe
por razão regulatória.

---

## 4. Modelo de dados — estrutura acadêmica

Convenções do repositório: `Mapped`/`mapped_column`, `Uuid`, `JSONBCompatible`, `metadata_`
mapeado para a coluna `metadata`, `__table_args__` com `CheckConstraint` e `Index`.

**Toda tabela desta seção tem `external_id` opcional, único por escola** — é a ponte da
§3.2. A exceção é `users`, cuja ponte já é o `external_user_id` que a coluna nomeia; ela
não ganha uma segunda.

### 4.1 Identidade

| Tabela | Papel | Colunas principais |
|---|---|---|
| `persons` | Um ser humano | `school_id`, `full_name`, `document_number`, `email`, `phone` |
| `users` | Uma conta | `person_id`, `external_identity_provider`, `external_user_id`, `status` |

**Nenhuma coluna de credencial em `users`.** Sem `password_hash`, sem `token`, sem `secret`. A
tabela existe para dar identidade estável e local a quem o hospedeiro autentica.

`persons` e `users` são separados porque, **dentro de uma escola**, uma professora que também
é mãe de aluno é uma pessoa com duas relações, e um funcionário que vira responsável é a
mesma pessoa em dois papéis. Colapsar isso funciona até o primeiro caso real, e aí não se
separa retroativamente.

**Entre escolas, não.** `persons` é escopada por `school_id`, então a mesma pessoa física
matriculada em duas escolas tem duas linhas. Isso é deliberado: o isolamento entre tenants
vale mais que a deduplicação de pessoas, e uma tabela de pessoas compartilhada entre escolas
vazaria quem estuda onde. Se um dia houver razão para reconciliar identidades entre tenants,
será um sub-projeto com sua própria discussão de privacidade — não um efeito colateral deste.

### 4.2 Hierarquia

| Tabela | Pertence a | Notas |
|---|---|---|
| `academic_years` | `schools` | `year`, `starts_on`, `ends_on`, `status` em (`PLANNED`,`ACTIVE`,`CLOSED`) |
| `school_units` | `schools` | Unidade ou campus |
| `segments` | `schools` | Fundamental I, Fundamental II, Médio |
| `grade_levels` | `segments` | 1ª, 2ª, 3ª série; `ordinal` |
| `classes` | `academic_years` **e** `grade_levels` | Opcionalmente `school_unit_id` |

`classes` pertence ao ano letivo porque a spec da REDAÇÃO §13 é explícita: **"1ª Série A - 2026"
e "1ª Série A - 2027" são entidades diferentes.** Sem isso o histórico do aluno vira uma pilha
de registros sem contexto.

### 4.3 Aluno e matrícula

| Tabela | Papel |
|---|---|
| `students` | Liga uma `person` a uma escola. `student_code`, `status` |
| `student_enrollments` | Matrícula de um `student` numa `class`. `enrolled_on`, `status` em (`ACTIVE`,`TRANSFERRED`,`EXITED`,`COMPLETED`) |
| `enrollment_transitions` | `from_enrollment_id`, `to_enrollment_id` (nulo na saída), `kind` em (`PROMOTED`,`RETAINED`,`TRANSFERRED`,`EXITED`), `decided_by_user_id`, `decided_at`, `reason` |

`enrollment_transitions` existe para que a passagem de ano seja **um fato registrado**, não um
UPDATE que apaga o estado anterior. É o mesmo princípio que versiona a régua em R1: o que
aconteceu precisa continuar legível depois que as coisas mudarem.

UNIQUE(`student_id`, `class_id`). Um aluno tem no máximo uma matrícula por turma.

### 4.4 O que NÃO é criado

**Não existe `StaffAssignment`.** `user_school_links` já existe, já tem papel e escopo, e já é
consultado pela autorização. Criar uma tabela paralela produziria dois jeitos de dizer quem
enxerga o quê, e o que decidiria de verdade seria o mais antigo.

`user_school_links` **ganha colunas FK opcionais** — `user_id`, `school_unit_id`, `segment_id`,
`grade_level_id`, `class_id` — ao lado do `scope_external_id` que já tem. Essa é a ponte da §3.2
na prática, e é aditivo: nenhuma linha existente muda.

---

## 5. Modelo de dados — configuração da instituição

### 5.1 `school_settings`

Uma linha por escola.

| Coluna | Tipo | Notas |
|---|---|---|
| `school_id` | UUID FK, UNIQUE | |
| `correction_mode` | VARCHAR(20) | CHECK IN (`FORMATIVO`, `AVALIATIVO`) |
| `validation_default` | VARCHAR(20) | CHECK IN (`UMA_A_UMA`, `EM_LOTE`, `AUTOMATICA`); NULL em formativo |
| `validation_teacher_can_disable` | BOOLEAN | Se o professor pode dispensar a validação |
| `validation_threshold_points` | INTEGER | Abaixo deste valor, validação obrigatória; NULL desliga a trava |
| `current_identity_version_id` | UUID FK | Identidade visual vigente |
| `metadata` | JSONB | |

Duas CHECK amarram o modo à política:

```sql
CHECK (correction_mode = 'AVALIATIVO' OR validation_default IS NULL)
CHECK (correction_mode = 'AVALIATIVO' OR validation_threshold_points IS NULL)
```

**A política aqui é o padrão e o permitido, não a decisão.** A escolha real acontece por turma,
quando o professor envia a proposta — isso é R3. Uma escola pode autorizar o professor a
desligar a validação; outra pode fixar que não.

> **Registro da decisão sobre validação opcional.** Em 13/09/2026 o usuário decidiu que o
> professor escolhe, por turma, se quer validação manual — com o risco regulatório à vista e as
> alternativas discutidas. As diretrizes do CNE aprovadas em 01/09/2026 têm, numa das leituras
> publicadas, vedação imediata ao uso de IA para atribuir nota sem supervisão humana; noutra
> leitura vedam até a pré-correção que apresenta ao professor uma sugestão numérica. As
> leituras divergem e o texto oficial não está publicado. `FORMATIVO` existe justamente como a
> configuração defensável em qualquer das leituras.

### 5.2 `school_identity_versions`

| Coluna | Tipo |
|---|---|
| `school_id` | UUID FK |
| `version` | INTEGER, crescente por escola |
| `display_name` | VARCHAR(255) |
| `logo_asset_uri` | TEXT |
| `primary_color`, `secondary_color` | VARCHAR(9), CHECK de formato hexadecimal |
| `published_at` | TIMESTAMPTZ |
| `published_by_user_id` | UUID FK |

UNIQUE(`school_id`, `version`).

**A identidade é versionada pela mesma razão que a régua é.** A spec §8 exige que regerar o PDF
de uma devolutiva antiga não reescreva o documento que a família recebeu. Se a escola trocar de
logomarca em março, a devolutiva de fevereiro continua saindo com a marca de fevereiro. A
devolutiva carimba o `identity_version_id` vigente, exatamente como carimba a `rubric_version`.

**Uma versão publicada é imutável.** Trocar a identidade cria versão nova; nunca edita a
anterior.

**Consequência:** o arquivo de logo é o primeiro caso real de object storage no core, que hoje
só tem cópia local em `services/material_storage.py`. R0 cria a menor abstração necessária, no
espírito daquele módulo, e documenta a limitação.

### 5.3 Auditoria

**Reusa `admin_audit_logs`**, que já existe com `school_id`, autor e metadata. Nenhuma tabela de
auditoria nova.

O que R0 acrescenta é a garantia de que toda escrita em `school_settings` e em
`school_identity_versions` passa pelo serviço que registra — nunca por UPDATE direto. Quem mudou
o modo de uma escola, quando, e de que valor para qual, tem de ser respondível.

---

## 6. O portão de disciplina

A escolha do gestor master cria ou vincula um `PedagogicalUniverse` com `owner_type = 'SCHOOL'`
e escopos `DISCIPLINE`, e R0 liga esse mecanismo aos três serviços que hoje o ignoram:
autorização, banco de questões e busca de estudo.

### A regra que precisa estar escrita

**Escola sem universo não tem restrição.** Todas as escolas existentes estão nesse estado. Um
portão implementado ingenuamente as trancaria fora no dia do deploy.

Ausência de universo significa acesso a tudo; restrição só existe quando alguém a declarou. Isto
é requisito explícito, não detalhe de implementação — é exatamente o tipo de coisa que um
implementador resolve do jeito "mais seguro" e derruba a base inteira.

---

## 7. Migração

A ordem não é negociável.

1. **Entidades e configuração.** Puramente aditivo. Nada quebra, nada muda de comportamento.
2. **Colunas FK em `user_school_links`.** Aditivo, todas anuláveis.
3. **Resolução de `external_id` para entidade.** Um serviço, testado sozinho, sem consumidor ainda.
4. **Consumidores de leitura, um a um.** Cada um com seu commit e seu teste.
5. **Autorização, sozinha, por último.** Num commit que não faz mais nada.
6. **Remoção do andaime `TURMA_3A`**, junto do passo 5 ou logo depois — nunca misturado com
   outra mudança, para que a causa de qualquer regressão seja identificável.
7. **Fim da transição:** `external_id` que não resolve passa a ser erro em vez de `None`. Commit
   próprio.

**Durante a transição**, um `external_id` que não resolve devolve `None` e o chamador segue pelo
caminho antigo. A mudança desse regime é o passo 7, não um efeito colateral de nenhum passo
anterior.

---

## 8. Testes

A suíte de R1 foi construída para provar que coisas certas acontecem. **Aqui a maior parte do
valor está em provar que coisas erradas não acontecem**, e a razão é específica:

**Um bug de autorização não quebra.** Não há exceção, não há teste vermelho. O sistema continua
respondendo — para a pessoa errada.

### Testes negativos, que são o núcleo

- Professor da turma A **não** vê aluno da turma B.
- Coordenador de uma unidade **não** vê outra unidade.
- Escola restrita a Química **não** recebe questão de Biologia na busca nem no banco.
- Professor **não** vê turma de ano letivo fora do seu escopo.
- Aluno **não** vê devolutiva de outro aluno.

### Testes positivos que protegem a migração

- **Escola sem universo vê tudo** — o caso que um portão ingênuo quebraria.
- `external_id` conhecido resolve para a entidade certa.
- `external_id` desconhecido devolve `None` durante a transição, e erro depois do passo 7.
- Turmas de mesmo nome em anos diferentes são entidades distintas, com históricos distintos.
- Transição de matrícula preserva a matrícula anterior legível.

### Estrutural

- `users` não tem nenhuma coluna cujo nome sugira credencial. Um teste que varre as colunas e
  falha se aparecer `password`, `token`, `secret` ou `credential` — barato, e transforma o
  princípio da §3.1 em algo que o CI recusa em vez de algo que alguém lembra.
- Escrita em `school_settings` fora do serviço não registra auditoria: teste que prova que o
  serviço registra, e revisão que garante que ninguém escreve fora dele.
- Identidade visual publicada é imutável.

### Convenções

`unittest.IsolatedAsyncioTestCase` com `sqlite+aiosqlite:///:memory:` e `StaticPool`, o padrão de
`tests/test_platform_administration.py`. Não existe `conftest.py` no projeto.

---

## 9. Critérios de aceite

- [ ] As dez entidades acadêmicas existem — `persons`, `users`, `academic_years`,
      `school_units`, `segments`, `grade_levels`, `classes`, `students`,
      `student_enrollments`, `enrollment_transitions` — todas com `external_id` opcional e
      único por escola, exceto `users`, que usa o `external_user_id` que já tem.
- [ ] `users` não tem coluna de credencial, e um teste recusa a introdução de uma.
- [ ] A docstring de `identity.py` registra a revogação parcial do princípio, com data e razão.
- [ ] `classes` pertence a `academic_years`; duas turmas homônimas de anos diferentes são
      entidades distintas.
- [ ] `enrollment_transitions` preserva a matrícula anterior; nada é sobrescrito.
- [ ] `user_school_links` ganhou FKs anuláveis; nenhuma linha existente foi alterada.
- [ ] `school_settings` recusa política de validação em modo formativo.
- [ ] Identidade visual publicada é imutável; trocar cria versão nova.
- [ ] Toda escrita em configuração registra em `admin_audit_logs`.
- [ ] Escola sem universo pedagógico continua vendo tudo.
- [ ] Escola restrita a uma disciplina não recebe conteúdo de outra na busca, no banco nem na
      autorização.
- [ ] O andaime `TURMA_3A` não existe mais no código.
- [ ] Os testes negativos da §8 passam, e cada um falha se sua verificação for removida.
- [ ] Nenhuma migration altera tabela existente de forma destrutiva.

---

## 10. Requisitos capturados aqui, implementados em outro sub-projeto

- **R2** — `EssayPrompt`, `PromptMaterial`, `PromptAssignment`. A escolha de validação por turma
  pendura em `PromptAssignment`.
- **R3** — o fluxo de aprovação em si, lendo o padrão e o permitido de `school_settings`. Toda
  escolha de desligar a validação é registrada com autor, papel, data, turma e proposta, de
  forma imutável e consultável.
- **R4** — a devolutiva carimba o `identity_version_id` vigente, além da `rubric_version`.
- **R8** — `RubricView` precisa ser estendida para carregar as regras de pontuação; hoje o teto
  `LIMITA_PONTUACAO` da régua ENEM é dado sem caminho de código que o aplique.

- **R3 e R8 — quantas IAs corrigem, e quando.** Pergunta levantada em 13/09/2026 e ainda
  em aberto; registrada aqui para não ser redecidida do zero.

  **O core já tem o mecanismo.** `services/classification_consensus.py` existe, é usado por
  três serviços, e abre declarando o princípio: *a IA propõe, o sistema decide*. Ele roda o
  mesmo pipeline N vezes (`DEFAULT_N = 3`), é independente de fornecedor — depende só do
  contrato `TextGenerationProvider` — e não escreve nada no banco, porque cada execução roda
  em transação revertida. Há também um `ProviderRouter` que roteia entre provedores. **Quem
  escrever R3 deve reusar isso, não reinventar.**

  **Mas a regra atual não transfere.** O consenso existente exige **unanimidade**, o que
  funciona para classificar uma questão numa categoria e rejeitaria quase toda redação: três
  execuções dando 160, 160 e 120 em C1 não são discordância de categoria, são variância num
  julgamento gradual. A métrica certa já está na spec do produto §16 — *percentual dentro de
  ±40 pontos*, concordância dentro de um nível, não identidade.

  **Duas perguntas distintas, fáceis de confundir.** Rodar o mesmo modelo N vezes mede
  **estabilidade**; consultar modelos diferentes mede **concordância**. Um modelo com viés
  sistemático — generoso em C5, digamos — passa no primeiro teste e falha no segundo. Para
  calibração contra o Banco Ouro, a segunda é a que importa.

  **Recomendação registrada:** passe único por padrão, com escalonamento por gatilho em vez
  de sempre-N. Os gatilhos já existem no material construído: `confidence` por competência no
  contrato do motor, os alertas da régua, divergência grande entre competências, e os casos
  limítrofes entre níveis que a §16 manda amostrar. Sempre-N triplica o custo por redação;
  escalonar paga o triplo só onde ele compra algo.

  **Alerta que não pode se perder:** múltiplas IAs **não mudam nada** no quadro regulatório
  do CNE. Três modelos concordando continua sendo IA atribuindo a nota. Consenso melhora
  confiabilidade, não conformidade, e tratar um como solução do outro seria um erro caro.

---

## 11. Fontes

**Produto.** *REDAÇÃO • Especificação Completa v1.0* (setembro de 2026), §8, §11, §13, §14, §21,
§22.

**Código existente auditado.** `services/authorization.py`, `services/teacher_portal.py`,
`identity.py`, `db/models/admin.py`, `db/models/pedagogical_universe.py`,
`db/models/catalog.py`, `services/material_storage.py`.

**Antecessor.** `docs/superpowers/specs/2026-09-13-r1-regua-enem-contrato-motor-design.md`.

**Regulatória.** Diretrizes do CNE aprovadas em 01/09/2026, aguardando homologação do MEC.
Cobertura jornalística; o parecer não está publicado. Ver §5.1.
