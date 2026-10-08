"""RELATORIO DE APOIO A APRENDIZAGEM - do estudante, e so dele.

O §17 e a regra mais enfatica da especificacao, e a unica marcada como
"obrigatoria e nao pode ser reinterpretada". Ela tem duas metades.

A PRIMEIRA: O RELATORIO EXISTE
===============================
Gerar, visualizar, baixar em PDF. Conciso, compreensivel e baseado em
evidencias - e e essa ultima palavra que manda aqui: SEM evidencia, a secao
diz que ainda nao ha o que relatar, em vez de preencher com suposicao.

A SEGUNDA: ELE NAO E ENCAMINHADO
=================================
Nao ha envio ao professor, encaminhamento a Coordenacao, compartilhamento
automatico, notificacao de terceiros nem integracao a mecanismo de
distribuicao. Nem botao.

Este modulo nao conhece destinatario, nao importa transporte e nao sabe
enviar nada - e ha teste varrendo a fonte e as rotas do app. A ausencia de
codigo sozinha e garantia fragil: alguem acrescenta a rota amanha sem saber
que nao podia.

O aluno pode mostrar o documento a um professor por conta propria. O que o
Edu nao faz e mandar.

O QUE ELE NAO E
================
Diagnostico. O §17 proibe "deficiencia" como diagnostico de dificuldade
escolar, e o texto nao usa essa nem palavra nenhuma de laudo. Ha ressalva
explicita no documento, e teste varrendo o texto gerado.

DE ONDE SAEM OS FATOS
======================
De `consolidacao` (o que ele fez sozinho, e em quantas ocasioes) e de
`guided_practice_items` (o que precisou de ajuda). Nada e recalculado aqui, e
nada novo e gravado: o relatorio e uma LEITURA.

A separacao entre as duas listas e a mesma do produto inteiro - resolver com
dica nao entra em "fez sozinho". E a invariante que mais importa num papel
que o aluno leva para alguem.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from agente_ia_edu.services.consolidacao import (
    ESTADO_CONSOLIDADO,
    ESTADO_DEMONSTRADO,
    ESTADO_EM_APRENDIZADO,
    ESTADO_RETIDO,
)

# As secoes do §17, na ordem em que ele as lista. A ordem importa: o
# documento comeca pelo que ELE JA FAZ, e nao pela lista de problemas - quem
# leva um papel ao professor nao precisa que ele abra com o que falta.
SECOES = (
    "sozinho",
    "com_apoio",
    "avancos",
    "dificuldades",
    "trabalhado",
    "sugestoes",
)

_TITULOS = {
    "sozinho": "O que você já faz sozinho",
    "com_apoio": "O que você conseguiu com apoio",
    "avancos": "O que avançou",
    "dificuldades": "O que ainda está custando",
    "trabalhado": "O que trabalhamos",
    "sugestoes": "Para conversar com seu professor",
}

_RESSALVA = (
    "Este material resume o que aconteceu nos seus estudos aqui. Ele não é "
    "uma avaliação da escola e não substitui a opinião de um professor."
)

# Os estados que significam "fez sozinho". Vem de `consolidacao`, e nao de
# uma lista nova: duas definicoes do que e fazer sozinho divergiriam.
_FEZ_SOZINHO = (ESTADO_DEMONSTRADO, ESTADO_CONSOLIDADO, ESTADO_RETIDO)

# QUANTOS ITENS CABEM NUMA SECAO - e por que ha um limite.
#
# Medido no QA de 2026-10-08, com historico real: "O que ainda esta
# custando" saiu com SETE frases identicas, uma por micro-habilidade, e a
# sugestao repetiu as mesmas sete numa linha so. Isso nao e um resumo - e uma
# lista de vereditos, e e o que faz um papel sobre aprendizagem parecer laudo.
#
# O §17 pede "conciso, compreensivel e baseado em evidencias". Quatro linhas
# por secao e o que um adulto le de verdade antes de passar o olho.
#
# O CORTE E HONESTO: o que sobra e CONTADO, nunca omitido em silencio, e o
# quadro inteiro continua em "Meu progresso".
ITENS_POR_SECAO = 4

# Quantos nomes cabem numa enumeracao de uma linha. Maior que o limite de
# itens porque uma lista de nomes se le de relance; sete frases, nao.
NOMES_POR_LINHA = 6


def _com_sobra(itens: list[str], limite: int = ITENS_POR_SECAO) -> list[str]:
    """Os primeiros `limite`, e uma linha dizendo quantos ficaram de fora."""
    if len(itens) <= limite:
        return itens
    sobra = len(itens) - limite
    plural = "pontos" if sobra > 1 else "ponto"
    return itens[:limite] + [
        f"E mais {sobra} {plural} — estão no seu progresso."]


def _enumera(nomes: list[str], limite: int = NOMES_POR_LINHA) -> str:
    """Uma lista de nomes em uma linha, com a sobra contada."""
    if len(nomes) <= limite:
        return ", ".join(nomes)
    sobra = len(nomes) - limite
    # "e outros 1" e portugues errado, e saiu assim no QA de 2026-10-08.
    return ", ".join(nomes[:limite]) + f" e mais {sobra}"


def _ordem_da_dificuldade(habilidades: Mapping[str, dict]):
    """A ordem do que esta custando: o ACIONAVEL primeiro.

    Quem ja mostrou e escorregou tem recuperacao possivel - vale uma
    conferida. Quem nunca saiu sem ajuda precisa de ensino, que e mais longo.
    A primeira informacao e a mais acionavel, e e a que cabe nas quatro
    linhas que o papel tem.

    O desempate e alfabetico, para que duas chamadas iguais produzam o mesmo
    papel - um relatorio que muda de ordem a cada abertura nao e relatorio.
    """
    def chave(codigo: str):
        s = habilidades.get(codigo) or {}
        return (0 if s.get("revisao_recomendada") else 1, codigo)

    return sorted(habilidades, key=chave)


def rotulo_legivel(codigo: str) -> str:
    """`MASSA_MOLAR` -> "Massa molar". FORMATACAO, nao traducao.

    O Nucleo nao tem cadastro de nome humano para micro-habilidade, e esta
    funcao nao inventa um: ela troca o separador e a caixa do MESMO texto.
    Um dia havera cadastro, e entao o nome vem de la - por enquanto, o aluno
    le o codigo escrito como frase em vez de como constante.
    """
    limpo = (codigo or "").strip()
    if not limpo:
        return ""
    palavras = limpo.replace("_", " ").replace("-", " ").split()
    frase = " ".join(palavras).lower()
    return frase[:1].upper() + frase[1:]


def montar_relatorio(*, aluno_nome: str, conteudo: str,
                     habilidades: Mapping[str, dict] | None,
                     apoios: Sequence[Mapping] | None,
                     agora: datetime | None = None,
                     rotulos: Mapping[str, str] | None = None) -> dict:
    """O relatorio, pronto para a tela e para o PDF.

    `habilidades` e o que `consolidacao_do_aluno.situacao_por_habilidade`
    devolve. `apoios` sao as linhas de `guided_practice_items` daquele aluno.
    `rotulos` traduz o codigo da micro-habilidade para o nome que o aluno
    conhece; sem ele, o codigo aparece como veio - melhor que inventar.

    Nada aqui e gravado. Nada aqui decide dominio.
    """
    agora = agora or datetime.now(timezone.utc)
    habilidades = dict(habilidades or {})
    apoios = list(apoios or [])
    rotulos = dict(rotulos or {})

    def nome(codigo: str) -> str:
        return rotulos.get(codigo, codigo)

    # O CORTE VALE PARA TODAS, e nao so para as que explodiram no QA: a
    # proxima a crescer seria outra, e um limite por secao escolhida a dedo
    # deixaria a parede so mudar de lugar.
    secoes = [
        _secao("sozinho", _com_sobra(_itens_sozinho(habilidades, nome))),
        _secao("com_apoio", _com_sobra(_itens_com_apoio(apoios, nome))),
        _secao("avancos", _com_sobra(_itens_avancos(habilidades, nome))),
        _secao("dificuldades",
               _com_sobra(_itens_dificuldades(habilidades, apoios, nome))),
        _secao("trabalhado", _itens_trabalhado(habilidades, apoios, nome)),
        _secao("sugestoes",
               _com_sobra(_itens_sugestoes(habilidades, apoios, nome))),
    ]

    return {
        "titulo": f"Apoio à aprendizagem — {aluno_nome}",
        "subtitulo": f"{conteudo} · {agora.strftime('%d/%m/%Y')}",
        "ressalva": _RESSALVA,
        "secoes": secoes,
        "gerado_em": agora.isoformat(),
    }


def _secao(chave: str, itens: list[str]) -> dict:
    """Uma secao. Vazia, ela diz que esta vazia - nao desaparece.

    Sumir faria o leitor supor; dizer "ainda nao ha" e a informacao certa, e
    e o que o §17 pede com "quando houver evidencias".
    """
    return {"chave": chave, "titulo": _TITULOS[chave],
            "itens": itens or [_VAZIO[chave]]}


_VAZIO = {
    "sozinho": "Ainda não há registro de algo resolvido sem ajuda.",
    "com_apoio": "Ainda não há registro de apoio usado.",
    "avancos": "Ainda não há avanços registrados.",
    "dificuldades": "Ainda não há dificuldade registrada.",
    "trabalhado": "Ainda não trabalhamos nada por aqui.",
    "sugestoes": "Ainda não há o que sugerir — faltam registros.",
}


def _itens_sozinho(habilidades: Mapping[str, dict], nome) -> list[str]:
    saida = []
    for codigo, s in sorted(habilidades.items()):
        if s.get("estado") not in _FEZ_SOZINHO:
            continue
        quantas = int(s.get("oportunidades") or 1)
        if s.get("estado") == ESTADO_RETIDO:
            saida.append(f"{nome(codigo)} — resolveu sozinho e lembrou "
                         f"depois de um tempo.")
        elif quantas > 1:
            saida.append(f"{nome(codigo)} — resolveu sozinho em "
                         f"{quantas} ocasiões diferentes.")
        else:
            saida.append(f"{nome(codigo)} — resolveu sozinho.")
    return saida


def _itens_com_apoio(apoios: Sequence[Mapping], nome) -> list[str]:
    """O que ele CONSEGUIU com ajuda - e a ajuda fica dita.

    Nao basta listar: o §17 pede a distincao, e "resolveu" sem dizer "com
    duas dicas" e a frase que transforma apoio em autonomia na cabeca de
    quem le.
    """
    saida = []
    for a in apoios:
        if not a.get("completed") or a.get("solved_unaided"):
            continue
        dicas = int(a.get("hints_used") or 0)
        pedidos = int(a.get("help_requests") or 0)
        como = []
        if dicas:
            como.append(f"{dicas} dica{'s' if dicas > 1 else ''}")
        if pedidos:
            como.append("pedindo para olhar junto")
        detalhe = f" (com {', '.join(como)})" if como else " (com apoio)"
        saida.append(f"{nome(a.get('skill') or '')} — resolveu{detalhe}.")
    return saida


def _itens_avancos(habilidades: Mapping[str, dict], nome) -> list[str]:
    """Avanco e repetir ou lembrar - nao e ter ido bem uma vez."""
    saida = []
    for codigo, s in sorted(habilidades.items()):
        estado = s.get("estado")
        if estado == ESTADO_CONSOLIDADO:
            saida.append(f"{nome(codigo)} — passou a sair em mais de uma "
                         f"ocasião.")
        elif estado == ESTADO_RETIDO:
            saida.append(f"{nome(codigo)} — continuou saindo depois de um "
                         f"intervalo.")
    return saida


def _itens_dificuldades(habilidades: Mapping[str, dict],
                        apoios: Sequence[Mapping], nome) -> list[str]:
    """O que esta custando - DESCRITO, nunca diagnosticado.

    "Ainda esta custando" e observacao. "Ele tem dificuldade de abstracao" e
    um laudo que nenhuma resposta a questao sustenta, e que o §17 proibe.
    """
    saida = []
    for codigo in _ordem_da_dificuldade(habilidades):
        s = habilidades[codigo]
        # A REVISAO VEM ANTES do estado: quem ja mostrou e escorregou entra
        # por este ramo, e e o item mais acionavel da secao.
        if s.get("revisao_recomendada"):
            saida.append(f"{nome(codigo)} — já saiu antes, e da última vez "
                         f"não saiu.")
        elif s.get("estado") == ESTADO_EM_APRENDIZADO:
            saida.append(f"{nome(codigo)} — ainda não saiu sem ajuda.")
    muita_ajuda = sorted({str(a.get("skill") or "") for a in apoios
                          if int(a.get("hints_used") or 0) >= 3})
    for codigo in muita_ajuda:
        if codigo and not any(codigo in i for i in saida):
            saida.append(f"{nome(codigo)} — precisou de bastante apoio para "
                         f"chegar lá.")
    return saida


def _itens_trabalhado(habilidades: Mapping[str, dict],
                      apoios: Sequence[Mapping], nome) -> list[str]:
    codigos = sorted(set(habilidades) | {str(a.get("skill") or "")
                                         for a in apoios if a.get("skill")})
    if not codigos:
        return []
    return [_enumera([nome(c) for c in codigos]) + "."]


def _itens_sugestoes(habilidades: Mapping[str, dict],
                     apoios: Sequence[Mapping], nome) -> list[str]:
    """O que um professor poderia olhar - objetivo, e sem prescrever laudo.

    Sugestao de INTERVENCAO, como o §17 pede: o que retomar e por que. Nunca
    o que o aluno "e".
    """
    saida = []
    travadas = [c for c, s in sorted(habilidades.items())
                if s.get("estado") == ESTADO_EM_APRENDIZADO]
    if travadas:
        saida.append("Retomar com ele: "
                     + _enumera([nome(c) for c in travadas]) + ".")
    revisar = [c for c, s in sorted(habilidades.items())
               if s.get("revisao_recomendada")]
    if revisar:
        saida.append("Vale uma conferida rápida em: "
                     + _enumera([nome(c) for c in revisar]) + ".")
    dependeu = sorted({str(a.get("skill") or "") for a in apoios
                       if int(a.get("hints_used") or 0) >= 3})
    if dependeu:
        saida.append("Ele chegou ao resultado com apoio em: "
                     + _enumera([nome(c) for c in dependeu if c])
                     + ". Vale ver se sai sem ajuda.")
    return saida


__all__ = ["ITENS_POR_SECAO", "NOMES_POR_LINHA", "SECOES",
           "montar_relatorio", "rotulo_legivel"]
