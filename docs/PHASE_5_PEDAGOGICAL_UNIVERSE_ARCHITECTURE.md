# Phase 5 - Bloco 3D.1: Contrato de Pedagogical Universe

Data: 2026-08-31
Escopo: auditoria e decisao arquitetural. Nenhum modelo, rota, servico de runtime, migration ou teste existente foi alterado.

## A) Estado Atual

| Necessidade | Contrato existente | Limite |
| --- | --- | --- |
| Tenant escolar | `School` e `school_id` | Um tenant nao expressa varios universos curriculares autorizados. |
| Papel e escopo | `UserSchoolLink` com `PLATFORM`, `SCHOOL`, `UNIT`, `SEGMENT`, `GRADE_LEVEL`, `CLASSROOM` | Escopo organizacional, nao uma permissao curricular. |
| Identidade | `ExternalIdentityContext` | O provedor entrega identidade e contexto escolar; nao entrega parceiro ou universo ativo. |
| Catalogo | `CatalogNode` generico, arvore livre, `root_id` | Pode representar AREA, DISCIPLINE, CONTENT e SUBCONTENT, mas nao define matricula/universo. |
| Question Bank | `Question`, `QuestionVersion`, classificacoes, `school_id`, visibilidade e metadata | Nao possui pertencimento a universo pedagogico. |
| Exames | `Institution`, `Exam`, `ExamApplication` | Sao origem de item/prova, nao matriz curricular autorizada. |
| Diagnostico | `InitialDiagnostic` e metadata snapshot | Guarda o contexto usado, mas nao consegue resolver um universo autorizado. |
| Contexto ensinado | `TeachingLesson` | Distingue aula registrada de catalogo disponivel; nao autoriza curriculo. |

`metadata_` e adequado para snapshots e configuracao auxiliar, mas nao atende integridade referencial, consulta eficiente de pertencimento, auditoria de alteracao ou resolucao segura de acesso. Dados vindos do cliente nunca podem definir universo, parceiro, escola ou permissao.

## B) Problema Arquitetural

Hoje o motor pode selecionar por escola, escopo, segmento, serie e disciplina, mas nao responde de forma confiavel: "qual conjunto curricular este sujeito esta autorizado a usar?". Isso impede parceiro especializado, multiplos universos por tenant, matriz ENEM versionada e um universo padrao independente sem converter solicitacao do cliente em autorizacao.

Tenant e universo devem continuar distintos: uma escola pode possuir Fundamental, Medio e ENEM; um parceiro pode possuir somente Quimica ENEM; um independente pode ter um universo de produto sem `school_id`.

## C) Alternativas

### Alternativa 1: Configuracao JSON em `School.metadata_` e `InitialDiagnostic.metadata_`

Vantagens: sem novas tabelas; implementacao inicial curta; snapshots ja existem.

Desvantagens: sem FK para catalogo, sem unicidade de slug, sem associacao segura de identidade, consultas de Question Bank dependentes de JSON, historico/auditoria fracos e independente/parceiro sem tenant ficam artificiais. Um request poderia sugerir identificadores que o backend nao consegue validar contra um vinculo formal.

Impacto: baixo em migration, alto em risco de seguranca e manutencao. Nao recomendada para autorizacao curricular.

### Alternativa 2: Reutilizar `School` como parceiro e criar colunas curriculares nele

Vantagens: reutiliza tenant e `UserSchoolLink`; simples para escola B2B.

Desvantagens: mistura tenant, parceiro, curso, produto e matriz; nao representa multiplos universos por escola sem listas JSON; nao atende independentes; cria semantica incorreta para `Institution`, que hoje e fonte de exames; dificulta white label e versionamento de matriz.

Impacto: migration media, mas acoplamento alto. Nao recomendada.

### Alternativa 3: Agregado `PedagogicalUniverse` com relacionamentos normalizados

Vantagens: separa autorizacao curricular de tenant e identidade; suporta varios universos por owner; permite parceiro, produto, curso e independente sem novas taxonomias; garante consulta SQL contextual antes de selecionar questao; versiona matrizes; preserva diagnosticos historicos por snapshot; e extensivel para white label.

Desvantagens: requer migration aditiva, servico de resolucao e administracao/auditoria. Exige definir a integracao do provedor de identidade para contextos nao escolares.

Impacto: migration media e mudancas localizadas em selecao/rotas, com melhor seguranca e escalabilidade. **Recomendada.**

## D) Recomendacao

Criar um agregado generico `PedagogicalUniverse`; nao criar `partner_id` isolado. O owner deve ser polimorfico de forma controlada, usando `owner_type` e `owner_external_id`:

