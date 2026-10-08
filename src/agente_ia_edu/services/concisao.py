"""QUANTO FALAR - a politica de extensao da resposta do Assessor.

O QUE A AUDITORIA DE 2026-10-08 ENCONTROU
==========================================
O prompt da conversa (`assessor-conversa-v2`) carregava duas regras que
contradizem o §4 da especificacao:

    1. "Responda [...] de forma direta e curta: 2 a 5 frases."
    9. "TERMINE oferecendo no maximo uma continuacao concreta."

A primeira e o teto rigido e universal que o §4 recusa - uma duvida de uma
linha e um pedido de aprofundamento recebiam a mesma medida.

A segunda e a que mais pesa, porque e obrigatoria: "termine oferecendo" faz
TODA resposta acabar com uma oferta. O §4 diz o contrario, em tres lugares:
nao terminar automaticamente com uma pergunta, nao oferecer exercicio depois
de toda explicacao, e - textualmente - "se a resposta for suficiente, a
interacao pode terminar naturalmente".

POR QUE ISTO E UM MODULO, E NAO UM PROMPT MELHOR
=================================================
Porque "o modelo foi instruido a ser conciso" nao e verificavel e nao
sobrevive a uma troca de modelo. QUANTO explicar e decisao pedagogica, e
pedagogia e do Nucleo Edu 360 - o modelo executa a linguagem. E a mesma
razao pela qual o passo, a evidencia e o dominio ja sao deterministicos.

O QUE ELE DECIDE, E O QUE NAO
==============================
Decide o tamanho da proxima resposta e se a conversa pode fechar.

Nao decide o que o aluno sabe, nao move dominio, nao escolhe intervencao e
nao conhece banco. Ha teste lendo a fonte atras de `sqlalchemy`, `mastery`,
`PASSO_` e afins - a garantia e a ausencia da dependencia, nao a promessa.

COMO ELE LE A INTENCAO
=======================
Por marcas EXPLICITAS no que o aluno escreveu, nunca por inferencia sobre
silencio, tempo ou comportamento - o §5 e explicito sobre isso. Um pedido de
aprofundamento esta escrito ("explica melhor", "passo a passo"); uma duvida
factual curta tambem se reconhece pela forma.

E por INSISTENCIA: perguntar tres vezes sobre o mesmo ponto e sinal de que o
breve nao bastou. Isso nao e inferencia sobre a pessoa - e contagem de
turnos que ja aconteceram.
"""

from __future__ import annotations

EXTENSAO_BREVE = "BREVE"
EXTENSAO_MEDIA = "MEDIA"
EXTENSAO_DESENVOLVIDA = "DESENVOLVIDA"

# A faixa SUGERIDA de cada extensao, em frases.
#
# Ela e sugestao para o modelo, nao corte do produto: nada aqui trunca
# resposta. O §4 proibe o teto universal, nao a orientacao - o que ele pede e
# que a medida varie com o contexto, e e exatamente o que este dicionario faz.
FRASES_SUGERIDAS = {
    EXTENSAO_BREVE: (1, 3),
    EXTENSAO_MEDIA: (3, 6),
    EXTENSAO_DESENVOLVIDA: (6, 12),
}

_ORIENTACOES = {
    EXTENSAO_BREVE:
        "Responda em poucas frases - o suficiente para a dúvida dele, e nada "
        "além. Se uma frase resolver, use uma.",
    EXTENSAO_MEDIA:
        "Explique o porquê, com um exemplo concreto quando ajudar. Algumas "
        "frases bastam; não transforme isto numa aula.",
    EXTENSAO_DESENVOLVIDA:
        "Ele pediu mais, então desenvolva: separe em etapas pequenas, mostre "
        "a conta ou o raciocínio por partes, e use um exemplo concreto.",
}

# O QUE O ALUNO ESCREVE QUANDO QUER MAIS.
#
# Marcas explicitas, no texto dele. Nao ha inferencia sobre silencio, pausa
# ou tempo de leitura - o §5 lista essas quatro como o que NAO autoriza
# concluir dificuldade.
_PEDE_MAIS = (
    "explica melhor", "explique melhor", "explica de novo", "explique de novo",
    "de outro jeito", "outra forma", "nao entendi", "não entendi",
    "nao entendo", "não entendo", "passo a passo", "detalha", "detalhe",
    "mais detalhe", "como assim", "pode explicar", "me ensina", "me ensine",
    "nao faz sentido", "não faz sentido", "to perdido", "tô perdido",
    "estou perdido",
)

