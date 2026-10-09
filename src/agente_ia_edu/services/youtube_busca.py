"""BUSCA REAL DE VIDEO NO YOUTUBE - §8, e so o que da para fazer certo.

O §8 fecha com a instrucao que manda neste modulo:

    "Se a integracao atual nao permitir busca e validacao confiaveis,
     identifique a limitacao e implemente apenas o que puder ser feito
     corretamente, sem inventar resultados."

O QUE ESTA FEITO
=================
A consulta, as duas chamadas da API v3, a validacao e o descarte. Tudo
deterministico, e tudo exercitado com respostas reais injetadas - sem rede e
sem chave. O cliente HTTP entra por argumento, como nos adapters de provedor.

DUAS CHAMADAS, E NAO UMA
=========================
`search.list` NAO devolve duracao nem o estado de incorporacao. Um sistema que
recomendasse so com ela estaria chutando os dois - e o §8.2 proibe inventar
duracao, e o §8.8 exige que o video possa ser incorporado. Entao:

    search.list  -> os ids dos videos
    videos.list  -> duracao, incorporavel, privacidade, idioma, legenda

NADA E PREENCHIDO POR FALTA
============================
Candidato sem titulo, sem id, sem duracao legivel, nao incorporavel ou nao
publico e DESCARTADO. Nao recebe valor padrao, nao recebe estimativa e nao
entra com um campo vazio. Duracao ilegivel e `None`, nunca 0: zero segundos e
um video de duracao zero, e `None` e nao saber.

E A LEGENDA NAO E PRESUMIDA: `transcricao_disponivel` so e True quando a API
disse que sim. O §8 proibe afirmar que se conhece o conteudo de um trecho sem
transcricao, e a defesa comeca em nao supor que ela existe.

DADO PESSOAL NAO VIAJA
=======================
O §8 e explicito: "nao envie informacoes pessoais do estudante nas consultas".
A consulta e RECUSADA se parecer carregar identificador - e nao apenas
limpada, porque limpar em silencio esconderia o chamador que errou.

TEMPO ASSISTIDO NAO E EVIDENCIA
================================
O §8.13. Este modulo nao sabe medir dominio, nao conhece aluno e nao tem campo
de progresso - e ha teste varrendo a fonte atras dessas palavras.

O QUE FALTA PARA LIGAR
=======================
A credencial. Ver `docs/youtube-o-que-falta-para-ligar.md` e
`disponibilidade()`, que responde a diferenca que mais importa: "nao procurei"
nao pode parecer "procurei e nao achei nada".
"""

from __future__ import annotations

import re
from typing import Any

BASE = "https://www.googleapis.com/youtube/v3"
URL_DA_BUSCA = f"{BASE}/search"
URL_DO_DETALHE = f"{BASE}/videos"

VARIAVEL_DA_CHAVE = "YOUTUBE_API_KEY"

# Quantos candidatos pedir. Pequeno de proposito: o §8.6 manda recomendar UM
# video principal, e uma lista longa so aumenta o custo da validacao humana.
LIMITE_PADRAO = 5

# §8.3: curtos e especificos. "medium" no vocabulario da API e de 4 a 20
# minutos - uma explicacao de um ponto cabe ai, e uma aula de uma hora nao.
DURACAO_PEDIDA = "medium"

# O IDIOMA do produto. Nao e dado do aluno: e a lingua da plataforma.
IDIOMA = "pt"


class BuscaIndisponivel(RuntimeError):
    """A busca real nao esta ligada - e isso nao e "nao achei nada"."""


class ConsultaComDadoPessoal(ValueError):
    """A consulta parecia carregar identificador de aluno. Nao sai daqui."""


# O que NAO pode aparecer numa consulta. Lista conservadora de proposito: um
# falso positivo custa uma consulta reescrita; um falso negativo manda o
# identificador de um adolescente para um servidor de terceiro.
_SINAIS_DE_DADO_PESSOAL = (
    "aluno_", "student", "@", "cpf", "matricula", "matrícula",
    "external_user", "school_id", "user_id",
)


