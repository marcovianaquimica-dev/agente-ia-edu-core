"""OS QUATRO NIVEIS DE AUTONOMIA DO EDU - §14.

A ESCADA DE APOIO decide quanto AJUDAR o aluno. Esta decide quanto o EDU pode
FAZER sozinho. Sao perguntas diferentes, e misturar as duas foi o que o §14
veio impedir: ajudar muito e local e reversivel; remarcar o compromisso de
alguem nao e.

AS TRES NATUREZAS DE PLANO, que o §14 manda nao misturar
=========================================================
    INSTITUCIONAL        o que a escola decidiu - prazo, tarefa, nota
    PESSOAL_CONFIRMADO   o que o ALUNO confirmou - horario, carga, objetivo
    RECOMENDACAO         o que o Edu sugere, e que ninguem confirmou ainda

Por que importa: uma pratica que o Edu cria e recomendacao, e vira dado do
mapa de dominio com `origin = PRACTICE`. Se nascesse com a origem
institucional, acertar numa pratica propria pesaria como acertar na prova da
escola - e ninguem teria decidido isso.

OS QUATRO NIVEIS
=================
    1  intervencao pedagogica local            AUTOMATICO
    2  ajuste dentro de compromisso existente  TRANSPARENCIA
    3  mudanca em horario/carga/prioridade/
       objetivo CONFIRMADO                     CONFIRMACAO
    4  obrigacao ou decisao institucional      AUTORIDADE

FECHADO POR FALHA
==================
Acao que ninguem classificou cai no nivel 4. E a decisao mais importante
deste modulo: uma politica que respondesse "nivel 1" para o desconhecido
autorizaria automaticamente justamente o que ninguem pensou. O §14 existe
para o contrario, e ha teste exigindo que o desconhecido seja nivel 4.

O QUE ESTE MODULO NAO FAZ
==========================
Ele nao executa nada, nao grava nada e nao sabe quem e o aluno. Ele responde
UMA pergunta - "isso pode acontecer sozinho?" - e quem chama obedece.

E ele nao e a garantia de que o Edu nao altera compromisso escolar: essa
garantia e a AUSENCIA de caminho de escrita, e ha teste fazendo o aluno
percorrer o ciclo inteiro e conferindo que a tarefa da escola saiu de la com
o mesmo prazo, o mesmo alvo e os mesmos metadados.

O QUE FALTA, E ESTA REGISTRADO
===============================
O §14 diz que "a recusa de uma recomendacao devera ser respeitada". Recusa e
fato NAO DERIVAVEL - nenhuma leitura do historico distingue "ele recusou" de
"ele ainda nao clicou" - e por isso exigiria uma linha gravada. Hoje nao ha
planejador pessoal e nao ha insistencia: o proximo passo e UM convite na tela,
sem repeticao e sem modal, entao nao ha o que respeitar ainda. Quando houver
planejamento com sessoes marcadas, a recusa precisa de tabela propria.
"""

from __future__ import annotations

NIVEL_LOCAL = 1
NIVEL_AJUSTE = 2
NIVEL_COMPROMISSO = 3
NIVEL_INSTITUCIONAL = 4

# Do mais livre ao mais travado. A ordem E o contrato.
NIVEIS = (NIVEL_LOCAL, NIVEL_AJUSTE, NIVEL_COMPROMISSO, NIVEL_INSTITUCIONAL)

EXIGE_AUTOMATICO = "AUTOMATICO"
EXIGE_TRANSPARENCIA = "TRANSPARENCIA"
EXIGE_CONFIRMACAO = "CONFIRMACAO"
EXIGE_AUTORIDADE = "AUTORIDADE"

_EXIGENCIA = {
    NIVEL_LOCAL: EXIGE_AUTOMATICO,
    NIVEL_AJUSTE: EXIGE_TRANSPARENCIA,
    NIVEL_COMPROMISSO: EXIGE_CONFIRMACAO,
    NIVEL_INSTITUCIONAL: EXIGE_AUTORIDADE,
}

