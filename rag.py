"""Retrieval-Augmented Generation support for the e-commerce assistant."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.api import ClientAPI
from chromadb.config import Settings
from chromadb.utils import embedding_functions
import google.generativeai as genai
from loguru import logger

from pipecat.frames.frames import Frame, LLMRunFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class GeminiEmbeddingFunction(embedding_functions.EmbeddingFunction):
    """Embedding function that uses Google Gemini embeddings."""

    def __init__(self, *, model: str):
        self._model = model

    def __call__(self, texts: List[str]) -> List[List[float]]:
        embeddings: List[List[float]] = []
        for text in texts:
            payload = text if text and text.strip() else " "
            try:
                response = genai.embed_content(model=self._model, content=payload)
            except Exception as exc:  # pragma: no cover - network failure handling
                logger.error("Gemini embedding request failed: {exc}")
                raise
            embedding = response.get("embedding")
            if embedding is None:
                raise RuntimeError("Gemini embedding response did not include embeddings")
            embeddings.append(embedding)
        return embeddings


class RAGService:
    """Loads JSON data and provides retrieval over a ChromaDB collection."""

    def __init__(
        self,
        *,
        data_dir: Path,
        api_key: Optional[str],
        embed_model: str,
        collection_name: str = "ecommerce_knowledge",
        persist_dir: Optional[Path] = None,
    ) -> None:
        self._collection = None
        self._enabled = False
        self._data_dir = data_dir
        self._collection_name = collection_name
        self._persist_dir = persist_dir
        self._embed_model = embed_model

        if not api_key:
            logger.warning("RAG disabled: GOOGLE_API_KEY not provided for embeddings.")
            return

        try:
            genai.configure(api_key=api_key)
        except Exception as exc:  # pragma: no cover - configuration failure is rare
            logger.error(f"Failed to configure Google Generative AI SDK: {exc}")
            return

        self._client = self._create_client()
        embedding_function = GeminiEmbeddingFunction(model=self._embed_model)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            embedding_function=embedding_function,
        )
        self._reload_documents()
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled and self._collection is not None

    def _create_client(self) -> ClientAPI:
        if self._persist_dir:
            settings = Settings(
                chroma_db_impl="duckdb+parquet",
                persist_directory=str(self._persist_dir),
                anonymized_telemetry=False,
            )
        else:
            settings = Settings(anonymized_telemetry=False)
        return chromadb.Client(settings)

    def _reload_documents(self) -> None:
        if not self._collection:
            return

        documents, metadatas, ids = self._load_documents()
        try:
            self._collection.delete(where={})
        except Exception:  # pragma: no cover - delete may fail if empty
            pass

        if not documents:
            logger.warning("No JSON documents found for RAG in %s", self._data_dir)
            return

        self._collection.add(documents=documents, metadatas=metadatas, ids=ids)
        logger.info("Loaded %d documents into ChromaDB collection '%s'", len(documents), self._collection_name)

    def _load_documents(self) -> tuple[List[str], List[Dict[str, Any]], List[str]]:
        documents: List[str] = []
        metadatas: List[Dict[str, Any]] = []
        ids: List[str] = []

        if not self._data_dir.exists():
            logger.warning("RAG data directory does not exist: %s", self._data_dir)
            return documents, metadatas, ids

        for json_file in sorted(self._data_dir.glob("*.json")):
            try:
                raw = json.loads(json_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                logger.error("Failed to parse %s: %s", json_file, exc)
                continue

            if not isinstance(raw, list):
                logger.warning("Skipping %s because it does not contain a list of records", json_file)
                continue

            for index, record in enumerate(raw):
                if not isinstance(record, dict):
                    continue

                source = json_file.stem
                identifier, identifier_key = self._resolve_identifier(record, source, index)
                document_text = json.dumps({"source": source, **record}, ensure_ascii=False)

                metadata: Dict[str, Any] = {
                    "source": source,
                    "identifier": identifier,
                    "raw_json": json.dumps(record, ensure_ascii=False),
                }
                if identifier_key:
                    metadata["id_key"] = identifier_key

                documents.append(document_text)
                metadatas.append(metadata)
                ids.append(f"{source}:{identifier}")

        return documents, metadatas, ids

    @staticmethod
    def _resolve_identifier(record: Dict[str, Any], source: str, index: int) -> tuple[str, Optional[str]]:
        for key, value in record.items():
            if key.endswith("_id") and value is not None:
                return str(value), key
        return f"{source}_{index}", None

    async def build_context(self, query: str, *, top_k: int) -> Optional[str]:
        if not self.enabled:
            logger.info("RAG build_context skipped: service disabled")
            return None

        question = query.strip()
        if not question:
            logger.debug("RAG build_context skipped: empty query after stripping input")
            return None

        try:
            result = await asyncio.to_thread(
                self._collection.query,
                query_texts=[question],
                n_results=top_k,
                include=["documents", "metadatas"],
            )
        except Exception as exc:  # pragma: no cover - network failure handling
            logger.error("ChromaDB query failed: %s", exc)
            return None

        documents_batches = result.get("documents") or []
        metadatas_batches = result.get("metadatas") or []
        if not documents_batches or not documents_batches[0]:
            logger.info("RAG build_context: no documents retrieved for query '%s'", question)
            return None

        documents = documents_batches[0]
        metadatas = metadatas_batches[0]

        entries: List[str] = []
        for idx, (doc_text, metadata) in enumerate(zip(documents, metadatas), start=1):
            metadata = metadata or {}
            pretty_payload = self._pretty_metadata(metadata, fallback=doc_text)
            header_parts = [f"Source: {metadata.get('source', 'unknown')}"]
            identifier = metadata.get("identifier")
            if identifier:
                header_parts.append(f"Identifier: {identifier}")
            entry_lines = [f"{idx}. {' | '.join(header_parts)}", "   Data:"]
            for line in pretty_payload.splitlines():
                entry_lines.append(f"     {line}")
            entries.append("\n".join(entry_lines))

        if not entries:
            return None

        context_lines = [
            "Use the following e-commerce records when answering the customer.",
            "Only rely on these facts; if information is missing, clearly state that.",
            f"User question: {question}",
            "",
            *entries,
        ]
        logger.info(
            "RAG build_context: returning %d context entries for query '%s'", len(entries), question
        )
        return "\n".join(context_lines)

    @staticmethod
    def _pretty_metadata(metadata: Dict[str, Any], *, fallback: str) -> str:
        raw_json = metadata.get("raw_json")
        if not raw_json:
            return fallback
        try:
            parsed = json.loads(raw_json)
            return json.dumps(parsed, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            return raw_json


class RAGAugmenter(FrameProcessor):
    """Pipeline processor that injects retrieved context before LLM inference."""

    def __init__(self, context: LLMContext, rag_service: RAGService, *, top_k: int = 4) -> None:
        super().__init__(name="RAGAugmenter")
        self._context = context
        self._rag_service = rag_service
        self._top_k = max(1, top_k)
        self._last_user_message: Optional[str] = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMRunFrame) and direction == FrameDirection.DOWNSTREAM:
            await self._handle_llm_run()
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    async def _handle_llm_run(self) -> None:
        if not self._rag_service.enabled:
            logger.info("RAGAugmenter: service disabled; skipping retrieval")
            self._last_user_message = None
            return

        latest_query = self._latest_user_message()
        if not latest_query:
            logger.debug("RAGAugmenter: no user message available; skipping retrieval")
            return

        if latest_query == self._last_user_message:
            logger.debug("RAGAugmenter: skipping retrieval because query is unchanged")
            return

        self._remove_existing_retrieval_messages()
        self._last_user_message = latest_query

        logger.info(
            "RAGAugmenter: running retrieval for query '%s' with top_k=%d",
            latest_query,
            self._top_k,
        )
        context_text = await self._rag_service.build_context(latest_query, top_k=self._top_k)
        if not context_text:
            logger.info("RAGAugmenter: no retrieval context found for query '%s'", latest_query)
            return

        self._context.add_message(
            {
                "role": "system",
                "name": "retrieval_context",
                "content": context_text,
            }
        )
        logger.info("RAGAugmenter: retrieval context injected for query '%s'", latest_query)

    def _latest_user_message(self) -> Optional[str]:
        for message in reversed(self._context.get_messages()):
            if isinstance(message, dict) and message.get("role") == "user":
                content = message.get("content")
                if content is None and "parts" in message:
                    content = message["parts"]
                if content is None and "text" in message:
                    content = message["text"]
                text = self._normalise_content(content)
                if text:
                    return text
        return None

    @staticmethod
    def _normalise_content(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text" and item.get("text"):
                        parts.append(str(item["text"]))
                    elif item.get("parts"):
                        nested = RAGAugmenter._normalise_content(item.get("parts"))
                        if nested:
                            parts.append(nested)
                elif isinstance(item, str):
                    parts.append(item)
            return " ".join(part.strip() for part in parts if part).strip()
        if isinstance(content, dict):
            text = content.get("text")
            if isinstance(text, str):
                return text.strip()
            parts = content.get("parts")
            if isinstance(parts, list):
                return RAGAugmenter._normalise_content(parts)
        return ""

    def _remove_existing_retrieval_messages(self) -> None:
        messages = self._context.get_messages()
        indices_to_remove = [
            index
            for index, msg in enumerate(messages)
            if isinstance(msg, dict) and msg.get("role") == "system" and msg.get("name") == "retrieval_context"
        ]
        for index in reversed(indices_to_remove):
            del messages[index]