def disponibilidade(*, chave: str | None) -> dict:
    """A busca real esta ligada? E, se nao, o que falta.

    Existe para que "nao procurei" nunca se pareca com "procurei e nao achei
    nada". Ate 2026-10-08 o provedor devolvia `[]` nos dois casos, e quem
    consumisse nao tinha como distinguir.
    """
    if (chave or "").strip():
        return {"busca_real": True, "motivo": None, "o_que_falta": []}
    return {
        "busca_real": False,
        "motivo": f"{VARIAVEL_DA_CHAVE} nao configurada neste ambiente",
        "o_que_falta": [
            f"Definir {VARIAVEL_DA_CHAVE} no ambiente do servidor",
            "Habilitar a YouTube Data API v3 no projeto do Google Cloud",
            "Revisar a cota diaria da API para o volume esperado",
            "Curadoria humana dos candidatos antes de qualquer recomendacao",
        ],
    }


def _recusa_dado_pessoal(consulta: str) -> None:
    baixa = (consulta or "").lower()
    for sinal in _SINAIS_DE_DADO_PESSOAL:
        if sinal in baixa:
            raise ConsultaComDadoPessoal(
                f"a consulta parece carregar dado pessoal ({sinal!r}); "
                f"monte a consulta a partir do CONTEUDO, nao do aluno")


def parametros_de_busca(*, consulta: str, chave: str,
                        limite: int = LIMITE_PADRAO) -> dict[str, Any]:
    """Os parametros de `search.list`.

    `videoEmbeddable=true` e `type=video` nao sao otimizacao: o §8.8 exige
    exibir incorporado, e pedir o que nao pode ser incorporado seria gastar
    cota para descartar depois.
    """
    texto = (consulta or "").strip()
    if not texto:
        raise ValueError("consulta vazia")
    _recusa_dado_pessoal(texto)
    return {
        "part": "snippet",
        "q": texto,
        "type": "video",
        "videoEmbeddable": "true",
        "videoDuration": DURACAO_PEDIDA,
        "relevanceLanguage": IDIOMA,
        "safeSearch": "strict",
        "maxResults": max(1, min(int(limite), 25)),
        "key": chave,
    }


def ids_dos_videos(busca: dict) -> list[str]:
    """Os ids dos itens que SAO video.

    `search.list` mistura canal e playlist no mesmo `items`. Tratar tudo como
    video faria um canal virar uma recomendacao de video.
    """
    saida = []
    for item in (busca or {}).get("items") or []:
        ident = item.get("id") or {}
        if ident.get("kind") != "youtube#video":
            continue
        vid = str(ident.get("videoId") or "").strip()
        if vid and vid not in saida:
            saida.append(vid)
    return saida


def parametros_de_detalhe(ids: list[str], *, chave: str) -> dict[str, Any]:
    return {
        "part": "contentDetails,status,snippet",
        "id": ",".join(ids),
        "key": chave,
    }


