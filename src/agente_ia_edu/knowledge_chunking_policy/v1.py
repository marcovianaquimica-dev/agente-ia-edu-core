"""Politica de chunking v1 - tamanhos, representacoes e criterios de PARTIAL.

IMUTAVEL. Uma mudanca de forma e um ``v2``, nunca uma edicao daqui: chunks ja
persistidos registram em ``metadata.chunking_policy_version`` a versao sob a
qual nasceram e precisam continuar explicaveis por ela.

Toda constante de tamanho e todo limiar vivem aqui. Nenhum numero destes pode
aparecer solto em outro modulo.

AS TRES REPRESENTACOES (spec 20.2)
-----------------------------------
``raw_text``       conteudo LITERAL extraido da fonte. O sistema nao
                   acrescenta nada: nem titulo, nem caminho hierarquico, nem
                   rotulo, nem separador inventado.
``heading_path``   contexto estrutural, derivado pelo sistema.
``retrieval_text`` representacao enriquecida para busca e embedding. Funcao
                   pura das duas acima. NAO e persistida - duplicaria
                   ~9 MB e seria uma segunda fonte de verdade capaz de sair
                   de sincronia.

``text_hash = sha256(retrieval_text)``. Decisao normativa: esse mesmo hash e
a chave de idempotencia em ``knowledge_chunk_embeddings``, e o embedding e
calculado a partir do ``retrieval_text``. Hashear o ``raw_text`` faria uma
mudanca de politica passar em silencio, deixando embeddings obsoletos
indistinguiveis de validos. Mudar esta politica muda o hash e dispara
re-embedding - que e o comportamento correto.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Iterable, Sequence

POLICY_VERSION = "v1"

# -- tamanhos (spec 20.6) ----------------------------------------------

#: Estimativa deterministica, sem dependencia de tokenizer. A coluna se chama
#: ``token_estimate`` justamente por isso.
CHARS_PER_TOKEN = 4

#: Faixa em que um trecho de livro didatico ainda carrega um raciocinio
#: completo (definicao + exemplo) sem diluir o vetor em varios assuntos.
TARGET_CHUNK_TOKENS = 700
#: Abaixo disso o chunk e fundido ao vizinho da mesma secao, em vez de virar
#: ruido no indice.
MIN_CHUNK_TOKENS = 120
#: Teto de unidade DIVISIVEL. Unidade indivisivel acima disso e emitida
#: inteira, com ``metadata.oversized``.
MAX_CHUNK_TOKENS = 1100
#: Sobreposicao entre PROSE da MESMA secao. Nunca atravessa secao, nunca
#: entra nem sai de unidade indivisivel.
OVERLAP_TOKENS = 120

#: Unidades pedagogicas que nunca sao cortadas por tamanho. Cortar um exemplo
#: resolvido ao meio produz dois chunks que nao sustentam afirmacao nenhuma.
INDIVISIBLE_CHUNK_TYPES: tuple[str, ...] = (
    "EXERCISE",
    "TABLE",
    "FORMULA",
    "WORKED_EXAMPLE",
    "CURRICULUM_ITEM",
)

# -- criterios de PARTIAL (spec 20.3) ----------------------------------

#: Abaixo disso a pagina nao tem texto UTIL - uma legenda solta nao salva a
#: pagina.
MIN_USEFUL_CHARS = 200
#: Corrida de paginas interiores sem texto util que ja e perda, haja imagem
#: ou nao.
CONTIGUOUS_GAP_MIN_PAGES = 3
#: Fracao de paginas interiores sem texto util acima da qual a perda e
#: difusa mas real.
HIGH_GAP_RATIO = 0.10
#: ...mas a proporcao sozinha pune documento curto: UMA pagina branca entre
#: nove interiores ja da 11%. A regra difusa exige tambem uma contagem
#: absoluta, para falar de perda espalhada e nao de um branco isolado.
HIGH_GAP_MIN_PAGES = 3


@dataclass(frozen=True)
class PageTextStats:
    """Medida de UMA pagina. Quem mede e ``extraction.py``; quem decide e
    ``classify_extraction_status`` aqui - a medicao toca arquivo, a decisao
    nao."""

    page: int
    char_count: int
    has_image: bool

    @property
    def has_useful_text(self) -> bool:
        return self.char_count >= MIN_USEFUL_CHARS


def estimate_tokens(char_count: int) -> int:
    """Estimativa deterministica, arredondando para cima."""
    if char_count <= 0:
        return 0
    return math.ceil(char_count / CHARS_PER_TOKEN)


def build_retrieval_text(*, raw_text: str, heading_path: Sequence[str] | None) -> str:
    """Monta a representacao enriquecida usada para busca e embedding.

    O ``raw_text`` entra INTACTO ao final. O contexto vai antes e separado,
    para que nada do que o sistema acrescenta possa ser confundido com texto
    da obra ao ler a string montada.
    """
    body = raw_text.strip()
    if not heading_path:
        return body
    context = "\n".join(part.strip() for part in heading_path if part and part.strip())
    if not context:
        return body
    return f"{context}\n\n{body}"


def retrieval_text_hash(*, raw_text: str, heading_path: Sequence[str] | None) -> str:
    """``text_hash`` do chunk. Ver a nota do modulo sobre POR QUE e sobre o
    ``retrieval_text`` e nao sobre o ``raw_text``."""
    built = build_retrieval_text(raw_text=raw_text, heading_path=heading_path)
    return hashlib.sha256(built.encode("utf-8")).hexdigest()


def source_text_hash(raw_text: str) -> str:
    """Hash do texto da FONTE isolado.

    Existe para que "o texto da fonte mudou" seja distinguivel de "nosso
    enriquecimento mudou". Vai para ``metadata.source_text_sha256``.
    """
    return hashlib.sha256(raw_text.strip().encode("utf-8")).hexdigest()


def classify_extraction_status(
    pages: Iterable[PageTextStats],
) -> tuple[str, list[str]]:
    """Decide entre ``EXTRACTED``, ``PARTIAL`` e ``FAILED``.

    Funcao PURA: nao abre arquivo, nao toca banco. Os criterios estao na
    secao 20.3 da spec, e o principio e um so - **pagina vazia nao e, por si,
    perda de conteudo**. Paginas podem ser intencionalmente vazias (verso de
    capa, folha de guarda) ou predominantemente visuais. Classificar todo
    documento com uma pagina vazia como PARTIAL tornaria o estado inutil por
    excesso de alarme.

    Devolve ``(status, reasons)``. ``reasons`` lista TODOS os criterios que
    dispararam, nao so o primeiro - saber que duas coisas deram errado e
    diferente de saber que uma deu.
    """
    page_list = list(pages)
    with_text = [page for page in page_list if page.has_useful_text]
    if not with_text:
        return "FAILED", ["NO_TEXT_EXTRACTED"]

    # Paginas INTERIORES: estritamente entre a primeira e a ultima com texto.
    # Capa, folhas de guarda e brancos finais ficam fora por construcao.
    first = with_text[0].page
    last = with_text[-1].page
    interior = [page for page in page_list if first < page.page < last]
    gaps = [page for page in interior if not page.has_useful_text]

    reasons: list[str] = []

    # Pagina COM imagem e SEM texto util: assinatura de pagina escaneada ou
    # achatada - havia conteudo, e ele nao saiu. Sem imagem e sem texto e, na
    # maioria das vezes, uma pagina de fato branca; dai a assimetria.
    if any(page.has_image for page in gaps):
        reasons.append("IMAGE_ONLY_INTERIOR_PAGE")

    if _longest_run(interior) >= CONTIGUOUS_GAP_MIN_PAGES:
        reasons.append("CONTIGUOUS_GAP")

    # Proporcao E contagem absoluta: sem a segunda, um unico branco no meio
    # de um documento curto viraria PARTIAL e o estado perderia utilidade por
    # excesso de alarme.
    if (
        interior
        and len(gaps) >= HIGH_GAP_MIN_PAGES
        and (len(gaps) / len(interior)) > HIGH_GAP_RATIO
    ):
        reasons.append("HIGH_GAP_RATIO")

    return ("PARTIAL", reasons) if reasons else ("EXTRACTED", [])


def _longest_run(interior: Sequence[PageTextStats]) -> int:
    longest = current = 0
    for page in interior:
        current = 0 if page.has_useful_text else current + 1
        longest = max(longest, current)
    return longest
