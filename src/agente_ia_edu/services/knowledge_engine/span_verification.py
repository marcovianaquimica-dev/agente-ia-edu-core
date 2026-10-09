"""Verificacao deterministica de span: o suporte vira propriedade checavel.

A MESMA FORMA QUE O MARCADOR JA USA
===================================

O modelo declara ``[E3]``, o codigo confere que ``E3`` existe. Aqui ele
declara um TRECHO LITERAL e o codigo confere, por substring, que aquele
trecho esta NAQUELE chunk.

Nenhum juiz, nenhum score, nenhuma semantica. O modelo declara, o codigo
confere - que e como a inteligencia fica no sistema e nao no prompt.

O QUE ISTO NAO PROVA, E PRECISA SER DITO
========================================

Que o trecho SUSTENTA a afirmacao. Um modelo pode citar um chunk real e
quotar dele uma frase irrelevante: a verificacao passa, o suporte nao
existe.

Esta camada troca "citou um chunk real?" por "quotou texto real daquele
chunk?". Estritamente mais forte, e ainda nao "esse texto sustenta o que
voce disse?". Fechar o resto exige julgamento, e julgamento nao entra
nesta fase.

AS REGRAS SAO MEDIDAS, NAO SUPOSTAS
===================================

Cada normalizacao abaixo tem caso real contado nos 5.945 chunks do
corpus. Regra sem caso real nao entra: cada normalizacao a mais aumenta a
chance de casar o que nao deveria, entao "por simetria" nao e
justificativa.

CAIXA: SENSIVEL, COM SAIDA NOMEADA
==================================

``Co`` e ``CO`` nao sao a mesma coisa, e o chunk que a derivacao de N8
cita contem apenas ``Entalpia`` maiusculo. Aceitar caixa diferente em
silencio esconderia os dois problemas de uma vez. Quando so a caixa
difere, o resultado e ``SPAN_CASE_MISMATCH`` - visivel, medivel, e nao
aceito.
"""

from __future__ import annotations

import re
import unicodedata

#: Versao da norma. Imutavel: mudar qualquer regra exige ``V2``, e os
#: registros antigos continuam dizendo sob qual norma foram aceitos.
SPAN_NORMALIZATION_V1 = "SPAN_NORMALIZATION_V1"

SPAN_VERIFIED = "SPAN_VERIFIED"
SPAN_NOT_FOUND = "SPAN_NOT_FOUND"
SPAN_CASE_MISMATCH = "SPAN_CASE_MISMATCH"
SPAN_EMPTY = "SPAN_EMPTY"

SPAN_STATUSES: tuple[str, ...] = (
    SPAN_VERIFIED, SPAN_NOT_FOUND, SPAN_CASE_MISMATCH, SPAN_EMPTY,
)

#: Hifenizacao de quebra de linha do extrator de PDF: "comenta- rios".
#: Medida em 1.878 chunks (32%) com hifen ASCII - e por isso a regra
#: procura o hifen ASCII, depois do NFKC.
_HIFEN_DE_QUEBRA = re.compile(r"(\w)-\s+(\w)")

#: minus em 820 chunks, en-dash em 1.816, em-dash em 165.
_TRACOS = re.compile(r"[−–—]")

#: aspas curvas em 958 + 951 + 10 + 117 chunks.
_ASPAS = str.maketrans({"“": '"', "”": '"',
                        "‘": "'", "’": "'"})

#: 2.711 chunks (46%) tem quebra de linha.
_ESPACO = re.compile(r"\s+")


def normalize_span(texto: str) -> str:
    """Aplica ``SPAN_NORMALIZATION_V1``.

    A ORDEM IMPORTA. ``NFKC`` roda primeiro porque ele PRODUZ um MINUS
    SIGN: ``NFKC("O2⁻")`` e ``"O2−"``. Se a unificacao de tracos viesse
    antes, esse minus nasceria depois dela e sobreviveria.
    """
    if not isinstance(texto, str):
        raise TypeError(
            f"normalize_span espera str, recebeu {type(texto).__name__}")
    t = unicodedata.normalize("NFKC", texto)
    t = _TRACOS.sub("-", t)
    t = _HIFEN_DE_QUEBRA.sub(r"\1\2", t)
    t = t.translate(_ASPAS)
    t = _ESPACO.sub(" ", t)
    return t.strip()


def verify_span(span: str, chunk_text: str) -> tuple[str, int | None]:
    """``(status, posicao)``. A posicao e no chunk NORMALIZADO.

    Nao recebe o conjunto de chunks: recebe UM. Procurar em qualquer
    chunk do contexto transformaria "quotou daquela evidencia" em
    "quotou de alguma" - e e exatamente a diferenca que a ma atribuicao
    explora.
    """
    if not isinstance(span, str):
        raise TypeError(
            f"verify_span espera str em span, recebeu {type(span).__name__}")
    if not isinstance(chunk_text, str):
        raise TypeError("verify_span espera str em chunk_text, recebeu "
                        f"{type(chunk_text).__name__}")

    alvo = normalize_span(span)
    if not alvo:
        return SPAN_EMPTY, None

    fonte = normalize_span(chunk_text)
    pos = fonte.find(alvo)
    if pos >= 0:
        return SPAN_VERIFIED, pos
    if alvo.casefold() in fonte.casefold():
        return SPAN_CASE_MISMATCH, None
    return SPAN_NOT_FOUND, None
