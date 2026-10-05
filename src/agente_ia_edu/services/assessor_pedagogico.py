"""ASSESSOR PEDAGOGICO - a proxima intervencao de aprendizagem.

O PROBLEMA
===========
Reproduzido no navegador em 2026-10-05, com o Aluno Teste A:

    Atividade de Estequiometria
    -> diagnostico de Balanceamento
    -> 1 de 3
    -> "Continuar"
    -> PRATICA: mais cinco questoes de Balanceamento

Entre errar e responder de novo nao havia nada. O sistema SABIA qual era a
lacuna - a decisao do diagnostico trazia `CONSERVACAO_DE_ATOMOS: 3
respondidas, 0,33` - e respondeu oferecendo mais perguntas. Isso e um
encadeador adaptativo de exercicios. Um assessor ensina antes de perguntar de
novo.

O QUE ESTE MODULO DECIDE
=========================
Uma coisa so: a proxima intervencao e ENSINAR, PRATICAR, ou nenhuma.

E DETERMINISTICO. Nenhuma decisao daqui consulta modelo de IA. O percurso
pedagogico pertence ao Nucleo Edu 360; um modelo podera, depois, adaptar a
LINGUAGEM de uma explicacao - nunca escolher se o aluno precisa dela. Ha
teste lendo a AST deste arquivo que falha se alguem importar um provider.

O QUE ELE NAO DECIDE
=====================
Se o aluno aprendeu. Isso continua sendo da evidencia, do mapa de dominio e
da `PerformanceThresholdPolicy`. Ter lido uma explicacao nao e ter aprendido.

NENHUM CORTE NOVO
==================
As faixas chegam prontas de quem mede (`diagnostico_por_habilidade` e o mapa
de dominio). Ha teste de AST proibindo literal float aqui: um numero neste
arquivo seria uma segunda politica, divergindo da primeira no primeiro
ajuste.

COMO O CICLO E CONTADO SEM TABELA NOVA
=======================================
    ja_ensinado          o aluno ja abriu a explicacao (MaterialProgress)
    praticas_concluidas  quantas praticas daquele conteudo ele terminou

Os dois ja sao gravados pelo sistema, por motivos proprios. O ciclo e
derivado deles. Nao ha persistencia nova nesta camada - e, se um dia houver,
que seja porque algo deixou de ser derivavel, nao por conveniencia.
"""

from __future__ import annotations

from agente_ia_edu.services.pedagogical_analysis import (
    BAND_IMPROVEMENT,
    BAND_INSUFFICIENT,
    BAND_NO_DATA,
)

ACAO_ENSINAR = "TEACH"
ACAO_PRATICAR = "PRACTICE"

# Depois de tantos ciclos de ensinar-praticar sem o aluno destravar, insistir
# sozinho deixa de ser ajuda. O sistema continua oferecendo pratica - parar de
# ajudar seria pior - mas marca que alguem humano precisa saber.
LIMITE_DE_CICLOS = 3

_FAIXAS_DE_LACUNA = (BAND_IMPROVEMENT,)


def _pior_habilidade(habilidades: dict) -> tuple[str | None, str | None]:
    """A habilidade com lacuna MEDIDA, ou (None, None).

    "Medida" e o ponto: uma habilidade com amostra insuficiente nao entra.
    Apontar a lacuna errada manda o aluno estudar o que ele ja sabe, e ele
    percebe.
    """
    por_habilidade = (habilidades or {}).get("por_habilidade") or {}
    candidatas = [
        (s, v) for s, v in por_habilidade.items()
        if v.get("band") in _FAIXAS_DE_LACUNA
    ]
    if not candidatas:
        return None, None
    # A de menor acerto primeiro; o codigo desempata para a saida ser estavel.
    # `or 0` e um default de ordenacao, nao um corte - o guarda de AST que
    # proibe literal float neste arquivo esta certo em ser cego a diferenca.
    pior = min(candidatas, key=lambda kv: (kv[1].get("accuracy") or 0, kv[0]))
    return pior[0], (pior[1].get("name") or pior[0])


def decidir_intervencao(
    *,
    habilidades: dict,
    banda_do_conteudo: str,
    ja_ensinado: bool,
    praticas_concluidas: int,
    ha_material: bool,
    objetivo_nome: str | None = None,
    conteudo_nome: str | None = None,
) -> dict:
    """A proxima intervencao, ou `action=None` quando nao ha o que intervir.

    `habilidades` e a saida de `diagnostico_por_habilidade`; `banda_do_conteudo`
    e a faixa do conteudo inteiro. Os dois ja passaram pela politica de cortes.
    """
    ciclo = int(praticas_concluidas or 0) + 1
    escalar = int(praticas_concluidas or 0) >= LIMITE_DE_CICLOS
    skill, skill_nome = _pior_habilidade(habilidades)

    base = {
        "action": None,
        "skill": skill,
        "skill_name": skill_nome,
        "cycle": ciclo,
        "escalate": escalar,
        "target_name": objetivo_nome,
        "blocking_name": conteudo_nome,
        "reason": None,
        "learning_objective": None,
        "next_check": None,
    }

    # SEM MEDIDA NAO HA LACUNA. Quem ainda nao foi medido e assunto do
    # diagnostico, nao do assessor - e "voce tem dificuldade em X" dito a
    # quem nunca respondeu nada sobre X e inventar sobre a pessoa.
    if banda_do_conteudo in (BAND_INSUFFICIENT, BAND_NO_DATA):
        return base

    ha_lacuna = banda_do_conteudo in _FAIXAS_DE_LACUNA or skill is not None
    if not ha_lacuna:
        # Quem ja demonstrou o que precisava nao e interrompido: intervir em
        # quem sabe custa o tempo do aluno e a credibilidade do sistema.
        return base

    assunto = skill_nome or conteudo_nome or "este conteúdo"

    # ENSINAR vem antes de perguntar de novo - mas so se houver o que ensinar.
    # Prometer uma explicacao que nao existe seria pior que a pratica.
    if ha_material and not ja_ensinado:
        acao = ACAO_ENSINAR
    else:
        acao = ACAO_PRATICAR

    base.update({
        "action": acao,
        "reason": _motivo(assunto, conteudo_nome, objetivo_nome),
        "learning_objective": f"Entender {assunto} e usar isso para resolver "
                              f"exercícios sem travar.",
        "next_check": "Depois da prática, eu confiro se você já consegue "
                      "resolver sozinho — e só então seguimos.",
    })
    return base


def _motivo(assunto: str, conteudo_nome: str | None, objetivo_nome: str | None) -> str:
    """Por que estou estudando isto, em linguagem de aluno.

    Sem `readiness`, `DIRECT`, `band` ou `origin_breakdown`: esses conceitos
    sao do sistema, e ler o proprio diagnostico escrito em codigo interno nao
    ajuda ninguem a aprender.
    """
    onde = conteudo_nome or "esta base"
    if objetivo_nome:
        return (f"Pelas suas respostas, {assunto} ainda está travando. "
                f"{onde} é a base de {objetivo_nome} — vale firmar isso antes.")
    return (f"Pelas suas respostas, {assunto} ainda está travando. "
            f"Vale firmar isso antes de seguir.")
