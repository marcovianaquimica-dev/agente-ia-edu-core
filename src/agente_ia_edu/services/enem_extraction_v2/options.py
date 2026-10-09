"""Reconhecimento de alternativas (C3). PROTOTIPO EXPERIMENTAL.

O defeito da V1 e um limiar arbitrario: ``^\\s*([A-E])\\s{2,}``, dois ou mais
espacos depois da letra. Levantamento das formas REAIS, nos 12 cadernos:

    pypdf     1ESPACO 3602 | COLADO 2760 | LETRA_SOZINHA 1332 | 2+ESPACOS 1171
    pymupdf   COLADO 2587 | TAB_SO 1888 | 1ESPACO 1867 | TAB+ESPACO 1840
              TAB 1722 | 2+ESPACOS 452 | LETRA_SOZINHA 346

Nao existe caderno em que exigir dois espacos seja melhor do que exigir um.
Em 2022 e 2023 o ``pypdf`` nao produz uma unica linha com dois espacos, e o
extrator enxerga zero alternativas em 188 itens perfeitamente detectados.

A V2 aceita as formas medidas e **nao** aceita ``COLADO`` (letra grudada na
palavra), porque ``'Alguém muito recentemente...'`` nao e a alternativa A.

O risco de afrouxar e falso positivo. Exemplo real, 2023 D1:

    'A part of the main.'

Por isso a regra de aceitacao nao e "a linha comeca com A-E". E:

    existe uma corrida A, B, C, D, E, **nessa ordem**, **consecutiva** entre
    os marcadores candidatos, todos com texto nao vazio, e toma-se a
    **ultima** corrida do corpo - alternativas encerram o item.

Uma linha de prosa iniciada por "A " so seria confundida se fosse seguida, em
ordem e sem marcador intercalado, por quatro outras linhas de prosa iniciadas
por B, C, D e E. Quando ha ambiguidade, a V2 prefere nao reconstruir o item a
reconstrui-lo errado.
"""

from __future__ import annotations

import re

from .contracts import OptionCandidate

# Letra no inicio da linha, seguida de um separador explicito ou do fim da
# linha. O lookahead e o que exclui COLADO.
PADRAO_MARCADOR = re.compile(r"(?m)^[ \t]*([A-E])(?=[ \t)\.\:–—-]|$)")

LETRAS = list("ABCDE")


def _forma(linha: str, letra_em: int) -> str:
    """Rotula como o marcador apareceu, para rastreabilidade."""
    resto = linha[letra_em + 1:]
    if resto == "":
        return "LETRA_SOZINHA"
    if resto.startswith("\t"):
        return "TAB+ESPACO" if resto[1:2] == " " else "TAB"
    if resto.startswith("  "):
        return "2+ESPACOS"
    if resto.startswith(" "):
        return "1ESPACO"
    return f"PONTUACAO({resto[0]})"


def _limpar(texto: str) -> str:
    return " ".join(texto.replace("\f", " ").split())


def marcadores(corpo: str) -> list[tuple[int, str, str, str]]:
    """Todos os marcadores candidatos: (posicao, letra, texto, forma).

    O texto de um marcador vai ate o marcador seguinte ou ate o fim do corpo.
    """
    achados = list(PADRAO_MARCADOR.finditer(corpo))
    saida = []
    for indice, achado in enumerate(achados):
        fim = achados[indice + 1].start() if indice + 1 < len(achados) else len(corpo)
        inicio_linha = corpo.rfind("\n", 0, achado.start()) + 1
        fim_linha = corpo.find("\n", achado.start())
        if fim_linha == -1:
            fim_linha = len(corpo)
        linha = corpo[inicio_linha:fim_linha]
        forma = _forma(linha, achado.end(1) - 1 - inicio_linha)
        bruto = corpo[achado.end(1):fim]
        # remove apenas o separador imediato, preservando o texto
        bruto = re.sub(r"^[ \t)\.\:–—-]+", "", bruto, count=1)
        saida.append((achado.start(), achado.group(1), _limpar(bruto), forma))
    return saida


