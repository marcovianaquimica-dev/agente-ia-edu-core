"""O QUE FAZ DE UM ITEM UM INSTRUMENTO DE SONDAGEM.

O PROBLEMA, MEDIDO NO NAVEGADOR EM 2026-10-07
==============================================
A sondagem de Estequiometria serviu tres itens, e um veio do banco generico,
com cinco alternativas. Existiam cinco itens curados, publicados, um por
micro-habilidade - e eles nao foram escolhidos.

A causa: `MicroDiagnosticService` pede `create_practice(content_code=...,
question_count=3)`. A selecao filtra por CONTEUDO e ordena por numero
oficial. A micro-habilidade nunca entra na pergunta, e os itens curados
competiam em pe de igualdade com 20 questoes comuns do conteudo.

Uma sondagem pedagogica nao pode depender de sorte.

O CONTRATO JA EXISTIA NO MODELO
================================
O publicador ja gravava, e ninguem lia:

    PedagogicalClassification.metadata_["purpose"] = "PROBE"

Medido no banco de desenvolvimento em 2026-10-07:

    purpose = PROBE              5 classificacoes   (as cinco curadas)
    reasoning_type = DIAGNOSTIC  39 classificacoes  (as 5 + 34 do banco
                                                     diagnostico gerado por IA)

Por isso o contrato e a FINALIDADE DECLARADA, e nao `reasoning_type`: este
ultimo promoveria 34 itens gerados a instrumento deliberado do Nucleo.

E nao e o prefixo `SOND-`. Codigo de questao serve a auditoria humana; usa-lo
como contrato esconderia semantica numa string e faria o segundo conteudo
custar o mesmo trabalho que o primeiro. Ha teste de que este arquivo nao
contem "SOND-", nem nome de micro-habilidade, nem nome de conteudo.

FINALIDADE NAO E FORMATO
=========================
`purpose` diz PARA QUE o item existe. Como se responde a ele e outra
dimensao, e este modulo nao a conhece: nao ha "multiple_choice" aqui, nem
contagem de alternativas. O candidato declara apenas `gabarito_definido`, e
quem o carrega sabe o que isso significa no formato dele.

A proxima evolucao do produto e uma sondagem conversacional. Se o contrato
exigisse alternativas, ela nasceria tendo de mentir sobre o proprio formato.

FALHA FECHADO
==============
Item com metadata parcial NAO vira instrumento por conveniencia. Rascunho,
classificacao aposentada, validacao sem nome, gabarito ausente, escopo de
outra escola - cada um desqualifica, e cada um diz por que. O motivo e
devolvido como texto porque e ele que torna a decisao auditavel (§16): sem
ele, "nao foi escolhido" e indistinguivel de "nao existe".

O QUE ESTE MODULO NAO FAZ
==========================
Nao decide QUAL micro-habilidade sondar - isso e do grafo e do motor
pedagogico. Ele recebe a habilidade ja escolhida e devolve o instrumento.
Nao mede, nao pontua, nao escreve: nao ha sessao de banco aqui, e ha teste
lendo o arquivo.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# A finalidade declarada. O valor e o que o publicador ja grava desde a
# publicacao dos cinco itens - nao foi inventado agora.
FINALIDADE_SONDAGEM = "PROBE"

# De onde veio o instrumento servido. Isto viaja ate o relatorio de selecao e
# e o que torna a escolha observavel sem ler log humano.
ORIGEM_CURADA = "CURATED"
ORIGEM_FALLBACK = "FALLBACK"

# As invariantes de publicacao e validacao, nomeadas. Sao os valores que o
# proprio modelo ja usa; estao aqui para que a regra apareca uma vez so.
_CLASSIFICACAO_VIVA = "ACTIVE"
_VALIDADO_POR_HUMANO = "HUMAN_VALIDATED"
_QUESTAO_PUBLICADA = "PUBLISHED"
_ESCOPO_ABERTO = "PUBLIC"
_ESCOPO_DA_ESCOLA = "SCHOOL"


@dataclass(frozen=True)
class Candidato:
    """Um item que PODE servir de instrumento, com o que basta para julgar.

    Nao e a questao inteira: e o recorte que o contrato precisa. Quem carrega
    do banco traduz; quem julga nao conhece tabela.

    `gabarito_definido` e deliberadamente um booleano, e nao uma contagem de
    alternativas - ver o cabecalho sobre finalidade e formato.
    """

    question_version_id: str
    conteudo: str | None
    habilidade: str | None
    finalidade: str | None
    lifecycle: str | None
    provenance: str | None
    validado_por: str | None
    status_da_questao: str | None
    visibilidade: str | None
    escola_id: str | None
    gabarito_definido: bool
    dependencia_visual: bool
    protegida: bool
    numero_oficial: int | None = None


@dataclass(frozen=True)
class Escolha:
    """O instrumento escolhido para uma habilidade, e por que ele."""

    question_version_id: str
    habilidade: str
    origem: str
    motivo: str


def compativel(candidato: Candidato, *, habilidade: str,
               conteudo: str) -> bool:
    """Este item mede a habilidade pedida, dentro do conteudo pedido?

    E o piso de qualquer escolha, curada ou nao: servir um item de outra
    micro-habilidade registra a lacuna errada, e o aluno e mandado estudar o
    que ele ja sabe.
    """
    return (bool(candidato.habilidade)
            and candidato.habilidade == habilidade
            and bool(candidato.conteudo)
            and candidato.conteudo == conteudo)


def inelegibilidade(candidato: Candidato, *, habilidade: str, conteudo: str,
                    escola_do_aluno: str | None = None,
                    finalidade: str = FINALIDADE_SONDAGEM) -> str | None:
    """Por que este item NAO e um instrumento curado - ou None se ele e.

    Devolve o motivo, e nao um booleano, porque e o motivo que torna a
    decisao auditavel: "nao havia curado" e "havia um, em rascunho" sao
    situacoes diferentes, e a segunda e um erro de publicacao que alguem
    precisa ver.

    A ordem das checagens vai do mais especifico ao mais geral, para que a
    primeira frase devolvida seja a mais informativa.

    `finalidade` E PARAMETRO, E O PADRAO E A SONDAGEM
    ==================================================
    Verificar a micro-habilidade depois do ensino exige este contrato
    inteiro - viva, validada por humano com nome, publicada, acessivel, com
    gabarito, sem imagem, nao protegida - e uma finalidade DIFERENTE.
    Escrever um segundo modulo com os mesmos oito critERIOS criaria duas
    definicoes de "instrumento utilizavel", e elas divergiriam no primeiro
    ajuste.

    E a finalidade precisa SEPARAR os dois: o item de sondagem pergunta
    NH3, e e a primeira coisa que o aluno ve; o de verificacao nao pode
    perguntar NH3, porque ele acabou de ver a resolucao do NH3 passo a
    passo. Intercambiaveis, o diagnostico mediria com um item escrito para
    depois do ensino, e a verificacao com o item que ele ja errou.
    """
    if not compativel(candidato, habilidade=habilidade, conteudo=conteudo):
        return (f"mede {candidato.habilidade!r} em {candidato.conteudo!r}, "
                f"e a sondagem pediu {habilidade!r} em {conteudo!r}")

    if (candidato.finalidade or "") != finalidade:
        return (f"sem finalidade {finalidade!r} declarada "
                f"(purpose={candidato.finalidade!r})")

    if (candidato.lifecycle or "") != _CLASSIFICACAO_VIVA:
        return f"classificacao nao esta viva (lifecycle={candidato.lifecycle!r})"

    if (candidato.provenance or "") != _VALIDADO_POR_HUMANO:
        return ("nao foi validado por humano "
                f"(provenance={candidato.provenance!r})")

    if not (candidato.validado_por or "").strip():
        # "Um humano validou" sem dizer qual nao e rastreavel - e o
        # CheckConstraint do banco ja exige o nome pelo mesmo motivo.
        return "validacao humana sem identidade registrada"

    if (candidato.status_da_questao or "") != _QUESTAO_PUBLICADA:
        return f"questao nao publicada (status={candidato.status_da_questao!r})"

    escopo = _escopo_inacessivel(candidato, escola_do_aluno)
    if escopo:
        return escopo

    if not candidato.gabarito_definido:
        # Sem gabarito a resposta do aluno nao pode ser conferida, e um probe
        # que nao confere nada nao mede nada.
        return "sem gabarito definido"

    if candidato.dependencia_visual:
        return "depende de imagem que o aluno pode nao receber"

    if candidato.protegida:
        return "item protegido, fora do uso em sondagem"

    return None


def _escopo_inacessivel(candidato: Candidato,
                        escola_do_aluno: str | None) -> str | None:
    """O aluno pode receber este item?

    PUBLIC serve a todos. SCHOOL serve a propria escola, e so quando o dono
    esta declarado: `SCHOOL` sem `school_id` e ambiguo, e ambiguidade nao
    vira acesso. Qualquer outro escopo - PRIVATE, ou um valor que este
    contrato nao conhece - nao serve.
    """
    visibilidade = (candidato.visibilidade or "").upper()
    if visibilidade == _ESCOPO_ABERTO:
        return None
    if visibilidade == _ESCOPO_DA_ESCOLA:
        if candidato.escola_id and candidato.escola_id == escola_do_aluno:
            return None
        return (f"escopo de escola que nao e a do aluno "
                f"(dono={candidato.escola_id!r})")
    return f"escopo nao acessivel na sondagem ({candidato.visibilidade!r})"


def curados(candidatos: Iterable[Candidato], *, habilidade: str,
            conteudo: str,
            escola_do_aluno: str | None = None,
            finalidade: str = FINALIDADE_SONDAGEM) -> list[Candidato]:
    """So os que cumprem o contrato inteiro, na ordem de desempate."""
    elegiveis = [c for c in candidatos
                 if inelegibilidade(c, habilidade=habilidade,
                                    conteudo=conteudo,
                                    escola_do_aluno=escola_do_aluno,
                                    finalidade=finalidade) is None]
    return sorted(elegiveis, key=_ordem)


def _ordem(c: Candidato) -> tuple:
    """A ordem de desempate, estavel e independente da ordem de entrada.

    Hoje ha um curado por habilidade e nenhum empate acontece. A politica
    existe assim mesmo porque "funciona porque so ha um" nao e politica: no
    dia em que houver dois, o aluno nao pode ver o instrumento mudar sozinho
    entre duas aberturas da mesma tela.

    Numero oficial primeiro - e a ordem em que o caderno foi publicado, a
    mesma que a pratica ja usa. Sem ele, o id, que e estavel e nao depende de
    como a lista chegou.
    """
    return (c.numero_oficial if c.numero_oficial is not None else 1_000_000,
            str(c.question_version_id))


def escolher(candidatos: Sequence[Candidato], *, habilidade: str,
             conteudo: str,
             escola_do_aluno: str | None = None,
             finalidade: str = FINALIDADE_SONDAGEM) -> Escolha | None:
    """O instrumento para esta habilidade, ou None quando nao ha nenhum.

    CURADO vence SEMPRE que houver um elegivel. So entao o generico entra, e
    mesmo assim precisa medir a mesma habilidade no mesmo conteudo.

    Devolver None e uma resposta: nao ha instrumento para sondar isto, e
    inventar um seria perguntar algo cujo resultado o sistema nao saberia
    interpretar. Quem chamou decide o que fazer.
    """
    lista = list(candidatos or ())

    elegiveis = curados(lista, habilidade=habilidade, conteudo=conteudo,
                        escola_do_aluno=escola_do_aluno,
                        finalidade=finalidade)
    if elegiveis:
        escolhido = elegiveis[0]
        # A FINALIDADE EXIGIDA ENTRA NO MOTIVO. Sem ela, dois itens
        # diferentes servidos ao mesmo aluno na mesma habilidade teriam a
        # mesma justificativa, e descobrir por que cada um foi escolhido
        # exigiria reexecutar a selecao.
        qual = ("de sondagem" if finalidade == FINALIDADE_SONDAGEM
                else f"de verificação ({finalidade})")
        return Escolha(
            question_version_id=escolhido.question_version_id,
            habilidade=habilidade,
            origem=ORIGEM_CURADA,
            motivo=("item curado do Núcleo para esta micro-habilidade: "
                    f"finalidade {qual} declarada, validado por "
                    f"{escolhido.validado_por}, publicado"))

    compativeis = sorted(
        (c for c in lista
         if compativel(c, habilidade=habilidade, conteudo=conteudo)
         and not _escopo_inacessivel(c, escola_do_aluno)
         and c.gabarito_definido
         and not c.dependencia_visual
         and not c.protegida
         and (c.status_da_questao or "") == _QUESTAO_PUBLICADA),
        key=_ordem)
    if not compativeis:
        return None

    escolhido = compativeis[0]
    return Escolha(
        question_version_id=escolhido.question_version_id,
        habilidade=habilidade,
        origem=ORIGEM_FALLBACK,
        motivo=("sem item curado elegível para esta micro-habilidade; "
                "questão do acervo classificada na mesma habilidade"))


__all__ = [
    "FINALIDADE_SONDAGEM",
    "ORIGEM_CURADA",
    "ORIGEM_FALLBACK",
    "Candidato",
    "Escolha",
    "compativel",
    "curados",
    "escolher",
    "inelegibilidade",
]
