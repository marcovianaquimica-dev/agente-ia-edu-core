"""O que o aluno LE depois do microdiagnostico.

O teste humano de 2026-10-04, tendo acertado as tres perguntas, recebeu:

    "Essa parte você sabe. Agora falta Reações químicas e balanceamento."

Duas coisas erradas numa frase. A primeira: o texto era montado no JavaScript,
num `switch` sobre a decisao - regra pedagogica onde o console do navegador
alcanca. A segunda: nomeava como "o que falta" exatamente o conteudo que o
aluno acabara de demonstrar.

Aqui o texto e do backend e depende do resultado REAL. O frontend traduz.

A REGRA
=======
Nunca afirmar dominio que a politica nao sustenta. Dizer "voce domina" a quem
tirou 2 de 3 e pior que nao dizer nada: o aluno acredita, e a escola descobre
na prova.

E, do outro lado, preparacao e AJUDA, nunca falta do aluno. Nenhuma frase aqui
diz que ele errou, que esta fraco ou que tem deficiencia - ha teste varrendo
essas palavras.
"""

from __future__ import annotations

from agente_ia_edu.services.micro_diagnostic import (
    DECISION_INSUFFICIENT,
    DECISION_PREPARE,
    DECISION_PROCEED,
)

TOM_BOM = "BOM"
TOM_REVISAR = "REVISAR"
TOM_NEUTRO = "NEUTRO"


def feedback_do_diagnostico(*, decision: str, band: str, content_name: str,
                            objective_name: str | None = None) -> dict:
    """Titulo, detalhe e tom - prontos para a tela, decididos aqui.

    ``objective_name`` e a tarefa da escola por tras do diagnostico. Quando ha,
    a frase diz PARA QUE aquilo serviu: o aluno acabou de responder tres
    perguntas de um conteudo que ninguem pediu, e merece saber por que.
    """
    conteudo = content_name or "esse conteúdo"
    para_que = (f" Essa base é importante para avançarmos em {objective_name}."
                if objective_name else "")

    if decision == DECISION_PROCEED:
        return {
            "tom": TOM_BOM,
            "titulo": "Muito bem!",
            "detalhe": f"Você demonstrou um bom domínio de {conteudo}.{para_que}",
        }

    if decision == DECISION_PREPARE:
        base = (f" Esse conteúdo é uma base importante para compreender "
                f"{objective_name}." if objective_name else "")
        return {
            "tom": TOM_REVISAR,
            "titulo": f"Vamos revisar {conteudo} antes de continuar.",
            # Sem "errou", sem "fraco": o que ele le e um convite, nao um
            # diagnostico sobre ele.
            "detalhe": f"Alguns pontos ainda precisam ficar mais firmes.{base}",
        }

    if decision == DECISION_INSUFFICIENT:
        return {
            "tom": TOM_NEUTRO,
            "titulo": "Ainda não dá para concluir.",
            "detalhe": (f"Preciso de mais um pouco de {conteudo} para saber "
                        f"por onde te ajudar."),
        }

    # Decisao que este modulo nao conhece. Nao elogia, nao acusa, nao conclui.
    return {
        "tom": TOM_NEUTRO,
        "titulo": "Resposta registrada.",
        "detalhe": f"Vamos continuar de onde você parou em {conteudo}.",
    }


__all__ = ["feedback_do_diagnostico", "TOM_BOM", "TOM_REVISAR", "TOM_NEUTRO"]
