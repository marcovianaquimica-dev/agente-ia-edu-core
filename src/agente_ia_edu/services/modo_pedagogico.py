"""OS QUATRO MODOS - e o unico em que errar interrompe a sequencia.

O QUE ESTE MODULO DECIDE
=========================
Uma coisa so: para que serve este lote de questoes. Nao decide pedagogia,
nao le respostas, nao toca banco. E o PORTAO que vem antes de qualquer
decisao sobre o erro.

    DIAGNOSTICO       reunir evidencia. Ensinar no meio contamina as
                      observacoes seguintes: o aluno erra a 2 porque
                      acabou de ouvir a explicacao da 1, e o mapa registra
                      uma lacuna que nao existe.
    PRATICA FORMATIVA desenvolver aprendizagem. E exatamente aqui que o
                      erro precisa produzir uma decisao antes do proximo
                      item - e era o que faltava.
    VERIFICACAO L0    coletar evidencia INDEPENDENTE. Uma dica durante a
                      tentativa transforma autonomia em apoio, e o acerto
                      deixa de significar o que a politica supoe.
    AVALIACAO FORMAL  avaliar segundo a regra da escola. Ensinar durante a
                      prova viola a politica da avaliacao.

O MODO NAO E UM CAMPO NOVO
===========================
Ele ja viaja no `metadata` da atribuicao desde que a pratica existe:
`origin` e `purpose`, gravados na criacao por `AdaptivePracticeService`.
Medido no banco de desenvolvimento em 2026-10-08:

    origin=MICRO_DIAGNOSTIC purpose=PRACTICE practice=true   diagnostico
    origin=PRACTICE         purpose=PRACTICE practice=true   formativa
    origin=PRACTICE         purpose=VERIFY   practice=true   verificacao
    (sem `practice`)                                         tarefa da escola

Inventar um quinto campo criaria uma segunda verdade sobre o que o lote e, e
as duas divergiriam no primeiro lote criado por um caminho que esquecesse de
preencher o campo novo.

FAIL-CLOSED, E A DIRECAO IMPORTA
=================================
Metadata ausente, vazio ou desconhecido vira AVALIACAO FORMAL - o modo mais
restritivo. Os dois erros possiveis nao tem o mesmo custo: errar para o lado
restritivo deixa um aluno sem uma intervencao que ele teria; errar para o
lado permissivo interrompe uma prova para ensinar.
"""

from __future__ import annotations

from collections.abc import Mapping

MODO_DIAGNOSTICO = "DIAGNOSTIC"
MODO_FORMATIVO = "FORMATIVE_PRACTICE"
MODO_VERIFICACAO = "VERIFICATION"
MODO_AVALIACAO = "FORMAL_ASSESSMENT"

# Os valores que a criacao da pratica grava hoje. Lista fechada: um valor
# novo e decisao consciente de quem o introduzir, nao um `else` que cai em
# formativo por descuido.
_ORIGEM_DIAGNOSTICO = "MICRO_DIAGNOSTIC"
_ORIGEM_PRATICA = "PRACTICE"
_PROPOSITO_PRATICA = "PRACTICE"
_PROPOSITO_VERIFICACAO = "VERIFY"

# O degrau que produz evidencia autonoma. Reexportado aqui so para quem
# precisa do nome; a invariante mora em `escada_de_apoio.produz_evidencia`.
_SO_O_FORMATIVO_INTERROMPE = (MODO_FORMATIVO,)


def modo_de(metadata: Mapping | None) -> str:
    """Para que serve este lote, lido do metadata da atribuicao.

    Devolve sempre um dos quatro - nunca None, nunca uma string livre -,
    para que quem chama nao precise tratar ausencia.
    """
    meta = dict(metadata or {})
    # SEM A MARCA `practice` E TAREFA DE PROFESSOR. Ela e escrita por
    # `create_practice` e por mais ninguem; uma atribuicao distribuida pela
    # escola nao a tem.
    if not meta.get("practice"):
        return MODO_AVALIACAO

    origem = str(meta.get("origin") or "")
    proposito = str(meta.get("purpose") or "")

    if origem == _ORIGEM_DIAGNOSTICO:
        # O proposito do diagnostico e PRACTICE por heranca do executor
        # unico; quem manda aqui e a ORIGEM, que e o que diz onde a
        # evidencia e arquivada.
        return MODO_DIAGNOSTICO
    if origem == _ORIGEM_PRATICA:
        if proposito == _PROPOSITO_VERIFICACAO:
            return MODO_VERIFICACAO
        if proposito == _PROPOSITO_PRATICA:
            return MODO_FORMATIVO
    return MODO_AVALIACAO


def intervem_no_erro(modo: str) -> bool:
    """Errar neste modo interrompe a sequencia para intervir?

    Esta funcao e a invariante inteira do modulo, e e por isso que ela
    existe separada de `modo_de`: ha teste exigindo que EXATAMENTE UM dos
    quatro devolva True. Acrescentar um modo obriga a decidir.
    """
    return modo in _SO_O_FORMATIVO_INTERROMPE


__all__ = [
    "MODO_AVALIACAO",
    "MODO_DIAGNOSTICO",
    "MODO_FORMATIVO",
    "MODO_VERIFICACAO",
    "intervem_no_erro",
    "modo_de",
]
