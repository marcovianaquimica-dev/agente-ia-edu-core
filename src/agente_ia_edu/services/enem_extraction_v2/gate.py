"""MVP_IMPORT_GATE - portao conservador de importacao. PROTOTIPO EXPERIMENTAL.

Decide, de forma deterministica e auditavel, se um ``QuestionCandidate`` pode
entrar automaticamente no Question Bank.

Principio, fixado pelo usuario:

    alta confianca -> banco
    qualquer duvida relevante -> REQUIRES_REVIEW

O portao **nunca descarta**. Um item reprovado continua existindo, com os
motivos nomeados. Aumentar cobertura sacrificando precisao e explicitamente
proibido nesta etapa.

Este portao e SEPARADO de ``QuestionBankImporter._validate`` da producao, e
nao o substitui: ele e mais estrito, e roda antes. A V1 continua intocada.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import QuestionCandidate

VERSAO_DO_PORTAO = "MVP_IMPORT_GATE_V1"

# --- motivos de revisao, todos auditaveis --------------------------------
NUMERO_AUSENTE = "NUMERO_AUSENTE"
NUMERO_FORA_DA_FAIXA = "NUMERO_FORA_DA_FAIXA"
ENUNCIADO_INCOMPLETO = "ENUNCIADO_INCOMPLETO"
ALTERNATIVAS_INCOMPLETAS = "ALTERNATIVAS_INCOMPLETAS"
ORDEM_DAS_ALTERNATIVAS = "ORDEM_DAS_ALTERNATIVAS"
ALTERNATIVA_DUPLICADA = "ALTERNATIVA_DUPLICADA"
GABARITO_AUSENTE = "GABARITO_AUSENTE"
GABARITO_FORA_DE_A_E = "GABARITO_FORA_DE_A_E"
PROBLEMA_ESTRUTURAL = "PROBLEMA_ESTRUTURAL"

# Todos os motivos que o portao pode emitir. Ha teste de exaustividade: um
# motivo novo que nao entre aqui quebra a suite, para que nenhum motivo
# nasca sem classificacao - erro ja cometido no CEREBRO com
# EMPTY_PUBLIC_ANSWER.
MOTIVOS_CONHECIDOS = frozenset({
    NUMERO_AUSENTE, NUMERO_FORA_DA_FAIXA, ENUNCIADO_INCOMPLETO,
    ALTERNATIVAS_INCOMPLETAS, ORDEM_DAS_ALTERNATIVAS, ALTERNATIVA_DUPLICADA,
    GABARITO_AUSENTE, GABARITO_FORA_DE_A_E, PROBLEMA_ESTRUTURAL,
})

# Enunciado mais curto que isto nao e enunciado. Medido nos 12 cadernos: o
# menor enunciado legitimo reconstruido tem 86 caracteres; o percentil 1 fica
# em 108. 40 e folgadamente abaixo de qualquer item real e acima de residuo.
TAMANHO_MINIMO_DE_ENUNCIADO = 40

LETRAS = ("A", "B", "C", "D", "E")


@dataclass(frozen=True)
class DecisaoDoPortao:
    aprovado: bool
    motivos: tuple[str, ...]
    detalhes: tuple[str, ...] = ()
    versao: str = VERSAO_DO_PORTAO

    @property
    def estado(self) -> str:
        return "MVP_IMPORT" if self.aprovado else "REQUIRES_REVIEW"


def avaliar(questao: QuestionCandidate, *,
            faixa: tuple[int, int] | None = None) -> DecisaoDoPortao:
    """Aplica o portao. Nao escreve nada, nao decide nada alem disto."""
    motivos: list[str] = []
    detalhes: list[str] = []

    # 1. numero
    if questao.numero is None:
        motivos.append(NUMERO_AUSENTE)
    elif faixa is not None and not (faixa[0] <= questao.numero <= faixa[1]):
        motivos.append(NUMERO_FORA_DA_FAIXA)
        detalhes.append(f"numero {questao.numero} fora de {faixa}")

    # 2. enunciado
    enunciado = (questao.enunciado or "").strip()
    if len(enunciado) < TAMANHO_MINIMO_DE_ENUNCIADO:
        motivos.append(ENUNCIADO_INCOMPLETO)
        detalhes.append(f"enunciado com {len(enunciado)} caracteres")

    # 3 e 4. alternativas: completude e ordem
    letras = tuple(o.letra for o in questao.opcoes)
    if len(questao.opcoes) != 5 or not all(o.texto.strip() for o in questao.opcoes):
        motivos.append(ALTERNATIVAS_INCOMPLETAS)
        detalhes.append(f"{len(questao.opcoes)} alternativas")
    elif letras != LETRAS:
        motivos.append(ORDEM_DAS_ALTERNATIVAS)
        detalhes.append(f"ordem {letras}")
    else:
        textos = [" ".join(o.texto.split()).casefold() for o in questao.opcoes]
        if len(set(textos)) != 5:
            motivos.append(ALTERNATIVA_DUPLICADA)

    # 5. gabarito
    if questao.gabarito is None:
        motivos.append(GABARITO_AUSENTE)
    elif questao.gabarito not in LETRAS:
        motivos.append(GABARITO_FORA_DE_A_E)
        detalhes.append(f"gabarito {questao.gabarito!r}")

    # 6. qualquer problema estrutural ja declarado pelo extrator
    #    (legibilidade, mobilia residual, dependencia visual, duplicata,
    #    gabarito ambiguo). O portao nao reinterpreta: confia na declaracao.
    if questao.problemas:
        motivos.append(PROBLEMA_ESTRUTURAL)
        detalhes.extend(sorted(set(questao.problemas)))

    assert set(motivos) <= MOTIVOS_CONHECIDOS, f"motivo nao classificado: {motivos}"
    return DecisaoDoPortao(not motivos, tuple(motivos), tuple(detalhes))
