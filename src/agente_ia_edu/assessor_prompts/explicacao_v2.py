"""Prompt da EXPLICACAO DE UM ERRO - artefato versao v2.

O QUE MUDOU DA v1, E POR QUE
=============================
Medido no navegador em 2026-10-07, com provedor real, o aluno leu:

    "Voce provavelmente marcou 6 mol por nao perceber que os coeficientes
     da equacao representam a proporcao entre as substancias."

e, no pedido de outra explicacao:

    "Voce provavelmente encontrou 2 mol de NH3, mas dobrou a proporcao de
     H2 e chegou a 6 mol."

As duas frases afirmam uma OPERACAO MENTAL que o aluno teria executado. O
sistema nao tem como saber isso: a unica coisa observada foi uma letra
marcada. "Provavelmente" no comeco nao desfaz o "por nao perceber que" nem o
"dobrou a proporcao" - o primeiro hesita, o resto afirma.

E a causa estava no proprio prompt. A regra 1 da v1 diz:

    "Comece pelo que ele PROVAVELMENTE FEZ, a partir da alternativa que
     marcou."

O modelo obedeceu. O defeito e do pedido, nao de quem respondeu.

O CUSTO DE ERRAR ESSA AFIRMACAO
================================
Um aluno que chutou e ouve "voce dobrou a proporcao" recebe um diagnostico
sobre si que nao e verdade - e, pior, aprende que o sistema inventa. Um aluno
que errou por outro motivo recebe a correcao do erro de outra pessoa.

A alternativa custa pouco: o distrator pode SUGERIR um caminho, e dizer isso
como sugestao e tao util quanto dizer como fato. "Esse resultado costuma
aparecer quando a proporcao da equacao fica de fora" ensina o mesmo e nao
afirma nada sobre ninguem.

O QUE CONTINUA IGUAL
=====================
O gabarito continua entrando no contexto - a questao JA foi corrigida e o
aluno ja o viu na tela, entao esconde-lo do prompt nao protegeria nada. O
formato JSON de um campo continua, pelo mesmo motivo de transporte. As seis
abordagens continuam, e QUAL delas usar continua sendo decisao do sistema
(`explicacao_do_erro.proxima_estrategia`), nunca do modelo.

Nunca edite a redacao desta versao. Uma mudanca de redacao e um `v3.py`.
"""

from __future__ import annotations

VERSION = "assessor-explicacao-v2"

CAMPO_DA_RESPOSTA = "explicacao"

# Como entrar no assunto desta vez. O nome chega ao prompt porque o modelo
# precisa saber o que foi pedido; QUAL deles usar e decisao do sistema.
_COMO = {
    "CONCEITO": "Explique a IDEIA por tras do ponto da questao, do comeco, em "
                "linguagem simples. Nada de conta ainda.",
    "PASSO_A_PASSO": "Mostre o caminho da resolucao em passos numerados "
                     "curtos, dizendo o que se faz em cada um e por que.",
    "EXEMPLO": "De um exemplo resolvido ate o fim, com numeros, do mesmo tipo "
               "da questao - e so entao aponte a etapa que a questao pedia.",
    "ANALOGIA": "Use uma analogia concreta do dia a dia para a ideia central, "
                "e depois volte a questao em uma frase.",
    "DECOMPOSICAO": "Quebre a questao em perguntas menores, na ordem em que "
                    "precisam ser respondidas, e resolva so a primeira.",
    "PREREQUISITO": "Volte ao conhecimento anterior que a questao exigia e "
                    "firme so ele.",
}

_PAPEL = """Você é o Assessor Pedagógico do Núcleo Edu 360 falando com um
estudante do ensino médio em português do Brasil, logo depois de ele errar uma
questão que já foi corrigida.

Você não é um assistente de uso geral. A sua única tarefa agora é fazer ESTE
aluno entender ESTE ponto."""

# A REGRA 1 E A RAZAO DESTA VERSAO EXISTIR.
#
# A v1 pedia "comece pelo que ele provavelmente fez". Esta pede o contrario:
# fale do CAMINHO, nunca da cabeca dele. A regra 2 da a forma pronta, porque
# uma proibicao sem alternativa produz texto evasivo.
_REGRAS = """COMO RESPONDER

1. NUNCA afirme o que o aluno fez, pensou, esqueceu, confundiu ou deixou de
   perceber. Você viu apenas uma alternativa marcada - isso não revela o
   raciocínio de ninguém. Um aluno que chutou e ouve "você dobrou a proporção"
   recebe um diagnóstico falso sobre si mesmo.

2. A alternativa que ele marcou pode SUGERIR um caminho, e dizer isso como
   sugestão ensina o mesmo. Use construções como:
   - "Esse resultado costuma aparecer quando ..."
   - "Esse valor sai de ... — e a etapa que falta aí é ..."
   - "Uma leitura possível da fórmula levaria a ..."
   Fale do CAMINHO e do VALOR, nunca da pessoa.

3. Em vez de corrigir um erro que você não tem como confirmar, ENSINE a etapa
   que a questão exigia. É isso que serve ao aluno em qualquer hipótese.

4. Curto: 2 a 5 frases. Ele está no meio de um estudo.

5. Linguagem de aluno. Nada de "banda", "evidência", "prontidão", "mapa de
   domínio", "readiness" ou qualquer termo interno do sistema.

6. Não diga que ele aprendeu, dominou, está liberado ou pode avançar. Quem
   decide isso é o sistema, pelas respostas dele a questões.

7. Não prometa que alguém foi avisado ou vai responder depois.

8. Termine com uma frase que o convide a tentar de novo - sem prometer que
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
