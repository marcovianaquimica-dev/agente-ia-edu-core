"""Como um arquivo de documento ENTRA no corpus.

A Fase 2 implementa um unico caminho de entrada, ``LOCAL_PATH``: o piloto e
operado por API e scripts na mesma infraestrutura, e os livros do acervo
chegam a 175 MB - atravessar HTTP com um arquivo que a maquina ja tem nao
serve a ninguem.

Mas ``LOCAL_PATH`` NAO e a interface conceitual do Cerebro. Este modulo
termina sempre num ``ResolvedDocumentFile``, e e esse tipo que
``KnowledgeSourceService.register_document`` aceita - nunca um caminho cru.
Quando a tela de administracao existir, ``UPLOAD`` grava o temporario e
constroi o MESMO tipo; o service, o modelo ``KnowledgeDocument`` e a
idempotencia por hash seguem intactos. O fluxo

    entrada validada -> MaterialStorage.store() -> hash -> KnowledgeDocument

e o mesmo para os dois.

SUPERFICIE DE RISCO. Aceitar um caminho do chamador tem forma de path
traversal, e por isso a validacao e explicita e fecha por omissao:

  * sem raiz configurada, nenhum caminho e aceito - nunca ha default;
  * a decisao e sobre o caminho RESOLVIDO (``Path.resolve()``), nao sobre o
    texto: uma checagem textual de ``..`` seria contornavel e ainda
    rejeitaria caminhos legitimos;
  * resolver segue symlink, e e isso que faz um atalho para fora da raiz ser
    rejeitado sem nenhuma regra especial sobre symlinks;
  * exige arquivo existente e regular;
  * exige extensao conhecida;
  * exige tamanho dentro do limite configurado.

Acesso e restrito a platform admin na camada de rota.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Os mesmos formatos que o parser autoral da PHASE 26 ja sabe ler
#: (``SUPPORTED_SUFFIXES`` em ``authorial_material_ingestion.py``).
ALLOWED_SUFFIXES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}

DEFAULT_MAX_DOCUMENT_MB = 512

_ROOT_ENV = "KNOWLEDGE_ENGINE_DOCUMENT_ROOT"
_MAX_MB_ENV = "KNOWLEDGE_ENGINE_MAX_DOCUMENT_MB"


@dataclass(frozen=True)
class ResolvedDocumentFile:
    """Um arquivo JA validado e normalizado, qualquer que tenha sido a entrada.

    E a fronteira entre "como o arquivo chegou" e "o que o corpus faz com
    ele". ``ingress`` registra a procedencia (``LOCAL_PATH`` hoje, ``UPLOAD``
    quando existir) para auditoria; nenhuma regra do service ramifica nesse
    valor - se um dia ramificar, o desacoplamento foi perdido.
    """

    path: Path
    original_filename: str
    mime_type: str
    size_bytes: int
    ingress: str


class DocumentIngressError(ValueError):
    """Arquivo recusado na entrada. ``code`` e estavel e vira resposta HTTP."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def configured_document_root() -> Path | None:
    """A raiz autorizada, resolvida, ou ``None`` se nao houver configuracao.

    ``None`` nao e um default permissivo: e a recusa de adivinhar. Cair no
    diretorio corrente transformaria a maquina inteira em raiz autorizada.
    """
    raw = os.getenv(_ROOT_ENV, "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def configured_max_document_bytes() -> int:
    """Limite de tamanho, configuravel; valor invalido cai no default.

    Um limite ilegivel nao deve derrubar o endpoint nem - pior - virar
    "sem limite".
    """
    raw = os.getenv(_MAX_MB_ENV, "").strip()
    megabytes = DEFAULT_MAX_DOCUMENT_MB
    if raw:
        try:
            parsed = int(raw)
        except ValueError:
            parsed = 0
        if parsed > 0:
            megabytes = parsed
    return megabytes * 1024 * 1024


def resolve_local_path(
    raw_path: str,
    *,
    root: Path,
    max_bytes: int | None = None,
) -> ResolvedDocumentFile:
    """Valida ``raw_path`` contra ``root`` e devolve o arquivo normalizado."""
    if not raw_path or not raw_path.strip():
        raise DocumentIngressError("EMPTY_PATH", "o caminho do documento esta vazio")

    limit = configured_max_document_bytes() if max_bytes is None else max_bytes
    authorized_root = root.expanduser().resolve()

    candidate = Path(raw_path.strip()).expanduser()
    if not candidate.is_absolute():
        candidate = authorized_root / candidate

    # resolve(strict=False) segue symlinks e normaliza '..' sem exigir que o
    # alvo exista - a existencia e checada depois, com uma mensagem propria.
    resolved = candidate.resolve()

    # A unica regra de escopo, aplicada sobre o caminho resolvido. Cobre
    # traversal e symlink de uma vez: um atalho dentro da raiz que aponte para
    # fora resolve para fora e cai aqui.
    if resolved != authorized_root and not resolved.is_relative_to(authorized_root):
        raise DocumentIngressError(
            "PATH_OUTSIDE_ROOT",
            "o caminho resolvido esta fora da raiz autorizada de documentos",
        )

    if not resolved.exists():
        raise DocumentIngressError("FILE_NOT_FOUND", "o arquivo indicado nao existe")
    if not resolved.is_file():
        raise DocumentIngressError(
            "NOT_A_REGULAR_FILE", "o caminho indicado nao e um arquivo regular"
        )

    mime_type = ALLOWED_SUFFIXES.get(resolved.suffix.lower())
    if mime_type is None:
        raise DocumentIngressError(
            "UNSUPPORTED_FILE_TYPE",
            f"extensao nao suportada: {resolved.suffix or '(nenhuma)'}; "
            f"esperado um de {sorted(ALLOWED_SUFFIXES)}",
        )

    size_bytes = resolved.stat().st_size
    if size_bytes > limit:
        raise DocumentIngressError(
            "FILE_TOO_LARGE",
            f"o arquivo tem {size_bytes} bytes e o limite configurado e {limit}",
        )

    return ResolvedDocumentFile(
        path=resolved,
        original_filename=resolved.name,
        mime_type=mime_type,
        size_bytes=size_bytes,
        ingress="LOCAL_PATH",
    )