_ISO = re.compile(r"^P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def duracao_em_segundos(iso: str | None) -> int | None:
    """`PT7M31S` -> 451. Ilegivel -> None, nunca 0.

    Zero segundos e um video de duracao zero; None e nao saber a duracao. O
    §8.2 proibe inventar duracao, e um 0 silencioso e uma duracao inventada.
    """
    texto = (iso or "").strip()
    if not texto:
        return None
    achado = _ISO.match(texto)
    if not achado:
        return None
    dias, horas, minutos, segundos = (int(g or 0) for g in achado.groups())
    total = dias * 86400 + horas * 3600 + minutos * 60 + segundos
    return total or None


def url_de_embutir(video_id: str) -> str:
    """O endereco do player oficial, SEM autoplay.

    §8.9: nao iniciar reproducao automaticamente. E sem `controls=0`: o §8
    manda nao prometer esconder os controles nativos, e fingir que esconde
    seria a promessa que ele proibe.
    """
    return f"https://www.youtube.com/embed/{video_id}"


def url_do_video(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def candidatos(busca: dict, detalhe: dict) -> list[dict[str, Any]]:
    """Os candidatos validos, dos mais curtos para os mais longos.

    O que falta, falta: titulo, id, duracao legivel, incorporavel e publico
    sao obrigatorios, e quem nao tiver todos sai da lista. O §8.2 proibe
    inventar, e preencher por falta e inventar com outro nome.
    """
    por_id: dict[str, dict] = {}
    for item in (detalhe or {}).get("items") or []:
        ident = str(item.get("id") or "").strip()
        if ident:
            por_id[ident] = item

    saida: list[dict[str, Any]] = []
    for item in (busca or {}).get("items") or []:
        ident = item.get("id") or {}
        if ident.get("kind") != "youtube#video":
            continue
        vid = str(ident.get("videoId") or "").strip()
        snippet = item.get("snippet") or {}
        titulo = str(snippet.get("title") or "").strip()
        if not vid or not titulo:
            continue

        d = por_id.get(vid)
        if not d:
            # Sem detalhe nao se sabe duracao nem incorporacao. Entrar assim
            # seria recomendar sem saber se o video toca dentro do Nucleo.
            continue
        status = d.get("status") or {}
        if status.get("embeddable") is not True:
            continue
        if str(status.get("privacyStatus") or "").lower() != "public":
            continue
        detalhes = d.get("contentDetails") or {}
        segundos = duracao_em_segundos(detalhes.get("duration"))
        if segundos is None:
            continue

        saida.append({
            "source": "YOUTUBE",
            "external_id": vid,
            "title": titulo,
            "description": str(snippet.get("description") or "").strip(),
            "channel_or_author": str(snippet.get("channelTitle") or "").strip(),
            "url": url_do_video(vid),
            "embed_url": url_de_embutir(vid),
            "thumbnail_url": (((snippet.get("thumbnails") or {}).get("high")
                               or {}).get("url") or None),
            "duration_seconds": segundos,
            "language": ((d.get("snippet") or {}).get("defaultAudioLanguage")
                         or None),
            # §8: sem legenda, nao se afirma conhecer um trecho. Só True
            # quando a API disse que sim - ausencia nao e sim.
            "transcricao_disponivel": str(
                detalhes.get("caption") or "false").lower() == "true",
        })

    # §8.3: curtos primeiro. A ordem e por duracao e, no empate, pelo id -
    # deterministica, para que duas buscas iguais devolvam a mesma ordem.
    saida.sort(key=lambda c: (c["duration_seconds"], c["external_id"]))
    return saida


async def buscar(consulta: str, *, chave: str | None, http,
                 limite: int = LIMITE_PADRAO) -> list[dict[str, Any]]:
    """A busca real: `search.list` e, se houve video, `videos.list`.

    `http` e injetado - qualquer objeto com `get(url, params=...)` devolvendo
    o JSON. E assim que este caminho e testado sem rede e sem chave, e e a
    mesma convencao dos adapters de provedor.

    Sem chave, LEVANTA. Devolver `[]` faria "nao procurei" e "nao achei"
    virarem a mesma resposta.
    """
    if not (chave or "").strip():
        d = disponibilidade(chave=chave)
        raise BuscaIndisponivel(d["motivo"])

    busca = await http.get(URL_DA_BUSCA,
                           params=parametros_de_busca(consulta=consulta,
                                                      chave=chave,
                                                      limite=limite))
    ids = ids_dos_videos(busca or {})
    if not ids:
        # Nenhum video: nao ha o que detalhar, e gastar a segunda chamada
        # seria pedir detalhe de lista vazia.
        return []
    detalhe = await http.get(URL_DO_DETALHE,
                             params=parametros_de_detalhe(ids, chave=chave))
    return candidatos(busca or {}, detalhe or {})


__all__ = [
    "BASE",
    "BuscaIndisponivel",
    "ConsultaComDadoPessoal",
    "DURACAO_PEDIDA",
    "LIMITE_PADRAO",
    "URL_DA_BUSCA",
    "URL_DO_DETALHE",
    "VARIAVEL_DA_CHAVE",
    "buscar",
    "candidatos",
    "disponibilidade",
    "duracao_em_segundos",
    "ids_dos_videos",
    "parametros_de_busca",
    "parametros_de_detalhe",
    "url_de_embutir",
    "url_do_video",
]
