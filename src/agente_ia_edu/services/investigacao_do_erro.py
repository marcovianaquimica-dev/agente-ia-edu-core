"""INVESTIGAR ANTES DE RESOLVER - uma pergunta curta no lugar do despejo.

O QUE ESTE MODULO MUDA
=======================
Ate aqui, errar levava a uma explicacao inteira. Para quem errou so a ultima
etapa, isso e ouvir de novo o que ja sabia; para quem errou a primeira, e
ouvir tres etapas construidas sobre a que falhou. Nos dois casos o sistema
nao fica sabendo NADA de novo sobre o aluno - ele falou, nao perguntou.

A investigacao troca o despejo por uma pergunta de cada vez:

    ERRO -> hipotese -> micropergunta -> resposta -> gargalo localizado

E o gargalo localizado e evidencia DIAGNOSTICA de verdade: o aluno respondeu
uma pergunta que isola uma etapa, e nao uma questao que mistura quatro.

A HIPOTESE E O PONTO DELICADO
==============================
O distrator SUGERE um raciocinio. Ele nao o prova. Quem marcou 8,50 g pode
ter pulado a proporcao, pode ter errado a massa molar, pode ter chutado -
8,50 e o que sai de `0,5 mol x 17 g/mol`, e isso e uma hipotese boa, nao um
fato sobre a cabeca de alguem.

Por isso toda hipotese aqui e escrita como hipotese, e ha teste varrendo os
textos atras de "voce fez", "voce esqueceu", "seu erro foi". A micropergunta
e que confirma ou refuta - e e justamente por isso que ela existe.

O QUE ESTE MODULO NAO E
========================
Nao e mastery, nao escreve em lugar nenhum e nao consulta modelo de IA. Nao
ha sessao de banco aqui, e ha teste lendo o arquivo - mesma garantia
estrutural de `sinal_diagnostico` e `conversa_do_assessor`.

A micropergunta serve ao DIAGNOSTICO e ao FADING. Ela nao entra no mapa de
dominio: acertar uma etapa isolada, logo depois de o sistema dizer qual
etapa e, nao e a mesma coisa que resolver o problema inteiro sozinho.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agente_ia_edu.services.grafo_estequiometria import (
    CONTEUDO,
    LEITURA_FORMULA,
    MASSA_MOL,
    MASSA_MOLAR,
    PROPORCAO,
)
from agente_ia_edu.services.hipotese_pedagogica import Hipotese
from agente_ia_edu.services.resposta_do_aluno import ESPERA_NUMERO


@dataclass(frozen=True)
class EtapaDaInvestigacao:
    """Uma pergunta que isola UMA etapa - e o que dizer de cada desfecho.

    DOIS NIVEIS PARA QUEM ERRA, E O QUE OS SEPARA E A TENTATIVA
    ============================================================
    Medido no navegador em 2026-10-06: o aluno errou a etapa 1 de massa molar
    e leu "o indice conta os atomos daquele elemento: em NH3 sao TRES
    hidrogenios". A regra esta certa e a frase e gentil - e ela entrega o
    numero. O aluno clica em C sem ter recontado nada, a etapa fica
    registrada como resolvida, e o sistema conclui que aquela
    micro-habilidade esta de pe quando a unica coisa que aconteceu foi ele
    ler a resposta.

        `pista`      1a vez: a REGRA, sem aplica-la a este item
        `se_errar`   2a vez em diante: a regra APLICADA

    Nao e esconder para sempre - e esconder por uma tentativa. Quem errou
    duas vezes ja mostrou que a regra sozinha nao bastou, e insistir em
    esconder ai vira castigo em vez de pedagogia.

    Nenhum dos dois e a resposta com outras palavras: dizer "a resposta e C"
    encerra a pergunta sem ensinar nada. Ha teste varrendo a pista atras do
    algarismo E do numero por extenso - foi a assercao que faltava, e por
    isso o problema chegou ao navegador.
    """

    ordem: int
    habilidade: str
    pergunta: str
    alternativas: dict[str, str]
    correta: str
    conferencia: str
    se_acertar: str
    pista: str
    se_errar: str


@dataclass(frozen=True)
class PerguntaAberta:
    """A pergunta que ABRE a investigacao, respondida por escrito.

    Ate 2026-10-07 a investigacao comecava ja na primeira micropergunta de
    multipla escolha - o erro que a motivou tinha acontecido numa questao
    anterior, noutra tela. Com a abertura, o Edu faz a pergunta ele mesmo e
    ve a resposta crua: "15" diz coisas que marcar a alternativa B nao diz.

    `resposta` e o valor certo; `espera` e o que a camada de normalizacao
    precisa saber para ler o texto. Nenhum dos dois vaza para o cliente
    antes da hora.
    """

    pergunta: str
    espera: str
    resposta: float
    conferencia: str
    unidade: str | None = None


@dataclass(frozen=True)
class Gatilho:
    """Um valor observado e a hipotese que ele SUGERE.

    Nao e um diagnostico: e a razao de fazer a proxima pergunta. Ver
    `hipotese_pedagogica` - o distrator sugere, a discriminante decide.
    """

    valor: float
    hipotese: Hipotese


@dataclass(frozen=True)
class Investigacao:
    key: str
    content_code: str
    habilidade_alvo: str
    hipotese: str
    etapas: tuple[EtapaDaInvestigacao, ...]
    abertura: PerguntaAberta | None = None
    gatilhos: tuple[Gatilho, ...] = ()


# ---------------------------------------------------------------------------
# AS CADEIAS CURADAS
#
# Cada uma comeca na etapa mais BASICA da cadeia, e nao na que o aluno errou.
# Perguntar a contribuicao dos hidrogenios a quem le 1 H no NH3 nao localiza
# nada - ele vai errar a segunda pergunta pelo motivo da primeira, e o
# sistema vai concluir a coisa errada.
# ---------------------------------------------------------------------------

_LEITURA = Investigacao(
    key="INV-EST-LEITURA-FORMULA",
    content_code=CONTEUDO,
    habilidade_alvo=LEITURA_FORMULA,
    hipotese="Esse resultado sugere que a contagem de átomos da fórmula pode "
             "estar escapando em algum ponto. Vamos conferir com três "
             "fórmulas rápidas.",
    etapas=(
        EtapaDaInvestigacao(
            ordem=1,
            habilidade=LEITURA_FORMULA,
            pergunta="Na fórmula H₂O, quantos átomos de hidrogênio estão "
                     "representados?",
            alternativas={"A": "1", "B": "2", "C": "3", "D": "4"},
            correta="B",
            conferencia="indice de H em H2O",
            se_acertar="Isso. O número pequeno depois do símbolo diz quantos "
                       "átomos daquele elemento a fórmula tem.",
            pista="O número pequeno que vem depois de um símbolo é o índice, e "
                  "ele conta os átomos daquele elemento. Olhe só para o H "
                  "desta fórmula e veja que número está colado nele.",
            se_errar="O número pequeno que vem depois do símbolo é o índice, e "
                     "ele conta os átomos daquele elemento. Em H₂O o 2 está "
                     "colado no H, então são dois hidrogênios; o oxigênio não "
                     "tem índice, e isso significa um só."),
        EtapaDaInvestigacao(
            ordem=2,
            habilidade=LEITURA_FORMULA,
            pergunta="E na fórmula NH₃, quantos átomos de hidrogênio?",
            alternativas={"A": "1", "B": "2", "C": "3", "D": "4"},
            correta="C",
            conferencia="indice de H em NH3",
            se_acertar="Exato: três hidrogênios, e um nitrogênio.",
            pista="Vale a mesma regra da anterior: o índice fica colado no "
                  "símbolo e conta os átomos daquele elemento. Confira qual "
                  "número está colado no hidrogênio nesta fórmula.",
            se_errar="O 3 está colado no H, então ele conta hidrogênios: são "
                     "três. O nitrogênio aparece sem índice nenhum, e símbolo "
                     "sem índice quer dizer um átomo."),
        EtapaDaInvestigacao(
            ordem=3,
            habilidade=LEITURA_FORMULA,
            pergunta="Na fórmula Ca(OH)₂, quantos átomos de oxigênio?",
            alternativas={"A": "1", "B": "2", "C": "3", "D": "4"},
            correta="B",
            conferencia="indice de O em Ca(OH)2",
            se_acertar="Isso mesmo. O índice fora do parêntese multiplica "
                       "tudo o que está dentro dele.",
            pista="Aqui apareceu um parêntese, e o índice que vem depois dele "
                  "multiplica tudo o que está lá dentro. Conte os oxigênios de "
                  "dentro e aplique esse multiplicador.",
            se_errar="O índice que vem depois do parêntese multiplica tudo o "
                     "que está lá dentro. Dentro de (OH) há um oxigênio e um "
                     "hidrogênio; com o 2 do lado de fora, a fórmula passa a "
                     "ter dois de cada um."),
    ),
)

_MASSA_MOLAR = Investigacao(
    key="INV-EST-MASSA-MOLAR",
    content_code=CONTEUDO,
    habilidade_alvo=MASSA_MOLAR,
    hipotese="Esse resultado sugere que a soma das massas pode ter ficado "
             "incompleta em algum ponto — às vezes na leitura da fórmula, às "
             "vezes na multiplicação pelo índice. Vamos conferir por partes.",
    # A PERGUNTA QUE O EDU FAZ, respondida por escrito.
    abertura=PerguntaAberta(
        pergunta="Qual é a massa molar do NH₃?\n"
                 "Considere N = 14 g/mol e H = 1 g/mol.",
        espera=ESPERA_NUMERO,
        resposta=17.0,
        conferencia="14 + 3*1",
        unidade="g/mol"),
    # O ERRO ESPERADO, E O QUE ELE SUGERE.
    #
    # 15 e 14 + 1: a massa do N mais a de UM hidrogenio, com o indice 3
    # fora da conta. E a hipotese mais util que o numero permite - e e so
    # uma hipotese. Quem chutou tambem escreve 15.
    #
    # Ela aponta para LEITURA_DE_FORMULA, um degrau ABAIXO do alvo, e e por
    # isso que confirma-la faz o percurso descer no grafo. A etapa 1 e a
    # discriminante: "quantos H aparecem no NH3?" separa quem nao le o
    # indice de quem le e nao o aplica.
    gatilhos=(
        Gatilho(
            valor=15.0,
            hipotese=Hipotese(
                codigo="INDEX_OMISSION",
                habilidade_suspeita=LEITURA_FORMULA,
                como_dizer="Esse resultado pode indicar que o índice da "
                           "fórmula ficou de fora da conta. Vamos conferir "
                           "uma coisa antes de seguir.",
                discriminante=1)),
    ),
    etapas=(
        EtapaDaInvestigacao(
            ordem=1,
            habilidade=LEITURA_FORMULA,
            pergunta="Na fórmula NH₃, quantos átomos de hidrogênio estão "
                     "representados?",
            alternativas={"A": "1", "B": "2", "C": "3", "D": "4"},
            correta="C",
            conferencia="indice de H em NH3",
            se_acertar="Isso. Então a massa molar vai precisar contar o "
                       "hidrogênio três vezes.",
            pista="O índice fica colado no símbolo e conta os átomos daquele "
                  "elemento; símbolo sem índice nenhum vale um átomo. Releia a "
                  "fórmula olhando apenas para o hidrogênio.",
            se_errar="O número pequeno colado no símbolo conta os átomos "
                     "daquele elemento: em NH₃ são três hidrogênios. O "
                     "nitrogênio vem sem índice, e isso quer dizer um átomo."),
        EtapaDaInvestigacao(
            ordem=2,
            habilidade=MASSA_MOLAR,
            pergunta="Se cada hidrogênio contribui com 1 g/mol, qual é a "
                     "contribuição dos três hidrogênios juntos?",
            alternativas={"A": "1 g/mol", "B": "3 g/mol", "C": "4 g/mol",
                          "D": "13 g/mol"},
            correta="B",
            conferencia="3 * 1",
            se_acertar="Isso. Cada elemento entra tantas vezes quanto o "
                       "índice manda.",
            pista="A contribuição de um elemento é a massa atômica dele "
                  "multiplicada pelo índice. Pegue o índice do hidrogênio "
                  "nesta fórmula e multiplique pela massa atômica que o "
                  "enunciado deu.",
            se_errar="A contribuição de um elemento é a massa atômica dele "
                     "multiplicada pelo índice. Com três hidrogênios de 1 "
                     "g/mol cada, a conta é uma multiplicação simples, e o "
                     "resultado entra inteiro na soma final."),
        EtapaDaInvestigacao(
            ordem=3,
            habilidade=MASSA_MOLAR,
            pergunta="O nitrogênio entra com 14 g/mol e os hidrogênios com 3 "
                     "g/mol. Qual é a massa molar do NH₃?",
            alternativas={"A": "15 g/mol", "B": "16 g/mol", "C": "17 g/mol",
                          "D": "18 g/mol"},
            correta="C",
            conferencia="14 + 3",
            se_acertar="É isso: massa molar é a soma das contribuições de "
                       "cada elemento da fórmula.",
            pista="A massa molar é a soma das contribuições de todos os "
                  "elementos da fórmula, sem deixar nenhum de fora. Some as "
                  "duas parcelas que o enunciado acabou de dar.",
            se_errar="A massa molar é a soma das contribuições de todos os "
                     "elementos, nenhum de fora. Some a parte do nitrogênio "
                     "com a parte dos hidrogênios e o total é a massa de um "
                     "mol da substância."),
    ),
)

_MASSA_MOL = Investigacao(
    key="INV-EST-MASSA-MOL",
    content_code=CONTEUDO,
    habilidade_alvo=MASSA_MOL,
    hipotese="Esse resultado sugere que a passagem entre massa e quantidade "
             "de matéria pode estar invertida em algum momento. Vamos olhar "
             "os dois sentidos.",
    etapas=(
        EtapaDaInvestigacao(
            ordem=1,
            habilidade=MASSA_MOLAR,
            pergunta="Com H = 1 g/mol e O = 16 g/mol, qual é a massa molar da "
                     "água (H₂O)?",
            alternativas={"A": "17 g/mol", "B": "18 g/mol", "C": "20 g/mol",
                          "D": "34 g/mol"},
            correta="B",
            conferencia="2*1 + 16",
            se_acertar="Isso. Dois hidrogênios valem 2, mais 16 do oxigênio.",
            pista="Multiplique a massa atômica de cada elemento pelo índice "
                  "dele e some as parcelas. Repare que o hidrogênio aparece "
                  "mais de uma vez nesta fórmula, e o oxigênio só uma.",
            se_errar="São dois hidrogênios de 1 g/mol e um oxigênio de 16 "
                     "g/mol. Multiplique cada massa atômica pelo índice e "
                     "some as duas partes para chegar à massa de um mol."),
        EtapaDaInvestigacao(
            ordem=2,
            habilidade=MASSA_MOL,
            pergunta="Quantos mol de água existem em 36 g?",
            alternativas={"A": "0,5 mol", "B": "1 mol", "C": "2 mol",
                          "D": "18 mol"},
            correta="C",
            conferencia="36 / 18",
            se_acertar="Exato. A massa molar diz quanto pesa um mol, então "
                       "dividir pela massa molar conta quantos mol cabem.",
            pista="A massa molar diz quanto pesa um mol da substância. Para "
                  "saber quantos mol cabem numa massa, divida a massa pela "
                  "massa molar — e confira a unidade: o resultado precisa sair "
                  "em mol.",
            se_errar="A massa molar diz quanto pesa um mol da substância. "
                     "Para descobrir quantos mol cabem numa massa, divida a "
                     "massa pela massa molar — e repare que o resultado sai "
                     "em mol, não em gramas."),
        EtapaDaInvestigacao(
            ordem=3,
            habilidade=MASSA_MOL,
            pergunta="E quanto pesam 0,5 mol de água?",
            alternativas={"A": "0,5 g", "B": "9 g", "C": "18 g", "D": "36 g"},
            correta="B",
            conferencia="0.5 * 18",
            se_acertar="Isso. No sentido contrário a conta é multiplicar.",
            pista="Indo de mol para massa o caminho se inverte: em vez de "
                  "dividir, multiplique a quantidade de matéria pela massa "
                  "molar que você acabou de confirmar.",
            se_errar="Indo de mol para massa o caminho se inverte: em vez de "
                     "dividir, multiplique a quantidade de matéria pela massa "
                     "molar. Metade de um mol pesa metade do que pesa um mol "
                     "inteiro."),
    ),
)

# O CASO OBSERVADO NO NAVEGADOR, em 2026-10-06.
#
#   N2 + 3 H2 -> 2 NH3, 14,0 g de N2, M(N2) = 28,0, M(NH3) = 17,0
#   resposta correta 17,0 g; o aluno marcou 8,50 g
#
# 8,50 e exatamente `0,5 mol x 17 g/mol` - o que sai de levar o mol de N2
# direto para a massa de NH3, sem a proporcao 1:2 do meio. Isso e uma
# hipotese BOA justamente porque e TESTAVEL: a segunda micropergunta a
# confirma ou a derruba, e ate la ninguem afirma nada.
_PROPORCAO = Investigacao(
    key="INV-EST-PROPORCAO",
    content_code=CONTEUDO,
    habilidade_alvo=PROPORCAO,
    hipotese="Essa resposta sugere que a conta pode ter ido do mol de N₂ "
             "direto para a massa de NH₃, sem a etapa da proporção entre as "
             "duas substâncias. Vamos conferir uma etapa de cada vez.",
    etapas=(
        EtapaDaInvestigacao(
            ordem=1,
            habilidade=MASSA_MOL,
            pergunta="A massa molar do N₂ é 28,0 g/mol. 14,0 g de N₂ "
                     "correspondem a quantos mol?",
            alternativas={"A": "0,25 mol", "B": "0,5 mol", "C": "1,0 mol",
                          "D": "2,0 mol"},
            correta="B",
            conferencia="14 / 28",
            se_acertar="Isso. Meio mol de N₂ — essa é a quantidade com que "
                       "vamos trabalhar.",
            pista="Para ir de massa para quantidade de matéria, divida a massa "
                  "que o enunciado deu pela massa molar informada. Confira a "
                  "unidade do resultado: ela precisa sair em mol.",
            se_errar="Para ir de massa para quantidade de matéria, divida a "
                     "massa pela massa molar. Como 14,0 g é metade dos 28,0 g "
                     "que um mol inteiro pesaria, o resultado também é "
                     "metade de um mol."),
        EtapaDaInvestigacao(
            ordem=2,
            habilidade=PROPORCAO,
            pergunta="Na equação N₂ + 3 H₂ → 2 NH₃, cada 1 mol de N₂ produz "
                     "2 mol de NH₃. Então 0,5 mol de N₂ produzem quantos mol "
                     "de NH₃?",
            alternativas={"A": "0,25 mol", "B": "0,5 mol", "C": "1,0 mol",
                          "D": "2,0 mol"},
            correta="C",
            conferencia="0.5 * 2/1",
            se_acertar="Exato. Os coeficientes da equação são a proporção "
                       "entre as quantidades — e é essa etapa que muda o "
                       "número.",
            pista="Os números na frente das fórmulas são a proporção entre as "
                  "quantidades de matéria. Compare o coeficiente do N₂ com o "
                  "do NH₃ e aplique essa razão à quantidade do passo anterior.",
            se_errar="Os números na frente das fórmulas são a proporção entre "
                     "as quantidades de matéria. Como saem 2 mol de NH₃ para "
                     "cada 1 mol de N₂, a quantidade de amônia é o dobro da "
                     "quantidade de nitrogênio que reagiu."),
        EtapaDaInvestigacao(
            ordem=3,
            habilidade=MASSA_MOL,
            pergunta="A massa molar do NH₃ é 17,0 g/mol. Quanto pesam 1,0 mol "
                     "de NH₃?",
            # "8,50 g" entra como distrator de proposito: e o valor que o
            # aluno marcou na questao original. Ve-lo aqui, lado a lado com
            # 17,0 g depois de ter feito a etapa da proporcao, e o momento em
            # que a hipotese se fecha para ele proprio.
            alternativas={"A": "8,50 g", "B": "17,0 g", "C": "28,0 g",
                          "D": "34,0 g"},
            correta="B",
            conferencia="1.0 * 17",
            se_acertar="É isso: 17,0 g de amônia. Repare que a etapa da "
                       "proporção é a que separa esse valor de 8,50 g.",
            pista="No último passo o caminho é multiplicar a quantidade de "
                  "matéria pela massa molar da substância. Os dois valores já "
                  "apareceram: um no passo anterior, o outro no enunciado.",
            se_errar="No último passo o caminho é multiplicar a quantidade de "
                     "matéria pela massa molar. Um mol da substância pesa "
                     "exatamente a massa molar dela, então o resultado vem "
                     "direto desse valor."),
    ),
)


INVESTIGACOES: tuple[Investigacao, ...] = (
    _LEITURA, _MASSA_MOLAR, _MASSA_MOL, _PROPORCAO,
)


def investigacao_para(content_code: str, skill: str | None) -> Investigacao | None:
    """A cadeia daquela lacuna, ou None quando nao ha uma escrita para ela.

    Nao ha cadeia generica, de proposito: uma investigacao que nao isola
    etapas do conteudo nao localiza nada, e o aluno responderia perguntas
    cujo resultado o sistema nao saberia interpretar.
    """
    if not skill:
        return None
    for inv in INVESTIGACOES:
        if inv.content_code == content_code and inv.habilidade_alvo == skill:
            return inv
    return None


def proxima_etapa(inv: Investigacao,
                  respostas: Mapping[int, str] | None) -> EtapaDaInvestigacao | None:
    """A primeira etapa ainda nao RESOLVIDA.

    Errar nao avanca: quem errou a etapa 1 precisa da etapa 1. Avancar ali
    seria perguntar a etapa 2 a quem acabou de mostrar que a 1 nao esta de pe
    - e a resposta da 2 nao significaria nada.
    """
    dadas = dict(respostas or {})
    for e in inv.etapas:
        marcada = (dadas.get(e.ordem) or "").strip().upper()
        if marcada != e.correta:
            return e
    return None


def gargalo(inv: Investigacao,
            respostas: Mapping[int, str] | None) -> str | None:
    """A habilidade da PRIMEIRA etapa que falhou, ou None.

    Errar duas etapas nao quer dizer duas lacunas: a segunda se apoia na
    primeira, e comecar pela mais alta e ensinar o telhado a quem ainda nao
    tem parede. Etapa nao respondida nao conta - falta de resposta nao e erro.
    """
    dadas = dict(respostas or {})
    for e in inv.etapas:
        marcada = dadas.get(e.ordem)
        if marcada is None:
            return None
        if (marcada or "").strip().upper() != e.correta:
            return e.habilidade
    return None


def hipotese_para(inv: Investigacao, valor: float | None) -> Hipotese | None:
    """A hipotese que ESTE valor sugere, se houver uma escrita para ele.

    Valor sem gatilho nao produz hipotese - e correto: o sistema so supoe
    onde alguem escreveu de antemao qual suposicao aquele numero sustenta.
    Inventar uma em tempo de execucao seria exatamente o que §4 proibe.
    """
    if valor is None:
        return None
    for g in inv.gatilhos:
        if abs(float(valor) - g.valor) < 1e-9:
            return g.hipotese
    return None


def etapa_de(inv: Investigacao, ordem: int) -> EtapaDaInvestigacao | None:
    return next((e for e in inv.etapas if e.ordem == int(ordem)), None)


def conferir_resposta(etapa: EtapaDaInvestigacao, escolha: str) -> bool:
    """Acertou? A conferencia e aqui, nunca no cliente.

    Aceita a LETRA ou o CONTEUDO da alternativa. Numa conversa o aluno
    digita "3", nao "C" - e exigir a letra o obrigaria a traduzir a propria
    resposta para o formato interno da tela.

    Ambiguidade continua sendo nao: "3 ou 4" nao acerta a etapa cuja
    resposta e 3. Ver `resposta_do_aluno` - o que nao se le com seguranca
    nao vira acerto.
    """
    return avaliar_resposta(etapa, escolha)[0]


def avaliar_resposta(etapa: EtapaDaInvestigacao,
                     escolha: str) -> tuple[bool, str]:
    """(acertou, observacao) para uma etapa de alternativas.

    Devolve a OBSERVACAO junto porque "errou" e "nao entendi o que voce
    escreveu" exigem respostas diferentes do Edu, e perder essa distincao
    faria o aluno ambiguo ser tratado como aluno que errou.
    """
    from agente_ia_edu.services.resposta_do_aluno import (
        ESPERA_ESCOLHA,
        ESPERA_NUMERO,
        OBS_AMBIGUA,
        OBS_CORRETA,
        OBS_INCORRETA,
        TIPO_AMBIGUO,
        TIPO_ESCOLHA,
        normalizar,
        observar,
    )

    from agente_ia_edu.services.resposta_do_aluno import (
        OBS_NAO_SEI,
        OBS_SEM_RESPOSTA,
        TIPO_NAO_SEI,
        TIPO_VAZIO,
    )

    bruto = escolha or ""

    # "NAO SEI" E VAZIO TEM OBSERVACAO PROPRIA, antes de qualquer tentativa
    # de leitura. Sem isto os dois caiam em AMBIGUO, e o Edu respondia
    # "nao entendi o que voce escreveu" a quem disse, com todas as letras,
    # que nao sabia - que e informacao pedagogica, nao ruido.
    inicial = normalizar(bruto, espera=ESPERA_ESCOLHA)
    if inicial.tipo == TIPO_VAZIO:
        return False, OBS_SEM_RESPOSTA
    if inicial.tipo == TIPO_NAO_SEI:
        return False, OBS_NAO_SEI

    # Primeiro como LETRA: e o que o clique na alternativa envia, e e a
    # leitura mais barata.
    como_letra = inicial
    if como_letra.tipo == TIPO_ESCOLHA and como_letra.texto in etapa.alternativas:
        obs = observar(como_letra, esperado_texto=etapa.correta)
        return obs == OBS_CORRETA, obs

    # Depois como CONTEUDO da alternativa. O texto certo e o da correta; os
    # demais sao comparados para distinguir "errou" de "nao entendi".
    certo = etapa.alternativas[etapa.correta]
    como_numero = normalizar(bruto, espera=ESPERA_NUMERO)
    if como_numero.tipo == TIPO_AMBIGUO:
        return False, OBS_AMBIGUA

    esperado = normalizar(certo, espera=ESPERA_NUMERO)
    if como_numero.numero is not None and esperado.numero is not None:
        obs = observar(como_numero, esperado_numero=esperado.numero)
        return obs == OBS_CORRETA, obs

    # Nem letra nem numero: comparacao textual direta, sem adivinhar.
    dito = bruto.strip().casefold()
    if dito and dito == certo.strip().casefold():
        return True, OBS_CORRETA
    if any(dito == t.strip().casefold() for t in etapa.alternativas.values()):
        return False, OBS_INCORRETA
    return False, OBS_AMBIGUA


# A abertura ocupa a ordem ZERO. Ela nao e uma etapa da cadeia - e a
# pergunta que a motiva -, e dar-lhe um numero fora do intervalo das etapas
# e o que permite guarda-la pelo mesmo caminho sem confundir as duas.
ORDEM_DA_ABERTURA = 0


def para_o_aluno(inv: Investigacao,
                 respostas: Mapping[int, str] | None,
                 tentativas: Mapping[int, int] | None = None,
                 abertura: Mapping | None = None) -> dict:
    """A investigacao como ela pode chegar ao cliente.

    O QUE NAO ESTA AQUI NAO VAZA. A letra correta de uma etapa ABERTA nao sai
    em campo nenhum - nem marcada na alternativa, nem solta. Ela so aparece
    depois que o aluno acertou aquela etapa, e ai e para ele conferir o
    raciocinio, nao para descobrir a resposta.

    Ha teste serializando esta saida e procurando o campo `correta` nela.
    """
    dadas = {int(k): (v or "").strip().upper()
             for k, v in dict(respostas or {}).items()}
    atual = proxima_etapa(inv, dadas)

    concluidas = []
    for e in inv.etapas:
        if dadas.get(e.ordem) == e.correta:
            concluidas.append({
                "ordem": e.ordem,
                "question": e.pergunta,
                "selected": dadas[e.ordem],
                "correct_option": e.correta,
                # O CONTEUDO da alternativa, e nao so a letra.
                #
                # Numa conversa a tela mostra o que o aluno respondeu, e
                # "C" nao e o que ele respondeu - ele escreveu "3". Uma
                # etapa resolvida foi resolvida acertando, entao o texto da
                # correta E o que ele disse, em substancia.
                "resposta_texto": e.alternativas.get(e.correta),
                "comentario": e.se_acertar,
            })

    # O DESFECHO DA ULTIMA TENTATIVA ERRADA DA ETAPA ABERTA.
    #
    # Nunca a letra. E, na PRIMEIRA vez, nem o numero: so a regra, para o
    # aluno recontar. Ver o cabecalho de `EtapaDaInvestigacao` - a versao
    # anterior entregava o resultado na primeira frase, e a etapa ficava
    # registrada como resolvida por leitura.
    #
    # Sem contagem de tentativas, o padrao e a pista: na duvida, o caminho
    # que ensina mais.
    ultima_errada = None
    if atual is not None and dadas.get(atual.ordem):
        quantas = int((tentativas or {}).get(atual.ordem) or 1)
        primeira = quantas <= 1
        ultima_errada = {
            "ordem": atual.ordem,
            "nivel": 1 if primeira else 2,
            "comentario": atual.pista if primeira else atual.se_errar,
        }

    # A ABERTURA VEM PRIMEIRO, e enquanto ela nao for respondida nenhuma
    # etapa aparece: a cadeia existe para investigar um erro, e sem o erro
    # ela seria uma bateria de perguntas sem motivo.
    aberta = dict(abertura or {})
    abertura_respondida = bool(aberta.get("respondida"))
    perguntar_abertura = inv.abertura is not None and not abertura_respondida

    return {
        "key": inv.key,
        "content_code": inv.content_code,
        "skill": inv.habilidade_alvo,
        "hipotese": inv.hipotese,
        "abertura": None if inv.abertura is None else {
            "pergunta": inv.abertura.pergunta,
            "espera": inv.abertura.espera,
            "unidade": inv.abertura.unidade,
            "respondida": abertura_respondida,
            # O que ELE escreveu volta para a tela. A nossa leitura do que
            # ele escreveu nao substitui o que ele disse.
            "resposta_do_aluno": aberta.get("bruto"),
            "observacao": aberta.get("observacao"),
            # A frase hedgeada da hipotese, quando o valor sugeriu uma.
            # NUNCA uma afirmacao sobre o raciocinio dele.
            "hipotese": aberta.get("hipotese_como_dizer"),
            "hipotese_codigo": aberta.get("hipotese_codigo"),
            "hipotese_estado": aberta.get("hipotese_estado"),
        },
        "perguntar_abertura": perguntar_abertura,
        "total_etapas": len(inv.etapas),
        "completed": atual is None and not perguntar_abertura,
        "bottleneck_skill": gargalo(inv, dadas),
        "etapa": None if (atual is None or perguntar_abertura) else {
            "ordem": atual.ordem,
            "skill": atual.habilidade,
            "question": atual.pergunta,
            "options": [{"key": k, "text": t}
                        for k, t in sorted(atual.alternativas.items())],
        },
        "concluidas": concluidas,
        "retorno": ultima_errada,
    }


def conferir() -> list[str]:
    """Refaz as contas de todas as etapas. Devolve os problemas, ou vazio.

    Mesmo papel de `sondagem_estequiometria.conferir`: um erro de digitacao
    em "17 g/mol" ensinaria quimica errada a quem confiou no sistema. As
    contas vem de `massa_molar` e `chemistry_balance`, nunca de um literal
    repetido aqui.
    """
    from agente_ia_edu.services.chemistry_balance import atomos_da_formula
    from agente_ia_edu.services.massa_molar import (
        contribuicoes,
        massa_molar,
        massa_para_mol,
        mol_para_massa,
        por_proporcao,
    )

    problemas: list[str] = []
    etapas = {(inv.key, e.ordem): e for inv in INVESTIGACOES for e in inv.etapas}

    def marcada(key: str, ordem: int) -> str:
        e = etapas[(key, ordem)]
        return e.alternativas[e.correta]

    def exige(key: str, ordem: int, esperado: str) -> None:
        valor = marcada(key, ordem)
        if not valor.startswith(esperado):
            problemas.append(
                f"{key} etapa {ordem}: gabarito {valor!r} nao bate com "
                f"{esperado!r}")

    # Leitura de formula - contagem real de atomos.
    exige("INV-EST-LEITURA-FORMULA", 1, str(atomos_da_formula("H2O")["H"]))
    exige("INV-EST-LEITURA-FORMULA", 2, str(atomos_da_formula("NH3")["H"]))
    exige("INV-EST-LEITURA-FORMULA", 3, str(atomos_da_formula("Ca(OH)2")["O"]))

    # Massa molar do NH3, por partes e no total.
    exige("INV-EST-MASSA-MOLAR", 1, str(atomos_da_formula("NH3")["H"]))
    contrib_h = next(c[3] for c in contribuicoes("NH3") if c[0] == "H")
    exige("INV-EST-MASSA-MOLAR", 2, _numero(contrib_h))
    exige("INV-EST-MASSA-MOLAR", 3, _numero(massa_molar("NH3")))

    # Massa <-> mol, nos dois sentidos.
    exige("INV-EST-MASSA-MOL", 1, _numero(massa_molar("H2O")))
    exige("INV-EST-MASSA-MOL", 2, _numero(massa_para_mol(36.0, "H2O")))
    exige("INV-EST-MASSA-MOL", 3, _numero(mol_para_massa(0.5, "H2O")))

    # A ABERTURA, e o valor do erro esperado.
    #
    # 17 e a massa molar do NH3; 15 e o que sai de somar N com UM hidrogenio.
    # Os dois sao refeitos por conta: um literal errado aqui faria o Edu
    # abrir a conversa com quimica errada, ou perseguir uma hipotese que o
    # numero nao sustenta.
    if _MASSA_MOLAR.abertura is not None:
        esperado = massa_molar("NH3")
        if abs(_MASSA_MOLAR.abertura.resposta - esperado) > 1e-9:
            problemas.append(
                f"INV-EST-MASSA-MOLAR abertura: resposta "
                f"{_MASSA_MOLAR.abertura.resposta} nao bate com {esperado}")
    por_elemento_nh3 = {c[0]: c[2] for c in contribuicoes("NH3")}
    sem_indice = por_elemento_nh3["N"] + por_elemento_nh3["H"]
    for g in _MASSA_MOLAR.gatilhos:
        if g.hipotese.codigo == "INDEX_OMISSION" and abs(
                g.valor - sem_indice) > 1e-9:
            problemas.append(
                f"INV-EST-MASSA-MOLAR gatilho: {g.valor} nao e o que sai de "
                f"somar as massas atomicas sem aplicar o indice ({sem_indice})")

    # A cadeia do caso 8,50 g.
    mols_n2 = massa_para_mol(14.0, "N2")
    exige("INV-EST-PROPORCAO", 1, _virgula(mols_n2))
    mols_nh3 = por_proporcao(mols_n2, 1, 2)
    exige("INV-EST-PROPORCAO", 2, _virgula(mols_nh3))
    exige("INV-EST-PROPORCAO", 3, _virgula(mol_para_massa(mols_nh3, "NH3")))

    return problemas


def _numero(valor: float) -> str:
    """18.0 -> "18"; 0.5 -> "0,5". A grafia que o enunciado usa."""
    if float(valor).is_integer():
        return str(int(valor))
    return f"{valor:g}".replace(".", ",")


def _virgula(valor: float) -> str:
    """Uma casa decimal, com virgula - a grafia do caso N2/NH3."""
    return f"{valor:.1f}".replace(".", ",")


__all__ = [
    "INVESTIGACOES",
    "ORDEM_DA_ABERTURA",
    "EtapaDaInvestigacao",
    "Gatilho",
    "Investigacao",
    "PerguntaAberta",
    "etapa_de",
    "hipotese_para",
    "avaliar_resposta",
    "conferir",
    "conferir_resposta",
    "gargalo",
    "investigacao_para",
    "para_o_aluno",
    "proxima_etapa",
]
