"""Remove os marcadores de evidencia para a apresentacao publica.

DEPOIS DA VALIDACAO, E ISSO E UMA PROPRIEDADE
=============================================

Os marcadores sao a UNICA evidencia verificavel de que a resposta cita o
que diz citar. Removidos antes da conferencia, a prova desaparece e a
rastreabilidade vira declaracao.

Por isso esta peca e PURA e nao conhece validacao, estado nem audiencia:
quem a chama ja validou. ``answer_text_public`` e DERIVADO; nunca
substitui o original.

NAO CONSERTA FRASE
==================

Se remover o marcador deixar o texto quebrado, o texto nao e reescrito -
e REPROVADO, com o defeito nomeado, e a resposta deixa de ser entregavel.
Pedir a um modelo que conserte o portugues antes de publicar trocaria um
problema visivel por um invisivel.

O LIMITE, DECLARADO EM VOZ ALTA
===============================

Corrupcao SEMANTICA nem sempre deixa marca textual. ``segundo [E1], a
concentracao`` vira ``segundo, a concentracao``: sem espaco duplo, sem
pontuacao orfa, sem nada que uma regra sintatica pegue.

Para esse caso ha ``_REGENTES`` - uma lista FECHADA de palavras que pedem
complemento. E heuristica, e falha para o lado seguro: na duvida,
reprova. Fora dessa lista o risco permanece, e esta registrado em vez de
disfarcado.

A alternativa seria instruir o modelo a so por marcador no fim de frase.
Isso e mudanca de prompt, e nao foi feita aqui.
"""

from __future__ import annotations

import re

MARKER_RESIDUE = "MARKER_RESIDUE"
LEADING_PUNCTUATION = "LEADING_PUNCTUATION"
DOUBLED_PUNCTUATION = "DOUBLED_PUNCTUATION"
EMPTY_AFTER_STRIP = "EMPTY_AFTER_STRIP"
UNBALANCED_BRACKETS = "UNBALANCED_BRACKETS"
GOVERNING_WORD_LEFT_DANGLING = "GOVERNING_WORD_LEFT_DANGLING"

#: Todo defeito que esta peca sabe nomear. Fonte unica de verdade: o teste
#: confere que nenhum artefato devolvido esta fora desta tupla.
ARTIFACTS: tuple[str, ...] = (
    MARKER_RESIDUE,
    LEADING_PUNCTUATION,
    DOUBLED_PUNCTUATION,
    EMPTY_AFTER_STRIP,
    UNBALANCED_BRACKETS,
    GOVERNING_WORD_LEFT_DANGLING,
)

#: A MESMA forma que a validacao reconhece. ``E1`` sem colchete nao e
#: tocado: pode ser conteudo legitimo, e adivinhar intencao e o que se
#: quer evitar.
_MARCADOR = re.compile(r"\[E\d+\]")

#: Palavras que pedem complemento. Se um marcador vier logo depois de uma
#: delas, remove-lo deixa a oracao pendurada. Lista curta de proposito:
#: cada entrada e uma reprovacao a mais, e reprovar demais tambem custa.
_REGENTES = frozenset({
    "segundo", "conforme", "consoante", "com", "em", "na", "no", "nas",
    "nos", "pela", "pelo", "pelas", "pelos", "por", "de", "do", "da",
    "dos", "das", "ver", "veja", "vide", "cf",
})

_PALAVRA_FINAL = re.compile(r"([A-Za-zÀ-ÿ]+)[\s]*$")
_PONTUACAO_DOBRADA = re.compile(r"[,;:!?]\s*[.,;:!?]|(?<!\.)\.\s*[,;:!?]")


def strip_markers(texto: str) -> tuple[str, tuple[str, ...]]:
    """Devolve ``(texto_sem_marcadores, artefatos)``.

    Nao muta a entrada. Entrada que nao seja ``str`` levanta ``TypeError``
    em vez de adivinhar - um ``None`` silenciosamente virando ``""``
    publicaria resposta vazia como se fosse resposta.
    """
    if not isinstance(texto, str):
        raise TypeError(f"strip_markers espera str, recebeu {type(texto).__name__}")

    artefatos: list[str] = []

    # A checagem de regencia olha o ORIGINAL: depois da remocao a palavra
    # regente ja esta colada no que vier a seguir.
    for m in _MARCADOR.finditer(texto):
        antes = texto[: m.start()]
        casou = _PALAVRA_FINAL.search(antes)
        if casou and casou.group(1).lower() in _REGENTES:
            artefatos.append(GOVERNING_WORD_LEFT_DANGLING)
            break

    limpo = _MARCADOR.sub("", texto)
    limpo = re.sub(r"[ \t]{2,}", " ", limpo)
    limpo = re.sub(r"[ \t]+([.,;:!?])", r"\1", limpo)
    limpo = re.sub(r"[ \t]+\n", "\n", limpo)
    limpo = limpo.strip()

    # Defeito INTRODUZIDO pela remocao, nao defeito que ja estava la.
    # ``Fim!! [E1]`` tem pontuacao dobrada no original: reprovar por isso
    # seria culpar a remocao por um cacoete do modelo, e bloquearia
    # resposta boa. So conta o que PIOROU.
    introduzidos = _defeitos_de_pontuacao(limpo) - _defeitos_de_pontuacao(texto)
    artefatos.extend(sorted(introduzidos))

    if texto.strip() and not limpo:
        artefatos.append(EMPTY_AFTER_STRIP)

    # Estes dois sao sobre MARCADOR VAZANDO para a saida publica, nao
    # sobre dano da remocao - valem mesmo que ja estivessem no original.
    # ``Texto [E1 sem fechar`` publica vocabulario interno quebrado.
    if _MARCADOR.search(limpo):
        artefatos.append(MARKER_RESIDUE)
    if limpo.count("[") != limpo.count("]"):
        artefatos.append(UNBALANCED_BRACKETS)

    return limpo, tuple(dict.fromkeys(artefatos))


def _defeitos_de_pontuacao(texto: str) -> set[str]:
    achados = set()
    if texto[:1] in {".", ",", ";", ":", "!", "?"}:
        achados.add(LEADING_PUNCTUATION)
    if _PONTUACAO_DOBRADA.search(texto):
        achados.add(DOUBLED_PUNCTUATION)
    return achados
