"""A ESCADA DE APOIO - e, sobretudo, a RETIRADA dele.

O QUE FALTAVA
==============
O sistema sabia oferecer ajuda. Ele nao sabia tira-la. Cada ciclo recomecava
com o mesmo grau de assistencia, e quem tivesse acabado de resolver duas
etapas sozinho recebia, no ciclo seguinte, o mesmo andaime do primeiro dia.

Andaime que nao sai nao e ajuda: vira dependencia, e o aluno aprende a
resolver COM O SISTEMA em vez de aprender o conteudo.

OS QUATRO DEGRAUS
==================
    L3  INVESTIGACAO   uma micropergunta de cada vez - "vamos fazer juntos"
    L2  ENSINO         a explicacao, agora sabendo o que explicar
    L1  GUIADA         ele tenta, e a dica chega se ele pedir
    L0  AUTONOMO       uma questao sozinho

A ORDEM NAO E ARBITRARIA. A investigacao vem ANTES do ensino porque e mais
barata e porque ENSINA O SISTEMA tambem: despejar a resolucao inteira em quem
errou so a ultima etapa e repetir o que ele ja sabia, e em quem errou a
primeira e construir tres etapas sobre a que falhou. A micropergunta descobre
qual das duas e o caso antes de gastar a explicacao.

NADA E PERSISTIDO SO PARA ISTO
===============================
Os tres fatos que movem a escada ja sao gravados por outros motivos:

    investigacao_concluida   as etapas resolvidas (`guided_practice_items`)
    ja_ensinado              a leitura do material (`material_progress`)
    guiada_concluida         a pratica guiada (`guided_practice_items`)

Nenhuma tabela nova, nenhuma coluna nova. Se um dia houver, que seja porque
algo deixou de ser derivavel - nao por conveniencia.

A LINHA QUE A ESCADA NAO CRUZA
===============================
Ela decide QUANTO AJUDAR. Ela nunca decide QUANTO O ALUNO SABE. Subir nao da
nota e descer nao tira; so o degrau autonomo produz evidencia, e isso e uma
propriedade deste modulo, nao uma convencao de quem o usa.
"""

from __future__ import annotations

NIVEL_INVESTIGACAO = "INVESTIGACAO"
NIVEL_ENSINO = "ENSINO"
NIVEL_GUIADA = "GUIADA"
NIVEL_AUTONOMO = "AUTONOMO"

# Do mais apoiado ao menos apoiado. A ordem E o contrato: ha teste
# percorrendo o caminho inteiro e exigindo que o indice nunca volte.
NIVEIS = (NIVEL_INVESTIGACAO, NIVEL_ENSINO, NIVEL_GUIADA, NIVEL_AUTONOMO)

# O degrau em que o aluno resolve sozinho - e o unico cuja resposta o mapa de
# dominio pode ler. Uma constante, e nao uma comparacao espalhada pelo
# codigo, para que mudar isso seja uma decisao visivel.
_DEGRAU_QUE_COMPROVA = NIVEL_AUTONOMO

_ROTULOS = {
    NIVEL_INVESTIGACAO: "Vamos olhar isso por partes, uma pergunta de cada vez.",
    NIVEL_ENSINO: "Agora a explicação do ponto que travou.",
    NIVEL_GUIADA: "Tente esta — e peça uma dica se precisar.",
    NIVEL_AUTONOMO: "Agora uma sozinho, sem ajuda.",
}


def nivel_de_apoio(*, investigacao_concluida: bool, ja_ensinado: bool,
                   guiada_concluida: bool,
                   ha_investigacao: bool = True) -> str:
    """Em que degrau o aluno esta, pelo que ele ja percorreu.

    `ha_investigacao` e False quando nao existe cadeia curada para aquela
    lacuna. Ai o primeiro degrau e o ensino: inventar uma investigacao
    generica seria perguntar coisas cujo resultado o sistema nao saberia
    interpretar, e o aluno perceberia.
    """
    if ha_investigacao and not investigacao_concluida:
        return NIVEL_INVESTIGACAO
    if not ja_ensinado:
        return NIVEL_ENSINO
    if not guiada_concluida:
        return NIVEL_GUIADA
    return NIVEL_AUTONOMO


def produz_evidencia(nivel: str) -> bool:
    """So o degrau autonomo. Esta funcao e a invariante inteira."""
    return nivel == _DEGRAU_QUE_COMPROVA


def rotulo_do_nivel(nivel: str) -> str:
    """O que o aluno le. Nunca o codigo do degrau: "voce esta no L2" nao quer
    dizer nada para um adolescente, e ha teste disso."""
    return _ROTULOS.get(nivel, "Vamos continuar de onde você parou.")


__all__ = [
    "NIVEIS",
    "NIVEL_AUTONOMO",
    "NIVEL_ENSINO",
    "NIVEL_GUIADA",
    "NIVEL_INVESTIGACAO",
    "nivel_de_apoio",
    "produz_evidencia",
    "rotulo_do_nivel",
]