# O QUE O ALUNO ESCREVE QUANDO ACABOU.
_ENCERRA = (
    "entendi", "entendi!", "ah entendi", "agora entendi", "faz sentido",
    "obrigado", "obrigada", "valeu", "era so isso", "era só isso",
    "ja entendi", "já entendi", "ok obrigado", "show", "beleza",
)

# O QUE PEDE CONTINUACAO - e por isso NAO encerra.
_CONTINUA = (
    "e como", "e se", "e quando", "e por que", "e porque", "e ai", "e aí",
    "entao como", "então como", "mas como", "mas e", "me mostra", "mostra",
    "tem exemplo", "da um exemplo", "dá um exemplo", "e na questao",
    "e na questão",
)

# A partir de quantos turnos sobre o MESMO ponto o breve deixou de bastar.
#
# Tres, e nao dois: a segunda pergunta sobre um assunto e conversa normal; a
# terceira ja diz que a forma anterior nao chegou.
_INSISTENCIA = 3


def _normalizar(texto: str | None) -> str:
    return " ".join(str(texto or "").lower().split())


def _contem(texto: str, marcas) -> bool:
    return any(m in texto for m in marcas)


def extensao_para(*, pergunta: str | None,
                  turnos_no_mesmo_ponto: int = 1) -> str:
    """Quanto desenvolver a proxima resposta.

    A ordem das checagens e a politica: um pedido EXPLICITO de
    aprofundamento vence tudo, porque e a unica evidencia direta do que ele
    quer. Depois vem a insistencia, que e contagem e nao suposicao. So entao
    a forma da pergunta decide entre breve e media.
    """
    texto = _normalizar(pergunta)
    if _contem(texto, _PEDE_MAIS):
        return EXTENSAO_DESENVOLVIDA
    if int(turnos_no_mesmo_ponto or 1) >= _INSISTENCIA:
        return EXTENSAO_DESENVOLVIDA
    if not texto:
        return EXTENSAO_BREVE
    # UMA PERGUNTA CURTA PEDE RESPOSTA CURTA.
    #
    # O comprimento da pergunta nao mede a intencao sozinho - mas uma duvida
    # de uma linha raramente pede tres paragrafos, e comecar breve e
    # recuperavel: ele pede mais e recebe mais. Comecar longo nao e
    # recuperavel, porque ele ja leu.
    palavras = len(texto.split())
    return EXTENSAO_BREVE if palavras <= 12 else EXTENSAO_MEDIA


def orientacao_de_extensao(extensao: str) -> str:
    """A frase que vai ao prompt. Nunca um numero de caracteres."""
    return _ORIENTACOES.get(extensao, _ORIENTACOES[EXTENSAO_MEDIA])


def pode_encerrar(*, pergunta: str | None) -> bool:
    """A conversa pode acabar aqui, sem oferecer mais nada?

    O §4: "se a resposta for suficiente, a interacao pode terminar
    naturalmente". Isto nao ENCERRA nada - diz ao Assessor que ele nao
    precisa inventar uma continuacao para preencher o fim da resposta.
    """
    texto = _normalizar(pergunta)
    if _contem(texto, _PEDE_MAIS):
        return False
    if _contem(texto, _CONTINUA):
        return False
    if _contem(texto, _ENCERRA):
        return True
    # Pergunta factual curta: respondida, acabou. Se ele quiser mais, pede.
    return len(texto.split()) <= 12


def orientacao_de_fechamento(podeEncerrar: bool) -> str:
    """O que dizer ao modelo sobre o FIM da resposta.

    A regra 9 da v2 - "termine oferecendo no maximo uma continuacao" - e a
    que esta funcao substitui. "No maximo uma" parecia moderacao e era
    obrigacao: toda resposta terminava com uma oferta.
    """
    if podeEncerrar:
        return ("Se a resposta já for suficiente, pode terminar aí. Não "
                "invente uma continuação, não ofereça exercício e não "
                "termine com uma pergunta só para manter a conversa aberta.")
    return ("Ele quer seguir. Termine com UMA continuação concreta - outro "
            "exemplo, uma analogia, ou o próximo passo do raciocínio. Uma "
            "só, não uma lista.")


__all__ = [
    "EXTENSAO_BREVE",
    "EXTENSAO_DESENVOLVIDA",
    "EXTENSAO_MEDIA",
    "FRASES_SUGERIDAS",
    "extensao_para",
    "orientacao_de_extensao",
    "orientacao_de_fechamento",
    "pode_encerrar",
]
