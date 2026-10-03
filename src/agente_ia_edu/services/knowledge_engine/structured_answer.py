"""Resposta estruturada: a afirmacao e a unidade de suporte.

SEGUNDO CAMINHO, NAO SUBSTITUICAO
=================================

``GroundedAnswerer`` e ``INSTRUCAO`` permanecem intactos. Este modulo tem
prompt proprio e roda em paralelo sobre o MESMO contexto, para que a
comparacao isole a mudanca de contrato da variacao de recuperacao - erro
que o par N1/N2 cometeu e que nao se repete aqui.

O QUE MUDA NO CONTRATO
======================

Antes: ``{answer, used_evidence}``. O escopo do marcador era adivinhado,
e 24% das frases das respostas reais nao tinham marcador nenhum.

Agora: a resposta E a lista de afirmacoes. Cada afirmacao factual declara
suas evidencias E, para cada uma, um TRECHO LITERAL. O texto entregue e
montado a partir das afirmacoes, entao nao existe deriva possivel entre o
que foi verificado e o que sai.

CONTRIBUICAO COMPLEMENTAR
=========================

A unidade de suporte e a AFIRMACAO, nao a evidencia. Varias evidencias
podem sustenta-la em conjunto: uma traz o metodo, outra os insumos. O que
nao se admite e evidencia citada sem contribuicao verificavel - uma boa
nao lava uma ruim, e por isso a verificacao e por PAR, nunca sobre a
uniao dos chunks.

O LIMITE DESTA ETAPA, DITO EM VOZ ALTA
======================================

Span verificado prova que o trecho EXISTE naquele chunk. NAO prova que
ele sustenta a afirmacao. Validamos ``claim -> evidence -> span
verificado``, e nao ``claim <= span``. Fechar o resto exigiria
julgamento, e nenhum segundo modelo juiz entra nesta fase.
"""

from __future__ import annotations

import ast
import json
import operator
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from ...providers.contracts import TextGenerationProvider
from ...providers.errors import ProviderError
from ...providers.models import TextGenerationRequest
from .context_builder import BuiltContext
from .grounded_answer import (
    DEGRADED_RETRIEVAL,
    EVIDENCE_DECLARED_INSUFFICIENT,
    NO_EVIDENCE,
    PROVIDER_FAILED,
    PROVIDER_INVALID_RESPONSE,
    SUFFICIENCY_DENIED,
    _classificar_suficiencia,
)
from .span_verification import (
    SPAN_CASE_MISMATCH,
    SPAN_NORMALIZATION_V1,
    SPAN_VERIFIED,
    normalize_span,
    verify_span,
)

CLAIM_FACTUAL = "CLAIM_FACTUAL"
CLAIM_META = "CLAIM_META"
CLAIM_CONNECTIVE = "CLAIM_CONNECTIVE"
CLAIM_KINDS = {"FACTUAL": CLAIM_FACTUAL, "META": CLAIM_META,
               "CONNECTIVE": CLAIM_CONNECTIVE}

EVIDENCE_WITHOUT_CONTRIBUTION = "EVIDENCE_WITHOUT_CONTRIBUTION"
STRUCTURED_CONTRACT_VIOLATION = "STRUCTURED_CONTRACT_VIOLATION"
STRUCTURED_OK = "STRUCTURED_OK"
STRUCTURED_UNVERIFIED_CLAIM = "STRUCTURED_UNVERIFIED_CLAIM"

DERIVATION_VERIFIED = "DERIVATION_VERIFIED"
DERIVATION_MISMATCH = "DERIVATION_MISMATCH"
DERIVATION_NOT_MECHANIZED = "DERIVATION_NOT_MECHANIZED"

#: A evidencia vive em CAMPO. Marcador dentro do texto seria o contrato
#: antigo entrando pela janela, e com ele a adivinhacao de escopo.
_MARCADOR_PROIBIDO = re.compile(r"\[E\d+\]")

