"""CONVERSAR COM O ASSESSOR - uma duvida, dentro de uma intervencao.

O QUE ISTO E
=============
O aluno travou num ponto, o sistema ja sabe qual, e ele quer perguntar. A
conversa acontece DENTRO dessa intervencao: o contexto e a dificuldade real
dele, nao um chat de uso geral com um campo de texto.

O QUE ISTO NAO E
=================
Nao e um segundo cerebro pedagogico. Este modulo nao decide passo, nao diz que
o aluno aprendeu, nao libera atividade, nao escolhe intervencao. Quem decide
continua sendo `assessor_pedagogico`, deterministico.

AS DUAS GARANTIAS, E POR QUE SAO ESTRUTURAIS
=============================================
1. CONVERSA NAO E EVIDENCIA. Este servico nao recebe sessao de banco. Nao e
   uma convencao que alguem possa quebrar sem perceber: ele nao tem como
   escrever em lugar nenhum. Ha teste lendo a assinatura do construtor.

2. O GABARITO NAO ENTRA NO PROMPT. A protecao contra "me diga a letra" nao e
   uma instrucao que o modelo possa desobedecer - e a AUSENCIA DO DADO. O
   contexto e montado por lista fechada de campos (`CAMPOS_DO_CONTEXTO`), e
   alternativa, enunciado e resposta correta nao estao nela. Um modelo nao
   vaza o que nao recebeu.

ONDE A IA ENTRA, E COMO ELA SAI
================================
Pelo `TextGenerationProvider` que ja existe, via `build_text_provider()` -
com `ProviderRouter` e fallback entre provedores de graca. Nenhum nome de
fornecedor aparece aqui. O prompt e artefato versionado do sistema
(`assessor_prompts`), pelo mesmo motivo da classificacao: trocar o modelo nao
pode custar o que o sistema aprendeu a pedir.

Falhou - timeout, indisponibilidade, resposta vazia - a resposta volta
marcada `fallback: true`, com `provider: None`, e com um texto que NAO se
passa por resposta do modelo.
"""

from __future__ import annotations

from collections.abc import Sequence

from agente_ia_edu.assessor_prompts import VERSAO_ATUAL, prompt_da_conversa
from agente_ia_edu.services.concisao import extensao_para, pode_encerrar
from agente_ia_edu.providers.contracts import TextGenerationProvider
from agente_ia_edu.providers.factory import build_text_provider
from agente_ia_edu.providers.models import TextGenerationRequest

# Uma duvida de aluno cabe bem antes disto. O limite existe porque o texto vem
# do navegador e vai para dentro de um prompt: entrada sem teto e superficie de
# ataque, e um "livro" colado no campo nao e uma pergunta pedagogica.
LIMITE_DA_PERGUNTA = 600
LIMITE_DO_TURNO = 600

# A LISTA FECHADA. Esta e a protecao contra gabarito, e nao o texto do prompt:
# qualquer campo que nao esteja aqui simplesmente nao chega ao modelo, venha de
# onde vier. Acrescentar um campo e uma decisao consciente, com teste.
CAMPOS_DO_CONTEXTO = (
    ("objetivo", "Atividade que o aluno está tentando entregar"),
    ("conteudo", "Conteúdo em que ele está travado agora"),
    ("habilidade", "A micro-habilidade que está travando"),
    ("passo", "O passo pedagógico atual decidido pelo sistema"),
    ("ciclo", "Quantas vezes já intervimos neste conteúdo"),
    ("tendencia", "Como ele vem indo nas últimas tentativas"),
)


class PerguntaInvalida(ValueError):
    """422 - pergunta vazia ou longa demais."""


class ConversaDoAssessor:
    """Uma pergunta, um contexto, uma resposta. Sem estado, sem persistencia."""

    def __init__(self, *, provider: TextGenerationProvider | None = None) -> None:
        # NAO HA `session` NEM `session_factory` AQUI, DE PROPOSITO.
        # Ver o cabecalho do modulo: e isto que torna "conversa nao e
        # evidencia" uma propriedade do codigo, e nao uma promessa.
        self._provider = provider

    def _resolver_provider(self) -> TextGenerationProvider:
        return self._provider if self._provider is not None else build_text_provider()

    async def responder(self, *, pergunta: str, contexto: dict,
                        historico: Sequence[dict] = ()) -> dict:
        texto = (pergunta or "").strip()
        if not texto:
            raise PerguntaInvalida("pergunta vazia")
        if len(texto) > LIMITE_DA_PERGUNTA:
            raise PerguntaInvalida(
                f"pergunta com {len(texto)} caracteres; limite {LIMITE_DA_PERGUNTA}")

        # QUANTO FALAR E SE PODE FECHAR - decisao do produto, nao do modelo.
        #
        # Ate 2026-10-08 o prompt mandava "2 a 5 frases" para toda pergunta e
        # "termine oferecendo" ao fim de toda resposta. Os dois contradizem o
        # §4: o teto era universal, e a oferta era obrigatoria. A politica
        # agora e `services/concisao`, deterministica e com teste, e o prompt
        # so a transmite.
        #
        # `turnos_no_mesmo_ponto` sai do historico que o cliente ja envia: e
        # contagem do que aconteceu, nao inferencia sobre silencio ou tempo -
        # o §5 lista essas como o que NAO autoriza concluir dificuldade.
        extensao = extensao_para(
            pergunta=texto,
            turnos_no_mesmo_ponto=_turnos_do_aluno(historico) + 1)
        encerrar = pode_encerrar(pergunta=texto)

        artefato = prompt_da_conversa()
        parametros = dict(
            contexto=_contexto_em_texto(contexto),
            historico=_historico_em_texto(historico, artefato.TURNOS_DE_HISTORICO),
            pergunta=texto,
        )
        # As versoes antigas do prompt nao conhecem a politica. Mante-las
        # chamaveis e o que permite comparar uma conversa de ontem com uma de
        # hoje sem reescrever o registro.
        if _aceita_politica(artefato.montar):
            parametros.update(extensao=extensao, pode_encerrar=encerrar)
        prompt = artefato.montar(**parametros)

        try:
            resultado = await self._resolver_provider().generate(
                TextGenerationRequest(prompt=prompt))
            resposta = _texto_para_o_aluno(
                getattr(resultado, "text", ""),
                getattr(artefato, "CAMPO_DA_RESPOSTA", None))
            if not resposta:
                # Resposta vazia e falha, nao resposta. Mostrar um balao em
                # branco ao aluno seria o mesmo que mentir baixinho.
                raise _RespostaVazia()
            return {"reply": resposta, "provider": resultado.provider,
                    "model": resultado.model, "fallback": False,
                    "prompt_version": VERSAO_ATUAL}
        except Exception:  # noqa: BLE001 - qualquer falha vira fallback honesto
            return {"reply": TEXTO_DE_FALLBACK, "provider": None, "model": None,
                    "fallback": True, "prompt_version": VERSAO_ATUAL}


