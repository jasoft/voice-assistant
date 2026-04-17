from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from press_to_talk.utils.logging import log, log_llm_prompt, log_multiline

from .cli_wrapper import CLIHistoryStore, CLIRememberStore
from .memory_backends import (
    SIMPLE_EXTENSION_PATH,
    Mem0RememberStore,
    SQLiteFTS5RememberStore,
    _default_match_query,
    _extract_mem0_results,
    _keywords_from_match_query,
    _localize_timestamp_fields,
    _normalize_match_text,
    _quote_match_token,
    _reduce_filter_keywords,
    _sanitize_rewritten_keywords,
    create_mem0_client,
    migrate_mem0_memories_to_sqlite,
)
from .models import (
    BaseHistoryStore,
    BaseRememberStore,
    KeywordRewriter,
    MemoryTranslator,
    RememberItemRecord,
    SessionHistoryRecord,
    StorageConfig,
)
from .sqlite_history import NullHistoryStore, SQLiteHistoryStore, migrate_history_table

APP_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_APP_DB_PATH = APP_ROOT / "data" / "voice_assistant_store.sqlite3"
DEFAULT_HISTORY_DB_PATH = DEFAULT_APP_DB_PATH
DEFAULT_REMEMBER_DB_PATH = DEFAULT_APP_DB_PATH
WORKFLOW_CONFIG_PATH = APP_ROOT / "workflow_config.json"


def env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_workflow_config() -> dict[str, Any]:
    try:
        with WORKFLOW_CONFIG_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _require_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"workflow config missing required section: {path}")
    return value


def _render_prompt_template(template: str, values: dict[str, str]) -> str:
    rendered = str(template or "")
    for key, value in values.items():
        rendered = rendered.replace(f"${{{key}}}", value)
    return rendered


def _workflow_storage_config() -> dict[str, Any]:
    workflow = load_workflow_config()
    storage = workflow.get("storage", {}) if isinstance(workflow, dict) else {}
    return storage if isinstance(storage, dict) else {}


def load_storage_config() -> StorageConfig:
    storage_cfg = _workflow_storage_config()
    sqlite_cfg = storage_cfg.get("sqlite_fts5", {})
    sqlite_cfg = sqlite_cfg if isinstance(sqlite_cfg, dict) else {}
    mem0_cfg = storage_cfg.get("mem0", {})
    mem0_cfg = mem0_cfg if isinstance(mem0_cfg, dict) else {}
    rewrite_cfg = sqlite_cfg.get("query_rewrite", sqlite_cfg.get("groq_query_rewrite", {}))
    rewrite_cfg = rewrite_cfg if isinstance(rewrite_cfg, dict) else {}
    global_max_results = int(storage_cfg.get("max_results", 20))
    raw_app_id = os.environ.get("MEM0_APP_ID")
    app_id = "voice-assistant" if raw_app_id is None else str(raw_app_id).strip()
    configured_backend = str(storage_cfg.get("provider", "mem0")).strip() or "mem0"
    default_model = env_str("PTT_MODEL", "qwen/qwen3-32b")

    config = StorageConfig(
        backend=str(env_str("PTT_REMEMBER_BACKEND", configured_backend)).strip()
        or configured_backend,
        mem0_api_key=env_str("MEM0_API_KEY", "").strip(),
        mem0_user_id=str(env_str("MEM0_USER_ID", "soj")).strip() or "soj",
        mem0_app_id=app_id,
        mem0_min_score=env_float("MEM0_MIN_SCORE", float(mem0_cfg.get("min_score", 0.8))),
        mem0_max_items=max(1, env_int("MEM0_MAX_ITEMS", int(mem0_cfg.get("max_items", global_max_results)))),
        history_db_path=env_str("PTT_HISTORY_DB_PATH", str(DEFAULT_HISTORY_DB_PATH)).strip()
        or str(DEFAULT_HISTORY_DB_PATH),
        remember_db_path=env_str(
            "PTT_REMEMBER_DB_PATH",
            str(sqlite_cfg.get("db_path", str(DEFAULT_REMEMBER_DB_PATH))),
        ).strip()
        or str(DEFAULT_REMEMBER_DB_PATH),
        remember_max_results=max(
            1,
            env_int("PTT_REMEMBER_MAX_RESULTS", int(sqlite_cfg.get("max_results", global_max_results))),
        ),
        query_rewrite_enabled=env_bool(
            "PTT_QUERY_REWRITE_ENABLED",
            env_bool("PTT_GROQ_REWRITE_ENABLED", bool(rewrite_cfg.get("enabled", False))),
        ),
        llm_api_key=env_str("OPENAI_API_KEY", "").strip(),
        llm_base_url=env_str("OPENAI_BASE_URL", "").strip(),
        llm_model=default_model,
        groq_rewrite_enabled=env_bool(
            "PTT_GROQ_REWRITE_ENABLED",
            bool(rewrite_cfg.get("enabled", False)),
        ),
        groq_rewrite_model=str(rewrite_cfg.get("model", "")).strip(),
    )
    safe_config = {
        key: (value if "api_key" not in key else ("***" if value else "None"))
        for key, value in config.__dict__.items()
    }
    log(f"Storage configuration loaded: {json.dumps(safe_config, ensure_ascii=False, indent=2)}")
    return config


