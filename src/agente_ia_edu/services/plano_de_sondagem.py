"""QUAIS MICRO-HABILIDADES SONDAR - a decisao, separada do instrumento.

A SEPARACAO QUE ESTE MODULO EXISTE PARA MARCAR
===============================================
    DECISAO PEDAGOGICA   qual micro-habilidade investigar   <- aqui
    SELECAO DE ITEM      com que instrumento investiga-la   <- outro modulo

Ate 2026-10-07 nao havia a primeira. O microdiagnostico pedia "tres questoes
de Estequiometria" e o banco devolvia as tres primeiras por numero oficial. A
micro-habilidade nunca entrava na pergunta - e sem ela a sondagem nao e
discriminativa, e sim uma amostra.

A ORDEM E DO GRAFO
===================
Da base para o topo: a habilidade que sustenta as outras vem primeiro. E a
mesma nocao de profundidade que `primeiro_gargalo` ja usa para decidir onde
intervir, e pelo mesmo motivo - comecar pelo topo da cadeia faz o aluno errar
por travar no primeiro elo, e o sistema registra a lacuna errada.

O FILTRO E DA REALIDADE
========================
Nem toda habilidade do grafo tem instrumento. Estequiometria declara nove;
duas delas - o conceito de mol e a leitura de coeficiente - nao tem nenhuma
questao classificada no acervo.

Perguntar sobre elas seria servir uma questao de outra habilidade, ou nao
servir nada. Entao o plano PULA o que nao se pode medir - e isso nao e o
banco decidindo pedagogia: a ORDEM continua sendo do grafo, e o que a
realidade faz e apenas dizer onde ha instrumento. O sistema recusa-se a
perguntar algo cuja resposta ele nao saberia interpretar.

Que habilidades ficaram de fora e informacao, nao detalhe: `planejar`
devolve as duas listas, e e assim que se descobre que falta instrumento para
uma parte do grafo.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from agente_ia_edu.services.grafo_pedagogico import GrafoPedagogico


@dataclass(frozen=True)
class PlanoDeSondagem:
    """O que sondar, e o que ficou de fora por falta de instrumento."""

    habilidades: tuple[str, ...]
    sem_instrumento: tuple[str, ...]
    # Quantas o plano queria sondar. Menos que isto significa que o grafo
    # nao tinha habilidades mensuraveis suficientes - e quem chama precisa
    # saber disso em vez de descobrir pelo tamanho da lista.
    pedidas: int


def ordem_de_base(grafo: GrafoPedagogico) -> tuple[str, ...]:
    """As habilidades da mais basica a mais dependente.

    Profundidade = quantos degraus ha embaixo dela. O codigo desempata, para
    que a ordem nao mude entre duas execucoes - o aluno nao pode ver a
    sondagem trocar de perguntas sozinha.
    """
    return tuple(sorted(
        grafo.codigos(),
        key=lambda c: (len(grafo.prerequisitos_em_profundidade(c)), c)))


def planejar(grafo: GrafoPedagogico, *, quantas: int,
             mensuravel: Callable[[str], bool] | None = None,
             ja_medidas: Sequence[str] | None = None) -> PlanoDeSondagem:
    """As micro-habilidades a sondar, na ordem da base para o topo.

    `mensuravel` responde "existe instrumento para esta habilidade?". Quem
    sabe isso e o acervo, e por isso ele entra como funcao e nao como
    dependencia: este modulo decide a ORDEM e nao consulta banco nenhum.

    `ja_medidas` sai da frente quando houver evidencia recente - hoje nao ha
    quem passe, e o parametro existe para que a sondagem nao precise mudar de
    forma quando houver.
    """
    if quantas <= 0:
        return PlanoDeSondagem(habilidades=(), sem_instrumento=(),
                               pedidas=max(0, quantas))

    medidas = set(ja_medidas or ())
    escolhidas: list[str] = []
    sem: list[str] = []
    for code in ordem_de_base(grafo):
        if code in medidas:
            continue
        if mensuravel is not None and not mensuravel(code):
            sem.append(code)
            continue
        if len(escolhidas) < quantas:
            escolhidas.append(code)
    return PlanoDeSondagem(habilidades=tuple(escolhidas),
                           sem_instrumento=tuple(sem),
                           pedidas=quantas)


__all__ = ["PlanoDeSondagem", "ordem_de_base", "planejar"]
