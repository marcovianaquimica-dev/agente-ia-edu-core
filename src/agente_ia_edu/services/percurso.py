"""OS TRES PERCURSOS DO §9 - e a volta ao ponto anterior.

O §9 diz que os tres sao "contextos do mesmo sistema, nao experiencias
desconectadas", e e essa frase que este modulo implementa:

    PLANEJADO     compromissos e prioridades - o que a escola e o aluno marcaram
    EXPLORACAO    curiosidade espontanea, fora do trilho
    APOIO         intervencao breve num pre-requisito

POR QUE O PERCURSO PRECISA DE UM NOME
======================================
Sem ele, uma pergunta sobre outro assunto chegava ao Assessor como se fosse
sobre o conteudo travado - e a resposta saia sobre a coisa errada. E, pior, a
unica regra que o prompt tinha para pergunta fora do trilho era "traga de
volta ao ponto em uma frase": lida ao pe da letra com uma curiosidade de outra
disciplina, isso e uma recusa. O §9 manda o contrario - "responder a
curiosidade espontanea" e "permitir exploracao introdutoria de conteudos
avancados".

O QUE EXPLORAR NAO FAZ
=======================
Nao move passo, nao grava evidencia, nao altera compromisso e nao apaga o
estado da atividade. Isso nao e garantido por este modulo - e garantido por
ele nao escrever nada em lugar nenhum, e ha teste fazendo o aluno explorar no
meio de uma atividade e conferindo posicao, respostas, proximo passo e as
duas tabelas de evidencia.

A VOLTA E NOMEADA
==================
"Voltar ao percurso" nao diz para onde. O §9 pede retorno ao ponto anterior
SEM PERDA DE CONTEXTO, e contexto inclui saber o que se esta retomando -
"Voltar para Atividade de Estequiometria" e a mesma acao com a informacao que
faltava.

E SEM NADA PARA VOLTAR, NAO SE INVENTA DESTINO: `retomada` devolve None, e a
tela nao oferece um botao que nao leva a lugar nenhum.
"""

from __future__ import annotations

PERCURSO_PLANEJADO = "PLANEJADO"
PERCURSO_EXPLORACAO = "EXPLORACAO"
PERCURSO_APOIO = "APOIO"

PERCURSOS = (PERCURSO_PLANEJADO, PERCURSO_EXPLORACAO, PERCURSO_APOIO)

# Como cada um se chama para o aluno, se um dia a tela precisar dizer.
_ROTULOS = {
    PERCURSO_PLANEJADO: "No seu plano de estudos",
    PERCURSO_EXPLORACAO: "Curiosidade sua",
    PERCURSO_APOIO: "Apoio no que travou",
}


def classificar(*, assunto: str | None, em_intervencao: bool) -> str:
    """Em que percurso esta interacao acontece.

    EXPLORACAO ganha de APOIO de proposito: o §9 permite sair do trilho mesmo
    no meio do apoio, e tratar a curiosidade como apoio faria a resposta sair
    sobre o conteudo travado em vez de sobre o que ele perguntou.

    Assunto em branco nao e exploracao - um campo vazio nao e curiosidade.
    """
    if (assunto or "").strip():
        return PERCURSO_EXPLORACAO
    return PERCURSO_APOIO if em_intervencao else PERCURSO_PLANEJADO


def rotulo_do_percurso(percurso: str) -> str:
    return _ROTULOS.get(percurso, "")


def retomada(*, conteudo: str | None, titulo_da_atividade: str | None,
             destino: str | None) -> dict | None:
    """Para onde voltar, e com que nome.

    O titulo da atividade ganha do nome do conteudo quando existe: e assim
    que o aluno chama a coisa ("aquela atividade de estequiometria"), e nao
    pelo no curricular.

    Sem destino, devolve None - oferecer uma volta sem lugar seria pior que
    nao oferecer.
    """
    if not (destino or "").strip():
        return None
    nome = (titulo_da_atividade or "").strip() or (conteudo or "").strip()
    if not nome:
        return None
    return {"rotulo": f"Voltar para {nome}", "destino": destino.strip(),
            "nome": nome}


__all__ = [
    "PERCURSOS",
    "PERCURSO_APOIO",
    "PERCURSO_EXPLORACAO",
    "PERCURSO_PLANEJADO",
    "classificar",
    "retomada",
    "rotulo_do_percurso",
]
