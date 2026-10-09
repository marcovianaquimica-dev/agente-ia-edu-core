"""O REGISTRO DE GRAFOS - quais conteudos ja tem contrato pedagogico V2.

A REGRA DE CONVIVENCIA
=======================
    conteudo COM grafo    -> motor pedagogico por micro-habilidade
    conteudo SEM grafo    -> comportamento legado, sem mudanca nenhuma

Isso nao e um detalhe de implementacao: e o que permite a arquitetura entrar
progressivamente, um conteudo por vez, sem que os outros 36 do catalogo
mudem de comportamento no mesmo dia. Um aluno com historico em Biologia
continua vendo exatamente o que via.

POR QUE UM REGISTRO, E NAO UMA BUSCA
=====================================
Para que a pergunta "este conteudo ja tem contrato V2?" tenha UMA resposta,
num lugar so. Espalhar `if content_code == ...` pelas camadas faria o
segundo conteudo custar o mesmo trabalho que o primeiro.
"""

from __future__ import annotations

from agente_ia_edu.services.grafo_estequiometria import (
    CONTEUDO as CONTEUDO_ESTEQUIOMETRIA,
    GRAFO as GRAFO_ESTEQUIOMETRIA,
)
from agente_ia_edu.services.grafo_pedagogico import GrafoPedagogico

# O piloto vertical. Um conteudo. Acrescentar o segundo e acrescentar uma
# linha aqui mais o modulo do grafo - nenhuma camada precisa saber.
_REGISTRO: dict[str, GrafoPedagogico] = {
    CONTEUDO_ESTEQUIOMETRIA: GRAFO_ESTEQUIOMETRIA,
}


def grafo_de(content_code: str | None) -> GrafoPedagogico | None:
    """O grafo deste conteudo, ou None quando ele ainda nao tem um."""
    if not content_code:
        return None
    return _REGISTRO.get(content_code)


def tem_contrato_v2(content_code: str | None) -> bool:
    return grafo_de(content_code) is not None


def conteudos_com_grafo() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRO))


__all__ = ["conteudos_com_grafo", "grafo_de", "tem_contrato_v2"]