# Marcador duplicado pela camada de texto. Achado pela adjudicacao humana da
# amostra aleatoria: 24 das 26 reprovas tinham a mesma observacao. Nos
# cadernos de 2022 e 2023, com pypdf, a letra vem DUAS vezes:
#
#     'A A desenho cru da realidade dramatica dos retirantes.'
#
# Nao da para remover cegamente: em portugues 'A' e artigo e 'E' e conjuncao,
# entao 'A A casa e bonita' pode ser marcador + artigo legitimo.
#
# O discriminador e ESTRUTURAL: a duplicacao vem do renderizador e portanto
# atinge AS CINCO alternativas. Um artigo atinge uma ou duas. A regra so
# dispara quando as cinco repetem a propria letra, seguida de separador.
SEPARADOR_APOS_LETRA = re.compile(r"^([A-E])[ \t)\.\:\u2013\u2014-]+(?=\S)")


def _marcador_duplicado(textos: list[str], letras: list[str]) -> bool:
    """As cinco alternativas comecam com a propria letra e um separador?"""
    if len(textos) != 5:
        return False
    for texto, letra in zip(textos, letras):
        achado = SEPARADOR_APOS_LETRA.match(texto)
        if achado is None or achado.group(1) != letra:
            return False
    return True


def _remover_marcador_duplicado(textos: list[str]) -> list[str]:
    return [SEPARADOR_APOS_LETRA.sub("", t, count=1) for t in textos]


def extrair(corpo: str) -> tuple[list[OptionCandidate], str]:
    """Devolve (opcoes, forma_predominante).

    Devolve lista vazia quando nao ha corrida A-E integra. Nao ha caminho de
    contingencia parcial: um item com 3 alternativas e um item que a V2 nao
    sabe reconstruir, e sera marcado como incompleto.
    """
    candidatos = [c for c in marcadores(corpo) if c[2]]
    if len(candidatos) < 5:
        return [], ""
    melhor = None
    for i in range(len(candidatos) - 4):
        janela = candidatos[i:i + 5]
        if [c[1] for c in janela] != LETRAS:
            continue
        if any(janela[j][0] >= janela[j + 1][0] for j in range(4)):
            continue
        melhor = janela  # fica com a ULTIMA corrida valida
    if melhor is None:
        return [], ""
    letras = [c[1] for c in melhor]
    textos = [c[2] for c in melhor]
    if _marcador_duplicado(textos, letras):
        limpos = _remover_marcador_duplicado(textos)
        if all(t.strip() for t in limpos):
            textos = limpos
    opcoes = [OptionCandidate(letra=le, texto=tx, inicio=c[0], forma=c[3])
              for le, tx, c in zip(letras, textos, melhor)]
    formas = [o.forma for o in opcoes]
    predominante = max(set(formas), key=formas.count)
    return opcoes, predominante


# A ultima alternativa nao tem marcador depois dela, entao absorve tudo que
# vier ate o proximo rotulo de item. Depois de remover mobilia de pagina
# sobra um caso que NAO e mobilia: o material ENTRE BLOCOS da prova -
# divisoria de area e coletanea compartilhada - que fica fisicamente depois
# da ultima alternativa do ultimo item do bloco.
#
# Medido nos 12 cadernos, apos o endurecimento, sobre 1.074 itens com corrida
# completa, a razao len(E) / mediana(len(A..D)) distribui assim:
#     < 1,5 -> 90,6% |  1,5-2 -> 4,6% |  2-3 -> 2,7%
#     3-5   ->  0,7% |  5-10  -> 0,5% | >=10 -> 0,9%
# e a cauda e dominada pelos itens 45, 90, 135 e 180 - exatamente os ultimos
# de cada bloco do ENEM.
#
# O limiar 3,0 deixa 97,9% dos itens intactos e marca 23. A V2 NAO corta o
# texto: corta-lo exigiria adivinhar onde termina a alternativa. Ela
# SINALIZA, e o item vai para revisao humana. Transformar um erro silencioso
# num erro declarado e o objetivo desta etapa.
RAZAO_MAXIMA_DA_ULTIMA_ALTERNATIVA = 3.0


def ultima_alternativa_anomala(opcoes: list[OptionCandidate]) -> bool:
    """A ultima alternativa e desproporcional as outras quatro?"""
    if len(opcoes) != 5:
        return False
    import statistics

    mediana = statistics.median(len(o.texto) for o in opcoes[:4])
    if mediana <= 0:
        return False
    return len(opcoes[4].texto) / mediana > RAZAO_MAXIMA_DA_ULTIMA_ALTERNATIVA


def inicio_das_alternativas(corpo: str, opcoes: list[OptionCandidate]) -> int | None:
    """Onde o enunciado termina. ``None`` quando nao ha corrida reconhecida."""
    return opcoes[0].inicio if opcoes else None