- `SCHOOL`: owner externo e `School.id`.
- `PARTNER`: identificador estavel de parceiro fornecido pelo host/administracao.
- `PRODUCT` ou `COURSE`: identificador estavel do produto/curso.
- `PLATFORM`: universo global administrado pela plataforma, inclusive padrao independente.

`partner_id` pode ser introduzido futuramente somente se parceiro adquirir ciclo de vida administrativo proprio. Hoje ele seria uma entidade vazia e duplicaria o conceito de owner.

## E) Modelo Conceitual

### `PedagogicalUniverse`

Campos obrigatorios:

- `id` UUID.
- `external_id` e `slug`, unicos e estaveis para integracao/URL interna.
- `name`, `status` (`DRAFT`, `ACTIVE`, `ARCHIVED`).
- `owner_type` (`PLATFORM`, `SCHOOL`, `PARTNER`, `PRODUCT`, `COURSE`).
- `owner_external_id`, obrigatorio exceto `PLATFORM`.
- `default_for_context`, para resolver o universo padrao dentro do owner/contexto.
- `configuration_version`, para matriz/versionamento sem alterar significado passado.
- `configuration` JSON apenas para parametros nao relacionais: marca futura, textos, pesos de cobertura, limites do motor e regras de apresentacao.
- timestamps e auditoria por evento administrativo existente ou futuro.

Campos normalizados e relacionais:

- `PedagogicalUniverseCatalogScope`: `universe_id`, `catalog_node_id`, `scope_kind` (`AREA`, `DISCIPLINE`, `CONTENT`), `include_descendants`. Esta e a fonte de verdade para questao/recurso pertencer ao universo. Usa `CatalogNode`; nao duplica disciplina, area ou conteudo.
- `PedagogicalUniverseAcademicScope`: `universe_id`, `segment`, `grade_level`, `unit_id` opcional, `active`. Segmento/serie continuam identificadores externos ate existir cadastro academico canonico; nao criar tabela paralela agora.
- `PedagogicalUniverseBinding`: `universe_id`, `subject_type` (`SCHOOL`, `EXTERNAL_IDENTITY`, `PRODUCT_CONTEXT`), `subject_external_id`, `active`, `priority`. E a autorizacao para resolver universo; um usuario pode ter varios, mas nao escolhe um sem binding.
- `PedagogicalUniverseMatrixVersion`: opcional para fase posterior se varias versoes de uma mesma matriz precisarem conviver como entidades publicadas. Enquanto uma versao por universo bastar, `configuration_version` e snapshot sao suficientes.

Indices futuros: `(owner_type, owner_external_id, status)`, `slug`, `(universe_id, catalog_node_id)`, `(subject_type, subject_external_id, active)`, `(universe_id, segment, grade_level, active)`.

Constraints futuras: unicidade de binding ativo por `(universe_id, subject_type, subject_external_id)`; unicidade de escopo de catalogo; check enums; no maximo um `default_for_context` ativo por owner/contexto, via indice parcial PostgreSQL.

## F) Areas, Disciplinas e Matrizes

`CatalogNode` continua a arvore canonica. Convencao proposta:

`AREA -> DISCIPLINE -> CONTENT -> SUBCONTENT`

`node_type` ja e livre e suporta `AREA`; `root_id` pode apontar para a raiz curricular escolhida. A migration futura nao deve criar outra taxonomia. Para catalogos legados cuja raiz e disciplina, a matriz pode vincular disciplinas diretamente; novos catalogos ENEM podem vincular AREAs e usar `include_descendants=True`.

ENEM e uma configuracao/versionamento de universo, por exemplo `ENEM_2026`, com escopos de area/disciplina no catalogo. O motor recebe os nós autorizados da configuracao; nunca deve conter uma lista fixa de quatro areas.

## G) Regras de Resolucao e Seguranca

Ordem deterministica:

1. Identidade autenticada.
2. Tenant/escola e papel/escopo resolvidos por `AuthorizationService` e `UserSchoolLink`.
3. Bindings ativos do sujeito/escola/produto.
4. Universo ativo por prioridade/default, ou universo explicitamente solicitado somente se pertencer aos bindings autorizados.
5. Contexto escolar da identidade: unidade, segmento, serie, turma, ano.
6. Objetivo e perfil pedagogico; eles apenas restringem/priorizam dentro do universo.
7. Area, disciplina, conteudo, dificuldade e questao.

Conflitos: a solicitacao e registrada em `InitialDiagnostic.metadata_.entry_profile`, mas o resolvedor retorna somente o universo autorizado. Exemplo: universo Quimica ENEM + objetivo Matematica resulta em `unmet_intent` no snapshot e selecao continua limitada a Quimica.

