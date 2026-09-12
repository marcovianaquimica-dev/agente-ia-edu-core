# Phase 9D - Pedagogical Classification Infrastructure

Status: CONCLUIDA COMO INFRAESTRUTURA; CLASSIFICACAO REAL PENDENTE

## Migration 023

- `023_curriculum_taxonomy` adiciona codigo curricular unico e `CatalogNodePrerequisite`.
- A migration foi validada em PostgreSQL descartavel por upgrade, downgrade e re-upgrade.
- Ela e aditiva/reversivel e nao altera `Question`, `QuestionVersion`, `QuestionOption` ou `ContentQuestionLink` existentes.
- Nao foi aplicada ao banco PostgreSQL de desenvolvimento.

## Contrato de Classificacao

- `ClassificationProposal` recebe codigo principal, complementares, conceitos, pre-requisitos, operacoes cognitivas, contexto, dificuldade proposta, confianca e evidencias.
- `ClassificationProposalService` consulta codigos ativos de `CatalogNode`; nunca cria conteudo curricular.
- Codigos inexistentes, evidencia nao verificavel, baixa confianca ou excesso de complementares produzem `NEEDS_REVIEW`.
- O maximo de conteudos complementares e configuravel por `CLASSIFICATION_MAX_COMPLEMENTARY_CONTENTS`.
- O registro reutiliza `PedagogicalClassification`, conserva `classifier_version`, `taxonomy_version`, provider/modelo, prompt_version, hash da questao, evidencias e resultado em metadados auditaveis.

## Estados e Determinismo

- A proposta validada permanece uma proposta (`metadata.proposal_status=PROPOSED`); nao cria `ContentQuestionLink`, nao publica e nao altera `recommended_difficulty`.
- Cada execucao cria registro historico, sem sobrescrever classificacoes anteriores.
- Para mesma questao, mesmo catalogo e mesma versao de classificador, a avaliacao logica e deterministica; timestamps/IDs nao participam da decisao.

## Seguranca e Isolamento

- Texto da questao e tratado como dado; evidencias precisam ser trechos reais desse texto e apontar para codigos existentes.
- Escopo institucional continua sendo aplicado por `PedagogicalUniverseCatalogScope`; esta fase nao habilita classificacao de questoes reais nem acesso entre tenants.
- Professor, coordenacao e diretor terao fluxos de consulta/admin separados em etapa futura; nenhuma tela ou permissao nova foi criada.

## Provas

- SQLite: fixture de Quimica, Fisica, Biologia e Matematica; arvore, caminhos, codigos, primario/complementar, pre-requisito, ciclo, evidencias e confianca.
- PostgreSQL isolado: migration 023, unicidade, pre-requisitos, ciclo e persistencia de proposta sem mutacao da questao.
- Resultado final focal: `49 passed`, 5 avisos de configuracao Alembic.
- `compileall` e `git diff --check`: sucesso.

## Deliberadamente Fora de Escopo

- Nenhuma das 24 questoes ENEM foi classificada, ligada ao catalogo, publicada ou teve dificuldade atualizada.
- As 66 questoes `REVIEW_REQUIRED` nao foram tocadas.
- Nenhuma chamada OpenAI, migration no banco de desenvolvimento ou operacao destrutiva ocorreu.

## Proximo Passo

Phase 9E: aprovar/aplicar a migration 023 ao ambiente de desenvolvimento e executar classificacao controlada, auditavel e revisavel das 24 questoes importadas, usando exclusivamente o catalogo aprovado.

## 9D.1B - Catalogo Promovido ao PostgreSQL

- Baseline confirmado no banco de desenvolvimento: 24 `Question`, 24 `QuestionVersion`, 120 `QuestionOption`, 24 `BookletQuestion`, 24 `AnswerKeyEntry`, zero classificacoes, zero links curriculares, zero nos e zero pre-requisitos.
- `023_curriculum_taxonomy` foi promovida de forma aditiva, sem downgrade, reset ou remocao de dados. Revisao atual: `023_curriculum_taxonomy`.
- O bootstrap controlado da fixture 9C criou 17 nos: Quimica (5), Fisica (4), Biologia (4) e Matematica (4). A fixture aprovada nao contem pre-requisitos a promover, portanto o total permaneceu zero.
- Segunda execucao: zero novos nos e IDs/codigos estaveis. Nenhum filho invalido, orfao ou codigo duplicado foi encontrado.
- As contagens do Question Bank permaneceram 24/24/120; `PedagogicalClassification` e `ContentQuestionLink` continuam zero.
- PostgreSQL isolado e SQLite validaram a migration, unicidade, ciclo de pre-requisito e bootstrap idempotente. Nenhuma classificacao ou chamada OpenAI foi feita.

## 9E - Preparacao do Contrato Real

- `ClassificationProposalService.propose_with_provider()` agora recebe apenas `TextGenerationProvider`; em producao ele sera atendido por `ProviderRouter`, sem client OpenAI no dominio.
- A entrada e canonica e inclui versao, texto, alternativas, hash da questao, catalogo autorizado e versoes de classificador/taxonomia/prompt. `input_hash` e SHA-256 dessa representacao.
- A saida exige JSON estruturado com quatro codigos hierarquicos, confidence `HIGH|MEDIUM|LOW`, evidencia, complementares, catalog gap, dependencia visual e status proposto. `output_hash` e calculado sobre a saida normalizada.
- Codigos inexistentes, hierarquia invalida, evidencia ausente/nao encontrada no texto, `LOW` ou dependencia visual resultam em `NEEDS_REVIEW`; nenhum no, link, dificuldade ou dado da questao e criado/alterado.
- A mesma combinacao de versao, catalogo, versoes e `input_hash` retorna a classificacao existente, evitando propostas duplicadas.
- FakeProvider e ProviderRouter foram comprovados em SQLite. Nenhuma classificacao real, chamada OpenAI ou alteracao no PostgreSQL ocorreu nesta preparacao.