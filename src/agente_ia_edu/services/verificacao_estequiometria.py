"""OS ITENS DE VERIFICACAO DE ESTEQUIOMETRIA - L0, e conferiveis.

POR QUE ELES PRECISARAM EXISTIR
================================
Medido no banco de desenvolvimento em 2026-10-08: o conteudo
CHEMISTRY-PHYSICAL-STOICHIOMETRY tem 25 classificacoes ativas, e exatamente
UMA e de `MASSA_MOLAR` - o proprio item da sondagem. A jornada da
micro-habilidade terminava assim:

    sondagem (diagnostico)   NH3  ->  17 g/mol     ele errou aqui
    investigacao (L3)        NH3  ->  17 g/mol     a mesma substancia
    ensino (L2)              NH3  ->  17 g/mol     a mesma
    pratica guiada (L1)      CO2  ->  44 g/mol     com quatro dicas
    autonomo (L0)            -                     nao havia item

A pratica pelo banco seleciona por CONTEUDO, nao por micro-habilidade: para
Estequiometria ela devolve questoes de proporcao e de relacao massa-mol. A
habilidade ensinada nunca era verificada sozinha, e pedir "a de massa molar
do banco" reserviria o item que ele ja tinha errado no diagnostico.

AS TRES SUBSTANCIAS, E POR QUE ESTAS
=====================================
    Na2O       indice no PRIMEIRO elemento
    SO3        indice no SEGUNDO, com valor diferente do que ele viu
    Mg(OH)2    indice FORA do parentese, multiplicando dois elementos

NH3 e CO2 tem o indice no segundo elemento. Um conjunto de verificacao que
repetisse so essa forma mediria o reconhecimento do padrao - "o numerinho
depois do segundo simbolo" - e nao a regra. Variar a POSICAO do indice e o
que separa quem aprendeu de quem decorou o NH3, e ha teste exigindo essa
variacao.

Ca(OH)2 nao entrou mesmo sendo a forma com parentese que a investigacao de
LEITURA_DE_FORMULA usa: ele apareceria em duas etapas da mesma jornada.
Mg(OH)2 tem a mesma estrutura e outro metal.

TODO DISTRATOR E UM ERRO NOMEADO, E TODO NUMERO E REFEITO POR CONTA
====================================================================
`erros` diz o que marcar cada alternativa significa, e `conferir()` refaz
cada um dos quatro numeros a partir de `massa_molar` e `contribuicoes`. Um
literal errado aqui nao derrubaria nada em producao: ele ensinaria quimica
errada a quem confiou no sistema, e registraria uma lacuna que nao existe.

AS MASSAS ATOMICAS VEM NO ENUNCIADO
====================================
Pelo mesmo motivo do item da sondagem: se o aluno precisasse lembra-las,
errar poderia significar "nao sei aplicar o indice" ou "nao lembro o valor
do Na", e a evidencia nao saberia qual. O item mede UMA coisa.

SEM IA
=======
Nada aqui chama provedor. Ha teste lendo o arquivo - a verificacao e o
degrau que produz evidencia, e um timeout nao pode decidir se o aluno
avanca.
"""

from __future__ import annotations

from agente_ia_edu.services.grafo_estequiometria import CONTEUDO, MASSA_MOLAR
from agente_ia_edu.services.verificacao import ItemDeVerificacao, itens_para

# O conteudo a que estes itens pertencem - reexportado para quem seleciona.
CONTENT_CODE = CONTEUDO


ITENS: tuple[ItemDeVerificacao, ...] = (
    ItemDeVerificacao(
        key="VER-EST-MASSA-MOLAR-1",
        habilidade=MASSA_MOLAR,
        fonte="Na2O",
        enunciado="Considere Na = 23 g/mol e O = 16 g/mol. Qual é a massa "
                  "molar do Na₂O?",
        alternativas={"A": "39 g/mol", "B": "46 g/mol", "C": "62 g/mol",
                      "D": "78 g/mol"},
        correta="C",
        erros={
            "A": "somou as massas atômicas sem aplicar o índice (23 + 16)",
            "B": "contou os dois sódios e esqueceu o oxigênio (2 × 23)",
            "D": "aplicou o índice do sódio também ao oxigênio "
                 "(2 × 23 + 2 × 16)",
        },
        conferencia="2 × 23 + 16"),

    ItemDeVerificacao(
        key="VER-EST-MASSA-MOLAR-2",
        habilidade=MASSA_MOLAR,
        fonte="SO3",
        enunciado="Considere S = 32 g/mol e O = 16 g/mol. Qual é a massa "
                  "molar do SO₃?",
        alternativas={"A": "48 g/mol", "B": "64 g/mol", "C": "80 g/mol",
                      "D": "144 g/mol"},
        correta="C",
        erros={
            "A": "somou as massas atômicas sem aplicar o índice (32 + 16)",
            # "so os tres oxigenios" NAO cabe aqui: 3 x 16 = 48, o mesmo
            # numero do indice esquecido. Dois erros diferentes apontando
            # para a mesma alternativa fariam a resposta do aluno ambigua -
            # e `conferir()` me mostrou isso ao recusar o 64 que eu havia
            # escrito com a frase errada.
            "B": "contou dois oxigênios em vez de três (32 + 2 × 16)",
            "D": "aplicou o índice do oxigênio também ao enxofre "
                 "(3 × 32 + 3 × 16)",
        },
        conferencia="32 + 3 × 16"),

    ItemDeVerificacao(
        key="VER-EST-MASSA-MOLAR-3",
        habilidade=MASSA_MOLAR,
        fonte="Mg(OH)2",
        enunciado="Considere Mg = 24 g/mol, O = 16 g/mol e H = 1 g/mol. "
                  "Qual é a massa molar do Mg(OH)₂?",
        alternativas={"A": "41 g/mol", "B": "58 g/mol", "C": "82 g/mol",
                      "D": "57 g/mol"},
        correta="B",
        erros={
            "A": "somou as massas atômicas sem aplicar o índice "
                 "(24 + 16 + 1)",
            "C": "aplicou o índice do parêntese ao magnésio também "
                 "(2 × 24 + 2 × 16 + 2 × 1)",
            "D": "aplicou o índice só ao oxigênio, deixando o hidrogênio "
                 "de fora da multiplicação (24 + 2 × 16 + 1)",
        },
        conferencia="24 + 2 × (16 + 1)"),
)


