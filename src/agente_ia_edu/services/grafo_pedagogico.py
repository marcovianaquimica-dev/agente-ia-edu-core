"""O GRAFO PEDAGOGICO - o que compoe um conteudo, e o que vem antes.

O QUE FALTAVA
==============
O sistema sabia conteudos, questoes, tentativas, dominio e trajetoria. Nao
sabia o que compoe um conteudo. A decisao entao acontecia no nivel errado: a
maquina identificava a pior micro-habilidade e intervinha no conteudo
INTEIRO - material do conteudo, pratica do conteudo. Um aluno que so tropeca
num passo do procedimento recebia uma aula sobre o assunto inteiro.

O QUE ESTE MODULO RESPONDE
===========================
    quais micro-habilidades compoem este conteudo?
    o que precisa vir antes desta?
    por onde comecar, quando varias estao fracas?

E so isso. Ele nao decide intervencao, nao mede aluno, nao fala com IA. E um
mapa; quem anda nele e `assessor_pedagogico`.

O PRIMEIRO GARGALO, E POR QUE ELE IMPORTA
==========================================
Com varias habilidades fracas, a pergunta nao e "qual esta pior" - e "em qual
delas o aluno esta PRONTO para aprender agora". Ensinar a resolver a equacao
a quem nao domina as operacoes basicas e falar sobre o telhado com quem ainda
nao tem parede. `primeiro_gargalo` desce ate a fraca mais basica da cadeia.

ELE NAO SABE DE QUE DISCIPLINA ESTAMOS FALANDO
===============================================
Nenhuma regra aqui cita assunto, e a documentacao tambem nao: um modulo
generico cuja prosa so ilustra com uma disciplina ensina ao proximo leitor
que ele e daquela disciplina. Ha teste lendo este arquivo e falhando se
alguem escrever o nome de um assunto - porque uma regra que aprende um
dominio sem querer obriga o segundo a ter um segundo motor.

POR QUE ISTO NAO E TABELA
==========================
Um grafo que muda por decisao pedagogica - e nao por uso - e artefato de
codigo, como `itens_guiados.py` e `conteudo_balanceamento.py` ja sao. Vira
tabela no dia em que alguem precisar edita-lo sem deploy, e nao antes. A
escolha esta registrada em docs/pedagogical-engine-audit.md.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field


class CicloDePreRequisito(ValueError):
    """A > B > A. Recusado na construcao, nao em producao."""


@dataclass(frozen=True)
class MicroHabilidade:
    """Uma coisa que da para aprender, ensinar e verificar separadamente.

    `code` e canonico e interno - e o mesmo string que
    `pedagogical_classifications.subcontent` ja usa, para que o modelo do
    aluno caiba em `domain_content_mastery.subcontent_code` sem coluna nova.

    `label` e o que o aluno e o professor leem. Nunca o codigo.
    """

    code: str
    label: str
    objetivo: str
    prerequisitos: tuple[str, ...] = ()
    # As estrategias que FAZEM SENTIDO para esta habilidade. Vazio significa
    # "todas as que o motor conhece" - o contrato nao obriga ninguem a
    # enumerar o obvio.
    estrategias: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)


class GrafoPedagogico:
    """As micro-habilidades de UM conteudo, e as relacoes entre elas."""

    def __init__(self, *, conteudo: str,
                 habilidades: Sequence[MicroHabilidade]) -> None:
        self.conteudo = conteudo
        por_codigo: dict[str, MicroHabilidade] = {}
        for h in habilidades:
            if h.code in por_codigo:
                raise ValueError(f"micro-habilidade repetida: {h.code!r}")
            por_codigo[h.code] = h
        for h in habilidades:
            for p in h.prerequisitos:
                if p not in por_codigo:
                    raise ValueError(
                        f"{h.code!r} declara pre-requisito inexistente: {p!r}")
        self._por_codigo = por_codigo
        self._ordem = tuple(h.code for h in habilidades)
        self._checar_ciclos()

    # -- leitura -----------------------------------------------------------

    def codigos(self) -> tuple[str, ...]:
        return self._ordem

    def tem(self, code: str) -> bool:
        return code in self._por_codigo

    def habilidade(self, code: str) -> MicroHabilidade | None:
        return self._por_codigo.get(code)

    def rotulo(self, code: str) -> str:
        """O nome em linguagem de gente - ou o proprio codigo.

        Devolver o codigo e honesto; inventar um nome nao.
        """
        h = self._por_codigo.get(code)
        return h.label if h and h.label else code

    def prerequisitos(self, code: str) -> tuple[str, ...]:
        h = self._por_codigo.get(code)
        return h.prerequisitos if h else ()

    def prerequisitos_em_profundidade(self, code: str) -> tuple[str, ...]:
        """Tudo o que sustenta esta habilidade, do mais basico ao mais proximo.

        A ordem importa: quem consome isto esta decidindo por onde comecar, e
        comecar pelo topo da cadeia e comecar pelo lugar errado.
        """
        vistos: list[str] = []

        def descer(atual: str) -> None:
            for p in self.prerequisitos(atual):
                descer(p)
                if p not in vistos:
                    vistos.append(p)

        if code in self._por_codigo:
            descer(code)
        return tuple(vistos)

    # -- a decisao que o grafo sustenta ------------------------------------

    def primeiro_gargalo(self, fracas: Iterable[str]) -> str | None:
        """Entre as habilidades fracas, aquela em que se deve intervir AGORA.

        E a mais basica da cadeia: se o aluno vai mal em A e em algo que
        depende de A, A vem primeiro. Intervir no dependente seria falar
        sobre o telhado com quem ainda nao tem parede.

        Fracas que nao estao no grafo sao ignoradas - um subconteudo antigo,
        sem no, nao pode derrubar a decisao. Se SO houver dessas, nao ha
        gargalo que este grafo saiba apontar, e quem chamou decide o que
        fazer com isso.
        """
        candidatas = [c for c in (fracas or ()) if c in self._por_codigo]
        if not candidatas:
            return None
        # Profundidade = quantos degraus esta habilidade tem embaixo dela. A
        # de menor profundidade sustenta as outras. O codigo desempata para a
        # saida ser estavel: o aluno nao pode ver a intervencao mudar sozinha
        # entre duas aberturas da mesma tela.
        return min(candidatas,
                   key=lambda c: (len(self.prerequisitos_em_profundidade(c)), c))

    # -- integridade -------------------------------------------------------

    def _checar_ciclos(self) -> None:
        EM_VISITA, PRONTO = 1, 2
        estado: dict[str, int] = {}

        def visitar(code: str, caminho: tuple[str, ...]) -> None:
            if estado.get(code) == PRONTO:
                return
            if estado.get(code) == EM_VISITA:
                raise CicloDePreRequisito(
                    "ciclo de pre-requisito: " + " > ".join(caminho + (code,)))
            estado[code] = EM_VISITA
            for p in self._por_codigo[code].prerequisitos:
                visitar(p, caminho + (code,))
            estado[code] = PRONTO

        for code in self._ordem:
            visitar(code, ())


__all__ = ["CicloDePreRequisito", "GrafoPedagogico", "MicroHabilidade"]
