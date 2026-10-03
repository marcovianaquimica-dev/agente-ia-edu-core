"""Resposta fundamentada nas evidencias, com citacao verificada.

A VALIDACAO E DETERMINISTICA, E E O PONTO
=========================================

O modelo cita ``[E3]``. O codigo confere que ``E3`` existe no contexto que
ele proprio montou. Marcador inventado NAO vira resposta boa com uma nota de
rodape errada - vira ``INVALID_EVIDENCE_REFERENCE``, um estado nomeado.

Sem essa conferencia, a rastreabilidade seria decorativa: o leitor veria
``[E3]``, confiaria, e nao teria como saber que o modelo fabricou a
referencia. Alucinacao bem formatada e o pior formato possivel para um erro.

ESTADOS, E NENHUM DELES E SILENCIOSO
====================================

    GROUNDED                    respondeu e toda citacao confere
    NO_EVIDENCE                 nao havia o que fundamentar
    DEGRADED_RETRIEVAL          o corpus nao estava integro na recuperacao
    ANSWER_WITHOUT_CITATION     respondeu sem citar nada
    INVALID_EVIDENCE_REFERENCE  citou marcador que nao existe
    PROVIDER_FAILED             o fornecedor nao respondeu
    PROVIDER_INVALID_RESPONSE   respondeu fora do contrato

So o primeiro tem ``is_grounded = True``. Nos demais a resposta, quando
existe, e preservada para INSPECAO - nunca apresentada como fundamentada.

NAO HA LIMIAR DE SIMILARIDADE
=============================

"Evidencia insuficiente" aqui e um fato ESTRUTURAL - nao ha evidencia, ou a
recuperacao estava degradada, ou o modelo nao citou -, nunca um score abaixo
de um numero. A Fase 6 recusou propor limiar com uma unica consulta de
controle negativo, e essa recusa continua valendo.

PROVIDER-NEUTRO
===============

Consome ``TextGenerationProvider``. Nao conhece fornecedor, modelo nem
formato de API.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from ...providers.contracts import TextGenerationProvider
from ...providers.errors import ProviderError
from ...providers.models import TextGenerationRequest
from .context_builder import BuiltContext, ContextEvidence

GROUNDED = "GROUNDED"
NO_EVIDENCE = "NO_EVIDENCE"
DEGRADED_RETRIEVAL = "DEGRADED_RETRIEVAL"
ANSWER_WITHOUT_CITATION = "ANSWER_WITHOUT_CITATION"
INVALID_EVIDENCE_REFERENCE = "INVALID_EVIDENCE_REFERENCE"
PROVIDER_FAILED = "PROVIDER_FAILED"
PROVIDER_INVALID_RESPONSE = "PROVIDER_INVALID_RESPONSE"

_MARCADOR = re.compile(r"\[(E\d+)\]")

#: Grafias aceitas de um marcador no campo ``used_evidence``: ``E1``, ``[E1]``
#: e as mesmas com espaco em volta. A ancoragem em ``^...$`` e o limite:
#: a string INTEIRA tem de ser o marcador.
_MARCADOR_SOLTO = re.compile(r"^\[?\s*(E\d+)\s*\]?$")


def _normalizar_marcador(valor: object) -> str | None:
    """``[E1]`` e ``E1`` sao o MESMO marcador; ``evidencia 1`` nao e nenhum.

    O modelo nao tem obrigacao de acertar a grafia de um campo JSON - foi
    exatamente o que aconteceu na primeira execucao real, em que ele
    devolveu ``["[E1]", "[E4]"]`` e uma resposta correta foi reprovada por
    causa de dois colchetes.

    Mas normalizar grafia e uma coisa e inferir intencao e outra. Texto
    arbitrario que CONTENHA um marcador - ``"fonte E1 e E2"``, ``"[E1] e
    [E2]"`` - nao vira citacao: a regex esta ancorada, entao a string
    inteira precisa ser o marcador. Tratar esses casos como citacao seria
    fabricar fundamentacao que o modelo nao declarou, que e o oposto do que
    esta validacao existe para fazer.

    ``e1`` minusculo tambem nao passa. Aceitar seria supor que o modelo
    quis dizer ``E1``, e supor e precisamente o que se quer evitar aqui.
    """
    if not isinstance(valor, str):
        return None
    casou = _MARCADOR_SOLTO.match(valor.strip())
    return casou.group(1) if casou else None

INSTRUCAO = """Voce responde perguntas de Quimica do ensino medio usando
EXCLUSIVAMENTE as evidencias numeradas abaixo. Regras:

1. Nao use conhecimento proprio. Se as evidencias nao bastarem, diga isso.
2. Cite a evidencia que sustenta cada afirmacao, no formato [E1], [E2].
3. Nao invente marcador: use apenas os que aparecem abaixo.
4. Responda em portugues, de forma direta.

Devolva JSON com exatamente estes campos:
  "answer": o texto da resposta, com os marcadores no corpo
  "used_evidence": lista dos marcadores usados
  "sufficient": true se as evidencias bastaram, false se nao
