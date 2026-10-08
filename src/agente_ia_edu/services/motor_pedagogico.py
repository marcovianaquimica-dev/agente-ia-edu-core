"""O MOTOR PEDAGOGICO - qual e a proxima intervencao, e sobre o que.

O QUE ELE DECIDE
=================
Uma coisa so, em duas partes: SOBRE QUAL micro-habilidade intervir agora, e
QUAL intervencao. O resto - medir, escrever texto, montar questao, falar com
IA - e de outras camadas.

    SONDAR               ninguem mediu nada ainda
    ENSINAR              ha lacuna, e ha estrategia nova a oferecer
    PRATICAR_COM_APOIO   acabou de aprender; tenta com ajuda
    PRATICAR             o apoio saiu; tenta sozinho
    VERIFICAR            acertou sozinho; confirmar com item novo
    ESCALAR              as estrategias acabaram
    AVANCAR              nao ha lacuna medida

A ORDEM DAS PERGUNTAS E A PROPRIA POLITICA
===========================================
1. Ha algo medido? Se nao, sondar. Intervir sobre o que ninguem mediu e
   inventar sobre a pessoa.
2. Ha lacuna? Se nao, avancar. Interromper quem ja demonstrou custa o tempo
   do aluno e a credibilidade do sistema.
3. QUAL lacuna? A mais basica da cadeia - `primeiro_gargalo`. Ensinar o
   dependente a quem nao tem a base e falar sobre o telhado com quem ainda
   nao tem parede.
4. Dai em diante a decisao e sobre UMA habilidade, e a escada manda:
   estrategia nova -> pratica com apoio -> apoio saindo -> sozinho ->
   verificacao. Quando nao ha mais estrategia, escala.

O QUE ELE NAO FAZ
==================
Nao mede o aluno (quem mede e a evidencia), nao escreve uma palavra do que o
aluno le, nao chama modelo. Por isso ele e testavel sem banco, sem HTTP e sem
provedor - e por isso ele cabe inteiro na cabeca de quem for mante-lo.

E nao sabe de que disciplina se trata. Ha teste lendo este arquivo e falhando
se alguem escrever o nome de um assunto, e outro rodando o motor com um grafo
de outra materia.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from agente_ia_edu.services.estrategia_de_ensino import (
    L0_AUTONOMO,
    L3_EXEMPLO_RESOLVIDO,
    esgotou_as_estrategias,
    nivel_apos,
    produz_evidencia_autonoma,
    proxima_estrategia,
)
from agente_ia_edu.services.grafo_pedagogico import GrafoPedagogico
from agente_ia_edu.services.sondagem import (
    ESTADO_NAO_MEDIDO,
    fracas_do_mapa,
)

PASSO_SONDAR = "PROBE"
PASSO_ENSINAR = "TEACH"
PASSO_PRATICAR_COM_APOIO = "SCAFFOLDED_PRACTICE"
PASSO_PRATICAR = "PRACTICE"
PASSO_VERIFICAR = "VERIFY"
PASSO_ESCALAR = "ESCALATE"
PASSO_AVANCAR = "ADVANCE"


@dataclass(frozen=True)
class EstadoDaHabilidade:
    """O que ja aconteceu com UMA micro-habilidade, nesta jornada.

    Tudo aqui e derivavel do que o sistema ja grava - estrategias oferecidas,
    nivel de apoio da ultima tentativa, se a ultima foi acerto. Nenhum campo
    e uma conclusao sobre o aluno: conclusao e da evidencia.
    """

    estrategias_usadas: tuple[str, ...] = ()
    nivel_de_apoio: str | None = None
    ultimo_acerto: bool | None = None
    tentativas: int = 0
    # Acabou de receber a explicacao e ainda nao tentou nada depois dela.
    ensinou_agora: bool = False


@dataclass(frozen=True)
class Decisao:
    passo: str
    habilidade: str | None = None
    rotulo: str | None = None
    estrategia: str | None = None
    nivel_de_apoio: str | None = None
    # Por que esta decisao, em uma linha - para o log estruturado e para quem
    # for depurar um percurso real.
    motivo: str = ""
    prerequisitos: tuple[str, ...] = field(default_factory=tuple)


def decidir(*, grafo: GrafoPedagogico, mapa: Mapping[str, str],
            historico: Mapping[str, EstadoDaHabilidade]) -> Decisao:
    """A proxima intervencao. Deterministica, e sem efeito colateral."""
    medidas = [c for c, estado in (mapa or {}).items()
               if estado != ESTADO_NAO_MEDIDO]
    if not medidas:
        return Decisao(passo=PASSO_SONDAR,
                       motivo="nada medido ainda nesta jornada")

    fracas = fracas_do_mapa(mapa)
    if not fracas:
        return Decisao(passo=PASSO_AVANCAR,
                       motivo="nenhuma lacuna medida")

    alvo = grafo.primeiro_gargalo(fracas)
    if alvo is None:
        # Fracas que o grafo nao conhece: o motor novo nao tem o que dizer, e
        # quem chamou cai no comportamento legado em vez de adivinhar.
        return Decisao(passo=PASSO_AVANCAR,
                       motivo="as lacunas medidas nao estao neste grafo")

    estado = (historico or {}).get(alvo) or EstadoDaHabilidade()
    tem_prerequisito = bool(grafo.prerequisitos(alvo))
    base = dict(habilidade=alvo, rotulo=grafo.rotulo(alvo),
                prerequisitos=grafo.prerequisitos(alvo))

    # ACABOU DE SER ENSINADO: tenta com o maximo de apoio, nao sozinho.
    if estado.ensinou_agora:
        return Decisao(passo=PASSO_PRATICAR_COM_APOIO,
                       nivel_de_apoio=L3_EXEMPLO_RESOLVIDO,
                       estrategia=estado.estrategias_usadas[-1]
                       if estado.estrategias_usadas else None,
                       motivo="explicacao recem-dada: tentar com apoio",
                       **base)

    # JA TENTOU DEPOIS DE APRENDER: a escada manda.
    if estado.nivel_de_apoio is not None and estado.ultimo_acerto is not None:
        if estado.ultimo_acerto and produz_evidencia_autonoma(estado.nivel_de_apoio):
            # Acertou SOZINHO. Confirmar com item novo - um acerto nao e um
            # padrao, e e por isso que a verificacao existe.
            return Decisao(passo=PASSO_VERIFICAR, nivel_de_apoio=L0_AUTONOMO,
                           motivo="acerto autonomo: confirmar com item novo",
                           **base)
        proximo = nivel_apos(estado.nivel_de_apoio, acertou=bool(estado.ultimo_acerto))
        if estado.ultimo_acerto:
            passo = (PASSO_PRATICAR if produz_evidencia_autonoma(proximo)
                     else PASSO_PRATICAR_COM_APOIO)
            return Decisao(passo=passo, nivel_de_apoio=proximo,
                           motivo="acerto com apoio: retirar um degrau", **base)
        # ERROU. Se ainda ha estrategia nova, ensinar de outro jeito; o apoio
        # volta junto. Senao, acabou o que este sistema sabe tentar.
        if esgotou_as_estrategias(usadas=estado.estrategias_usadas,
                                  tem_prerequisito=tem_prerequisito):
            return Decisao(passo=PASSO_ESCALAR, nivel_de_apoio=proximo,
                           motivo="estrategias esgotadas apos erro", **base)
        return Decisao(
            passo=PASSO_ENSINAR, nivel_de_apoio=proximo,
            estrategia=proxima_estrategia(usadas=estado.estrategias_usadas,
                                          tem_prerequisito=tem_prerequisito),
            motivo="erro apos apoio: outra estrategia", **base)

    # AINDA NAO TENTOU NADA NESTA HABILIDADE: ensinar.
    if esgotou_as_estrategias(usadas=estado.estrategias_usadas,
                              tem_prerequisito=tem_prerequisito):
        return Decisao(passo=PASSO_ESCALAR,
                       motivo="estrategias esgotadas", **base)
    return Decisao(
        passo=PASSO_ENSINAR,
        estrategia=proxima_estrategia(usadas=estado.estrategias_usadas,
                                      tem_prerequisito=tem_prerequisito),
        motivo="lacuna medida sem estrategia tentada ainda", **base)


__all__ = [
    "Decisao",
    "EstadoDaHabilidade",
    "PASSO_AVANCAR",
    "PASSO_ENSINAR",
    "PASSO_ESCALAR",
    "PASSO_PRATICAR",
    "PASSO_PRATICAR_COM_APOIO",
    "PASSO_SONDAR",
    "PASSO_VERIFICAR",
    "decidir",
]
