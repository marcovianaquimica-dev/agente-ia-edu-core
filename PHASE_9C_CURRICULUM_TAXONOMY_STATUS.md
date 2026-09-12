# Phase 9C - Curriculum Taxonomy

Status: ESPECIFICACAO E FIXTURE SQLITE CONCLUIDAS; POSTGRESQL E CLASSIFICACAO PENDENTES

## Modelo

- A arvore controlada usa `CatalogNode` com os niveis `DISCIPLINE -> AREA -> CONTENT -> SUBCONTENT`.
- Cada no possui um `code` estavel globalmente unico, nome legivel, posicao deterministica, estado `active`, um unico pai e `root_id` da disciplina.
- Somente `DISCIPLINE` pode ser raiz. Tipos de pai/filho sao validados pelo servico para impedir arvores ambiguas.
- Inativacao preserva identificador e historico; recriar um conteudo com codigo novo para o mesmo conceito nao e permitido sem revisao explicita.

## Fixture SQLite

- Quimica: Fisico-Quimica -> Solucoes -> Concentracao e Diluicao de solucoes.
- Fisica: Mecanica -> Cinematica -> Movimento uniforme.
- Biologia: Citologia -> Bioquimica celular -> Proteinas.
- Matematica: Algebra -> Funcoes -> Razao e proporcao.

## Question Bank

- `ContentQuestionLink` permanece N:N: uma versao de questao pode ter conteudo principal e complementares.
- O principal e registrado em `QuestionVersion.metadata.primary_content_node_id`; os complementares sao links adicionais. Nenhuma questao cria nos curriculares.
- Conceitos relacionados, operacao cognitiva, contexto, confianca e dificuldade pertencem a `PedagogicalClassification`, nao a arvore.

## Pre-requisitos

- `CatalogNodePrerequisite` representa `content -> requires -> prerequisite` com unicidade por par e bloqueio SQL de auto-referencia.
- `CurriculumTaxonomyService` percorre a relacao antes de inserir e rejeita ciclos transitivos.
- Isso prepara a futura Trilha de Aprendizagem sem implementar motor de dominio ou recomendacao novo.

## Isolamento e Administracao Futura

- O catalogo curricular e global/controlado e independente de escola; isolamento ocorre por `PedagogicalUniverseCatalogScope` e permissoes existentes.
- Professor consulta somente nos liberados pelo universo. Coordenacao podera administrar escopo institucional apos workflow explicito. Diretor permanece na camada gerencial/configuracao institucional.

## Migration

- `023_curriculum_taxonomy` adiciona somente unicidade de codigo e a tabela de pre-requisitos, com upgrade/downgrade reversivel.
- Nao foi aplicada ao PostgreSQL de desenvolvimento nesta etapa.

## Validacao e Limites

- `tests/test_curriculum_taxonomy.py`, `tests/test_catalog.py` e `tests/test_content_resolution.py`: `50 passed` em SQLite.
- Nenhuma questao ENEM foi classificada, alterada ou vinculada a conteudo. Nenhum `CatalogNode` foi criado no PostgreSQL.
- OpenAI nao foi usado.

## Proximo Passo

Validar `023_curriculum_taxonomy` em PostgreSQL isolado e definir o workflow de classificacao pedagogica auditavel para as questoes ja importadas, sem classifica-las automaticamente nesta fase.