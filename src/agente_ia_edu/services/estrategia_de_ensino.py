"""ESTRATEGIA E APOIO - o que fazer quando a explicacao nao funcionou.

O QUE FALTAVA
==============
O ciclo anterior ja sabia mudar o JEITO de explicar: `explicacao_do_erro` tem
seis abordagens em escada e nunca repete a anterior. Faltavam tres coisas:

    qual micro-habilidade esta sendo ensinada;
    quanto apoio o aluno esta recebendo agora;
    quando o apoio deve sair.

Sem a terceira, o aluno fica eternamente assistido - e acerto com ajuda nao e
evidencia de que ele resolve sozinho. Este modulo e a politica das tres.

DUAS COISAS QUE ELE GARANTE, E UMA QUE ELE NAO FAZ
===================================================
Garante que a estrategia nunca repete a que acabou de falhar, e que o loop
TERMINA: quando acabam as estrategias, `proxima_estrategia` devolve None, e
quem chamou sabe que chegou a hora de escalar.

Nao escreve texto nenhum. Qual estrategia usar e decisao do Nucleo; a
LINGUAGEM de cada uma pode vir de um modelo, e vem - mas por `explicacao_do_erro`,
que recebe a estrategia ja escolhida aqui.

A ESCADA DE APOIO
==================
    L3  exemplo resolvido ate o fim
    L2  perguntas guiadas
    L1  uma dica curta
    L0  sozinho

Acertar retira um degrau; errar devolve um. E so L0 produz evidencia: esta e
a regra que impede o sistema de dar por aprendido quem foi conduzido ate a
resposta. Na duvida sobre o nivel, nao conta - fail closed.

ELE NAO SABE DE QUE DISCIPLINA ESTAMOS FALANDO
===============================================
Nenhuma regra aqui cita assunto, e nenhuma chama provedor. Ha teste lendo o
arquivo.
"""

from __future__ import annotations

from collections.abc import Sequence

# AS ESTRATEGIAS, EM ORDEM DE OFERTA.
#
# Nao e uma sequencia a aplicar cegamente: e a ordem em que elas sao
# OFERECIDAS quando a anterior nao bastou. Comecar decompondo e terminar no
# pre-requisito tambem nao e arbitrario - quem nao entendeu de varios jeitos
# provavelmente nao esta travado nesta habilidade, e sim no que vinha antes.
DECOMPOSICAO = "DECOMPOSITION"
EXEMPLO_RESOLVIDO = "WORKED_EXAMPLE"
PERGUNTAS_GUIADAS = "GUIDED_QUESTIONS"
REPRESENTACAO_VISUAL = "VISUAL_REPRESENTATION"
ANALOGIA = "ANALOGY"
REVISAO_DE_PREREQUISITO = "PREREQUISITE_REVIEW"

ESTRATEGIAS: tuple[str, ...] = (
    DECOMPOSICAO,
    EXEMPLO_RESOLVIDO,
    PERGUNTAS_GUIADAS,
    REPRESENTACAO_VISUAL,
    ANALOGIA,
    REVISAO_DE_PREREQUISITO,
)

# A escada de apoio, do mais apoiado ao autonomo.
L3_EXEMPLO_RESOLVIDO = "L3"
L2_PERGUNTAS_GUIADAS = "L2"
L1_DICA = "L1"
L0_AUTONOMO = "L0"

NIVEIS: tuple[str, ...] = (L3_EXEMPLO_RESOLVIDO, L2_PERGUNTAS_GUIADAS,
                           L1_DICA, L0_AUTONOMO)


def _disponiveis(permitidas: Sequence[str] | None,
                 tem_prerequisito: bool) -> tuple[str, ...]:
    base = tuple(permitidas) if permitidas else ESTRATEGIAS
    base = tuple(e for e in base if e in ESTRATEGIAS)
    if not tem_prerequisito:
        # Mandar revisar o pre-requisito de quem nao tem nenhum e mandar o
        # aluno para lugar nenhum.
        base = tuple(e for e in base if e != REVISAO_DE_PREREQUISITO)
    return base


def proxima_estrategia(*, usadas: Sequence[str] = (),
                       permitidas: Sequence[str] | None = None,
                       tem_prerequisito: bool = True) -> str | None:
    """A proxima abordagem - nunca uma ja usada.

    `None` significa que acabaram: nao e um erro, e o sinal de que a escada
    terminou e quem chamou precisa escalar. Devolver uma repetida aqui seria
    exatamente o loop que esta camada existe para impedir.

    `permitidas` deixa a micro-habilidade restringir o conjunto - nem toda
    abordagem serve a toda habilidade. Valores desconhecidos sao ignorados em
    vez de quebrar: uma versao antiga da tela nao pode derrubar a jornada.
    """
    ja = {e for e in (usadas or ())}
    for estrategia in _disponiveis(permitidas, tem_prerequisito):
        if estrategia not in ja:
            return estrategia
    return None


def esgotou_as_estrategias(*, usadas: Sequence[str] = (),
                           permitidas: Sequence[str] | None = None,
                           tem_prerequisito: bool = True) -> bool:
    """Nao ha mais nada pedagogicamente diferente a oferecer."""
    return proxima_estrategia(usadas=usadas, permitidas=permitidas,
                              tem_prerequisito=tem_prerequisito) is None


def nivel_apos(nivel: str | None, *, acertou: bool) -> str:
    """O apoio da proxima vez.

    Acertar retira um degrau; errar devolve um. Nao ha salto: quem acerta com
    exemplo resolvido ainda nao mostrou que resolve sozinho, e quem erra
    sozinho nao precisa voltar ao comeco.

    Nivel desconhecido volta ao mais apoiado - na duvida, apoiar mais.
    """
    if nivel not in NIVEIS:
        return L3_EXEMPLO_RESOLVIDO
    i = NIVEIS.index(nivel)
    novo = i + 1 if acertou else i - 1
    return NIVEIS[max(0, min(novo, len(NIVEIS) - 1))]


def produz_evidencia_autonoma(nivel: str | None) -> bool:
    """So L0. Acerto com ajuda nao e acerto sozinho.

    Esta e a linha que impede o sistema de dar por aprendido quem foi
    conduzido ate a resposta - e ela e um `==`, nao uma convencao.
    """
    return nivel == L0_AUTONOMO


__all__ = [
    "ANALOGIA",
    "DECOMPOSICAO",
    "ESTRATEGIAS",
    "EXEMPLO_RESOLVIDO",
    "L0_AUTONOMO",
    "L1_DICA",
    "L2_PERGUNTAS_GUIADAS",
    "L3_EXEMPLO_RESOLVIDO",
    "NIVEIS",
    "PERGUNTAS_GUIADAS",
    "REPRESENTACAO_VISUAL",
    "REVISAO_DE_PREREQUISITO",
    "esgotou_as_estrategias",
    "nivel_apos",
    "produz_evidencia_autonoma",
    "proxima_estrategia",
]
