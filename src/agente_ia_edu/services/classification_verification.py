"""O contrato que decide se o sistema aprova uma classificacao sozinho.

    QUESTAO -> CLASSIFICADOR -> candidata -> VERIFICADOR -> decisao

Este modulo e a ultima etapa, e e deliberadamente DETERMINISTICO: recebe o
que as duas etapas de IA produziram e aplica uma regra que nao consulta
modelo nenhum. O julgamento pedagogico e da IA; a decisao de confiar nela e
do sistema, e precisa ser auditavel linha a linha.

POR QUE CONFIANCA DECLARADA NAO APROVA
=======================================
`confidence` e o quanto o modelo ACHA que acertou - nao o quanto ele acerta.
Um modelo mal calibrado diz 0,97 e erra; um bem calibrado diz 0,70 e acerta.
`if confidence >= 0.95: approve()` terceiriza a decisao para a autoestima do
modelo, e o numero escolhido seria arbitrario de qualquer forma.

O que aprova aqui e CONCORDANCIA ENTRE DUAS ETAPAS INDEPENDENTES, sobre uma
taxonomia que existe, numa questao integra, com evidencia curricular. A
confianca e gravada para auditoria e nao entra na decisao - ha teste que
varre a arvore sintatica deste arquivo e falha se alguem compara-la com um
limiar.

POR QUE O VERIFICADOR NAO VE A JUSTIFICATIVA DO CLASSIFICADOR
==============================================================
Duas etapas que leem o mesmo raciocinio nao sao duas etapas: a segunda
ancora na primeira e a concordancia deixa de ser evidencia. O verificador
recebe a questao, as alternativas, a taxonomia permitida e o ROTULO
candidato - para aceitar ou rejeitar - e produz a propria conclusao. Isso e
imposto em `ClassificacaoCandidata`: a justificativa do classificador nao e
campo deste objeto.

FAIL-CLOSED
===========
Qualquer duvida relevante devolve AI_SUGGESTED + requires_review. O custo de
mandar uma questao boa para revisao e o tempo de alguem; o custo de aprovar
uma errada e um aluno sendo avaliado em conteudo que a questao nao cobra.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# As tres proveniencias, espelhando o CHECK da migration 065.
PROV_AI_SUGGESTED = "AI_SUGGESTED"
PROV_AI_VERIFIED = "AI_VERIFIED"
# Existe aqui para o resto do sistema importar de um lugar so. O caminho
# AUTOMATICO nunca o emite - ver `decidir`, que sequer o menciona.
PROV_HUMAN_VALIDATED = "HUMAN_VALIDATED"

# A saida de emergencia. Sem ela, duas etapas obrigadas a escolher dentro de
# uma taxonomia que nao cobre a questao convergem para a mesma resposta errada
# - e o contrato le isso como concordancia. Aconteceu de verdade: uma varredura
# de Quimica aprovou "x2 + bx + 49 = 0" como conteudo quimico, porque as duas
# etapas so tinham codigos de Quimica para escolher.
FORA_DO_ESCOPO = "FORA_DO_ESCOPO"


@dataclass(frozen=True)
class ClassificacaoCandidata:
    """O que o classificador propos.

    NAO carrega a justificativa textual do classificador: ela nao pode
    chegar ao verificador, e o jeito mais simples de garantir isso e nao
    existir campo para ela neste objeto.
    """

    question_version_id: str
    primary: str
    secondary: list[str] = field(default_factory=list)
    confidence: float | None = None
    classifier_version: str = ""


@dataclass(frozen=True)
class Veredito:
    """O que o verificador concluiu POR CONTA PROPRIA."""

    primary: str
    secondary: list[str] = field(default_factory=list)
    evidencias: list[str] = field(default_factory=list)
    verifier_version: str = ""
    integro: bool = True
    problemas: list[str] = field(default_factory=list)
    # O Player nao entrega imagem. Uma questao que depende de figura chega ao
    # aluno impossivel de responder, por mais correta que a classificacao
    # esteja. A varredura aprovou uma que dizia "o aparato ilustrado na
    # figura" - este campo existe por causa dela.
    depende_de_figura: bool = False


@dataclass(frozen=True)
class ContratoDeAprovacao:
    """O que precisa ser verdade para o sistema aprovar sozinho.

    Nenhum campo e um limiar de confianca, de proposito.
    """

    exige_concordancia_no_primary: bool = True
    exige_taxonomia_valida: bool = True
    exige_integridade_da_questao: bool = True
    minimo_de_evidencias: int = 1
    exige_primary_fora_do_secondary: bool = True
    exige_primary_preenchido: bool = True
    recusa_fora_do_escopo: bool = True
    recusa_dependencia_de_figura: bool = True

    @classmethod
    def default(cls) -> "ContratoDeAprovacao":
        return cls()


@dataclass(frozen=True)
class Decisao:
    provenance: str
    requires_review: bool
    primary: str
    secondary: list[str]
    motivos: list[str]
    evidencias: list[str]
    classifier_version: str
    verifier_version: str
    confidence: float | None


def decidir(candidata: ClassificacaoCandidata, veredito: Veredito, *,
            taxonomia: set[str], contrato: ContratoDeAprovacao) -> Decisao:
    """Aplica o contrato. Determinístico, sem chamada de modelo.

    Acumula TODOS os motivos em vez de parar no primeiro: quem for revisar
    precisa ver o quadro inteiro, nao o primeiro problema encontrado.
    """
    motivos: list[str] = []

    # -- ha rotulo? ---------------------------------------------------------
    # A primeira versao filtrava a taxonomia com `if c and c not in taxonomia`,
    # e o `if c` pulava a string vazia: um primary em branco nunca era
    # comparado com nada e passava direto. Duas questoes - uma de sociologia,
    # outra em ingles - foram aprovadas assim.
    if contrato.exige_primary_preenchido:
        for quem, valor in (("classificador", candidata.primary),
                            ("verificacao", veredito.primary)):
            if not (valor or "").strip():
                motivos.append(f"{quem} nao devolveu um PRIMARY")

    # -- a questao cabe nesta taxonomia? ------------------------------------
    if contrato.recusa_fora_do_escopo and FORA_DO_ESCOPO in (
            candidata.primary, veredito.primary):
        motivos.append(
            "ao menos uma etapa declarou a questao FORA DO ESCOPO desta "
            "taxonomia - concordancia forcada nao e evidencia")

    # -- o aluno consegue responder? ---------------------------------------
    if contrato.recusa_dependencia_de_figura and veredito.depende_de_figura:
        motivos.append(
            "a questao depende de uma figura que o Player nao entrega")

    # -- as duas etapas concordam no rotulo principal? ---------------------
    if contrato.exige_concordancia_no_primary and candidata.primary != veredito.primary:
        motivos.append(
            f"divergencia no PRIMARY: classificador disse {candidata.primary!r}, "
            f"verificacao independente disse {veredito.primary!r}")

    # -- os codigos existem? -----------------------------------------------
    if contrato.exige_taxonomia_valida:
        desconhecidos = sorted(
            {c for c in [candidata.primary, veredito.primary]
             + list(candidata.secondary) + list(veredito.secondary)
             # FORA_DO_ESCOPO ja foi tratado acima com motivo proprio; repeti-lo
             # aqui como "codigo desconhecido" confundiria quem le o relatorio.
             if (c or "").strip() and c != FORA_DO_ESCOPO and c not in taxonomia})
        if desconhecidos:
            motivos.append(
                "fora da taxonomia vigente: " + ", ".join(desconhecidos))

    # -- a questao da para responder? --------------------------------------
    if contrato.exige_integridade_da_questao and not veredito.integro:
        detalhe = "; ".join(veredito.problemas) or "nao especificado"
        motivos.append(f"integridade da questao em duvida: {detalhe}")

    # -- ha evidencia curricular? ------------------------------------------
    if len(veredito.evidencias) < contrato.minimo_de_evidencias:
        motivos.append(
            f"evidencia curricular insuficiente: {len(veredito.evidencias)} "
            f"item(ns), minimo {contrato.minimo_de_evidencias}")

    # -- um PRIMARY, e so um ------------------------------------------------
    # Repetir o principal entre os secundarios nao e multirrotulo: e perder a
    # informacao de qual conteudo a questao de fato avalia.
    if contrato.exige_primary_fora_do_secondary:
        for rotulo, sec in ((candidata.primary, candidata.secondary),
                            (veredito.primary, veredito.secondary)):
            if rotulo in sec:
                motivos.append(
                    f"o PRIMARY {rotulo!r} aparece tambem como SECONDARY")
                break

    # -- SECONDARY: so o que as DUAS etapas viram --------------------------
    # Mencao no enunciado nao basta. Um rotulo que so uma das etapas
    # reconheceu nao entra - mas tambem nao derruba o principal, porque a
    # pergunta "a questao avalia X?" e independente de "ela tambem toca Y?".
    secundarios = [c for c in candidata.secondary
                   if (c or "").strip() and c in veredito.secondary
                   and c in taxonomia]

    aprovado = not motivos
    if aprovado:
        motivos.append(
            "as duas etapas concordaram no conteudo principal, os codigos "
            "existem na taxonomia, a questao esta integra e ha evidencia "
            "curricular")

    return Decisao(
        provenance=PROV_AI_VERIFIED if aprovado else PROV_AI_SUGGESTED,
        requires_review=not aprovado,
        primary=veredito.primary if aprovado else candidata.primary,
        secondary=secundarios if aprovado else [],
        motivos=motivos,
        evidencias=list(veredito.evidencias),
        classifier_version=candidata.classifier_version,
        verifier_version=veredito.verifier_version,
        confidence=candidata.confidence,   # gravada para auditoria, nao decide
    )


__all__ = [
    "ClassificacaoCandidata", "Veredito", "ContratoDeAprovacao", "Decisao",
    "decidir", "PROV_AI_SUGGESTED", "PROV_AI_VERIFIED", "PROV_HUMAN_VALIDATED",
    "FORA_DO_ESCOPO",
]