O resolvedor precisa ser servidor-side. `school_id`, `partner_id`, `universe_id`, disciplina, serie e texto livre do payload nao podem ampliar owner, binding, escopo de catalogo ou contexto academico.

## H) Historico, Auditoria e Diagnostico

Ao iniciar, `InitialDiagnostic.metadata_.context_snapshot` deve congelar:

- `universe_id`, `external_id`, `configuration_version` e owner.
- escopos de catalogo/academicos efetivos ou uma referencia de versao publicada.
- contexto escolar autenticado e objetivos aceitos/rejeitados.

Uma alteracao administrativa cria nova versao publicada ou nova configuracao auditada; nunca reinterpreta uma selecao historica. `DiagnosticQuestionSelection` ja fixa versao da questao, e o snapshot completa a reprodutibilidade curricular.

Mudancas de universo devem gerar `AdminAuditLog` com ator, anterior, novo e motivo. Na implementacao futura, a atualizacao de universo deve ser transacional e bloquear alteracao de versao ativa que possua diagnosticos iniciados; publica-se versao nova.

## I) Impacto nas Entidades Existentes

| Superficie | Impacto futuro |
| --- | --- |
| `School` | Permanece tenant; pode ser owner/binding de varios universos. |
| `UserSchoolLink` | Continua papel e escopo organizacional; nao vira matriz curricular. |
| Identidade externa | Deve fornecer `product_context`/parceiro autenticado quando aplicavel; nenhum usuario local e criado. |
| `CatalogNode` | Reutilizado para area, disciplina e conteudo. |
| Question Bank | Consulta junta escopos de universo ao selecionar candidatos, antes de carregar questoes. |
| Diagnostico | Recebe universo resolvido e snapshot imutavel. |
| Learning Path, busca, pratica, materiais e listas | No futuro consultam o mesmo resolvedor/escopo de catalogo. |
| White label | `configuration` pode conter nome comercial, logo, paleta, dominio, cabecalho e rodape, sem afetar autorizacao. |

## J) Migration Futura Necessaria

Uma migration sera necessaria para a alternativa recomendada. Ela deve ser aditiva a partir da head vigente e criar somente `pedagogical_universes`, `pedagogical_universe_catalog_scopes`, `pedagogical_universe_academic_scopes` e `pedagogical_universe_bindings`.

Nao alterar migrations historicas. Nao adicionar `partner_id` em `Question`, `InitialDiagnostic` ou `School` nesta primeira migration: o universo e a fronteira curricular e o snapshot de diagnostico ja permite historico imediato. Uma FK opcional de `InitialDiagnostic.universe_id` so deve ser avaliada depois de uma estrategia de backfill e compatibilidade para diagnosticos existentes.

## K) Testes Futuros

1. Escola com dois universos, Fundamental e Medio, resolve o correto pelo contexto autorizado.
2. Parceiro Quimica ENEM seleciona somente descendentes de Quimica e bloqueia objetivo Matematica.
3. Parceiro Matematica e escola com universo restrito nao vazam questoes entre universos.
4. Independente resolve apenas universo plataforma/binding explicitamente autorizado.
5. ENEM_2026 seleciona áreas/disciplinas configuradas, sem hardcode no motor.
6. Um aluno com dois bindings recebe default deterministico ou seletor permitido, nunca ID arbitrario.
7. Segmento, serie, unidade e turma restringem candidatos antes da selecao.
8. Questao publicada, mas fora do catalog scope, nao entra no diagnostico/pratica/busca.
9. Alterar configuracao cria versao auditada e diagnostico iniciado preserva snapshot anterior.
10. HTTP: payload com `universe_id`, `partner_id` ou `school_id` externo retorna 403/ignora sem mutacao.
11. Linguagem natural registra intencao, mas nao altera binding, universo ou permissoes.
12. White-label configuration e lida somente apos universo autorizado ser resolvido.

## L) Riscos

- O provedor host precisa entregar um `product_context` autenticado para parceiros sem `school_id`; sem isso, o backend nao pode distinguir parceiro de aluno independente.
- A convencao AREA no catalogo deve ser migrada/adotada gradualmente; catalogos atuais de raiz DISCIPLINE permanecem compativeis por escopo direto.
- `configuration` nao pode virar fonte de controle de acesso sem validacao/versionamento administrativo.
- Consultas de candidatos devem usar joins/exists pelos scopes autorizados para evitar buscar e filtrar em Python.

## M) Proximo Bloco

Com aprovacao deste contrato, o proximo trabalho deve ser uma implementacao incremental do schema e de um `PedagogicalUniverseResolver`, seguida de integracao no seletor diagnostico. Nao avancar para interface, dashboard, devolutiva, TRI ou MIRT.