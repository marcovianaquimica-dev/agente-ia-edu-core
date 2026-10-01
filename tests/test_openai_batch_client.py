# tests/test_openai_batch_client.py
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from agente_ia_edu.providers.openai_batch_client import (
    create_batch,
    download_file_lines,
    get_batch,
    upload_batch_file,
)


class OpenAIBatchClientTests(unittest.IsolatedAsyncioTestCase):
    @patch("agente_ia_edu.providers.openai_batch_client.AsyncOpenAI")
    async def test_upload_batch_file_serializes_lines_as_jsonl_and_returns_file_id(self, mock_cls):
        mock_client = MagicMock()
        mock_client.files.create = AsyncMock(return_value=MagicMock(id="file-abc123"))
        mock_cls.return_value = mock_client

        file_id = await upload_batch_file(
            [{"custom_id": "a", "body": {"x": 1}}, {"custom_id": "b", "body": {"x": 2}}],
            api_key="sk-test",
        )

        self.assertEqual(file_id, "file-abc123")
        call_kwargs = mock_client.files.create.call_args.kwargs
        self.assertEqual(call_kwargs["purpose"], "batch")
        uploaded_bytes = call_kwargs["file"][1]
        lines = uploaded_bytes.decode("utf-8").strip().split("\n")
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0]), {"custom_id": "a", "body": {"x": 1}})

    @patch("agente_ia_edu.providers.openai_batch_client.AsyncOpenAI")
    async def test_create_batch_posts_the_right_endpoint_and_window(self, mock_cls):
        mock_client = MagicMock()
        mock_batch = MagicMock()
        mock_batch.model_dump.return_value = {"id": "batch-xyz", "status": "validating"}
        mock_client.batches.create = AsyncMock(return_value=mock_batch)
        mock_cls.return_value = mock_client

        result = await create_batch("file-abc123", api_key="sk-test")

        self.assertEqual(result, {"id": "batch-xyz", "status": "validating"})
        call_kwargs = mock_client.batches.create.call_args.kwargs
        self.assertEqual(call_kwargs["input_file_id"], "file-abc123")
        self.assertEqual(call_kwargs["endpoint"], "/v1/chat/completions")
        self.assertEqual(call_kwargs["completion_window"], "24h")

    @patch("agente_ia_edu.providers.openai_batch_client.AsyncOpenAI")
    async def test_get_batch_returns_the_raw_status_object(self, mock_cls):
        mock_client = MagicMock()
        mock_batch = MagicMock()
        mock_batch.model_dump.return_value = {
            "id": "batch-xyz", "status": "completed", "output_file_id": "file-out",
        }
        mock_client.batches.retrieve = AsyncMock(return_value=mock_batch)
        mock_cls.return_value = mock_client

        result = await get_batch("batch-xyz", api_key="sk-test")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["output_file_id"], "file-out")
        mock_client.batches.retrieve.assert_called_once_with("batch-xyz")

    @patch("agente_ia_edu.providers.openai_batch_client.AsyncOpenAI")
    async def test_download_file_lines_parses_each_line_as_json(self, mock_cls):
        mock_client = MagicMock()
        mock_content = MagicMock()
        mock_content.text = (
            json.dumps({"custom_id": "a", "response": {"body": {"ok": 1}}})
            + "\n"
            + json.dumps({"custom_id": "b", "response": {"body": {"ok": 2}}})
            + "\n"
        )
        mock_client.files.content = AsyncMock(return_value=mock_content)
        mock_cls.return_value = mock_client

        lines = await download_file_lines("file-out", api_key="sk-test")

        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["custom_id"], "a")
        self.assertEqual(lines[1]["response"]["body"]["ok"], 2)


if __name__ == "__main__":
    unittest.main()
