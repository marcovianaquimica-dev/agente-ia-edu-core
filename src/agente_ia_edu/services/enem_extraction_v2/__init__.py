"""EXTRACAO ENEM V2 - PROTOTIPO EXPERIMENTAL, FORA DO CAMINHO DE PRODUCAO.

Este pacote existe para ser COMPARADO com a V1
(``agente_ia_edu.services.ingestion_parser``), nao para substitui-la.

Garantias desta etapa:
  - a V1 permanece intacta e e o baseline;
  - nenhum modulo de producao importa este pacote (ha teste que falha se
    isso mudar: ``tests/test_enem_extraction_v2_isolamento.py``);
  - nada aqui abre banco, escreve em disco, acessa rede ou chama IA;
  - nenhuma tabela, nenhuma migration: os contratos sao em memoria.

Causas atacadas, todas medidas em ``docs/qbank-diagnostico-enem-2020-2025.md``:
  C1  quebra de pagina invisivel para ``^``      -> ``text_layer``
  C2  rotulo estilhacado nos PDFs de 2025        -> ``text_layer``
  C3  limiar de dois espacos nas alternativas    -> ``options``
  C4  gabarito de 1 digito e de duas colunas     -> ``answer_key``
  C5  ativo detectado por palavra na pagina      -> ``assets``

C6 (bloco Ingles/Espanhol) e C7 (2021 sem ToUnicode) NAO sao resolvidos aqui.
C6 tem desenho de contrato no relatorio e aguarda autorizacao; C7 e
documentado como sem solucao deterministica.
"""

from .contracts import (  # noqa: F401
    AssetCandidate,
    Caixa,
    OptionCandidate,
    QuestionCandidate,
    ResultadoCaderno,
)
from .pipeline import avaliar_motor, extrair_caderno  # noqa: F401

__all__ = [
    "AssetCandidate",
    "Caixa",
    "OptionCandidate",
    "QuestionCandidate",
    "ResultadoCaderno",
    "avaliar_motor",
    "extrair_caderno",
]
