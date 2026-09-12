# Phase 9A - Official Inventory and PDF Pilot

Status: PILOTO TECNICO CONCLUIDO; IMPORTACAO DEFINITIVA NAO INICIADA

## Fonte Oficial

- Indice primario: `https://www.gov.br/inep/pt-br/areas-de-atuacao/avaliacao-e-exames-educacionais/enem/provas-e-gabaritos`.
- Cada edicao tem pagina oficial em `/provas-e-gabaritos/{ano}`.
- O INEP disponibiliza prova (`PV`) e gabarito (`GB`) separados por dia e caderno. Desde 2009, cada area objetiva possui 45 itens.
- A pagina do governo informa licenca CC BY-ND 3.0 para seu conteudo. PDFs brutos nao foram versionados nem publicados pelo projeto; uso e redistribuicao exigem avaliacao juridica antes de qualquer distribuicao de itens.

## Inventario 2020-2025

| Ano | Pagina oficial | Prova Natureza/Matematica impressa padrao | Gabarito correspondente |
|---|---|---|---|
| 2020 | `/provas-e-gabaritos/2020` | `2020_PV_impresso_D2_CD5.pdf` | `2020_GB_impresso_D2_CD5.pdf` |
| 2021 | `/provas-e-gabaritos/2021` | `2021_PV_impresso_D2_CD5.pdf` | `2021_GB_impresso_D2_CD5.pdf` |
| 2022 | `/provas-e-gabaritos/2022` | `2022_PV_impresso_D2_CD5.pdf` | `2022_GB_impresso_D2_CD5.pdf` |
| 2023 | `/provas-e-gabaritos/2023` | `2023_PV_impresso_D2_CD5.pdf` | `2023_GB_impresso_D2_CD5.pdf` |
| 2024 | `/provas-e-gabaritos/2024` | `2024_PV_impresso_D2_CD5.pdf` | `2024_GB_impresso_D2_CD5.pdf` |
| 2025 | `/provas-e-gabaritos/2025` | `2025_PV_impresso_D2_CD5.pdf` | `2025_GB_impresso_D2_CD5.pdf` |

Os caminhos acima sao relativos a `https://download.inep.gov.br/enem/provas_e_gabaritos/`. Cadernos adicionais, formatos ampliados e digitais ficam no mesmo indice oficial e devem ser inventariados antes de processamento futuro.

## Piloto Controlado

- Documento: ENEM 2020, 2o dia, Caderno 5 Amarelo, Ciencias da Natureza e suas Tecnologias (questoes 91-135) e Matematica (136-180).
- Prova: `2020_PV_impresso_D2_CD5.pdf`; SHA-256 `854495fd40b9bd61b7280f7df87b7154dadb4e2ab705f12a3c757579f369c12b`.
- Gabarito: `2020_GB_impresso_D2_CD5.pdf`; SHA-256 `5ea5529addb8ee52ac6fb4b6cafea5c54b8c2e9d00a5376f4cbdfb7e3181ef56`.
- Data de aquisicao: 2026-09-02.
- Armazenamento: `var/inep-pilot/`, explicitamente ignorado pelo Git.

## Parser

- Biblioteca: `pypdf 5.9.0`, escolhida para leitura deterministica da camada de texto sem OCR.
- `PdfParser` preserva texto extraido, numero, ordem e pagina de origem.
- Alternativas so sao consideradas completas quando a sequencia estrutural A-E e encontrada. Itens parciais sao preservados e marcados para revisao, sem completar ou reescrever texto.
- Imagens raster e referencias textuais a figura/grafico/tabela geram assets de evidencia por pagina; nao ha OCR nem reconstrucao visual.
- O gabarito e lido separadamente e associado somente por numero explicito. Itens anulados ou ausentes ficam sem resposta e exigem revisao.

## Resultado da Extracao

- 32 paginas.
- 90 questoes detectadas, 91 a 180, na ordem de origem.
- 67 com alternativas A-E completas na camada de texto.
- 23 com alternativas parciais/ausentes; 66 marcadas `requires_review` por incompletude, ativo visual ou gabarito ausente.
- 21 assets de evidencia visual por pagina.
- 88 gabaritos explicitos; questoes 114 e 141 anuladas/sem resposta no documento oficial.
- Conflitos de gabarito: 0. Nenhum gabarito foi inferido.

## Persistencia Intermediaria

- `IngestionDocument`, `IngestionRun`, `IngestionQuestion` e `IngestionAsset` sao reutilizados.
- Reexecutar o mesmo arquivo retorna o mesmo documento por SHA-256 e nao duplica registros.
- Nenhum `Question`, `QuestionVersion`, `QuestionOption`, `ContentQuestionLink` ou `CatalogNode` foi criado pelo piloto.
- Nenhuma classificacao de Quimica foi feita. O caderno contem candidatas de Ciencias da Natureza; separacao disciplinar continua pendente de regra revisavel.

## Validacao

- `tests/test_enem_pdf_parser.py` e `tests/test_ingestion.py`: `40 passed`.
- Testes usam PDF oficial somente quando o arquivo local ignorado esta presente; nao baixam documentos durante execucao automatica.
- Banco principal permaneceu vazio e inalterado. OpenAI nao foi chamado.

## Limitacoes e Proximo Passo

- A camada de texto nao garante completude de itens dependentes de diagrama, grafico, tabela ou estrutura quimica. Esses itens devem permanecer em revisao humana.
- O proximo incremento seguro e definir classificacao disciplinar auditavel para candidatas de Ciencias da Natureza e um workflow de revisao/importacao em banco temporario, antes de criar qualquer Questao definitiva.