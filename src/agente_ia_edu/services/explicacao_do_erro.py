"""EXPLICAR O ERRO - porque errar precisa ensinar alguma coisa.

O QUE FOI MEDIDO, EM 2026-10-06
================================
No banco de desenvolvimento:

    SELECT count(*) FROM question_versions;                        595
    ... WHERE resolution_text IS NOT NULL AND btrim(...) <> '';       0

Nenhuma das 595 questoes do acervo tem resolucao curada. Entao todo aluno que
erra e abre "Entenda a resposta" le, hoje, exatamente isto:

    "Nao ha resolucao oficial passo a passo armazenada para esta questao.
     A geracao de resolucao por IA e uma fase futura e nao e usada aqui."

Duas frases sobre a divida tecnica do produto, para um adolescente que acabou
de errar. Ele nao aprende nada com elas, e fica sabendo de um problema que nao
e dele.

A ORDEM DAS FONTES
===================
    1. CURADA     resolucao escrita e revisada por gente
    2. IA         quando nao ha curada e ha provedor
    3. FALLBACK   quando a IA nao respondeu

Material curado NUNCA e trocado por IA. Se alguem escreveu a resolucao, ela
vale mais que qualquer geracao - e trocar as duas seria desperdicar o trabalho
humano e gastar uma chamada por nada. A IA entra onde hoje nao ha nada, que e
(medido) 100% do acervo.

A excecao e quando o aluno PEDE outro jeito: a curada ja foi mostrada e nao
bastou, entao relê-la nao e uma segunda tentativa. Dai em diante a estrategia
muda, e quem explica e a IA.

AS DUAS GARANTIAS ESTRUTURAIS
==============================
1. LER NAO E EVIDENCIA. Este servico nao recebe sessao de banco: ele nao tem
   como escrever em lugar nenhum. Mesmo mecanismo da `ConversaDoAssessor`, e
   ha teste lendo a assinatura do construtor e o proprio arquivo.

2. CONTEXTO POR LISTA FECHADA. So os campos de `CAMPOS_DO_CONTEXTO` chegam ao
   modelo. A diferenca para a conversa e deliberada: aqui o gabarito ENTRA,
   porque a questao ja foi corrigida e o aluno ja o viu na tela - esconde-lo
   do prompt nao protegeria nada e so impediria o modelo de ensinar o
   exercicio.

O QUE ELE NAO DECIDE
=====================
Se o aluno aprendeu, qual e o proximo passo, se ele pode avancar. Isso
continua sendo da evidencia e de `assessor_pedagogico`, deterministico. Uma
explicacao e intervencao, nao medida.
"""

from __future__ import annotations

from agente_ia_edu.assessor_prompts import (
    VERSAO_ATUAL_DA_EXPLICACAO,
    prompt_da_explicacao,
)
from agente_ia_edu.providers.contracts import TextGenerationProvider
from agente_ia_edu.providers.factory import build_text_provider
from agente_ia_edu.providers.models import TextGenerationRequest

FONTE_CURADA = "CURADA"
FONTE_IA = "IA"
FONTE_FALLBACK = "FALLBACK"

# AS ESTRATEGIAS, EM ORDEM.
#
# Nao e uma sequencia a aplicar cegamente: e a ordem em que elas sao
# OFERECIDAS quando o aluno diz que nao entendeu. O que a ordem garante e a
# unica coisa que importa aqui - que a segunda tentativa nunca seja a primeira
# repetida. Comecar pela ideia e terminar no pre-requisito tambem nao e
# arbitrario: quem nao entendeu de tres jeitos diferentes provavelmente nao
# esta travado nesta questao, e sim no que vinha antes dela.
ESTRATEGIA_CONCEITO = "CONCEITO"
ESTRATEGIA_PASSO_A_PASSO = "PASSO_A_PASSO"
ESTRATEGIA_EXEMPLO = "EXEMPLO"
ESTRATEGIA_ANALOGIA = "ANALOGIA"
ESTRATEGIA_DECOMPOSICAO = "DECOMPOSICAO"
ESTRATEGIA_PREREQUISITO = "PREREQUISITO"

ESTRATEGIAS = (
    ESTRATEGIA_CONCEITO,
    ESTRATEGIA_PASSO_A_PASSO,
    ESTRATEGIA_EXEMPLO,
    ESTRATEGIA_ANALOGIA,
    ESTRATEGIA_DECOMPOSICAO,
    ESTRATEGIA_PREREQUISITO,
)
ESTRATEGIA_INICIAL = ESTRATEGIAS[0]

# A LISTA FECHADA do que chega ao modelo. Acrescentar um campo e decisao
# consciente, com teste - exatamente como em `conversa_do_assessor`.
CAMPOS_DO_CONTEXTO = (
    ("conteudo", "Conteúdo da questão"),
    ("habilidade", "A micro-habilidade que está travando"),
    ("enunciado", "Enunciado da questão"),
    ("alternativa_escolhida", "O que o aluno marcou"),
    ("alternativa_correta", "A resposta correta (a questão JÁ foi corrigida)"),
)


