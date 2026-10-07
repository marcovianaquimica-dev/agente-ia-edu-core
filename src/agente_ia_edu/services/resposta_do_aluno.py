"""O QUE O ALUNO ESCREVEU, E O QUE DISSO SE PODE AFIRMAR.

A CAMADA QUE FALTAVA
=====================
Ate aqui o aluno so podia clicar numa letra. Para o Edu conversar, ele
precisa aceitar "15", "15 g/mol", "tres", "nao sei" - e precisa fazer isso
sem inventar entendimento.

Entre o texto cru e qualquer decisao pedagogica entram duas etapas:

    texto cru  ->  RESPOSTA NORMALIZADA  ->  OBSERVACAO

A primeira pergunta "o que ele escreveu?". A segunda, "o que isso significa
para esta pergunta?". Pular direto do texto para "acertou" e o atalho que
transforma ambiguidade em conclusao - e e justamente nas bordas (um "nao
sei", um "3 ou 4") que esta a informacao pedagogica mais util.

OBSERVACAO NAO E EVIDENCIA DE DOMINIO
======================================
Observar que a resposta esta correta e dizer o que se viu. Se isso conta
para o mapa do aluno e decisao do Evidence Engine, com as regras dele, e
depende de QUANTO APOIO havia. Nenhum valor deste modulo significa mastery,
e ha teste varrendo as constantes atras de "MASTER", "DOMIN", "LEARNED".

FALHA FECHADO
==============
"3 ou 4" nao vira 3. "nao sei se e 3" nao vira 3 nem vira desistencia. Um
texto que o sistema nao entende com seguranca vira AMBIGUO, e quem chamou
pergunta de novo - porque inventar entendimento e pior que admitir que nao
entendeu.

E E DETERMINISTICO
===================
Nada aqui chama modelo. "17" == 17 nao e trabalho para uma LLM, e fazer
dela a primeira opcao tornaria o percurso essencial refem de um timeout.
A interpretacao semantica, quando vier, entra como camada DE CIMA - para o
que este modulo devolver como AMBIGUO, nunca no lugar dele.

NAO CONHECE A DISCIPLINA
=========================
Nenhuma palavra de quimica aqui, e ha teste. O que se espera de cada
pergunta (numero, texto, alternativa) e o gabarito dela vem de quem chama.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# O QUE O INSTRUMENTO PEDE. A mesma entrada significa coisas diferentes
# conforme isto: "14 + 3" e ambiguo quando se espera UM numero, e e uma
# resposta perfeitamente boa quando se pergunta "como voce pensou?".
ESPERA_NUMERO = "NUMERIC"
ESPERA_TEXTO = "SHORT_TEXT"
ESPERA_ESCOLHA = "CHOICE"

# O QUE SE CONSEGUIU LER.
TIPO_NUMERO = "NUMERIC"
TIPO_TEXTO = "SHORT_TEXT"
TIPO_ESCOLHA = "CHOICE"
TIPO_NAO_SEI = "UNKNOWN"
TIPO_AMBIGUO = "AMBIGUOUS"
TIPO_VAZIO = "EMPTY"

# O QUE ISSO SIGNIFICA PARA A PERGUNTA. Nenhum destes valores significa
# dominio - ver o cabecalho.
OBS_CORRETA = "CORRECT_RESPONSE"
OBS_INCORRETA = "INCORRECT_RESPONSE"
OBS_NAO_SEI = "UNKNOWN_RESPONSE"
OBS_AMBIGUA = "AMBIGUOUS_RESPONSE"
OBS_SEM_RESPOSTA = "EMPTY_RESPONSE"
OBS_REGISTRADA = "RECORDED_RESPONSE"

# Comparacao de grandezas: "17" e "17,0" sao a mesma resposta. A folga e
# para a GRAFIA, nao para a conta - nao e um corte pedagogico, e por isso
# nao vem de politica nenhuma.
_FOLGA = 1e-9

# "Nao sei" em suas formas, e tambem o pedido de ajuda: as duas coisas
# dizem "nao consigo seguir daqui sozinho", que e a mesma informacao
# pedagogica. Normalizadas sem acento, para "nao sei" e "não sei" caiirem
# no mesmo lugar.
_DESISTENCIAS = (
    "nao sei", "nao lembro", "nao faco ideia", "nao tenho ideia",
    "nenhuma ideia", "sei la", "nao entendi", "nao consigo",
    "me ajuda", "me ajude", "pode ajudar", "preciso de ajuda", "socorro",
    "ajuda",
)

# Numeros por extenso, em portugues. Ate vinte porque e o que as
# microperguntas deste tipo pedem - contagens e somas pequenas. Acrescentar
# mais e trivial; inventar um parser universal nao e o trabalho deste modulo.
_POR_EXTENSO = {
    "zero": 0, "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3,
    "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9,
    "dez": 10, "onze": 11, "doze": 12, "treze": 13, "catorze": 14,
    "quatorze": 14, "quinze": 15, "dezesseis": 16, "dezessete": 17,
    "dezoito": 18, "dezenove": 19, "vinte": 20,
}

_NUMERO = re.compile(r"\d+(?:[.,]\d+)?")
_SO_LETRA = re.compile(r"^[A-Ea-e]$")


@dataclass(frozen=True)
class RespostaNormalizada:
    """O que ele escreveu, lido - e o bruto preservado ao lado.

    O bruto fica porque e ele que vai para a tela da conversa: o aluno
    precisa ver o que ELE disse, nao a nossa leitura do que ele disse.
    """

    bruto: str
    tipo: str
    numero: float | None = None
    texto: str | None = None


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn")


def _numeros_de(limpo: str) -> list[float]:
    """Todo numero do texto, em algarismo ou por extenso, sem repetidos.

    Sem repetidos porque "3, tres" diz a mesma coisa duas vezes e nao e
    ambiguidade. "3 ou 4" sao dois valores diferentes, e e.
    """
    achados: list[float] = []
    for bruto in _NUMERO.findall(limpo):
        achados.append(float(bruto.replace(",", ".")))
    for palavra in re.findall(r"[a-z]+", limpo):
        if palavra in _POR_EXTENSO:
            achados.append(float(_POR_EXTENSO[palavra]))
    unicos: list[float] = []
    for n in achados:
        if not any(abs(n - j) < _FOLGA for j in unicos):
            unicos.append(n)
    return unicos


def normalizar(bruto: str | None, *, espera: str) -> RespostaNormalizada:
    """Le a resposta segundo o que a pergunta pede.

    A ordem das decisoes importa:

    1. VAZIO antes de tudo - nao e desistencia nem ambiguidade, e ausencia.
    2. DESISTENCIA antes de procurar numero. Mas se houver numero junto, e
       AMBIGUO: "nao sei se e 3" nao e uma resposta nem uma desistencia, e
       escolher um dos dois seria inventar.
    3. So entao a leitura propriamente dita.
    """
    original = bruto if bruto is not None else ""
    limpo = _sem_acento(str(original)).strip().lower()
    if not limpo:
        return RespostaNormalizada(bruto=original, tipo=TIPO_VAZIO)

    numeros = _numeros_de(limpo)
    desistiu = any(m in limpo for m in _DESISTENCIAS)
    if desistiu:
        if numeros:
            return RespostaNormalizada(bruto=original, tipo=TIPO_AMBIGUO)
        return RespostaNormalizada(bruto=original, tipo=TIPO_NAO_SEI)

    if espera == ESPERA_ESCOLHA:
        marcado = str(original).strip()
        if _SO_LETRA.match(marcado):
            return RespostaNormalizada(bruto=original, tipo=TIPO_ESCOLHA,
                                       texto=marcado.upper())
        return RespostaNormalizada(bruto=original, tipo=TIPO_AMBIGUO)

    if espera == ESPERA_NUMERO:
        if len(numeros) == 1:
            return RespostaNormalizada(bruto=original, tipo=TIPO_NUMERO,
                                       numero=numeros[0])
        # Nenhum numero, ou mais de um. Nos dois casos nao da para dizer
        # qual e a resposta sem escolher por ele.
        return RespostaNormalizada(bruto=original, tipo=TIPO_AMBIGUO)

    # ESPERA_TEXTO: qualquer coisa escrita serve, inclusive uma conta. O
    # texto e aparado e nao reescrito - "chutei" precisa chegar como
    # "chutei" a quem for interpreta-lo.
    return RespostaNormalizada(bruto=original, tipo=TIPO_TEXTO,
                               texto=str(original).strip(),
                               numero=numeros[0] if len(numeros) == 1 else None)


def observar(resposta: RespostaNormalizada, *,
             esperado_numero: float | None = None,
             esperado_texto: str | None = None) -> str:
    """O que se observou - e nao o que o aluno sabe.

    Sem gabarito esperado, a resposta e REGISTRADA e nada mais: "como voce
    pensou?" nao tem resposta certa, e julga-la certa ou errada seria
    avaliar uma explicacao como se fosse um calculo.
    """
    if resposta.tipo == TIPO_VAZIO:
        return OBS_SEM_RESPOSTA
    if resposta.tipo == TIPO_NAO_SEI:
        # NAO e erro. Confundir os dois faria "nao sei" alimentar a conta de
        # erros do aluno, e ele seria punido por ser honesto.
        return OBS_NAO_SEI
    if resposta.tipo == TIPO_AMBIGUO:
        return OBS_AMBIGUA

    if esperado_numero is not None and resposta.numero is not None:
        certo = abs(resposta.numero - float(esperado_numero)) < _FOLGA
        return OBS_CORRETA if certo else OBS_INCORRETA

    if esperado_texto is not None and resposta.texto is not None:
        certo = (resposta.texto.strip().upper()
                 == str(esperado_texto).strip().upper())
        return OBS_CORRETA if certo else OBS_INCORRETA

    return OBS_REGISTRADA


__all__ = [
    "ESPERA_ESCOLHA",
    "ESPERA_NUMERO",
    "ESPERA_TEXTO",
    "OBS_AMBIGUA",
    "OBS_CORRETA",
    "OBS_INCORRETA",
    "OBS_NAO_SEI",
    "OBS_REGISTRADA",
    "OBS_SEM_RESPOSTA",
    "RespostaNormalizada",
    "TIPO_AMBIGUO",
    "TIPO_ESCOLHA",
    "TIPO_NAO_SEI",
    "TIPO_NUMERO",
    "TIPO_TEXTO",
    "TIPO_VAZIO",
    "normalizar",
    "observar",
]
