# Phase 9A.1 - Visual Asset Preservation

Status: CONCLUIDO PARA O PILOTO TECNICO

## Regra Aplicada

Elementos visuais nunca sao reconstruidos, OCRizados ou alterados durante a ingestao. Texto continua sendo extraido separadamente; raster preserva seus bytes quando disponivel e elementos vetoriais/ambiguos preservam evidencia da pagina original.

## Camadas do Parser

1. `pypdf` extrai a camada de texto, numero, alternativas e paginas.
2. Imagens raster incorporadas sao extraidas em bytes originais, com SHA-256, MIME, tamanho, pagina e associacao quando segura.
3. Referencias a figura, grafico, tabela, esquema ou conteudo vetorial sem raster extraivel produzem `PAGE_REGION` com referencia ao PDF original, hash do documento e pagina.
4. Associacoes por mesma pagina sem bounding box sao marcadas como nao confiaveis e exigem revisao; nenhuma regiao e adivinhada.

## Piloto ENEM 2020

- Documento oficial: 2o dia, Caderno 5 Amarelo, em armazenamento local ignorado.
- O PDF nao apresentou imagens raster via `pypdf`; seus visuais sao tratados como vetoriais ou referencias de pagina.
- 21 assets `PAGE_REGION` foram registrados como evidencia. Eles apontam para bytes do PDF original, nao para thumbnail ou reconstrução.
- Questoes dependentes de ativos ou de estrutura textual incompleta permanecem `requires_review`.

## Persistencia Intermediaria

- `IngestionAsset` recebe o tipo permitido existente (`OTHER` para `PAGE_REGION`) e metadados: tipo visual original, hash, MIME, tamanho e confianca da associacao.
- O arquivo original e o `storage_uri`; nao ha thumbnail nesta etapa e nenhum asset original e substituido.
- O modelo suporta questoes multipagina por `page_start`/`page_end`; a deteccao precisa de continuidade visual fica para revisao posterior, sem inferencia automatica.

## Validacao

- `tests/test_enem_pdf_parser.py` e `tests/test_ingestion.py`: `40 passed`.
- `compileall` e `git diff --check`: sucesso.
- Nenhuma chamada OpenAI, migration ou escrita no banco principal ocorreu.

## Limitacoes

- `pypdf` nao fornece bounding boxes confiaveis para associacao de objetos vetoriais neste caderno.
- A proxima etapa segura e adicionar uma analise de layout por pagina que registre regioes apenas quando a geometria for confiavel, mantendo `PAGE_REGION` como fallback.