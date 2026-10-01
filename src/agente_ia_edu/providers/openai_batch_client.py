# src/agente_ia_edu/providers/openai_batch_client.py
"""Cliente fino da Batch API da OpenAI (upload de arquivo, criacao de lote,
consulta de status, download de resultado), usando a SDK oficial (versao
instalada ja suporta client.files/client.batches). Usado pelos 3 estagios
de correcao em massa (services/mass_correction_batch.py) - nenhuma logica
de negocio aqui, so a mecanica de chamada da Batch API em si.

Cada funcao constroi seu proprio AsyncOpenAI(api_key=...) - mesmo padrao
que providers/adapters/openai.py's _create_client() ja usa - em vez de
receber um client pronto, para manter a assinatura simples e nao acoplar
quem chama a um client de longa duracao."""

from __future__ import annotations

import json

from openai import AsyncOpenAI


async def upload_batch_file(lines: list[dict], *, api_key: str) -> str:
    jsonl_bytes = "\n".join(json.dumps(line, ensure_ascii=False) for line in lines).encode("utf-8")
    client = AsyncOpenAI(api_key=api_key)
    file_object = await client.files.create(
        file=("batch_input.jsonl", jsonl_bytes, "application/jsonl"), purpose="batch",
    )
    return file_object.id


async def create_batch(input_file_id: str, *, api_key: str) -> dict:
    client = AsyncOpenAI(api_key=api_key)
    batch = await client.batches.create(
        input_file_id=input_file_id, endpoint="/v1/chat/completions", completion_window="24h",
    )
    return batch.model_dump()


async def get_batch(batch_id: str, *, api_key: str) -> dict:
    client = AsyncOpenAI(api_key=api_key)
    batch = await client.batches.retrieve(batch_id)
    return batch.model_dump()


async def download_file_lines(file_id: str, *, api_key: str) -> list[dict]:
    client = AsyncOpenAI(api_key=api_key)
    content = await client.files.content(file_id)
    return [json.loads(line) for line in content.text.splitlines() if line.strip()]
