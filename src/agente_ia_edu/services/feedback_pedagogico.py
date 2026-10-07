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

from agente_ia_edu.services.assessor_pedagogico import (
    ABORDAGEM_EXEMPLO,
    ACAO_ENSINAR,
    ACAO_ESCALAR,
    ACAO_GUIADA,
    ACAO_INVESTIGAR,
    ACAO_VERIFICAR,
)
from agente_ia_edu.services.micro_diagnostic import (
    DECISION_INSUFFICIENT,
    DECISION_PREPARE,
    DECISION_PROCEED,
)
from agente_ia_edu.services.trajetoria_do_aluno import TENDENCIA_CONFIRMADA

TOM_BOM = "BOM"
TOM_REVISAR = "REVISAR"
TOM_NEUTRO = "NEUTRO"


def feedback_do_diagnostico(*, decision: str, band: str, content_name: str,
                            objective_name: str | None = None,
                            alvo_nome: str | None = None) -> dict:
    """Titulo, detalhe e tom - prontos para a tela, decididos aqui.

    ``objective_name`` e a tarefa da escola por tras do diagnostico. Quando ha,
    a frase diz PARA QUE aquilo serviu: o aluno acabou de responder tres
    perguntas de um conteudo que ninguem pediu, e merece saber por que.

    ``alvo_nome`` E A MICRO-HABILIDADE QUE O MOTOR VAI TRABALHAR A SEGUIR.
    ====================================================================
    Medido no navegador em 2026-10-07: o aluno acertou leitura de formula,
    acertou proporcao e ERROU massa molar. A tela disse "Muito bem! Voce
    demonstrou um bom dominio de Estequiometria", e o botao abaixo levava a
    investigar massa molar.

    As duas frases estao certas, cada uma sob a sua regra. A politica
    concluiu PROCEED - 2 de 3, acuracia 0,667 - e isso e uma decisao
    OPERACIONAL: ele pode comecar a atividade. A assimetria diagnostica
    concluiu SUSPECTED_GAP em massa molar, e um erro basta para suspeitar.

    O defeito estava em comunicar a decisao GLOBAL como se fosse uma
    conclusao sobre o conteudo inteiro. Ausencia de bloqueio nao e
    declaracao de dominio.

    Nada da politica mudou aqui: nem corte, nem minimo de amostra, nem a
    existencia de PROCEED. Mudou a frase - ela passa a refletir a decisao
    mais ESPECIFICA disponivel, que e o alvo, quando ele existe.
    """
    conteudo = content_name or "esse conteúdo"
    para_que = (f" Essa base é importante para avançarmos em {objective_name}."
                if objective_name else "")

    if decision == DECISION_PROCEED:
        if alvo_nome:
            # Reconhece o que ele mostrou E nomeia o que ficou. Dizer so a
            # ressalva seria tao impreciso quanto dizer so o elogio: ele
            # acertou duas de tres.
            return {
                "tom": TOM_REVISAR,
                "titulo": "Boa parte disso você já faz.",
                "detalhe": (f"Encontrei um ponto que vale a pena olharmos "
                            f"antes de seguir: {alvo_nome}.{para_que}"),
            }
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


