"""A saida de AUDIENCIA: o que aluno, professor e demais usuarios recebem.

TRES CAMPOS, E OS OUTROS NAO EXISTEM
====================================

    ``answer_text``         a resposta, sem marcadores
    ``outcome``             ANSWERED ou UNAVAILABLE
    ``unavailable_reason``  um de dois valores, ou None

Os campos administrativos nao estao ausentes por terem sido filtrados -
eles NAO EXISTEM nesta estrutura. Nao ha o que esquecer de remover, e
nenhuma rota consegue devolver o que o tipo nao carrega. Filtro se esquece
de atualizar; ausencia, nao.

DOIS SENTIDOS DE "PUBLICO"
==========================

``admin_payload()`` tambem e seguro - em DIREITOS: nao carrega literal de
obra comercial. Mas carrega o APARATO DE FUNDAMENTACAO: ``chunk_id``,
``text_hash``, pagina, score, papel editorial, marcadores. Isso e seguro
para o ADMIN e indevido para o aluno.

Direitos e audiencia sao eixos ortogonais, e este modulo cuida do segundo.

A ENTREGA E PROPRIEDADE DO ESTADO, NAO ESCOLHA DO CHAMADOR
==========================================================

``to_public()`` devolve ``answer_text = None`` para todo estado nao
entregavel. Nao ha parametro que permita o contrario. Um chamador nao
consegue publicar resposta nao fundamentada nem por descuido nem de
proposito - que e diferente de "nao deve".

POR QUE O MOTIVO E GROSSO
=========================

"o modelo inventou uma citacao", "nao havia evidencia no acervo" e "o
modelo declarou que as evidencias nao bastavam" viram todos
``NO_ANSWER_FROM_CORPUS``.

Nao e preguica: distinguir entrega a quem perguntou informacao sobre o
estado interno do acervo e sobre o comportamento do modelo. Quem precisa
distinguir e o ADMIN, e ele tem canal proprio.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from .grounded_answer import (
    ANSWER_WITHOUT_CITATION,
    DEGRADED_RETRIEVAL,
    EMPTY_PUBLIC_ANSWER,
    EVIDENCE_DECLARED_INSUFFICIENT,
    GroundedAnswer,
    INVALID_EVIDENCE_REFERENCE,
    NO_EVIDENCE,
    PROVIDER_FAILED,
    PROVIDER_INVALID_RESPONSE,
    SANITIZATION_FAILED,
)
from .structured_answer import (
    STRUCTURED_CONTRACT_VIOLATION,
    STRUCTURED_UNVERIFIED_CLAIM,
)


ANSWERED = "ANSWERED"
UNAVAILABLE = "UNAVAILABLE"

#: O acervo nao sustenta uma resposta. Cobre ausencia de evidencia,
#: citacao invalida, resposta sem citacao e suficiencia negada.
NO_ANSWER_FROM_CORPUS = "NO_ANSWER_FROM_CORPUS"
#: Condicao operacional: recuperacao degradada, falha do provider,
#: resposta fora do contrato, defeito na sanitizacao.
TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"

UNAVAILABLE_REASONS: tuple[str, ...] = (
    NO_ANSWER_FROM_CORPUS, TEMPORARILY_UNAVAILABLE,
)

#: Bloqueios que sao sobre o ACERVO: o material nao sustenta a resposta.
_RAZOES_DE_CORPUS = frozenset({
    NO_EVIDENCE,
    ANSWER_WITHOUT_CITATION,
    INVALID_EVIDENCE_REFERENCE,
    EVIDENCE_DECLARED_INSUFFICIENT,
    # o caminho estruturado: afirmacao que o acervo nao sustenta
    STRUCTURED_UNVERIFIED_CLAIM,
})

#: Bloqueios OPERACIONAIS: o acervo nao esta em questao, alguma coisa
#: falhou. ``EMPTY_PUBLIC_ANSWER`` e ``STRUCTURED_CONTRACT_VIOLATION``
#: estao aqui porque sao desvio de contrato do provider, nao afirmacao
#: sobre o corpus.
_RAZOES_OPERACIONAIS = frozenset({
    DEGRADED_RETRIEVAL,
    PROVIDER_FAILED,
    PROVIDER_INVALID_RESPONSE,
    SANITIZATION_FAILED,
    EMPTY_PUBLIC_ANSWER,
    STRUCTURED_CONTRACT_VIOLATION,
})

#: Todo motivo de bloqueio tem de estar classificado em UM dos dois. Um
#: motivo novo que nao esteja em nenhum cai no padrao operacional e
#: dispara este teste, para que a classificacao seja decisao e nao
#: omissao - foi exatamente o que aconteceu com EMPTY_PUBLIC_ANSWER na
#: primeira versao.
KNOWN_BLOCK_REASONS: frozenset[str] = _RAZOES_DE_CORPUS | _RAZOES_OPERACIONAIS

#: Nomes que JAMAIS podem aparecer na saida publica. Fonte unica de
#: verdade, consumida pelos testes - o mesmo padrao de
#: ``_rejection_ladder`` em ``vector_search``.
ADMIN_ONLY_FIELDS: frozenset[str] = frozenset({
    "status", "is_grounded", "grounding", "sufficiency", "deliverable",
    "delivery_block_reason", "needs_human_review", "question", "answer",
    "answer_text_public", "stripping_artifacts", "cited_markers",
    "invalid_markers", "available_markers", "raw_used_evidence",
    "normalized_used_evidence", "model_says_sufficient", "degraded",
    "degradation_reasons", "input_tokens", "output_tokens", "error",
    "cited_evidences", "chunk_id", "text_hash", "source_title",
    "source_id", "document_filename", "document_id", "page_start",
    "page_end", "chunk_type", "editorial_role", "rights_class",
    "quotable", "excerpt", "rank", "retrieval_rank", "score", "distance",
    "heading_path", "chars_used",
})


@dataclass(frozen=True)
class PublicAnswer:
    answer_text: str | None
    outcome: str
    unavailable_reason: str | None

    def payload(self) -> dict[str, Any]:
        return {
            "answer_text": self.answer_text,
            "outcome": self.outcome,
            "unavailable_reason": self.unavailable_reason,
        }


#: Os unicos nomes que a saida publica conhece.
PUBLIC_FIELDS: frozenset[str] = frozenset(f.name for f in fields(PublicAnswer))


def to_public(answer: GroundedAnswer) -> PublicAnswer:
    """A UNICA porta para a saida de audiencia.

    Nao ha caminho alternativo que monte ``PublicAnswer`` a partir de uma
    resposta: quem quiser publicar passa por aqui, e aqui a entrega e
    decidida pelo estado.
    """
    motivo = answer.delivery_block_reason
    if motivo is None:
        return PublicAnswer(
            answer_text=answer.answer_text_public,
            outcome=ANSWERED,
            unavailable_reason=None,
        )
    return PublicAnswer(
        answer_text=None,
        outcome=UNAVAILABLE,
        unavailable_reason=(
            NO_ANSWER_FROM_CORPUS if motivo in _RAZOES_DE_CORPUS
            else TEMPORARILY_UNAVAILABLE
        ),
    )


def structured_to_public(answer: Any) -> PublicAnswer:
    """A UNICA porta de audiencia do caminho estruturado.

    Mesmo contrato e mesmo tipo de retorno de ``to_public``: tres campos,
    e os administrativos nao existem neles. O span - que E literal da
    obra - fica no canal ADMIN e nunca chega aqui.

    ``answer_text`` so sai quando TODAS as afirmacoes estao verificadas.
    Entregar a parte boa e omitir a ruim seria apresentar como valida uma
    resposta que o sistema nao conseguiu sustentar inteira.
    """
    motivo = answer.delivery_block_reason
    if motivo is None:
        return PublicAnswer(answer_text=answer.answer_text,
                            outcome=ANSWERED, unavailable_reason=None)
    return PublicAnswer(
        answer_text=None,
        outcome=UNAVAILABLE,
        unavailable_reason=(
            NO_ANSWER_FROM_CORPUS if motivo in _RAZOES_DE_CORPUS
            else TEMPORARILY_UNAVAILABLE
        ),
    )
