"""O MODELO DO ALUNO POR MICRO-HABILIDADE - sobre o que ja se mede.

A PONTE QUE ESTE MODULO E
==========================
O motor pedagogico pede `{habilidade: estado}`. O sistema ja produz
`{habilidade: banda}`: `diagnostico_por_habilidade` agrega as respostas
corrigidas e a `PerformanceThresholdPolicy` diz em que faixa cada uma caiu.

Falta so a traducao - e nenhuma tabela nova. O modelo do aluno por
micro-habilidade ja e representavel com o que existe; ver
docs/pedagogical-engine-audit.md.

ELA E CONSERVADORA NUMA DIRECAO ESPECIFICA
===========================================
Amostra insuficiente NAO vira lacuna. Chamar de "precisa de apoio" uma
habilidade medida por uma resposta so mandaria o aluno estudar o que ele
talvez ja saiba, e a chance de acertar no chute e 1 em 5. A faixa
intermediaria tambem nao vira: a politica ja se recusa a chama-la de lacuna,
e o modelo nao inventa uma segunda opiniao.

So a faixa de MELHORIA - a que a politica reconhece como lacuna - vira
PRECISA_APOIO. Faixa desconhecida fica NAO_MEDIDO: uma faixa nova nao pode
virar intervencao por padrao.

AS ESTRATEGIAS JA OFERECIDAS, E A LIMITACAO DESTA V1
=====================================================
Elas nao sao persistidas em lugar nenhum. `explicacao_do_erro` e sem estado e
a tela carrega a anterior; nada disso sobrevive ao fim da sessao.

A escolha aqui e derivar do CICLO, como o assessor ja deriva o ciclo das
praticas concluidas: a estrategia do ciclo N e a N-esima da escada. Nao e
tao bom quanto um historico real - duas sessoes diferentes podem repetir uma
abordagem - mas garante a propriedade que importa dentro de um percurso: a
estrategia nunca repete enquanto o ciclo avanca, e o loop termina.

Persistir o historico e a extensao natural, e esta registrada como divida.
"""

from __future__ import annotations

from collections.abc import Mapping

from agente_ia_edu.services.estrategia_de_ensino import ESTRATEGIAS
from agente_ia_edu.services.motor_pedagogico import EstadoDaHabilidade
from agente_ia_edu.services.pedagogical_analysis import BAND_IMPROVEMENT
from agente_ia_edu.services.sondagem import (
    ESTADO_CONFIRMADO,
    ESTADO_NAO_MEDIDO,
    ESTADO_PRECISA_APOIO,
)

# A unica faixa que o sistema reconhece como DOMINIO. Importada, nao escrita:
# ha teste de AST proibindo literal float neste arquivo, pelo mesmo motivo
# dos outros modulos da politica.
from agente_ia_edu.services.pedagogical_analysis import BAND_STRONG


def mapa_do_aluno(habilidades: Mapping | None) -> dict[str, str]:
    """De `diagnostico_por_habilidade` para o mapa que o motor le.

    `habilidades` e a saida de `diagnostico_por_habilidade` - um dicionario
    com `por_habilidade`, cada entrada trazendo a `band` ja decidida pela
    politica.
    """
    por_habilidade = ((habilidades or {}).get("por_habilidade") or {})
    saida: dict[str, str] = {}
    for skill, dados in por_habilidade.items():
        banda = (dados or {}).get("band")
        if banda == BAND_STRONG:
            saida[skill] = ESTADO_CONFIRMADO
        elif banda == BAND_IMPROVEMENT:
            saida[skill] = ESTADO_PRECISA_APOIO
        else:
            # Insuficiente, intermediaria, sem dado, ou uma faixa que ainda
            # nao existe: nada disso autoriza intervencao.
            saida[skill] = ESTADO_NAO_MEDIDO
    return saida


def historico_por_habilidade(skill: str, *, ciclo: int,
                             nivel: str | None = None,
                             ultimo_acerto: bool | None = None,
                             ensinou_agora: bool = False) -> EstadoDaHabilidade:
    """O que ja se tentou com esta habilidade, derivado do ciclo.

    Ver o cabecalho: as estrategias nao sao persistidas, e o ciclo e o melhor
    proxy disponivel sem tabela nova. A escada e truncada no tamanho dela -
    um ciclo 99 nao inventa estrategias que nao existem.
    """
    quantas = max(0, min(int(ciclo or 0), len(ESTRATEGIAS)))
    return EstadoDaHabilidade(
        estrategias_usadas=ESTRATEGIAS[:quantas],
        nivel_de_apoio=nivel,
        ultimo_acerto=ultimo_acerto,
        tentativas=max(0, int(ciclo or 0)),
        ensinou_agora=ensinou_agora,
    )


__all__ = ["historico_por_habilidade", "mapa_do_aluno"]