PLANO_INSTITUCIONAL = "INSTITUCIONAL"
PLANO_PESSOAL_CONFIRMADO = "PESSOAL_CONFIRMADO"
PLANO_RECOMENDACAO = "RECOMENDACAO_ADAPTATIVA"

PLANOS = (PLANO_INSTITUCIONAL, PLANO_PESSOAL_CONFIRMADO, PLANO_RECOMENDACAO)

# ---------------------------------------------------------------------------
# AS ACOES QUE O EDU SABE TOMAR - uma a uma, classificadas
# ---------------------------------------------------------------------------
#
# Esta lista e o inventario honesto do que o produto FAZ hoje, mais as
# fronteiras que ele nao atravessa. As de nivel 3 e 4 nao tem implementacao
# nenhuma por tras, e e exatamente por isso que estao aqui: a politica
# precisa de um nome para recusar antes de alguem escrever o caminho.

ACAO_INTERROMPER_PARA_INTERVIR = "INTERROMPER_PARA_INTERVIR"
ACAO_OFERECER_PROXIMO_PASSO = "OFERECER_PROXIMO_PASSO"
ACAO_ABRIR_INVESTIGACAO = "ABRIR_INVESTIGACAO"
ACAO_LIBERAR_NIVEL_DE_AJUDA = "LIBERAR_NIVEL_DE_AJUDA"
ACAO_EXPLICAR_O_ERRO = "EXPLICAR_O_ERRO"
ACAO_SUGERIR_RELATORIO_DE_APOIO = "SUGERIR_RELATORIO_DE_APOIO"
ACAO_MUDAR_FOCO_DA_SESSAO = "MUDAR_FOCO_DA_SESSAO"

ACAO_CRIAR_PRATICA_PROPRIA = "CRIAR_PRATICA_PROPRIA"
ACAO_GERAR_RELATORIO_DE_APOIO = "GERAR_RELATORIO_DE_APOIO"
ACAO_REORDENAR_PRIORIDADE_SUGERIDA = "REORDENAR_PRIORIDADE_SUGERIDA"

ACAO_MUDAR_OBJETIVO_CONFIRMADO = "MUDAR_OBJETIVO_CONFIRMADO"
ACAO_REMARCAR_SESSAO_CONFIRMADA = "REMARCAR_SESSAO_CONFIRMADA"
ACAO_MUDAR_CARGA_CONFIRMADA = "MUDAR_CARGA_CONFIRMADA"

ACAO_ALTERAR_PRAZO_DA_TAREFA = "ALTERAR_PRAZO_DA_TAREFA"
ACAO_ALTERAR_TAREFA_DA_ESCOLA = "ALTERAR_TAREFA_DA_ESCOLA"
ACAO_DISPENSAR_TAREFA_DA_ESCOLA = "DISPENSAR_TAREFA_DA_ESCOLA"
ACAO_CRIAR_TAREFA_DA_ESCOLA = "CRIAR_TAREFA_DA_ESCOLA"
ACAO_LANCAR_NOTA = "LANCAR_NOTA"