def feedback_do_passo(*, action: str | None, trend: str, cycle: int,
                      skill_name: str | None = None,
                      content_name: str | None = None,
                      objective_name: str | None = None,
                      approach: str | None = None) -> dict:
    """O que o aluno le depois de uma pratica - e por que ele continua aqui.

    O ACHADO 3 DO TESTE HUMANO (2026-10-05)
    ========================================
    Depois de uma pratica ruim, a tela dizia:

        "Você acertou 1 de 5.
         Isso entra no seu progresso e ajusta o próximo passo."

    Duas frases sobre o SISTEMA. O aluno nao ficava sabendo o que foi
    observado, o que vem agora, nem por que esse proximo passo ajuda. E o
    texto morava no JavaScript - exatamente de onde o feedback do diagnostico
    ja teve de ser tirado uma vez.

    Aqui a frase vem da DECISAO, entao as duas nao podem divergir: se o passo
    mudar, o texto muda junto.
    """
    assunto = skill_name or content_name or "esse ponto"
    onde = content_name or "esse conteúdo"
    para = objective_name

    if action is None and trend == TENDENCIA_CONFIRMADA:
        # DOMINIO DEMONSTRADO - e demonstrado DUAS vezes seguidas, que e o que
        # a trajetoria exige. Dizer isso a quem nao demonstrou seria a mentira
        # que este modulo inteiro existe para nao contar.
        # "Avancar para X" dito a quem JA esta em X nao e um destino: e a
        # mesma frase dobrada. Acontece sempre que a atividade exige o proprio
        # conteudo que precisava de preparacao, que e o caso comum.
        destino = (f" Agora podemos avançar para {para}."
                   if para and para != content_name else "")
        return {"tom": TOM_BOM,
                "titulo": "Essa base está consolidada.",
                "detalhe": f"Você resolveu sozinho duas vezes seguidas — não "
                           f"foi sorte.{destino}"}

    if action is None:
        return {"tom": TOM_BOM,
                "titulo": "Você já tem o que precisa aqui.",
                "detalhe": f"A evidência sobre {onde} é suficiente para seguir."}

    if action == ACAO_VERIFICAR:
        # RECUPERACAO. O elogio e legitimo - ele melhorou mesmo - e o pedido
        # tambem: uma tentativa boa depois de um comeco ruim pode ser o lote
        # mais facil. Nenhuma das duas frases promete liberacao.
        return {"tom": TOM_BOM,
                "titulo": "Você evoluiu bastante.",
                "detalhe": "Vamos fazer uma verificação rápida para confirmar "
                           "que essa base ficou firme. São poucas questões."}

    if action == ACAO_ESCALAR:
        # ESCALONAMENTO. DUAS COISAS QUE A FRASE NAO PODE FAZER.
        #
        # A primeira: prometer que alguem foi avisado. Nao ha tela de
        # professor que receba isto, e um aviso inexistente deixa o aluno
        # esperando por algo que nao vem.
        #
        # A segunda, medida no navegador em 2026-10-06: dizer "o melhor
        # proximo passo e conversar com seu professor" e, logo abaixo,
        # oferecer "Pergunte ao Assessor". As duas frases juntas dizem ao
        # aluno que o sistema desistiu e que ele continue nele - e a primeira
        # soa como fim de linha quando nao e.
        #
        # ESCALAR nao quer dizer que o Edu desistiu. Quer dizer que ESTA
        # ESTRATEGIA chegou ao limite previsto. O que continua disponivel
        # continua disponivel, e a frase diz isso sem fingir um canal que nao
        # existe.
        return {"tom": TOM_NEUTRO,
                "titulo": f"{onde} continua difícil — e isso acontece.",
                "detalhe": "Já tentei pelos caminhos que tenho aqui, e seguir "
                           "repetindo não ajudaria. Você pode voltar à "
                           "explicação, me perguntar sobre o ponto exato onde "
                           "travou, ou levar esta dúvida para o seu professor "
                           "— ele vê coisas que eu não vejo."}

    primeira_vez = int(cycle or 1) <= 1

    if action == ACAO_INVESTIGAR:
        # INVESTIGAR. A frase precisa dizer POR QUE o aluno vai responder mais
        # perguntas logo depois de ter errado - sem isso, microperguntas
        # parecem mais do mesmo.
        #
        # E ela nao afirma o que ele fez. "Vamos conferir uma etapa de cada
        # vez" e verdade; "voce esqueceu a proporcao" seria afirmar sobre a
        # cabeca de alguem a partir de uma letra marcada.
        return {"tom": TOM_REVISAR,
                "titulo": f"Vamos localizar onde {assunto} está travando.",
                "detalhe": "São três perguntas curtas, uma etapa de cada vez. "
                           "Não valem nota — servem para eu descobrir o que "
                           "explicar, em vez de explicar tudo de novo."}

    if action == ACAO_ENSINAR:
        if primeira_vez:
            return {"tom": TOM_REVISAR,
                    "titulo": f"Vamos olhar {assunto} com calma.",
                    "detalhe": "Antes de continuar, uma explicação curta com "
                               "um exemplo resolvido até o fim."}
        if approach == ABORDAGEM_EXEMPLO:
            # A FRASE E O COMPORTAMENTO SAO A MESMA DECISAO. Se o texto diz
            # "pelo exemplo" e a tela abre no primeiro paragrafo, uma das duas
            # mente - e foi assim que o UX-4 aconteceu.
            return {"tom": TOM_REVISAR,
                    "titulo": f"{assunto.capitalize()} ainda está travando.",
                    "detalhe": "Reler o mesmo texto raramente destrava. Vamos "
                               "direto ao exemplo resolvido, acompanhando a "
                               "conta passo a passo."}
        return {"tom": TOM_REVISAR,
                "titulo": f"{assunto.capitalize()} ainda está travando.",
                "detalhe": "Vamos rever a explicação de outro jeito antes de "
                           "tentar mais uma vez — repetir questões sem isso "
                           "não ajudaria."}

    if action == ACAO_GUIADA:
        return {"tom": TOM_REVISAR,
                "titulo": "Agora vamos tentar juntos.",
                "detalhe": f"Uma questão sobre {assunto}, e a ajuda aparece "
                           f"em etapas se você travar."}

    # PRATICAR
    #
    # A FRASE NOMEIA A HABILIDADE, QUANDO HA UMA.
    #
    # Medido no navegador em 2026-10-06: o motor ja tinha escolhido
    # `MASSA_MOLAR` como alvo, e a tela dizia "Vamos praticar Estequiometria e
    # calculos quimicos um pouco" - o conteudo inteiro. O aluno nao ficava
    # sabendo em que PONTO estava sendo ajudado, que e justamente o que o
    # motor por micro-habilidade passou a saber.
    #
    # `assunto` ja prefere `skill_name` ao nome do conteudo, e o rotulo vem do
    # grafo em portugues - nunca o codigo.
    if primeira_vez:
        return {"tom": TOM_NEUTRO,
                "titulo": "Hora de tentar sozinho.",
                "detalhe": f"Algumas questões de {assunto} para ver o que já "
                           f"ficou firme."}
    return {"tom": TOM_NEUTRO,
            "titulo": "Vamos tentar de novo, sozinho.",
            "detalhe": f"Agora com {assunto} fresco — é assim que eu confiro "
                       f"se a explicação funcionou."}


__all__ = ["feedback_do_diagnostico", "feedback_do_passo",
           "TOM_BOM", "TOM_REVISAR", "TOM_NEUTRO"]