class LLMKeywordRewriter:
    def __init__(self, *, api_key: str, llm_model: str, base_url: str = "") -> None:
        self.api_key = api_key.strip()
        self.model = llm_model.strip()
        self.base_url = base_url.strip()
        self._client: Any | None = None

    def _client_instance(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            client_kwargs: dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            self._client = OpenAI(**client_kwargs)
        return self._client

    def rewrite(self, query: str) -> str:
        cleaned_query = str(query or "").strip()
        if not cleaned_query:
            return ""
        workflow = _require_mapping(load_workflow_config(), "workflow")
        prompts = _require_mapping(workflow.get("prompts"), "prompts")
        rewrite_cfg = _require_mapping(prompts.get("query_rewrite"), "prompts.query_rewrite")
        system_prompt = str(rewrite_cfg.get("system_prompt", "")).strip()
        if not system_prompt:
            raise RuntimeError(
                "workflow config missing required section: prompts.query_rewrite.system_prompt"
            )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": cleaned_query},
        ]
        log_llm_prompt("keyword rewrite", messages)
        response = self._client_instance().chat.completions.create(
            model=self.model,
            temperature=0,
            messages=messages,
        )
        content = str(response.choices[0].message.content or "").strip()
        log_multiline("keyword rewrite raw", content)
        text_result = re.sub(r"(?is)<think>.*?</think>", "", content).strip()
        cleaned_keywords: list[str] = []
        if "{" in text_result and "}" in text_result:
            try:
                json_start = text_result.find("{")
                json_end = text_result.rfind("}") + 1
                payload = json.loads(text_result[json_start:json_end])
                keywords = payload.get("keywords", []) if isinstance(payload, dict) else []
                cleaned_keywords = [str(item).strip() for item in keywords if str(item).strip()]
            except Exception:
                pass
        if not cleaned_keywords:
            raw_tokens = re.split(r"[\n,，\s]+", text_result)
            cleaned_keywords = [token.strip() for token in raw_tokens if token.strip()]
        log("keyword rewrite parsed: " + json.dumps(cleaned_keywords, ensure_ascii=False))
        cleaned_keywords = _sanitize_rewritten_keywords(cleaned_keywords, cleaned_query)
        if not cleaned_keywords:
            return _quote_match_token(cleaned_query)
        rewritten_query = " OR ".join(
            _quote_match_token(keyword) for keyword in cleaned_keywords if keyword
        )
        log(f"keyword rewrite match_query: {rewritten_query}")
        return rewritten_query


class GroqKeywordRewriter(LLMKeywordRewriter):
    def __init__(self, *, api_key: str, model: str, base_url: str = "") -> None:
        super().__init__(api_key=api_key, llm_model=model, base_url=base_url)

    def rewrite(self, query: str) -> str:
        cleaned_query = str(query or "").strip()
        if not cleaned_query:
            return ""

        normalize_prompt = (
            "你是一个查询纠错改写器。请在保持原意不变的前提下，修正语音转文字或听写造成的明显错字、错词、同音词错误。"
            "不要扩写，不要解释，只返回 JSON：{\"query\":\"纠正后的问句\"}。"
        )
        normalize_messages = [
            {"role": "system", "content": normalize_prompt},
            {"role": "user", "content": cleaned_query},
        ]
        log_llm_prompt("query normalize", normalize_messages)
        normalize_response = self._client_instance().chat.completions.create(
            model=self.model,
            temperature=0,
            messages=normalize_messages,
        )
        normalize_content = str(normalize_response.choices[0].message.content or "").strip()
        log_multiline("keyword rewrite raw", normalize_content)
        normalized_query = cleaned_query
        try:
            payload = json.loads(normalize_content)
            normalized_query = str(payload.get("query") or cleaned_query).strip() or cleaned_query
        except Exception:
            pass

        keyword_prompt = (
            "你是一个 SQLite FTS5 查询改写器。把用户原始问题拆成 2 到 7 个最可能命中的短关键词或短语。"
            "先保留原句里的核心实体词，再补充少量与原意高度接近、最可能出现在记忆里的常见别称或近义词。"
            "每个关键词都必须尽量短，优先保留实体词，通常 2 到 8 个字，最长不要超过 12 个字。"
            "关键词应该是名词、专有名词、地点、物品或动作，不要把“在哪里”“哪儿呢”“在哪儿”“哪里有”这类纯查询尾巴当成关键词。"
            "只返回 JSON：{\"keywords\":[\"词1\",\"词2\"]}。不要解释，不要补充其它字段。"
        )
        rewrite_messages = [
            {"role": "system", "content": keyword_prompt},
            {"role": "user", "content": normalized_query},
        ]
        log_llm_prompt("keyword rewrite", rewrite_messages)
        rewrite_response = self._client_instance().chat.completions.create(
            model=self.model,
            temperature=0,
            messages=rewrite_messages,
        )
        rewrite_content = str(rewrite_response.choices[0].message.content or "").strip()
        log_multiline("keyword rewrite raw", rewrite_content)
        keywords: list[str] = []
        try:
            payload = json.loads(rewrite_content)
            raw_keywords = payload.get("keywords", []) if isinstance(payload, dict) else []
            keywords = [str(item).strip() for item in raw_keywords if str(item).strip()]
        except Exception:
            pass
        log("keyword rewrite parsed: " + json.dumps(keywords, ensure_ascii=False))
        cleaned_keywords = _sanitize_rewritten_keywords(keywords, normalized_query)
        if not cleaned_keywords:
            return _quote_match_token(normalized_query)
        rewritten_query = " OR ".join(
            _quote_match_token(keyword) for keyword in cleaned_keywords if keyword
        )
        log(f"keyword rewrite match_query: {rewritten_query}")
        return rewritten_query


