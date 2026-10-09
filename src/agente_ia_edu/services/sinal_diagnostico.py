"""SINAL DIAGNOSTICO - evidencia que nao basta para afirmar, e basta para agir.

O PROBLEMA MEDIDO
==================
A sondagem pergunta UMA coisa de cada micro-habilidade - e o que a torna
discriminativa, e o que permite que errar signifique uma coisa so. A politica
de dominio exige TRES respostas antes de concluir qualquer coisa. As duas
regras estao certas isoladamente e se anulam em serie:

    LEITURA_DE_FORMULA  1/1   INSUFFICIENT_SAMPLE
    MASSA_MOLAR         0/1   INSUFFICIENT_SAMPLE

Nenhuma lacuna medida, nenhum alvo, nenhuma intervencao. O aluno respondia a
sondagem e o sistema nao sabia o que fazer com a resposta.

O PRINCIPIO
============
    Evidencia insuficiente para AFIRMAR DOMINIO pode ser suficiente
    para ORIENTAR UMA INTERVENCAO.

A assimetria vem da diferenca de CUSTO, nao de uma frouxidao de criterio:

    intervir sobre suspeita   barato e reversivel - o aluno recebe uma
                              explicacao que talvez ja soubesse
    afirmar dominio           caro e errado - com quatro alternativas, o
                              chute acerta uma vez em quatro

Entao um erro basta para SUSPEITAR. Um acerto nao basta para CONFIRMAR.

O QUE ESTE MODULO NAO E
========================
Nao e evidencia. Nao vira mastery. Nao desce o minimo de amostra. Nao escreve
em lugar nenhum - nao ha sessao aqui, e ha teste lendo o arquivo.

Ele tambem nao importa `PerformanceThresholdPolicy`: se importasse, alguem
acabaria usando os dois juntos e a separacao viraria convencao em vez de
estrutura. O sinal vive AO LADO da politica, nao no lugar dela - depois da
sondagem, a banda do dominio continua INSUFFICIENT_SAMPLE, e ha teste disso.

E NAO ACUSA O ALUNO
====================
`SUSPEITA_DE_LACUNA` nao quer dizer "ele nao sabe". Quer dizer "ha informacao
suficiente para investigar". A tela diz "vamos reforcar massa molar", nunca
"voce nao domina massa molar" - a segunda frase afirma mais do que se sabe.
"""

from __future__ import annotations

from collections.abc import Mapping

from agente_ia_edu.services.grafo_pedagogico import GrafoPedagogico

# Os dois unicos valores. Nao ha um terceiro que signifique dominio, de
# proposito: se existisse, alguem o leria como mastery - e ha teste varrendo
# as constantes deste modulo atras de "CONFIRM", "MASTER" e "DOMIN".
SUSPEITA_DE_LACUNA = "SUSPECTED_GAP"
SEM_SINAL = "NO_SIGNAL"


def sinais_de_sondagem(habilidades: Mapping | None) -> dict[str, str]:
    """{habilidade: sinal}, a partir do que a sondagem registrou.

    `habilidades` e a saida de `diagnostico_por_habilidade` - cada entrada com
    `answered` e `correct`. A banda que vem junto NAO e usada aqui: ela e da
    politica de dominio, que continua respondendo o que sempre respondeu.

    A regra e uma linha: errou alguma, suspeita. E nao ha regra para o
    contrario - acertar nao produz sinal nenhum, porque nao ha o que agir.
    """
    por_habilidade = ((habilidades or {}).get("por_habilidade") or {})
    saida: dict[str, str] = {}
    for skill, dados in por_habilidade.items():
        respondidas = int((dados or {}).get("answered") or 0)
        certas = int((dados or {}).get("correct") or 0)
        errou = respondidas > 0 and certas < respondidas
        saida[skill] = SUSPEITA_DE_LACUNA if errou else SEM_SINAL
    return saida


def suspeitas(habilidades: Mapping | None) -> tuple[str, ...]:
    """So as habilidades sob suspeita."""
    return tuple(s for s, sinal in sinais_de_sondagem(habilidades).items()
                 if sinal == SUSPEITA_DE_LACUNA)


def alvo_sugerido(grafo: GrafoPedagogico,
                  habilidades: Mapping | None) -> str | None:
    """Em qual habilidade intervir, entre as suspeitas.

    Nao e a de menor acerto nem a ultima registrada: e o PRIMEIRO GARGALO do
    grafo - a mais basica da cadeia. Com leitura de formula e massa molar as
    duas erradas, ensinar massa molar seria falar sobre o telhado com quem
    ainda nao tem parede.

    Suspeita que o grafo nao conhece nao vira alvo: ele nao tem como ordena-la,
    e chutar a ordem e pior que nao responder. Quem chamou decide o que fazer
    com isso.
    """
    sob_suspeita = suspeitas(habilidades)
    if not sob_suspeita:
        return None
    return grafo.primeiro_gargalo(sob_suspeita)


__all__ = [
    "SEM_SINAL",
    "SUSPEITA_DE_LACUNA",
    "alvo_sugerido",
    "sinais_de_sondagem",
    "suspeitas",
]
