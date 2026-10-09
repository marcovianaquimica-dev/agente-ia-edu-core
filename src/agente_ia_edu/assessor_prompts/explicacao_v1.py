"""Prompt da EXPLICACAO DE UM ERRO - artefato versao v1.

O QUE ELE PEDE, E O QUE ELE NAO PEDE
=====================================
Pede uma explicacao curta do PONTO EM QUE O RACIOCINIO DESVIOU, a partir da
alternativa que o aluno marcou. Nao pede "diga por que a correta e correta":
isso o gabarito ja diz, e o aluno acabou de ve-lo.

POR QUE O GABARITO ESTA AQUI, SE NA CONVERSA NAO ESTA
======================================================
Sao dois contextos diferentes, e a diferenca e deliberada.

Na conversa livre (`assessor_prompts/v2`) o aluno pode estar com uma questao
de avaliacao ABERTA, e a protecao contra "me diz a letra" e a AUSENCIA DO
DADO. Aqui a questao JA FOI corrigida: o aluno ja viu a resposta certa na
propria tela de revisao. Esconder o gabarito do prompt nao protegeria nada -
so impediria o modelo de explicar o exercicio.

O FORMATO
==========
JSON de um campo, pelo mesmo motivo da v2 da conversa: o adaptador OpenAI
deste repositorio fixa `response_format={"type": "json_object"}`, e um prompt
que pede texto corrido perde a briga com o transporte - o aluno ja viu o
envelope cru na tela uma vez.

Nunca edite a redacao desta versao. Uma mudanca de redacao e um `v2.py`.
"""

from __future__ import annotations

VERSION = "assessor-explicacao-v1"

CAMPO_DA_RESPOSTA = "explicacao"

# Como entrar no assunto desta vez. O nome chega ao prompt porque o modelo
# precisa saber o que foi pedido; QUAL deles usar e decisao do sistema
# (`explicacao_do_erro.proxima_estrategia`), nunca do modelo.
_COMO = {
    "CONCEITO": "Explique a IDEIA por tras do que ele errou, do comeco, em "
                "linguagem simples. Nada de conta ainda.",
    "PASSO_A_PASSO": "Mostre o caminho da resolucao em passos numerados "
                     "curtos, dizendo o que se faz em cada um e por que.",
    "EXEMPLO": "De um exemplo resolvido ate o fim, com numeros, do mesmo tipo "
               "da questao - e so entao diga o que mudava na dele.",
    "ANALOGIA": "Use uma analogia concreta do dia a dia para a ideia central, "
                "e depois volte a questao em uma frase.",
    "DECOMPOSICAO": "Quebre a questao em perguntas menores, na ordem em que "
                    "precisam ser respondidas, e resolva so a primeira.",
    "PREREQUISITO": "Volte ao conhecimento anterior que a questao exigia e "
                    "que provavelmente esta faltando, e firme so ele.",
}

_PAPEL = """Você é o Assessor Pedagógico do Núcleo Edu 360 falando com um
estudante do ensino médio em português do Brasil, logo depois de ele errar uma
questão que já foi corrigida.

Você não é um assistente de uso geral. A sua única tarefa agora é fazer ESTE
aluno entender ESTE ponto."""

_REGRAS = """COMO RESPONDER

1. Comece pelo que ele provavelmente fez, a partir da alternativa que marcou.
   Se der para identificar onde o raciocínio desviou, diga isso - é mais útil
   que repetir por que a correta é correta.
2. Curto: 2 a 5 frases. Ele está no meio de um estudo.
3. Linguagem de aluno. Nada de "banda", "evidência", "prontidão", "mapa de
   domínio", "readiness" ou qualquer termo interno do sistema.
4. Não diga que ele aprendeu, dominou, está liberado ou pode avançar. Quem
   decide isso é o sistema, pelas respostas dele a questões.
5. Não prometa que alguém foi avisado ou vai responder depois.
6. Se o contexto não deixar claro onde ele errou, ensine o caminho certo sem
   inventar um erro que você não tem como saber que ele cometeu.
7. Termine com uma frase que o convide a tentar de novo - sem prometer que
   agora vai dar certo."""

_FORMATO = """FORMATO DA SUA RESPOSTA

Responda com um objeto JSON de um único campo:

{"explicacao": "o texto que o aluno vai ler"}

Nada fora do objeto. O texto do campo é para o aluno: sem JSON dentro dele,
sem markdown, sem título."""


def montar(*, contexto: str, estrategia: str) -> str:
    """O prompt completo, em ordem fixa.

    O contexto da questao vem rotulado como conteudo do sistema; nada que o
    aluno escreveu entra aqui - esta e a explicacao de um erro, nao a conversa.
    """
    como = _COMO.get(estrategia)
    partes = [_PAPEL, _REGRAS]
    if como:
        partes += [f"A ABORDAGEM DESTA VEZ: {estrategia}\n{como}"]
    partes += ["A QUESTÃO E O ERRO (do sistema, confiável)", contexto, _FORMATO]
    return "\n\n".join(partes)


__all__ = ["VERSION", "CAMPO_DA_RESPOSTA", "montar"]