def proxima_estrategia(anterior: str | None) -> str:
    """A proxima abordagem - que nunca e a anterior.

    Sem anterior, comeca pela primeira. Com uma desconhecida (uma versao
    antiga da tela, um valor adulterado), tambem: o certo e recomecar, nao
    quebrar. Depois da ultima, volta ao inicio - o aluno que percorreu as seis
    ja esta em territorio de ESCALATE, e quem decide isso nao e este modulo.
    """
    if anterior not in ESTRATEGIAS:
        return ESTRATEGIA_INICIAL
    i = ESTRATEGIAS.index(anterior)
    return ESTRATEGIAS[(i + 1) % len(ESTRATEGIAS)]


class ExplicacaoDoErro:
    """Uma questao errada, um jeito de explicar, um texto. Sem persistencia."""

    def __init__(self, *, provider: TextGenerationProvider | None = None) -> None:
        # NAO HA `session` NEM `session_factory` AQUI, DE PROPOSITO.
        # Ver o cabecalho: e isto que torna "ler nao e evidencia" uma
        # propriedade do codigo, e nao uma promessa.
        self._provider = provider

    async def explicar(self, *, resolucao_curada: str | None, contexto: dict,
                       estrategia: str = ESTRATEGIA_INICIAL) -> dict:
        estrategia = estrategia if estrategia in ESTRATEGIAS else ESTRATEGIA_INICIAL
        curada = (resolucao_curada or "").strip()

        # A curada so vale na PRIMEIRA entrada. Se o aluno pediu outro jeito,
        # ela ja foi lida e nao bastou.
        if curada and estrategia == ESTRATEGIA_INICIAL:
            return _resposta(curada, FONTE_CURADA, estrategia)

        artefato = prompt_da_explicacao()
        prompt = artefato.montar(contexto=_contexto_em_texto(contexto),
                                 estrategia=estrategia)
        try:
            provider = (self._provider if self._provider is not None
                        else build_text_provider())
            resultado = await provider.generate(TextGenerationRequest(prompt=prompt))
            texto = _texto_para_o_aluno(getattr(resultado, "text", ""),
                                        artefato.CAMPO_DA_RESPOSTA)
            if not texto:
                # Resposta em branco e falha, nao resposta: mostrar um espaco
                # vazio ao aluno seria mentir baixinho.
                raise _RespostaVazia()
            return _resposta(texto, FONTE_IA, estrategia,
                             provider=resultado.provider, model=resultado.model)
        except Exception:  # noqa: BLE001 - qualquer falha vira fallback honesto
            return _resposta(TEXTO_DE_FALLBACK, FONTE_FALLBACK, estrategia)


class _RespostaVazia(RuntimeError):
    """Interna: resposta em branco do provedor."""


# O FALLBACK NAO FINGE SER EXPLICACAO, E NAO FALA DE DIVIDA TECNICA.
#
# Ele admite que nao consegue explicar agora e devolve o aluno ao que existe e
# funciona sem IA nenhuma. O que ele nunca faz e contar ao aluno o que o
# produto ainda nao construiu: isso nao e problema dele, e nao ensina nada.
TEXTO_DE_FALLBACK = (
    "Não consegui montar a explicação desta questão agora. O que costuma "
    "destravar: releia o enunciado marcando o que ele dá e o que ele pede, "
    "confira se a sua conta responde exatamente o que foi pedido, e compare "
    "com a alternativa correta para achar onde os caminhos se separam. Se "
    "quiser, me pergunte sobre o ponto em que você travou."
)


def _resposta(texto: str, fonte: str, estrategia: str, *,
              provider: str | None = None, model: str | None = None) -> dict:
    return {
        "texto": texto,
        "fonte": fonte,
        "estrategia": estrategia,
        "fallback": fonte == FONTE_FALLBACK,
        "provider": provider,
        "model": model,
        "prompt_version": VERSAO_ATUAL_DA_EXPLICACAO,
    }


def _texto_para_o_aluno(bruto: str, campo: str | None) -> str:
    """Desembrulha o envelope JSON quando ha um - e segue em frente quando nao.

    Mesmo motivo de `conversa_do_assessor._texto_para_o_aluno`: o adaptador
    OpenAI deste repositorio fixa `response_format={"type":"json_object"}`, e o
    aluno ja viu um envelope cru na tela uma vez. Tolerante de proposito - um
    provedor que devolva texto puro continua funcionando.
    """
    texto = (bruto or "").strip()
    if not texto or not campo or not texto.startswith("{"):
        return texto
    import json

    try:
        dados = json.loads(texto)
    except ValueError:
        return texto
    if isinstance(dados, dict):
        valor = dados.get(campo)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
        # JSON sem o campo combinado: mostrar as chaves internas ao aluno seria
        # pior que mostrar nada, e isto vira falha -> fallback honesto.
        return ""
    return texto


def _contexto_em_texto(contexto: dict) -> str:
    """So os campos da lista fechada, e so os que existem de verdade."""
    c = contexto or {}
    linhas = [f"{rotulo}: {c[chave]}"
              for chave, rotulo in CAMPOS_DO_CONTEXTO
              if c.get(chave) not in (None, "")]
    return "\n".join(linhas) or "Sem contexto da questão."


__all__ = [
    "CAMPOS_DO_CONTEXTO",
    "ESTRATEGIAS",
    "ESTRATEGIA_INICIAL",
    "FONTE_CURADA",
    "FONTE_FALLBACK",
    "FONTE_IA",
    "TEXTO_DE_FALLBACK",
    "ExplicacaoDoErro",
    "proxima_estrategia",
]