# (nivel, plano afetado, o que o aluno leria se precisasse saber)
ACOES: dict[str, tuple[int, str, str]] = {
    # --- 1: intervencao pedagogica local, automatica -----------------------
    ACAO_INTERROMPER_PARA_INTERVIR: (
        NIVEL_LOCAL, PLANO_RECOMENDACAO,
        "Parar na questao que deu errado para olhar o ponto que travou"),
    ACAO_OFERECER_PROXIMO_PASSO: (
        NIVEL_LOCAL, PLANO_RECOMENDACAO,
        "Sugerir o proximo passo do estudo"),
    ACAO_ABRIR_INVESTIGACAO: (
        NIVEL_LOCAL, PLANO_RECOMENDACAO,
        "Fazer perguntas curtas para achar onde esta a duvida"),
    ACAO_LIBERAR_NIVEL_DE_AJUDA: (
        NIVEL_LOCAL, PLANO_RECOMENDACAO,
        "Dar a proxima dica quando voce pedir"),
    ACAO_EXPLICAR_O_ERRO: (
        NIVEL_LOCAL, PLANO_RECOMENDACAO,
        "Explicar por que a resposta escolhida nao funciona"),
    ACAO_SUGERIR_RELATORIO_DE_APOIO: (
        NIVEL_LOCAL, PLANO_RECOMENDACAO,
        "Sugerir que voce gere o relatorio de apoio"),
    ACAO_MUDAR_FOCO_DA_SESSAO: (
        NIVEL_LOCAL, PLANO_RECOMENDACAO,
        "Trocar o assunto da conversa quando voce pergunta de outro"),

    # --- 2: ajuste dentro do que existe, com transparencia -----------------
    ACAO_CRIAR_PRATICA_PROPRIA: (
        NIVEL_AJUSTE, PLANO_RECOMENDACAO,
        "Montar uma pratica sua, que nao e tarefa da escola nem vale nota"),
    ACAO_GERAR_RELATORIO_DE_APOIO: (
        NIVEL_AJUSTE, PLANO_RECOMENDACAO,
        "Montar o relatorio de apoio com o que ja ficou registrado"),
    ACAO_REORDENAR_PRIORIDADE_SUGERIDA: (
        NIVEL_AJUSTE, PLANO_RECOMENDACAO,
        "Mudar a ordem do que foi sugerido, sem mexer no que voce confirmou"),

    # --- 3: compromisso pessoal confirmado, exige confirmacao --------------
    ACAO_MUDAR_OBJETIVO_CONFIRMADO: (
        NIVEL_COMPROMISSO, PLANO_PESSOAL_CONFIRMADO,
        "Mudar um objetivo que voce ja confirmou"),
    ACAO_REMARCAR_SESSAO_CONFIRMADA: (
        NIVEL_COMPROMISSO, PLANO_PESSOAL_CONFIRMADO,
        "Remarcar um estudo que voce ja marcou"),
    ACAO_MUDAR_CARGA_CONFIRMADA: (
        NIVEL_COMPROMISSO, PLANO_PESSOAL_CONFIRMADO,
        "Mudar quanto voce combinou estudar"),

    # --- 4: institucional, exige autoridade --------------------------------
    ACAO_ALTERAR_PRAZO_DA_TAREFA: (
        NIVEL_INSTITUCIONAL, PLANO_INSTITUCIONAL,
        "Mudar o prazo de uma tarefa da escola"),
    ACAO_ALTERAR_TAREFA_DA_ESCOLA: (
        NIVEL_INSTITUCIONAL, PLANO_INSTITUCIONAL,
        "Mudar uma tarefa que a escola passou"),
    ACAO_DISPENSAR_TAREFA_DA_ESCOLA: (
        NIVEL_INSTITUCIONAL, PLANO_INSTITUCIONAL,
        "Dispensar voce de uma tarefa da escola"),
    ACAO_CRIAR_TAREFA_DA_ESCOLA: (
        NIVEL_INSTITUCIONAL, PLANO_INSTITUCIONAL,
        "Criar uma tarefa em nome da escola"),
    ACAO_LANCAR_NOTA: (
        NIVEL_INSTITUCIONAL, PLANO_INSTITUCIONAL,
        "Lancar uma nota"),
}


def nivel_da_acao(acao: str | None) -> int:
    """O nivel daquela acao. DESCONHECIDA e nivel 4 - fechado por falha."""
    chave = (acao or "").strip()
    linha = ACOES.get(chave)
    return linha[0] if linha else NIVEL_INSTITUCIONAL