"""


@dataclass(frozen=True)
class GroundedAnswer:
    status: str
    question: str
    answer: str | None
    cited_markers: tuple[str, ...]
    invalid_markers: tuple[str, ...]
    cited_evidences: tuple[ContextEvidence, ...]
    available_markers: tuple[str, ...]
    model_says_sufficient: bool | None
    degraded: bool
    degradation_reasons: tuple[str, ...]
    input_tokens: int | None
    output_tokens: int | None
    error: str | None
    #: Texto cru do provider, so para diagnostico. ``repr=False`` porque ele
    #: pode ecoar trecho de evidencia comercial.
    _raw: str | None = field(default=None, repr=False)

    @property
    def is_grounded(self) -> bool:
        return self.status == GROUNDED

    def admin_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "is_grounded": self.is_grounded,
            "question": self.question,
            "answer": self.answer,
            "cited_markers": list(self.cited_markers),
            "invalid_markers": list(self.invalid_markers),
            "available_markers": list(self.available_markers),
            "model_says_sufficient": self.model_says_sufficient,
            "degraded": self.degraded,
            "degradation_reasons": list(self.degradation_reasons),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "error": self.error,
            "cited_evidences": [
                {
                    "marker": e.marker,
                    "chunk_id": str(e.chunk_id),
                    "text_hash": e.text_hash,
                    "source_title": e.source_title,
                    "source_id": str(e.source_id),
                    "document_filename": e.document_filename,
                    "document_id": str(e.document_id),
                    "page_start": e.page_start,
                    "page_end": e.page_end,
                    "chunk_type": e.chunk_type,
                    "editorial_role": e.editorial_role,
                    "rights_class": e.rights_class,
                    "quotable": e.quotable,
                    "excerpt": e.excerpt,
                }
                for e in self.cited_evidences
            ],
        }


class GroundedAnswerer:
    def __init__(self, *, provider: TextGenerationProvider,
                 model: str | None = None) -> None:
        self._provider = provider
        self._model = model

    async def answer(
        self,
        question: str,
        context: BuiltContext,
        *,
        retrieval_degraded: bool = False,
        degradation_reasons: Sequence[str] = (),
        allow_degraded: bool = False,
    ) -> GroundedAnswer:
        disponiveis = tuple(sorted(context.marker_set(),
                                   key=lambda m: int(m[1:])))

        def _falha(status, **extra):
            base = dict(
                status=status, question=question, answer=None,
                cited_markers=(), invalid_markers=(), cited_evidences=(),
                available_markers=disponiveis, model_says_sufficient=None,
                degraded=retrieval_degraded,
                degradation_reasons=tuple(degradation_reasons),
                input_tokens=None, output_tokens=None, error=None,
            )
            base.update(extra)
            return GroundedAnswer(**base)

        # Os dois portoes que NAO gastam chamada: sem evidencia nao ha o que
        # fundamentar, e corpus degradado produziria resposta sobre acervo
        # incompleto com cara de completo.
        if not context.evidences:
            return _falha(NO_EVIDENCE)
        if retrieval_degraded and not allow_degraded:
            return _falha(DEGRADED_RETRIEVAL)

        prompt = (
            f"{INSTRUCAO}\n\nPERGUNTA: {question}\n\n"
            f"EVIDENCIAS:\n\n{context.prompt_payload()}\n"
        )
        try:
            resultado = await self._provider.generate(
                TextGenerationRequest(prompt=prompt, model=self._model)
            )
        except ProviderError as erro:
            return _falha(PROVIDER_FAILED, error=type(erro).__name__)

        try:
            corpo = json.loads(resultado.text)
            texto = corpo["answer"]
            if not isinstance(texto, str):
                raise TypeError("answer nao e texto")
        except (json.JSONDecodeError, KeyError, TypeError) as erro:
            return _falha(
                PROVIDER_INVALID_RESPONSE, error=type(erro).__name__,
                input_tokens=resultado.input_tokens,
                output_tokens=resultado.output_tokens,
                _raw=resultado.text,
            )

        # Os marcadores que VALEM sao os do corpo da resposta, mais os
        # declarados no campo. Um modelo pode citar no texto e esquecer o
        # campo; o que o leitor ve e o texto. A uniao dos dois e entao
        # conferida contra as evidencias que o modelo de fato recebeu - a
        # validacao cruzada que torna a rastreabilidade verificavel em vez
        # de declarada.
        do_campo = [
            m for m in (
                _normalizar_marcador(v)
                for v in (corpo.get("used_evidence") or [])
            ) if m
        ]
        citados = list(dict.fromkeys(_MARCADOR.findall(texto) + do_campo))
        invalidos = tuple(m for m in citados if m not in disponiveis)
        validos = tuple(m for m in citados if m in disponiveis)
        por_marcador = {e.marker: e for e in context.evidences}

        comum = dict(
            question=question, answer=texto,
            cited_markers=tuple(citados), invalid_markers=invalidos,
            cited_evidences=tuple(por_marcador[m] for m in validos),
            available_markers=disponiveis,
            model_says_sufficient=corpo.get("sufficient"),
            degraded=retrieval_degraded,
            degradation_reasons=tuple(degradation_reasons),
            input_tokens=resultado.input_tokens,
            output_tokens=resultado.output_tokens,
            error=None, _raw=resultado.text,
        )
        if invalidos:
            # Uma citacao fabricada contamina a resposta inteira: nao da
            # para confiar no resto quando uma parte foi inventada.
            return GroundedAnswer(status=INVALID_EVIDENCE_REFERENCE, **comum)
        if not citados:
            return GroundedAnswer(status=ANSWER_WITHOUT_CITATION, **comum)
        return GroundedAnswer(status=GROUNDED, **comum)