INSTRUCAO_ESTRUTURADA = """Voce responde perguntas de Quimica do ensino
medio usando EXCLUSIVAMENTE as evidencias numeradas abaixo.

Devolva JSON com "claims" (lista) e "sufficient" (booleano).

Cada item de "claims" tem:
  "kind"  : "FACTUAL", "META" ou "CONNECTIVE"
  "text"  : o texto daquele trecho da resposta, SEM marcadores [E1]

FACTUAL  afirmacao sobre o mundo. EXIGE "support" ou "derivation".
META     observacao sobre as proprias evidencias. Nao exige suporte.
CONNECTIVE  ligacao ou transicao sem conteudo novo. Nao exige suporte.

Em "support", para CADA evidencia usada:
  "evidence" : o identificador, por exemplo "E1"
  "span"     : um trecho COPIADO LITERALMENTE daquela evidencia
  "role"     : o que essa evidencia contribui para a afirmacao

O "span" precisa ser copia exata do texto da evidencia indicada. Nao
parafraseie dentro do span - a parafrase vai no "text". Nao use um trecho
de uma evidencia e atribua a outra.

Quando a afirmacao for CALCULADA, use "derivation" no lugar de "support":
  "expression" : a conta, apenas numeros, + - * / e parenteses
  "result"     : o resultado
  "unit"       : a unidade, se houver
  "inputs"     : lista como a de "support", com os valores usados

Responda em portugues, de forma direta.
"""

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv}


def evaluate_expression(expressao: object) -> float | None:
    """Avalia uma conta simples. Devolve ``None`` quando nao da.

    SEM ``eval``. Gramatica fechada: numero, ``+ - * /``, parenteses,
    sinal unario. Qualquer outra coisa - nome, chamada, potencia,
    compreensao - sai como ``None``, que significa "nao mecanizavel", nao
    "errado".
    """
    if not isinstance(expressao, str) or not expressao.strip():
        return None
    try:
        arvore = ast.parse(expressao, mode="eval")
    except SyntaxError:
        return None

    def _calcular(no):
        if isinstance(no, ast.Expression):
            return _calcular(no.body)
        if isinstance(no, ast.Constant) and isinstance(no.value, (int, float)):
            return float(no.value)
        if isinstance(no, ast.UnaryOp) and isinstance(no.op, (ast.UAdd,
                                                              ast.USub)):
            v = _calcular(no.operand)
            return v if isinstance(no.op, ast.UAdd) else -v
        if isinstance(no, ast.BinOp) and type(no.op) in _OPS:
            return _OPS[type(no.op)](_calcular(no.left), _calcular(no.right))
        raise ValueError("fora da gramatica")

    try:
        return _calcular(arvore)
    except (ValueError, ZeroDivisionError, OverflowError, TypeError):
        return None


@dataclass(frozen=True)
class SupportSpan:
    evidence_marker: str
    span_text: str
    span_normalized: str
    role: str
    status: str
    offset: int | None
    normalization: str = SPAN_NORMALIZATION_V1

    def payload(self) -> dict[str, Any]:
        return {
            "evidence_marker": self.evidence_marker,
            "span_text": self.span_text,
            "span_normalized": self.span_normalized,
            "role": self.role,
            "status": self.status,
            "offset": self.offset,
            "normalization": self.normalization,
        }


@dataclass(frozen=True)
class Derivation:
    expression: str
    declared_result: str
    unit: str | None
    inputs: tuple[SupportSpan, ...]
    computed: float | None
    status: str

    def payload(self) -> dict[str, Any]:
        return {
            "expression": self.expression,
            "declared_result": self.declared_result,
            "unit": self.unit,
            "computed": self.computed,
            "status": self.status,
            "inputs": [i.payload() for i in self.inputs],
        }


