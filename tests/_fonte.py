"""Ler o CÓDIGO de um módulo, sem a prosa que o explica.

POR QUE ISTO EXISTE
====================
Vários testes deste projeto perguntam "este módulo conhece X?" — se o
contrato depende de um prefixo, se a camada genérica menciona química, se o
seletor sabe o nome de uma micro-habilidade. A varredura mais óbvia é ler o
arquivo e procurar a string.

E ela está errada, de um jeito que só aparece depois: a docstring que
EXPLICA por que X não é usado contém X. Procurar no texto acusa a
explicação.

Isso me pegou quatro vezes neste trabalho:

    "SOND-"   na docstring que diz que o prefixo não é o contrato
    "ia"      dentro de "enunc-IA-do"
    "qa_purpose"  no comentário que diz por que a chave não está lá
    "NH"      dentro de "CO-NH-ECE", e "mol" dentro de "g/mol"

As duas lições viraram as duas funções daqui: varrer CÓDIGO, não texto, e
comparar PALAVRA INTEIRA, não substring.

Este arquivo não começa com `test_`, então o pytest não o coleta.
"""

from __future__ import annotations

import io
import re
import tokenize


def codigo(caminho) -> str:
    """O arquivo sem docstrings e sem comentários.

    O que sobra é o que o módulo de fato faz. Uma string que só aparece na
    prosa some; uma que o código usa, fica.
    """
    fonte = caminho.read_text(encoding="utf-8")
    pedacos: list[str] = []
    anterior = tokenize.INDENT
    for tok in tokenize.generate_tokens(io.StringIO(fonte).readline):
        if tok.type == tokenize.COMMENT:
            continue
        # String na posição de statement é docstring — de módulo, de classe
        # ou de função.
        if tok.type == tokenize.STRING and anterior in (
                tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE,
                tokenize.NL, tokenize.ENCODING):
            anterior = tok.type
            continue
        pedacos.append(tok.string)
        if tok.type != tokenize.NL:
            anterior = tok.type
    return " ".join(pedacos)


def menciona(texto: str, termo: str) -> bool:
    """O termo aparece como PALAVRA INTEIRA?

    `menciona("enunciado", "ia")` é False. `menciona("a ia decide", "ia")`
    é True. Para termos com caracteres não-alfanuméricos (`g/mol`, `SOND-`)
    a borda é "não colado em letra ou dígito".
    """
    return re.search(rf"(?<![0-9A-Za-z_]){re.escape(termo)}(?![0-9A-Za-z_])",
                     texto) is not None


__all__ = ["codigo", "menciona"]