def plano_afetado(acao: str | None) -> str:
    """Que natureza de plano a acao toca. Desconhecida: institucional.

    Pelo mesmo motivo do nivel: supor que o desconhecido so mexe em sugestao
    e supor a favor de agir.
    """
    chave = (acao or "").strip()
    linha = ACOES.get(chave)
    return linha[1] if linha else PLANO_INSTITUCIONAL


def descricao_da_acao(acao: str | None) -> str:
    """O que a acao e, em palavras de gente - nunca o codigo dela.

    A transparencia do nivel 2 depende disto: "CRIAR_PRATICA_PROPRIA" nao
    informa ninguem.
    """
    chave = (acao or "").strip()
    linha = ACOES.get(chave)
    if linha:
        return linha[2]
    return "Uma acao que ainda nao foi classificada, e que por isso precisa de permissao"


def exigencia_do_nivel(nivel: int) -> str:
    return _EXIGENCIA.get(nivel, EXIGE_AUTORIDADE)


def nivel_age_sozinho(nivel: int) -> bool:
    """So o 1. Esta funcao e a invariante do §14 inteira."""
    return nivel == NIVEL_LOCAL


def exige(acao: str | None) -> str:
    return exigencia_do_nivel(nivel_da_acao(acao))


def pode_agir_sozinho(acao: str | None) -> bool:
    return nivel_age_sozinho(nivel_da_acao(acao))


def exige_transparencia(acao: str | None) -> bool:
    """Do nivel 2 para cima.

    Nao e so o 2: confirmar sem saber o que se confirma nao e confirmacao, e
    autoridade exercida em silencio e a "alteracao silenciosa" que o §14
    proibe por nome.
    """
    return nivel_da_acao(acao) >= NIVEL_AJUSTE


def exige_confirmacao(acao: str | None) -> bool:
    return nivel_da_acao(acao) >= NIVEL_COMPROMISSO


def exige_autoridade(acao: str | None) -> bool:
    return nivel_da_acao(acao) >= NIVEL_INSTITUCIONAL


def acoes_do_nivel(nivel: int) -> tuple[str, ...]:
    return tuple(a for a in ACOES if nivel_da_acao(a) == nivel)


def transparencia(acao: str | None) -> dict:
    """O bloco que uma resposta carrega quando o Edu agiu.

    Existe para que a transparencia seja CONTRATO, e nao comentario: quem
    consome a resposta sabe em que nivel aquilo aconteceu e o que aquilo era.
    """
    nivel = nivel_da_acao(acao)
    return {
        "acao": (acao or "").strip(),
        "nivel": nivel,
        "exige": exigencia_do_nivel(nivel),
        "plano": plano_afetado(acao),
        "descricao": descricao_da_acao(acao),
    }


__all__ = [
    "ACOES",
    "EXIGE_AUTOMATICO",
    "EXIGE_AUTORIDADE",
    "EXIGE_CONFIRMACAO",
    "EXIGE_TRANSPARENCIA",
    "NIVEIS",
    "NIVEL_AJUSTE",
    "NIVEL_COMPROMISSO",
    "NIVEL_INSTITUCIONAL",
    "NIVEL_LOCAL",
    "PLANOS",
    "PLANO_INSTITUCIONAL",
    "PLANO_PESSOAL_CONFIRMADO",
    "PLANO_RECOMENDACAO",
    "acoes_do_nivel",
    "descricao_da_acao",
    "exige",
    "exige_autoridade",
    "exige_confirmacao",
    "exige_transparencia",
    "exigencia_do_nivel",
    "nivel_age_sozinho",
    "nivel_da_acao",
    "plano_afetado",
    "pode_agir_sozinho",
    "transparencia",
]

# Os nomes das acoes tambem sao exportados, para que quem chama use a
# constante e nao um literal - literal errado silenciosamente cai no nivel 4,
# que e seguro, mas recusaria uma acao legitima sem explicar por que.
__all__ += [n for n in dir() if n.startswith("ACAO_")]