@dataclass(frozen=True)
class ClaimRecord:
    index: int
    kind: str
    text: str
    support: tuple[SupportSpan, ...] = ()
    derivation: Derivation | None = None
    evidences_without_contribution: tuple[str, ...] = ()

    @property
    def verified(self) -> bool:
        """DERIVADO, nunca declarado pelo modelo."""
        if self.kind in (CLAIM_META, CLAIM_CONNECTIVE):
            return True
        if self.evidences_without_contribution:
            return False
        todos = list(self.support) + list(
            self.derivation.inputs if self.derivation else ())
        if not todos:
            return False
        if any(s.status != SPAN_VERIFIED for s in todos):
            return False
        if self.derivation and self.derivation.status == DERIVATION_MISMATCH:
            return False
        return True

    @property
    def unverified_reasons(self) -> tuple[str, ...]:
        """POR QUE nao verificada. Sem isto o ADMIN ve um ``False`` e
        precisa reconstruir o motivo lendo spans um a um."""
        if self.verified:
            return ()
        motivos = []
        if self.evidences_without_contribution:
            motivos.append(EVIDENCE_WITHOUT_CONTRIBUTION)
        todos = list(self.support) + list(
            self.derivation.inputs if self.derivation else ())
        if not todos:
            motivos.append(STRUCTURED_CONTRACT_VIOLATION)
        for s in todos:
            if s.status != SPAN_VERIFIED:
                motivos.append(s.status)
        if self.derivation and self.derivation.status == DERIVATION_MISMATCH:
            motivos.append(DERIVATION_MISMATCH)
        return tuple(dict.fromkeys(motivos))

    @property
    def needs_human_review(self) -> bool:
        return bool(self.derivation
                    and self.derivation.status == DERIVATION_NOT_MECHANIZED)

    def payload(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "text": self.text,
            "verified": self.verified,
            "unverified_reasons": list(self.unverified_reasons),
            "needs_human_review": self.needs_human_review,
            "support": [s.payload() for s in self.support],
            "derivation": self.derivation.payload() if self.derivation else None,
            "evidences_without_contribution": list(
                self.evidences_without_contribution),
        }


@dataclass(frozen=True)
class StructuredAnswer:
    status: str
    question: str
    claims: tuple[ClaimRecord, ...]
    sufficiency: str
    available_markers: tuple[str, ...]
    degraded: bool
    degradation_reasons: tuple[str, ...]
    input_tokens: int | None
    output_tokens: int | None
    error: str | None
    contract_errors: tuple[str, ...] = ()
    _raw: str | None = field(default=None, repr=False)

    @property
    def answer_text(self) -> str:
        """MONTADO a partir das afirmacoes. Nao existe texto paralelo."""
        return " ".join(c.text.strip() for c in self.claims if c.text.strip())

    @property
    def factual_count(self) -> int:
        return sum(1 for c in self.claims if c.kind == CLAIM_FACTUAL)

    @property
    def meta_count(self) -> int:
        return sum(1 for c in self.claims if c.kind == CLAIM_META)

    @property
    def connective_count(self) -> int:
        return sum(1 for c in self.claims if c.kind == CLAIM_CONNECTIVE)

    @property
    def span_case_mismatches(self) -> int:
        return sum(1 for c in self.claims
                   for s in list(c.support) + list(
                       c.derivation.inputs if c.derivation else ())
                   if s.status == SPAN_CASE_MISMATCH)

    @property
    def delivery_block_reason(self) -> str | None:
        """Precedencia declarada: contrato, suficiencia, verificacao.

        O contrato vem primeiro porque sem ele nao ha o que avaliar; a
        suficiencia antes da verificacao porque e declaracao explicita do
        modelo sobre o material, nao defeito do que ele produziu.
        """
        if self.status != STRUCTURED_OK:
            return self.status
        if self.sufficiency == SUFFICIENCY_DENIED:
            return EVIDENCE_DECLARED_INSUFFICIENT
        if any(not c.verified for c in self.claims):
            return STRUCTURED_UNVERIFIED_CLAIM
        return None

    @property
    def deliverable(self) -> bool:
        return self.delivery_block_reason is None

    @property
    def needs_human_review(self) -> bool:
        return (self.sufficiency == SUFFICIENCY_DENIED
                or any(c.needs_human_review for c in self.claims))

    def admin_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "question": self.question,
            "answer_text": self.answer_text,
            "deliverable": self.deliverable,
            "delivery_block_reason": self.delivery_block_reason,
            "needs_human_review": self.needs_human_review,
            "sufficiency": self.sufficiency,
            "available_markers": list(self.available_markers),
            "factual_count": self.factual_count,
            "meta_count": self.meta_count,
            "connective_count": self.connective_count,
            "span_case_mismatches": self.span_case_mismatches,
            "contract_errors": list(self.contract_errors),
            "degraded": self.degraded,
            "degradation_reasons": list(self.degradation_reasons),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "error": self.error,
            "claims": [c.payload() for c in self.claims],
        }