def itens_de(content_code: str, habilidade: str
             ) -> tuple[ItemDeVerificacao, ...]:
    """Os itens de verificacao daquele conteudo e habilidade.

    Conteudo que nao e este devolve vazio - e nao os itens de
    Estequiometria por descuido.
    """
    if content_code != CONTENT_CODE:
        return ()
    return itens_para(ITENS, habilidade=habilidade)


def conferir() -> list[str]:
    """Refaz TODOS os numeros dos itens. Devolve os problemas, ou vazio.

    Nao so o gabarito: cada distrator tambem, porque um distrator que nao e
    o erro que ele diz ser faz o sistema interpretar a resposta errada. Se
    "esqueceu o indice" apontasse para um numero que ninguem obteria
    esquecendo o indice, marcar aquela alternativa nao informaria nada - e o
    item pareceria estar funcionando.
    """
    from agente_ia_edu.services.massa_molar import contribuicoes, massa_molar

    problemas: list[str] = []

    def grafia(valor: float) -> str:
        if float(valor).is_integer():
            return str(int(valor))
        return f"{valor:g}".replace(".", ",")

    def texto(item: ItemDeVerificacao, letra: str) -> str:
        return item.alternativas[letra]

    def exige(item: ItemDeVerificacao, letra: str, esperado: float,
              porque: str) -> None:
        valor = texto(item, letra)
        if not valor.startswith(grafia(esperado)):
            problemas.append(
                f"{item.key} alternativa {letra}: {valor!r} nao bate com "
                f"{grafia(esperado)!r} ({porque})")

    por_chave = {i.key: i for i in ITENS}

    for item in ITENS:
        if not item.fonte:
            problemas.append(f"{item.key}: sem formula para conferir")
            continue
        partes = contribuicoes(item.fonte)
        # A correta e sempre a massa molar calculada.
        exige(item, item.correta, massa_molar(item.fonte), "a massa molar")
        # O erro do indice esquecido: somar as massas ATOMICAS, uma vez cada.
        sem_indice = sum(massa_atomica for _, _, massa_atomica, _ in partes)
        letra_sem_indice = next(
            (letra for letra, frase in item.erros.items()
             if "sem aplicar o índice" in frase), None)
        if letra_sem_indice is None:
            problemas.append(
                f"{item.key}: nenhum distrator declara o indice esquecido - "
                f"o erro que a investigacao existe para encontrar")
        else:
            exige(item, letra_sem_indice, sem_indice, "indice esquecido")

    # O INDICE ESPALHADO PARA ONDE NAO VAI, item por item. Cada conta e
    # escrita aqui e refeita das contribuicoes - nunca copiada do enunciado.
    na2o = por_chave["VER-EST-MASSA-MOLAR-1"]
    massas_na2o = {e: m for e, _, m, _ in contribuicoes("Na2O")}
    exige(na2o, "B", 2 * massas_na2o["Na"], "so os dois sodios")
    exige(na2o, "D", 2 * massas_na2o["Na"] + 2 * massas_na2o["O"],
          "indice aplicado ao oxigenio tambem")

    so3 = por_chave["VER-EST-MASSA-MOLAR-2"]
    massas_so3 = {e: m for e, _, m, _ in contribuicoes("SO3")}
    exige(so3, "B", massas_so3["S"] + 2 * massas_so3["O"],
          "dois oxigenios em vez de tres")
    exige(so3, "D", 3 * massas_so3["S"] + 3 * massas_so3["O"],
          "indice aplicado ao enxofre tambem")

    mgoh2 = por_chave["VER-EST-MASSA-MOLAR-3"]
    massas_mg = {e: m for e, _, m, _ in contribuicoes("Mg(OH)2")}
    exige(mgoh2, "C",
          2 * massas_mg["Mg"] + 2 * massas_mg["O"] + 2 * massas_mg["H"],
          "indice do parentese aplicado ao magnesio tambem")
    exige(mgoh2, "D",
          massas_mg["Mg"] + 2 * massas_mg["O"] + massas_mg["H"],
          "indice aplicado so ao oxigenio")

    return problemas


__all__ = ["CONTENT_CODE", "ITENS", "conferir", "itens_de"]
