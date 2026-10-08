"""Camada de texto da V2: dois motores, escolha por evidencia (C1, C2).

PROTOTIPO EXPERIMENTAL. Nada aqui e chamado pela producao.

Duas correcoes em relacao a V1, cada uma com o fato que a justifica:

C1 - a V1 une as paginas com ``"\\f".join(...)``. Em ``re.MULTILINE`` o ``^``
     casa no inicio da string e depois de ``\\n`` - **nunca depois de ``\\f``**.
     Quando a ultima linha da pagina anterior nao termina em ``\\n``, o
     cabecalho no topo da pagina seguinte fica inalcancavel. Medido: 26 itens
     em 2024 D1 e 28 em 2024 D2 (o slug de impressao do InDesign ocupa a
     ultima linha). Aqui as paginas sao unidas com ``"\\n\\f"``.

C2 - em 2025 o ``pypdf`` estilhaca o rotulo: ``'QU EST ãO 24  '``. Medido: a
     palavra integra aparece 4 vezes em 2025 D1 contra 95 em 2023 D1. O
     PyMuPDF le o mesmo arquivo como ``'QUESTÃO 24'``. A V2 **nao** afrouxa a
     regex do rotulo - afrouxar convidaria falso positivo. Ela escolhe o motor
     cujo texto permite ler o rotulo.

A escolha de motor NAO e fixa por ano. E feita por caderno, por medicao, com
a regra declarada em :func:`escolher_motor` antes de qualquer execucao.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .contracts import MOTOR_PYMUPDF, MOTOR_PYPDF

# Separador de pagina. A V1 usa "\f"; a V2 usa "\n\f" (ver C1 acima).
SEPARADOR_PAGINA = "\n\f"

# Rotulo do item. Estrito de proposito: ver C2.
PADRAO_CABECALHO = re.compile(r"(?mi)^[ \t\f]*Quest[ãa]o[ \t]+(\d{1,3})\b")


@dataclass(frozen=True)
class CamadaDeTexto:
    motor: str
    paginas: tuple[str, ...]
    documento: str
    deslocamentos: tuple[int, ...]
    limpeza: dict = field(default_factory=dict)

    def pagina_de(self, posicao: int) -> int:
        """1-based. Qual pagina contem esta posicao do documento.

        Uma posicao caida DENTRO do separador pertence a pagina SEGUINTE: o
        ``^`` casa logo apos o ``\\n`` do separador, antes do ``\\f``, entao o
        inicio do cabecalho de topo de pagina fica no separador. Atribui-lo a
        pagina anterior deslocaria em 1 a pagina de todo item de topo.
        """
        for indice, deslocamento in enumerate(self.deslocamentos):
            if posicao < deslocamento + len(self.paginas[indice]):
                return indice + 1
        return len(self.paginas) or 1


def normalizar(texto: str) -> str:
    """Normalizacao minima e justificada, aplicada igualmente aos dois motores.

    - ``\\xa0`` (espaco inquebravel) -> espaco. Medido no texto do PyMuPDF em
      2023 D1: aparece dentro do texto das alternativas (``'os\\xa0lixões'``).
      Sem isto a contagem de separadores fica dependente do caractere.
    - ``\\r\\n`` e ``\\r`` -> ``\\n``, para que ``^`` tenha um unico significado.

    Nada mais. Em particular **nao** se mexe em acento, caixa, hifen ou
    pontuacao: a fidelidade do texto reconstruido e o criterio desta fase.
    """
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    return texto.replace("\xa0", " ")


# --- mobilia de pagina ----------------------------------------------------
# Cabecalho corrido, rodape, codigo de barras e marca d'agua de seguranca nao
# sao conteudo do item, e contaminam o que estiver perto. Medido em 2024 D1:
# a alternativa E do item 3 vinha com 1.631 caracteres, dos quais ~1.560 eram
#
#     '*010175AZ2* ENEM2024ENEM2024ENEM2024... LINGUAGENS, CODIGOS ... AZUL'
#
# contra 71 caracteres da alternativa D. A ultima alternativa e sempre a que
# absorve, porque nao ha marcador depois dela para interromper.
#
# A remocao e por FREQUENCIA ENTRE PAGINAS, nao por vocabulario: uma linha
# que se repete em metade ou mais das paginas de um caderno de 32 paginas e
# mobilia. Nenhuma regra menciona "ENEM", "CADERNO" ou "AZUL" - assim a
# limpeza vale para FUVEST, UNICAMP ou qualquer outra prova.
FRACAO_DE_PAGINAS_PARA_SER_MOBILIA = 0.5
TAMANHO_MAXIMO_DE_LINHA_DE_MOBILIA = 120

# Marca d'agua de seguranca: a mesma sequencia repetida muitas vezes.
# Precisa tolerar espaco DENTRO da unidade, porque 2024 imprime
# 'ENEM2024ENEM2024...' (colado) e 2022 imprime 'ENEM 2022 ENEM 2022 ...'
# (com espaco). Exige 5 ocorrencias ou mais e unidade de 4+ caracteres, para
# nao destruir repeticao legitima: medido contra '10 10 10', 'nao nao nao' e
# '1 1 1 1 1 1', que sobrevivem. Limitacao conhecida: cinco repeticoes
# consecutivas de uma mesma palavra longa - possivel em poema - seriam
# removidas. Ver o teste que caracteriza isso.
PADRAO_MARCA_DAGUA = re.compile(r"((?:\S{2,}[ \t]+){0,3}\S{2,}[ \t]*)(?:\1){4,}")
TAMANHO_MINIMO_DA_UNIDADE_DE_MARCA = 4


def _remover_marca_dagua(linha: str) -> tuple[str, int]:
    n = 0

    def troca(achado: re.Match) -> str:
        nonlocal n
        if len(achado.group(1).strip()) < TAMANHO_MINIMO_DA_UNIDADE_DE_MARCA:
            return achado.group(0)
        n += 1
        return " "

    return PADRAO_MARCA_DAGUA.sub(troca, linha), n

# Codigo de barras impresso entre asteriscos.
PADRAO_CODIGO_DE_BARRAS = re.compile(r"\*[0-9][0-9A-Z]{3,}\*")


def _linhas_de_mobilia_v1(paginas: list[str]) -> set[str]:
    """Regra da primeira V2, preservada para reproduzir o baseline."""
    contagem: dict[str, int] = {}
    for texto in paginas:
        for linha in {ln.strip() for ln in texto.splitlines() if ln.strip()}:
            if len(linha) <= TAMANHO_MAXIMO_DE_LINHA_DE_MOBILIA:
                contagem[linha] = contagem.get(linha, 0) + 1
    minimo = max(2, int(len(paginas) * FRACAO_DE_PAGINAS_PARA_SER_MOBILIA))
    return {linha for linha, n in contagem.items() if n >= minimo}


def limpar_mobilia(paginas: list[str], *,
                   endurecido: bool = True) -> tuple[list[str], dict]:
    """Remove mobilia de pagina. Devolve (paginas, relatorio do que saiu).

    ``endurecido=False`` reproduz exatamente a V2 da primeira medicao, para
    que o baseline continue executavel e a comparacao seja possivel.
    """
    mobilia = (linhas_de_mobilia(paginas) if endurecido
               else _linhas_de_mobilia_v1(paginas))
    fundo = tokens_de_fundo(paginas) if endurecido else set()
    ngramas = ngramas_de_mobilia(paginas) if endurecido else set()
    extensao = ngramas_de_extensao(paginas) if endurecido else set()
    deslocamento = deslocamento_da_numeracao(paginas) if endurecido else None
    removidas = marcas = codigos = tokens = palavras_ng = numeros = 0
    saida = []
    for indice, texto in enumerate(paginas, start=1):
        if deslocamento is not None:
            texto, n = _remover_numero_de_pagina(texto, indice + deslocamento)
            numeros += n
        linhas = []
        for linha in texto.splitlines():
            if linha.strip() in mobilia:
                removidas += 1
                continue
            # ORDEM IMPORTA. _remover_marca_dagua parte o token gigante
            # em fragmentos, e os fragmentos deixam de casar com o conjunto
            # `fundo`, que foi medido no texto cru. Defeito observado: a
            # marca d'agua de 2024 sobrevivia dentro da alternativa E.
            nova = linha
            if endurecido:
                nova, n = _remover_sequencias_de_fundo(nova, fundo)
                tokens += n
            nova, n = _remover_marca_dagua(nova)
            marcas += n
            nova, n = PADRAO_CODIGO_DE_BARRAS.subn(" ", nova)
            codigos += n
            if endurecido:
                nova, n = _remover_ngramas(nova, ngramas, extensao)
                palavras_ng += n
            linhas.append(nova)
        saida.append("\n".join(linhas))
    return saida, {"linhas_de_mobilia_distintas": len(mobilia),
                   "linhas_removidas": removidas,
                   "marcas_dagua_removidas": marcas,
                   "codigos_de_barras_removidos": codigos,
                   "tokens_de_fundo_distintos": len(fundo),
                   "tokens_de_fundo_removidos": tokens,
                   "ngramas_de_mobilia": len(ngramas),
                   "palavras_de_ngrama_removidas": palavras_ng,
                   "numeros_de_pagina_removidos": numeros,
                   "deslocamento_da_numeracao": deslocamento,
                   "endurecido": endurecido}


# --- ENDURECIMENTO: mobilia que escapou da primeira versao ---------------
#
# A adjudicacao humana reprovou 7 itens que a V2 importaria com texto
# estranho. A forense achou tres modos, e os dois primeiros sao deterministicos:
#
# modo 1 - marca d'agua com glifo corrompido no meio:
#              'ENEM2024ENEM20E 4ENEM2024ENEM2024'
#          o padrao PADRAO_MARCA_DAGUA exige repeticao EXATA e nao casa.
#
# modo 2 - cabecalho corrido com o numero da pagina no fim:
#              '... CADERNO 1 | AZUL 30'
#          cada ocorrencia e unica, entao a contagem por frequencia nao ve.
#
# As duas regras abaixo sao estruturais e nao citam vocabulario de ENEM.

# Multiplicidade por pagina acima da qual um token e fundo de pagina.
# Medido nos 12 cadernos: o token legitimo de maior mediana por pagina e
# 'para', com 4. A marca d'agua de 2022 mede 63. Qualquer piso entre 5 e 60
# produz o mesmo resultado; 20 fica no meio da folga medida.
MULTIPLICIDADE_DE_FUNDO = 20
PRESENCA_MINIMA_DE_FUNDO = 0.5

# Token colado que contem uma unidade repetida imediatamente. Periodo >= 4
# para nao pegar 'CH3CH3' nem 'H2OH2O'; comprimento >= 12 para nao pegar
# token curto por acaso.
PERIODO_MINIMO_NO_TOKEN = 4
REPETICOES_NO_TOKEN = 2
TAMANHO_MINIMO_DO_TOKEN_DE_FUNDO = 12
TAMANHO_MAXIMO_EXAMINADO = 400

# Piso de tamanho do NUCLEO da linha (ja sem os numeros de borda) para ela
# poder ser considerada mobilia. GUARDA CRITICA: sem este piso, normalizar os
# digitos faz 'Questao 18' virar 'Questao', que se repete 95 vezes num
# caderno, e a regra apagaria todos os cabecalhos de item. Tambem protege os
# marcadores 'A'..'E', que repetem de 29 a 53 vezes. Medido: os nucleos de
# mobilia reais tem 39 e 73 caracteres.
TAMANHO_MINIMO_DO_NUCLEO = 25

# Abaixo disso a estatistica de "linha que se repete entre paginas" nao
# significa nada. Um caderno de prova tem 32 paginas.
PAGINAS_MINIMAS_PARA_FREQUENCIA = 8

# Numero de pagina e separadores decorativos nas bordas da linha.
BORDA_DE_PAGINA = re.compile(r"^[\s\u2022|.\-\u2013\u2014]*\d{1,3}[\s\u2022|.\-\u2013\u2014]*"
                             r"|[\s\u2022|.\-\u2013\u2014]*\d{1,3}[\s\u2022|.\-\u2013\u2014]*$")

# Minimo de tokens de fundo seguidos para remover a sequencia. Evita apagar
# uma mencao legitima isolada a uma palavra que tambem compoe a marca d'agua.
TOKENS_DE_FUNDO_SEGUIDOS = 3


def nucleo_da_linha(linha: str) -> str:
    """Remove numeros e separadores das bordas, repetidamente."""
    anterior, atual = None, linha.strip()
    while atual != anterior:
        anterior = atual
        atual = BORDA_DE_PAGINA.sub("", atual).strip()
    return atual


def token_autossimilar(token: str) -> bool:
    """O token contem uma unidade de 4+ caracteres repetida imediatamente."""
    if len(token) < TAMANHO_MINIMO_DO_TOKEN_DE_FUNDO:
        return False
    alvo = token[:TAMANHO_MAXIMO_EXAMINADO]
    n = len(alvo)
    for inicio in range(n - PERIODO_MINIMO_NO_TOKEN * REPETICOES_NO_TOKEN + 1):
        resto = alvo[inicio:]
        for periodo in range(PERIODO_MINIMO_NO_TOKEN,
                             len(resto) // REPETICOES_NO_TOKEN + 1):
            if resto.startswith(resto[:periodo] * REPETICOES_NO_TOKEN):
                return True
    return False


def tokens_de_fundo(paginas: list[str]) -> set[str]:
    """Tokens que sao fundo de pagina, nao conteudo.

    Dois sinais independentes, ambos estruturais:
      S1 - token colado autossimilar presente na maioria das paginas;
      S2 - token com mediana de ocorrencias por pagina muito alta.
    """
    if not paginas:
        return set()
    import statistics

    contagens: dict[str, list[int]] = {}
    for texto in paginas:
        locais: dict[str, int] = {}
        for token in re.findall(r"\S+", texto):
            locais[token] = locais.get(token, 0) + 1
        for token, n in locais.items():
            contagens.setdefault(token, []).append(n)

    total = len(paginas)
    fundo: set[str] = set()
    for token, ns in contagens.items():
        presenca = len(ns) / total
        mediana = statistics.median(ns + [0] * (total - len(ns)))
        if mediana >= MULTIPLICIDADE_DE_FUNDO:
            fundo.add(token)
        elif presenca >= PRESENCA_MINIMA_DE_FUNDO and token_autossimilar(token):
            fundo.add(token)
    return fundo


def linhas_de_mobilia(paginas: list[str]) -> set[str]:
    """Linhas de cabecalho/rodape corrido, pelo nucleo sem o numero da pagina."""
    if len(paginas) < PAGINAS_MINIMAS_PARA_FREQUENCIA:
        return set()
    por_nucleo: dict[str, set[str]] = {}
    contagem: dict[str, int] = {}
    for texto in paginas:
        vistos = set()
        for bruta in texto.splitlines():
            linha = bruta.strip()
            if not linha or len(linha) > TAMANHO_MAXIMO_DE_LINHA_DE_MOBILIA:
                continue
            nucleo = nucleo_da_linha(linha)
            if len(nucleo) < TAMANHO_MINIMO_DO_NUCLEO:
                continue
            por_nucleo.setdefault(nucleo, set()).add(linha)
            if nucleo not in vistos:
                vistos.add(nucleo)
                contagem[nucleo] = contagem.get(nucleo, 0) + 1
    minimo = max(2, int(len(paginas) * FRACAO_DE_PAGINAS_PARA_SER_MOBILIA))
    saida: set[str] = set()
    for nucleo, n in contagem.items():
        if n >= minimo:
            saida |= por_nucleo[nucleo]
    return saida


def _remover_sequencias_de_fundo(linha: str, fundo: set[str]) -> tuple[str, int]:
    """Apaga apenas SEQUENCIAS de tokens de fundo, nunca uma ocorrencia solta.

    Assim uma mencao legitima a uma palavra que tambem compoe a marca d'agua
    sobrevive, e a faixa de marca d'agua desaparece.
    """
    if not fundo:
        return linha, 0
    partes = re.split(r"(\s+)", linha)
    saida, buffer_, removidas = [], [], 0
    for parte in partes:
        if parte.strip() and parte in fundo:
            buffer_.append(parte)
            continue
        if parte.strip() == "" and buffer_:
            buffer_.append(parte)
            continue
        if buffer_:
            uteis = [p for p in buffer_ if p.strip()]
            if len(uteis) >= TOKENS_DE_FUNDO_SEGUIDOS or any(
                    token_autossimilar(p) for p in uteis):
                saida.append(" ")
                removidas += len(uteis)
            else:
                saida.extend(buffer_)
            buffer_ = []
        saida.append(parte)
    if buffer_:
        uteis = [p for p in buffer_ if p.strip()]
        if len(uteis) >= TOKENS_DE_FUNDO_SEGUIDOS or any(
                token_autossimilar(p) for p in uteis):
            saida.append(" ")
            removidas += len(uteis)
        else:
            saida.extend(buffer_)
    return "".join(saida), removidas



# --- cabecalho corrido COLADO ao texto do item ---------------------------
# Medido: em 2024 D1 a alternativa E terminava com
#     '... ENEM2024ENEM2024 • CIENCIAS HUMANAS ... • CADERNO 1 • AZUL 27'
# tudo na MESMA linha do texto da alternativa. Remocao por linha inteira nao
# alcanca isso. A deteccao passa a ser por n-grama de palavras com os digitos
# normalizados: uma sequencia de 8 palavras que reaparece em metade ou mais
# das paginas e cabecalho corrido, nao prosa.
#
# Medido nos 12 cadernos: de 5 a 15 octagramas por caderno cruzam o limiar, e
# a inspecao mostrou que todos sao cabecalho. Oito palavras foi escolhido por
# ser improvavel em texto corrido; com 4 o risco de pegar frase legitima sobe.
PALAVRAS_NO_NGRAMA = 8
PADRAO_DIGITOS = re.compile(r"\d+")
# GUARDA: um octagrama de cabecalho pode terminar em 'QUESTAO', porque o
# cabecalho vem colado antes do rotulo do item. Remover a palavra destruiria
# a deteccao do proprio item.
PADRAO_PALAVRA_PROTEGIDA = re.compile(r"^Quest[\u00e3a]o$", re.IGNORECASE)


def ngramas_de_mobilia(paginas: list[str]) -> set[str]:
    """Octagramas de palavras, com digitos normalizados, que sao cabecalho."""
    if len(paginas) < PAGINAS_MINIMAS_PARA_FREQUENCIA:
        return set()
    presenca: dict[str, int] = {}
    for texto in paginas:
        palavras = [PADRAO_DIGITOS.sub("#", w) for w in texto.split()]
        vistos = set()
        for i in range(len(palavras) - PALAVRAS_NO_NGRAMA + 1):
            vistos.add(" ".join(palavras[i:i + PALAVRAS_NO_NGRAMA]))
        for g in vistos:
            presenca[g] = presenca.get(g, 0) + 1
    minimo = max(2, int(len(paginas) * FRACAO_DE_PAGINAS_PARA_SER_MOBILIA))
    return {g for g, n in presenca.items() if n >= minimo}


# EXTENSAO DE BORDA, ancorada.
#
# O octagrama prova que ali ha cabecalho, mas as PRIMEIRAS palavras do
# cabecalho ficam descobertas: a janela que as contem comeca no texto do
# item, que muda de pagina para pagina. Medido em 2024 D1, sobrava
#     '• CIENCIAS HUMANAS E' colado na alternativa E.
#
# A extensao usa tetragramas frequentes, mas SO adjacentes a um octagrama ja
# confirmado. O octagrama e a ancora - caro de obter por acaso; o tetragrama
# so faz crescer uma regiao ja provada. Um tetragrama sozinho nunca remove
# nada. A extensao para sozinha: em 2024 D1 o tetragrama
#     'interacoes. • CIENCIAS HUMANAS'  muda a cada pagina e nao e frequente.
PALAVRAS_NA_EXTENSAO = 4

# A extensao usa um limiar de presenca MENOR que o do octagrama. Razao
# medida: o cabecalho corrido MUDA entre as secoes da prova - no dia 1 e
# 'LINGUAGENS, CODIGOS E SUAS TECNOLOGIAS E REDACAO' na primeira metade e
# 'CIENCIAS HUMANAS E SUAS TECNOLOGIAS' na segunda. Cada variante aparece em
# cerca de metade das paginas e fica exatamente na fronteira do limiar de
# 50%. Como a extensao so age colada a um octagrama ja confirmado, baixar
# este limiar nao cria falso positivo sozinho.
FRACAO_DE_PAGINAS_PARA_EXTENSAO = 0.20


def ngramas_de_extensao(paginas: list[str]) -> set[str]:
    """Tetragramas frequentes. So valem colados a um octagrama confirmado."""
    if len(paginas) < PAGINAS_MINIMAS_PARA_FREQUENCIA:
        return set()
    presenca: dict[str, int] = {}
    for texto in paginas:
        palavras = [PADRAO_DIGITOS.sub("#", w) for w in texto.split()]
        vistos = set()
        for i in range(len(palavras) - PALAVRAS_NA_EXTENSAO + 1):
            vistos.add(" ".join(palavras[i:i + PALAVRAS_NA_EXTENSAO]))
        for g in vistos:
            presenca[g] = presenca.get(g, 0) + 1
    minimo = max(2, int(len(paginas) * FRACAO_DE_PAGINAS_PARA_EXTENSAO))
    return {g for g, n in presenca.items() if n >= minimo}


def _remover_ngramas(texto: str, ngramas: set[str],
                     extensao: set[str] | None = None) -> tuple[str, int]:
    """Apaga as palavras cobertas por um octagrama de mobilia, com extensao.

    A palavra protegida (o rotulo do item) nunca e apagada, mesmo coberta:
    o cabecalho vem colado antes de 'QUESTAO 22' e remove-lo destruiria a
    deteccao do proprio item.
    """
    if not ngramas:
        return texto, 0
    palavras = texto.split()
    if len(palavras) < PALAVRAS_NO_NGRAMA:
        return texto, 0
    normalizadas = [PADRAO_DIGITOS.sub("#", w) for w in palavras]
    coberta = [False] * len(palavras)
    for i in range(len(palavras) - PALAVRAS_NO_NGRAMA + 1):
        if " ".join(normalizadas[i:i + PALAVRAS_NO_NGRAMA]) in ngramas:
            for j in range(i, i + PALAVRAS_NO_NGRAMA):
                coberta[j] = True

    if extensao:
        k = PALAVRAS_NA_EXTENSAO
        mudou = True
        while mudou:
            mudou = False
            for i in range(len(palavras)):
                if coberta[i]:
                    continue
                # a esquerda de uma regiao coberta
                direita = i + 1 < len(palavras) and coberta[i + 1]
                esquerda = i > 0 and coberta[i - 1]
                if not (direita or esquerda):
                    continue
                if PADRAO_PALAVRA_PROTEGIDA.match(palavras[i]):
                    continue
                janelas = []
                if direita and i + k <= len(palavras):
                    janelas.append(" ".join(normalizadas[i:i + k]))
                if esquerda and i - k + 1 >= 0:
                    janelas.append(" ".join(normalizadas[i - k + 1:i + 1]))
                if any(j in extensao for j in janelas):
                    coberta[i] = True
                    mudou = True

    removidas = 0
    saida = []
    for palavra, fora in zip(palavras, coberta):
        if fora and not PADRAO_PALAVRA_PROTEGIDA.match(palavra):
            removidas += 1
            continue
        saida.append(palavra)
    return (" ".join(saida), removidas) if removidas else (texto, 0)



# --- numero de pagina em linha propria -----------------------------------
# Resto medido depois das regras acima: sobrava um '27' colado ao fim da
# alternativa E. O numero esta em LINHA PROPRIA, e a remocao por n-grama
# descarta linhas curtas (menos de 8 palavras), entao ele escapava e so se
# juntava ao texto depois, na linearizacao.
#
# A regra NAO e "linha que so tem numero e lixo" - isso apagaria uma
# resposta numerica legitima. A regra e: existe um deslocamento `o` tal que,
# na maioria das paginas, ha uma linha contendo exatamente o numero
# (indice da pagina + o). Isso e auto-verificavel: so uma numeracao de
# pagina produz essa progressao.
PADRAO_SO_NUMERO = re.compile(r"^[\s\u2022|.\-\u2013\u2014]*(\d{1,3})[\s\u2022|.\-\u2013\u2014]*$")
FRACAO_DE_PAGINAS_NUMERADAS = 0.6


def deslocamento_da_numeracao(paginas: list[str]) -> int | None:
    """Deslocamento entre o indice da pagina e o numero impresso, se houver."""
    if len(paginas) < PAGINAS_MINIMAS_PARA_FREQUENCIA:
        return None
    numeros_por_pagina = []
    for texto in paginas:
        achados = set()
        for linha in texto.splitlines():
            m = PADRAO_SO_NUMERO.match(linha.strip())
            if m:
                achados.add(int(m.group(1)))
        numeros_por_pagina.append(achados)
    melhor, melhor_n = None, 0
    for deslocamento in range(-2, 4):
        n = sum(1 for i, achados in enumerate(numeros_por_pagina)
                if (i + 1 + deslocamento) in achados)
        if n > melhor_n:
            melhor, melhor_n = deslocamento, n
    minimo = len(paginas) * FRACAO_DE_PAGINAS_NUMERADAS
    return melhor if melhor_n >= minimo else None


def _remover_numero_de_pagina(texto: str, esperado: int) -> tuple[str, int]:
    saida, removidas = [], 0
    for linha in texto.splitlines():
        m = PADRAO_SO_NUMERO.match(linha.strip())
        if m and int(m.group(1)) == esperado:
            removidas += 1
            continue
        saida.append(linha)
    return "\n".join(saida), removidas


def _paginas_pypdf(caminho: Path) -> list[str]:
    from pypdf import PdfReader

    return [pagina.extract_text() or "" for pagina in PdfReader(caminho).pages]


def _paginas_pymupdf(caminho: Path) -> list[str]:
    import pymupdf

    documento = pymupdf.open(caminho)
    try:
        return [pagina.get_text() or "" for pagina in documento]
    finally:
        documento.close()


LEITORES = {MOTOR_PYPDF: _paginas_pypdf, MOTOR_PYMUPDF: _paginas_pymupdf}


def ler(caminho: Path, motor: str, *, limpar: bool = True,
        endurecido: bool = True) -> CamadaDeTexto:
    cruas = [normalizar(t) for t in LEITORES[motor](caminho)]
    if limpar:
        cruas, relatorio = limpar_mobilia(cruas, endurecido=endurecido)
    else:
        relatorio = {}
    paginas = tuple(cruas)
    documento = SEPARADOR_PAGINA.join(paginas)
    deslocamentos, posicao = [], 0
    for texto in paginas:
        deslocamentos.append(posicao)
        posicao += len(texto) + len(SEPARADOR_PAGINA)
    return CamadaDeTexto(motor, paginas, documento, tuple(deslocamentos), relatorio)


def legibilidade(camada: CamadaDeTexto) -> float:
    """Fracao de caracteres alfabeticos entre os nao-espaco.

    Mesma definicao usada pelo diagnostico da fase 10.6, para que os numeros
    sejam comparaveis com os que ja estao registrados. Cadernos limpos medem
    0,72-0,94; 2021 D1 mede 0,284.
    """
    nao_espaco = [c for c in camada.documento if not c.isspace()]
    if not nao_espaco:
        return 0.0
    letras = sum(1 for c in nao_espaco if unicodedata.category(c).startswith("L"))
    return letras / len(nao_espaco)


# Codigo de glifo cru. Quando a fonte e subsetada sem ToUnicode CMap, o
# extrator nao tem como saber que caractere o glifo representa e emite o
# proprio indice: '/g50/g3/g70/g82/g85/g72'. Isso NAO e texto, e nenhuma
# quantidade de regex o transforma em texto. Observado em 2021 D1 e D2.
PADRAO_GLIFO_CRU = re.compile(r"/g\d+")

# Caractere de controle C0. Nunca e texto de questao: e byte de glifo cru,
# a mesma doenca do /gNN em outra roupa. Achado varrendo as 665 aprovadas
# depois do endurecimento de fragmentacao - 25 itens o carregavam, e 22
# estao em 2021 D2, o caderno sem ToUnicode:
#
#     '<17><02><0e><05> <07> <10> ... formato que se assemelha a um triangulo'
#
# A trava anterior nao pegava: PADRAO_GLIFO_CRU procura '/gNN', e a fracao
# de letras fica perto do piso porque a parte legitima dilui o lixo.
#
# Tab e quebra de linha ficam de fora: `normalizar` ja os trata, e eles sao
# separadores legitimos. Nao ha limiar aqui - e presenca ou ausencia.
PADRAO_CONTROLE_C0 = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]")

# Piso de legibilidade. NAO e um numero novo: e o mesmo 0,65 ja registrado
# pelo portao da fase 10.6 em var/inep-pilot/phase10_6_recovery_matrix.json.
# Medido la: cadernos limpos 0,72-0,94; 2021 D1 0,284; 2021 D2 0,406.
PISO_DE_LEGIBILIDADE = 0.65


def fracao_legivel(texto: str) -> float:
    """Fracao de caracteres alfabeticos entre os nao-espaco de um trecho."""
    nao_espaco = [c for c in texto if not c.isspace()]
    if not nao_espaco:
        return 0.0
    letras = sum(1 for c in nao_espaco if unicodedata.category(c).startswith("L"))
    return letras / len(nao_espaco)


def texto_ilegivel(texto: str) -> bool:
    """Decide se um trecho e texto ou e lixo de codificacao.

    Duas provas, nesta ordem:

    1. **deterministica** - presenca de ``/gNN`` ou de caractere de controle
       C0. Nao ha limiar envolvido: os dois sao evidencia direta de glifo
       cru vazando para o texto.
    2. **por razao** - fracao de letras abaixo de :data:`PISO_DE_LEGIBILIDADE`,
       em trecho com algum conteudo.

    Existe porque a V2, antes desta trava, declarava COMPLETO um item de 2021
    cuja alternativa B era ``'/g3 /g70/g85/g76/g68/g85/g3'``. Numero maior nao
    e numero melhor: a trava DERRUBA a contagem, de proposito.
    """
    if PADRAO_GLIFO_CRU.search(texto):
        return True
    if PADRAO_CONTROLE_C0.search(texto):
        return True
    limpo = texto.strip()
    if len(limpo) < 12:
        return False
    return fracao_legivel(limpo) < PISO_DE_LEGIBILIDADE


def numeros_de_cabecalho(camada: CamadaDeTexto) -> list[int]:
    return [int(m.group(1)) for m in PADRAO_CABECALHO.finditer(camada.documento)]


def escolher_motor(avaliacoes: dict[str, dict]) -> tuple[str, str]:
    """Regra de escolha, declarada antes de qualquer execucao.

    Ordem lexicografica, na ordem de prioridade que o usuario fixou
    (fidelidade, depois cobertura):

    1. maior numero de itens com reconstrucao ESTRUTURALMENTE COMPLETA
       (cabecalho + corrida A-E integra);
    2. empate -> maior cobertura de cabecalho;
    3. empate -> maior legibilidade;
    4. empate -> ``pypdf``, por ser o motor da V1 (preferir o baseline).

    Nao ha ajuste por ano, nem excecao embutida. Se um caderno novo chegar, a
    mesma regra decide.
    """
    def chave(motor: str) -> tuple:
        a = avaliacoes[motor]
        return (a["completas_estruturais"], a["cabecalhos_distintos"],
                a["legibilidade"], 1 if motor == MOTOR_PYPDF else 0)

    vencedor = max(avaliacoes, key=chave)
    perdedor = next(m for m in avaliacoes if m != vencedor)
    v, p = avaliacoes[vencedor], avaliacoes[perdedor]
    motivo = (f"{vencedor}: completas={v['completas_estruturais']} "
              f"cabecalhos={v['cabecalhos_distintos']} legib={v['legibilidade']:.3f} | "
              f"{perdedor}: completas={p['completas_estruturais']} "
              f"cabecalhos={p['cabecalhos_distintos']} legib={p['legibilidade']:.3f}")
    return vencedor, motivo