class StructuredAnswerer:
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
    ) -> StructuredAnswer:
        disponiveis = tuple(sorted(context.marker_set(),
                                   key=lambda m: int(m[1:])))
        textos = {e.marker: context.evidence_text(e.marker)
                  for e in context.evidences}

        def _falha(status, **extra):
            base = dict(
                status=status, question=question, claims=(),
                sufficiency=_classificar_suficiencia(None),
                available_markers=disponiveis, degraded=retrieval_degraded,
                degradation_reasons=tuple(degradation_reasons),
                input_tokens=None, output_tokens=None, error=None,
            )
            base.update(extra)
            return StructuredAnswer(**base)

        if not context.evidences:
            return _falha(NO_EVIDENCE)
        if retrieval_degraded and not allow_degraded:
            return _falha(DEGRADED_RETRIEVAL)

        prompt = (f"{INSTRUCAO_ESTRUTURADA}\n\nPERGUNTA: {question}\n\n"
                  f"EVIDENCIAS:\n\n{context.prompt_payload()}\n")
        try:
            resultado = await self._provider.generate(
                TextGenerationRequest(prompt=prompt, model=self._model))
        except ProviderError as erro:
            return _falha(PROVIDER_FAILED, error=type(erro).__name__)

        try:
            corpo = json.loads(resultado.text)
            if not isinstance(corpo, dict):
                raise TypeError("corpo nao e objeto")
        except (json.JSONDecodeError, TypeError) as erro:
            return _falha(PROVIDER_INVALID_RESPONSE, error=type(erro).__name__,
                          input_tokens=resultado.input_tokens,
                          output_tokens=resultado.output_tokens,
                          _raw=resultado.text)

        comum = dict(
            question=question,
            sufficiency=_classificar_suficiencia(corpo.get("sufficient")),
            available_markers=disponiveis, degraded=retrieval_degraded,
            degradation_reasons=tuple(degradation_reasons),
            input_tokens=resultado.input_tokens,
            output_tokens=resultado.output_tokens,
            error=None, _raw=resultado.text,
        )

        bruto = corpo.get("claims")
        if not isinstance(bruto, list) or not bruto:
            return StructuredAnswer(
                status=STRUCTURED_CONTRACT_VIOLATION, claims=(),
                contract_errors=("claims ausente, vazio ou nao e lista",),
                **comum)

        claims, erros = [], []
        for i, item in enumerate(bruto):
            registro, falhas = self._um_claim(i, item, textos, disponiveis)
            erros.extend(falhas)
            if registro is not None:
                claims.append(registro)

        if erros:
            return StructuredAnswer(
                status=STRUCTURED_CONTRACT_VIOLATION,
                claims=tuple(claims), contract_errors=tuple(erros), **comum)
        return StructuredAnswer(status=STRUCTURED_OK, claims=tuple(claims),
                                **comum)

    def _um_claim(self, i, item, textos, disponiveis):
        erros = []
        if not isinstance(item, dict):
            return None, [f"claim {i}: nao e objeto"]
        kind = CLAIM_KINDS.get(str(item.get("kind", "")).upper())
        if kind is None:
            return None, [f"claim {i}: kind desconhecido {item.get('kind')!r}"]
        texto = item.get("text")
        if not isinstance(texto, str) or not texto.strip():
            return None, [f"claim {i}: text ausente ou vazio"]
        if _MARCADOR_PROIBIDO.search(texto):
            erros.append(f"claim {i}: marcador no texto; a evidencia vai "
                         f"no campo, nao no corpo")

        apoios = self._spans(item.get("support"), textos, disponiveis,
                             i, erros)
        derivacao = self._derivacao(item.get("derivation"), textos,
                                    disponiveis, i, erros)

        if kind == CLAIM_FACTUAL and not apoios and derivacao is None:
            erros.append(f"claim {i}: FACTUAL sem support nem derivation")

        declaradas = {m for m in (item.get("evidence") or [])
                      if isinstance(m, str)}
        declaradas |= {s.evidence_marker for s in apoios}
        if derivacao:
            declaradas |= {s.evidence_marker for s in derivacao.inputs}
        com_span = {s.evidence_marker for s in apoios}
        if derivacao:
            com_span |= {s.evidence_marker for s in derivacao.inputs}
        sem_contribuicao = tuple(sorted(declaradas - com_span))
        for m in declaradas:
            if m not in disponiveis:
                erros.append(f"claim {i}: evidencia {m!r} nao existe no "
                             f"contexto")

        return ClaimRecord(
            index=i, kind=kind, text=texto, support=apoios,
            derivation=derivacao,
            evidences_without_contribution=sem_contribuicao,
        ), erros

    def _spans(self, bruto, textos, disponiveis, i, erros):
        if bruto is None:
            return ()
        if not isinstance(bruto, list):
            erros.append(f"claim {i}: support nao e lista")
            return ()
        saida = []
        for j, s in enumerate(bruto):
            if not isinstance(s, dict):
                erros.append(f"claim {i}.support[{j}]: nao e objeto")
                continue
            marcador = str(s.get("evidence", ""))
            span = s.get("span", "")
            if not isinstance(span, str):
                erros.append(f"claim {i}.support[{j}]: span nao e texto")
                continue
            if marcador not in disponiveis:
                erros.append(f"claim {i}.support[{j}]: evidencia "
                             f"{marcador!r} nao existe no contexto")
                continue
            status, pos = verify_span(span, textos.get(marcador, ""))
            saida.append(SupportSpan(
                evidence_marker=marcador, span_text=span,
                span_normalized=normalize_span(span),
                role=str(s.get("role", "")), status=status, offset=pos))
        return tuple(saida)

    def _derivacao(self, bruto, textos, disponiveis, i, erros):
        if bruto is None:
            return None
        if not isinstance(bruto, dict):
            erros.append(f"claim {i}: derivation nao e objeto")
            return None
        inputs = self._spans(bruto.get("inputs"), textos, disponiveis,
                             i, erros)
        if not inputs:
            # Derivacao sem insumo nao esta ancorada em nada: a conta
            # pode fechar sozinha e mesmo assim nao vir das evidencias.
            # E a forma mais barata de fabricar um numero com aparencia
            # de verificado.
            erros.append(f"claim {i}: derivation sem inputs verificaveis")
        expressao = str(bruto.get("expression", ""))
        declarado = str(bruto.get("result", ""))
        calculado = evaluate_expression(expressao)
        if calculado is None:
            status = DERIVATION_NOT_MECHANIZED
        else:
            try:
                esperado = float(declarado.replace("−", "-").strip())
                status = (DERIVATION_VERIFIED
                          if abs(calculado - esperado) <= 1e-6 * max(
                              1.0, abs(esperado))
                          else DERIVATION_MISMATCH)
            except ValueError:
                status = DERIVATION_NOT_MECHANIZED
        return Derivation(expression=expressao, declared_result=declarado,
                          unit=bruto.get("unit"), inputs=inputs,
                          computed=calculado, status=status)
