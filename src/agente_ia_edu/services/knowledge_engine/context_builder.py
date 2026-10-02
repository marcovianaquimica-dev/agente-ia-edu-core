"""Monta o contexto de evidencias para geracao fundamentada.

A UNICA CAMADA QUE PRECISA DO LITERAL E PRODUZ ARTEFATO QUE ALGUEM LE
=====================================================================

Dai o desenho: duas representacoes fisicamente separadas, nunca uma com um
campo opcional.

    ``prompt_payload()``   tem o literal. Vai ao provider. Nao e serializado,
                           nao entra em ``repr``, nao aparece em log.
    ``public_payload()``   nao tem literal de obra comercial. E o que a CLI
                           imprime, o que vai ao relatorio, o que pode ser
                           logado.

``ContextEvidence`` nao TEM campo ``raw_text``. Nao e que ele venha vazio:
ele nao existe, entao ninguem o preenche depois por distracao. O literal
vive apenas nos blocos do prompt, marcados ``repr=False``.

AS TRES ZONAS DE DIREITOS
=========================

Ver ``rights.py``. Resumidamente: ler no processamento interno e permitido;
enviar ao provider e decisao explicita da politica do piloto e esta
condicionada a ``may_send_literal_to_provider``; expor em saida publica e
proibido para obra comercial. Fonte cuja classe nao autorize o envio e
EXCLUIDA do contexto com motivo nomeado, em vez de entrar sem texto - um
bloco de evidencia sem conteudo so gastaria orcamento e confundiria o
modelo.

SELECAO DETERMINISTICA
======================

Ordem de rank, ate o orcamento. Dois tetos independentes - caracteres e
numero de evidencias -, e o que vier primeiro corta. Toda evidencia que
entra ou sai aparece no resultado: a soma de incluidas e excluidas e sempre
o total recebido, e cada exclusao traz o motivo. Nenhuma evidencia some em
silencio.

NAO HA LIMIAR DE SIMILARIDADE AQUI
==================================

Nenhum corte por score. A Fase 6 recusou propor limiar com uma unica
consulta de controle negativo, e isso nao mudou. O que entra no contexto e
decidido por rank e orcamento, nao por confianca.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence
from uuid import UUID

from .rights import (
    max_excerpt_chars,
    may_expose_literal_text,
    may_send_literal_to_provider,
)

#: Orcamento padrao, em CARACTERES. Caracteres e nao tokens porque e
#: deterministico e nao depende do tokenizador de um fornecedor - a mesma
#: razao de ``CHARS_PER_TOKEN`` existir na politica de chunking. A ~4
#: caracteres por token, isto da da ordem de 3 mil tokens de evidencia.
DEFAULT_BUDGET_CHARS = 12_000

#: Teto de evidencias. Existe separado do orcamento porque muitas evidencias
#: curtas cansam o modelo tanto quanto poucas longas.
DEFAULT_MAX_EVIDENCES = 10

BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
MAX_EVIDENCES_REACHED = "MAX_EVIDENCES_REACHED"
RIGHTS_NO_PROVIDER = "RIGHTS_NO_PROVIDER"


@dataclass(frozen=True)
class ContextEvidence:
    """Uma evidencia, na representacao PUBLICA.

    Repare no que nao esta aqui: ``raw_text``. O literal nunca viaja nesta
    estrutura. ``excerpt`` so e preenchido para fonte que admite citacao.
    """

    marker: str
    chunk_id: UUID
    text_hash: str
    retrieval_rank: int
    score: float
    chunk_type: str
    editorial_role: str
    heading_path: tuple[str, ...]
    page_start: int | None
    page_end: int | None
    source_id: UUID
    source_title: str
    document_id: UUID
    document_filename: str
    rights_class: str
    quotable: bool
    excerpt: str | None
    chars_used: int


@dataclass(frozen=True)
class BuiltContext:
    evidences: tuple[ContextEvidence, ...]
    #: Cada exclusao com ``chunk_id``, ``rank`` e ``reason``.
    excluded: tuple[dict[str, Any], ...]
    budget_chars: int
    used_chars: int
    max_evidences: int
    #: Os blocos COM literal. ``repr=False`` para que imprimir o objeto num
    #: log de diagnostico - o vazamento mais facil de cometer - nao exponha
    #: texto de obra comercial.
    _blocks: tuple[str, ...] = field(default=(), repr=False)

    def prompt_payload(self) -> str:
        """O texto das evidencias, com marcadores. VAI AO PROVIDER."""
        return "\n\n".join(self._blocks)

    def public_payload(self) -> dict[str, Any]:
        """Tudo que pode ser impresso, logado ou serializado."""
        return {
            "evidences": [
                {
                    "marker": e.marker,
                    "chunk_id": str(e.chunk_id),
                    "text_hash": e.text_hash,
                    "retrieval_rank": e.retrieval_rank,
                    "score": e.score,
                    "chunk_type": e.chunk_type,
                    "editorial_role": e.editorial_role,
                    "heading_path": list(e.heading_path),
                    "page_start": e.page_start,
                    "page_end": e.page_end,
                    "source_id": str(e.source_id),
                    "source_title": e.source_title,
                    "document_id": str(e.document_id),
                    "document_filename": e.document_filename,
                    "rights_class": e.rights_class,
                    "quotable": e.quotable,
                    "excerpt": e.excerpt,
                    "chars_used": e.chars_used,
                }
                for e in self.evidences
            ],
            "excluded": [dict(x) for x in self.excluded],
            "budget_chars": self.budget_chars,
            "used_chars": self.used_chars,
            "max_evidences": self.max_evidences,
        }

    def marker_set(self) -> frozenset[str]:
        return frozenset(e.marker for e in self.evidences)


class ContextBuilder:
    def __init__(
        self,
        *,
        budget_chars: int = DEFAULT_BUDGET_CHARS,
        max_evidences: int = DEFAULT_MAX_EVIDENCES,
    ) -> None:
        self._budget = budget_chars
        self._max = max_evidences

    def build(
        self, hits: Sequence[Any], *, texts: Mapping[UUID, str]
    ) -> BuiltContext:
        """Seleciona por rank ate o orcamento. ``texts`` traz o literal lido
        no processamento interno, por ``chunk_id``."""
        evidencias: list[ContextEvidence] = []
        blocos: list[str] = []
        excluidas: list[dict[str, Any]] = []
        usados = 0

        for hit in hits:
            if not may_send_literal_to_provider(hit.rights_class):
                # Excluir, e nao incluir sem texto: um bloco de evidencia
                # vazio gastaria orcamento e confundiria o modelo.
                excluidas.append(self._excluir(hit, RIGHTS_NO_PROVIDER))
                continue
            if len(evidencias) >= self._max:
                excluidas.append(self._excluir(hit, MAX_EVIDENCES_REACHED))
                continue

            texto = (texts.get(hit.chunk_id) or "").strip()
            marcador = f"E{len(evidencias) + 1}"
            bloco = self._bloco(marcador, hit, texto)
            # A PRIMEIRA evidencia entra mesmo estourando: cortar a melhor
            # por tamanho deixaria o contexto vazio havendo resultado.
            if evidencias and usados + len(bloco) > self._budget:
                excluidas.append(self._excluir(hit, BUDGET_EXHAUSTED))
                continue

            usados += len(bloco)
            blocos.append(bloco)
            evidencias.append(
                ContextEvidence(
                    marker=marcador,
                    chunk_id=hit.chunk_id,
                    text_hash=hit.text_hash,
                    retrieval_rank=hit.rank,
                    score=float(hit.score),
                    chunk_type=hit.chunk_type,
                    editorial_role=hit.editorial_role,
                    heading_path=tuple(hit.heading_path or ()),
                    page_start=hit.page_start,
                    page_end=hit.page_end,
                    source_id=hit.source_id,
                    source_title=hit.source_title,
                    document_id=hit.document_id,
                    document_filename=hit.document_filename,
                    rights_class=hit.rights_class,
                    quotable=may_expose_literal_text(hit.rights_class),
                    excerpt=self._excerpt(hit.rights_class, texto),
                    chars_used=len(bloco),
                )
            )

        return BuiltContext(
            evidences=tuple(evidencias),
            excluded=tuple(excluidas),
            budget_chars=self._budget,
            used_chars=usados,
            max_evidences=self._max,
            _blocks=tuple(blocos),
        )

    @staticmethod
    def _excluir(hit, motivo: str) -> dict[str, Any]:
        return {
            "chunk_id": str(hit.chunk_id),
            "text_hash": hit.text_hash,
            "rank": hit.rank,
            "reason": motivo,
            "source_title": hit.source_title,
            "page_start": hit.page_start,
        }

    @staticmethod
    def _bloco(marcador: str, hit, texto: str) -> str:
        """O bloco COM literal. Traz fonte e pagina para que o modelo possa
        atribuir, e para que a resposta seja conferivel contra a obra."""
        cabeca = " > ".join(hit.heading_path or ())
        return (
            f"[{marcador}] fonte: {hit.source_title} | pagina: "
            f"{hit.page_start} | secao: {cabeca}\n{texto}"
        )

    @staticmethod
    def _excerpt(rights_class: str, texto: str) -> str | None:
        limite = max_excerpt_chars(rights_class)
        if limite is None or not texto:
            return None
        return texto[:limite]