def _turnos_do_aluno(historico: Sequence[dict]) -> int:
    """Quantas vezes ele ja falou nesta conversa.

    A conversa acontece DENTRO de uma intervencao sobre um ponto - entao
    "turnos desta conversa" e "turnos sobre o mesmo ponto" sao a mesma coisa
    aqui. Se um dia a conversa atravessar pontos, esta funcao e que muda.
    """
    # A chave e `de`, e nao `quem`: e assim que `_historico_em_texto` le o
    # mesmo turno, logo abaixo. Escrevi `quem` primeiro e o contador teria
    # devolvido zero em silencio - a insistencia nunca ampliaria a resposta,
    # e nenhum teste de prompt notaria.
    return sum(1 for t in (historico or ())
               if str((t or {}).get("de") or "").lower() == "aluno")


def _aceita_politica(montar) -> bool:
    """O `montar` desta versao recebe extensao e autorizacao de fechamento?"""
    import inspect

    try:
        parametros = inspect.signature(montar).parameters
    except (TypeError, ValueError):  # pragma: no cover - callable exotico
        return False
    return "extensao" in parametros and "pode_encerrar" in parametros


class _RespostaVazia(RuntimeError):
    """Interna: resposta em branco do provedor."""


# A FRASE NAO FINGE SER DO MODELO.
#
# Dizer qualquer coisa "como se" tivesse vindo da IA seria exatamente o chat
# falso que este bloco proibiu. Ela admite a falha e devolve o aluno ao
# percurso que existe e funciona sem IA nenhuma.
TEXTO_DE_FALLBACK = (
    "Não consegui responder por conversa agora. Mas o seu estudo não precisa "
    "parar: podemos seguir pela explicação, ver o exemplo resolvido de novo "
    "ou praticar — é por aí que eu confiro se a ideia ficou firme."
)


def _texto_para_o_aluno(bruto: str, campo: str | None) -> str:
    """Desembrulha o envelope JSON quando ha um - e segue em frente quando nao.

    O ADAPTADOR DESTE REPOSITORIO PEDE JSON.
    `providers/adapters/openai.py` fixa `response_format={"type":"json_object"}`
    e um system message "Return only valid JSON", porque foi construido para a
    classificacao. Na primeira chamada real da conversa o aluno viu, na tela:

        {"resposta":"O índice é o número pequeno dentro da fórmula..."}

    O conteudo estava certo; o envelope nao era para ele.

    Nao tocamos no adaptador: ele e compartilhado, e mudar o transporte por
    causa de um consumidor espalharia o problema. O prompt (v2) passou a pedir
    o envelope com NOME CONHECIDO, e e ele que se abre aqui.

    Tolerante de proposito: um provedor que devolva texto puro - ou um JSON
    sem o campo esperado - continua funcionando, porque a alternativa seria
    cair no fallback por um detalhe de transporte.
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
    if c.get("avaliacao_aberta"):
        linhas.append("ATENÇÃO: há uma questão de avaliação aberta para este "
                      "aluno agora. Ensine o caminho, nunca a alternativa.")
    return "\n".join(linhas) or "Sem contexto pedagógico disponível."


def _historico_em_texto(historico: Sequence[dict], turnos: int) -> str:
    recentes = list(historico or [])[-turnos:]
    linhas = []
    for turno in recentes:
        quem = "Aluno" if (turno or {}).get("de") == "aluno" else "Assessor"
        texto = str((turno or {}).get("texto") or "").strip()[:LIMITE_DO_TURNO]
        if texto:
            linhas.append(f"{quem}: {texto}")
    return "\n".join(linhas)


__all__ = ["ConversaDoAssessor", "PerguntaInvalida", "LIMITE_DA_PERGUNTA",
           "CAMPOS_DO_CONTEXTO", "TEXTO_DE_FALLBACK"]