class LLMMemoryTranslator:
    def __init__(self, *, api_key: str, llm_model: str, base_url: str = "") -> None:
        self.api_key = api_key.strip()
        self.model = llm_model.strip()
        self.base_url = base_url.strip()
        self._client: Any | None = None

    def _client_instance(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            client_kwargs: dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            self._client = OpenAI(**client_kwargs)
        return self._client

    def translate(self, text: str) -> str:
        cleaned_text = str(text or "").strip()
        if not cleaned_text:
            return ""
        workflow = _require_mapping(load_workflow_config(), "workflow")
        prompts = _require_mapping(workflow.get("prompts"), "prompts")
        translate_cfg = _require_mapping(
            prompts.get("memory_translate"), "prompts.memory_translate"
        )
        system_prompt = str(translate_cfg.get("system_prompt", "")).strip()
        if not system_prompt:
            raise RuntimeError(
                "workflow config missing required section: prompts.memory_translate.system_prompt"
            )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": cleaned_text},
        ]
        log_llm_prompt("memory translate", messages)
        response = self._client_instance().chat.completions.create(
            model=self.model,
            temperature=0,
            messages=messages,
        )
        content = str(response.choices[0].message.content or "").strip()
        log_multiline("memory translate raw", content)
        translated_text = re.sub(r"(?is)<think>.*?</think>", "", content).strip() or cleaned_text
        log(f"memory translate parsed: {translated_text}")
        return translated_text


class StorageService:
    def __init__(self, config: StorageConfig, use_cli: bool = True) -> None:
        normalized = StorageConfig(**config.__dict__)
        if not str(normalized.history_db_path).strip():
            normalized.history_db_path = str(DEFAULT_HISTORY_DB_PATH)
        if not str(normalized.remember_db_path).strip():
            normalized.remember_db_path = str(DEFAULT_REMEMBER_DB_PATH)
        self.config = normalized
        if use_cli:
            self._history_store = CLIHistoryStore()
            self._remember_store = CLIRememberStore()
            return
        self._history_store = SQLiteHistoryStore(self.config.history_db_path)
        if self.config.backend == "sqlite_fts5":
            self._remember_store = SQLiteFTS5RememberStore(
                db_path=self.config.remember_db_path,
                max_results=self.config.remember_max_results,
                keyword_rewriter=self.keyword_rewriter(),
            )
        else:
            self._remember_store = Mem0RememberStore(
                api_key=self.config.mem0_api_key,
                user_id=self.config.mem0_user_id,
                app_id=self.config.mem0_app_id,
            )

    @classmethod
    def from_env(cls, use_cli: bool = True) -> "StorageService":
        return cls(load_storage_config(), use_cli=use_cli)

    def close(self) -> None:
        return None

    def keyword_rewriter(self) -> KeywordRewriter | None:
        if not self.config.query_rewrite_enabled or not self.config.llm_api_key.strip():
            return None
        return LLMKeywordRewriter(
            api_key=self.config.llm_api_key,
            llm_model=self.config.llm_model,
            base_url=self.config.llm_base_url,
        )

    def _sqlite_memory_translator(self) -> MemoryTranslator | None:
        if not self.config.llm_api_key.strip():
            return None
        return LLMMemoryTranslator(
            api_key=self.config.llm_api_key,
            llm_model=self.config.llm_model,
            base_url=self.config.llm_base_url,
        )

    def remember_store(self) -> BaseRememberStore:
        return self._remember_store

    def history_store(self) -> BaseHistoryStore:
        return self._history_store
