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
    APIToken,
    BaseHistoryStore,
    BaseRememberStore,
    EmbeddingClient,
    KeywordRewriter,
    MemoryTranslator,
    RememberEntry,
    RememberItemRecord,
    SessionHistory,
    SessionHistoryRecord,
    StorageConfig,
    User,
    db,
)
from .providers import Mem0RememberStore, SQLiteFTS5RememberStore
from .sqlite_history import NullHistoryStore, PeeweeHistoryStore

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


_storage_config_logged = False


def reset_storage_config_logged() -> None:
    """Reset the configuration logging flag. Used primarily for testing."""
    global _storage_config_logged
    _storage_config_logged = False


def load_storage_config(
    user_id_override: str | None = None,
    api_key_override: str | None = None,
) -> StorageConfig:
    global _storage_config_logged
    workflow_cfg = load_workflow_config()
    storage_cfg = workflow_cfg.get("storage", {}) if isinstance(workflow_cfg, dict) else {}
    storage_cfg = storage_cfg if isinstance(storage_cfg, dict) else {}
    sqlite_cfg = storage_cfg.get("sqlite_fts5", {})
    sqlite_cfg = sqlite_cfg if isinstance(sqlite_cfg, dict) else {}
    mem0_cfg = workflow_cfg.get("mem0", {}) if isinstance(workflow_cfg, dict) else {}
    mem0_cfg = mem0_cfg if isinstance(mem0_cfg, dict) else {}
    rewrite_cfg = sqlite_cfg.get("query_rewrite", sqlite_cfg.get("groq_query_rewrite", {}))
    rewrite_cfg = rewrite_cfg if isinstance(rewrite_cfg, dict) else {}
    embedding_cfg = sqlite_cfg.get("embedding_search", {})
    embedding_cfg = embedding_cfg if isinstance(embedding_cfg, dict) else {}
    reranker_cfg = sqlite_cfg.get("reranker", {})
    reranker_cfg = reranker_cfg if isinstance(reranker_cfg, dict) else {}
    global_max_results = int(storage_cfg.get("max_results", 20))
    raw_app_id = os.environ.get("MEM0_APP_ID")
    app_id = "voice-assistant" if raw_app_id is None else str(raw_app_id).strip()
    configured_backend = str(storage_cfg.get("provider", "mem0")).strip() or "mem0"
    default_model = env_str("PTT_MODEL", "qwen/qwen3-32b")

    h_path = env_str("PTT_HISTORY_DB_PATH", str(DEFAULT_HISTORY_DB_PATH)).strip() or str(DEFAULT_HISTORY_DB_PATH)
    r_path = env_str(
        "PTT_REMEMBER_DB_PATH",
        str(sqlite_cfg.get("db_path", str(DEFAULT_REMEMBER_DB_PATH))),
    ).strip() or str(DEFAULT_REMEMBER_DB_PATH)

    # Normalize paths to be absolute
    h_path_obj = Path(h_path)
    if not h_path_obj.is_absolute():
        h_path_obj = (APP_ROOT / h_path_obj).resolve()
    
    r_path_obj = Path(r_path)
    if not r_path_obj.is_absolute():
        r_path_obj = (APP_ROOT / r_path_obj).resolve()

    # Resolve user identities from their respective config blocks
    local_config_user_id = str(storage_cfg.get("user_id", sqlite_cfg.get("user_id", "default"))).strip()
    mem0_config_user_id = str(mem0_cfg.get("user_id", "default")).strip()

    # The effective base ID: either override, or environment, or config, or default
    # Priority: Override > Env PTT_USER_ID > Config > default
    base_user_id = user_id_override or str(env_str("PTT_USER_ID", local_config_user_id)).strip()

    config = StorageConfig(
        backend=str(env_str("PTT_REMEMBER_BACKEND", configured_backend)).strip()
        or configured_backend,
        user_id=base_user_id,
        user_token=api_key_override
        or env_str("PTT_API_KEY", "").strip()
        or env_str("PTT_USER_API_KEY", "").strip()
        or None,
        mem0_api_key=env_str("MEM0_API_KEY", "").strip(),
        mem0_user_id=user_id_override or str(env_str("PTT_USER_ID", str(env_str("MEM0_USER_ID", mem0_config_user_id)))).strip(),
        mem0_app_id=app_id,
        mem0_min_score=env_float("MEM0_MIN_SCORE", float(mem0_cfg.get("min_score", 0.8))),
        mem0_max_items=max(1, env_int("MEM0_MAX_ITEMS", int(mem0_cfg.get("max_items", global_max_results)))),
        history_db_path=str(h_path_obj),
        remember_db_path=str(r_path_obj),
        remember_max_results=max(
            1,
            env_int("PTT_REMEMBER_MAX_RESULTS", int(sqlite_cfg.get("max_results", global_max_results))),
        ),
        keyword_search_enabled=env_bool("PTT_ENABLE_KEYWORD_SEARCH", True),
        semantic_search_enabled=env_bool("PTT_ENABLE_SEMANTIC_SEARCH", True),
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
        embedding_search_enabled=env_bool(
            "PTT_EMBEDDING_SEARCH_ENABLED",
            bool(embedding_cfg.get("enabled", False)),
        ),
        embedding_base_url=env_str(
            "PTT_EMBEDDING_BASE_URL",
            str(embedding_cfg.get("base_url", "http://127.0.0.1:1234/v1")),
        ).strip(),
        embedding_api_key=env_str(
            "PTT_EMBEDDING_API_KEY",
            str(embedding_cfg.get("api_key", "lm-studio")),
        ).strip(),
        embedding_model=env_str(
            "PTT_EMBEDDING_MODEL",
            str(embedding_cfg.get("model", "text-embedding-bge-m3")),
        ).strip(),
        embedding_max_results=max(
            1,
            env_int("PTT_EMBEDDING_MAX_RESULTS", int(embedding_cfg.get("max_results", 5))),
        ),
        embedding_min_score=env_float(
            "PTT_EMBEDDING_MIN_SCORE",
            float(embedding_cfg.get("min_score", 0.45)),
        ),
        embedding_context_min_score=env_float(
            "PTT_EMBEDDING_CONTEXT_MIN_SCORE",
            float(embedding_cfg.get("context_min_score", 0.55)),
        ),
        reranker_enabled=env_bool(
            "PTT_RERANKER_ENABLED",
            bool(reranker_cfg.get("enabled", False)),
        ),
        reranker_api_key=env_str(
            "JINA_API_KEY",
            str(reranker_cfg.get("api_key", "")),
        ).strip(),
        reranker_base_url=env_str(
            "PTT_RERANKER_BASE_URL",
            str(reranker_cfg.get("base_url", "https://api.jina.ai/v1/rerank")),
        ).strip(),
        reranker_model=env_str(
            "PTT_RERANKER_MODEL",
            str(reranker_cfg.get("model", "jina-reranker-v2-base-multilingual")),
        ).strip(),
    )
    safe_config = {
        key: (value if "api_key" not in key else ("***" if value else "None"))
        for key, value in config.__dict__.items()
    }
    if not _storage_config_logged:
        log(f"Storage configuration loaded: {json.dumps(safe_config, ensure_ascii=False, indent=2)}", level="info")
        _storage_config_logged = True

    return config


def ensure_storage_database(config: StorageConfig | None = None) -> None:
    cfg = config or load_storage_config()
    db_path = str(cfg.remember_db_path or cfg.history_db_path or DEFAULT_APP_DB_PATH)
    db_path_obj = Path(db_path).expanduser().resolve()
    db_path_obj.parent.mkdir(parents=True, exist_ok=True)
    db.init(str(db_path_obj))
    db.connect(reuse_if_open=True)
    db.create_tables([User, APIToken, SessionHistory, RememberEntry])


def resolve_user_id_from_api_key(api_key: str) -> str | None:
    token = str(api_key or "").strip()
    if not token:
        return None
    ensure_storage_database()
    token_record = APIToken.get_or_none(APIToken.token == token)
    if token_record is None:
        return None
    return str(token_record.user_id)


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
                # Strip trailing slash to avoid double-slash issues with proxies
                client_kwargs["base_url"] = self.base_url.rstrip("/")
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
        log(f"DEBUG LLMKeywordRewriter: using base_url={self.base_url} model={self.model}", level="debug")
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
        log("keyword rewrite parsed: " + json.dumps(cleaned_keywords, ensure_ascii=False), level="debug")
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

        workflow = _require_mapping(load_workflow_config(), "workflow")
        prompts = _require_mapping(workflow.get("prompts"), "prompts")
        
        # 1. Normalize
        normalize_cfg = _require_mapping(prompts.get("query_normalize"), "prompts.query_normalize")
        normalize_prompt = str(normalize_cfg.get("system_prompt", "")).strip()
        if not normalize_prompt:
             raise RuntimeError("workflow config missing: prompts.query_normalize.system_prompt")
             
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
        log_multiline("query normalize raw", normalize_content)
        normalized_query = cleaned_query
        try:
            # Strip think tags if any
            text_result = re.sub(r"(?is)<think>.*?</think>", "", normalize_content).strip()
            if "{" in text_result and "}" in text_result:
                json_start = text_result.find("{")
                json_end = text_result.rfind("}") + 1
                payload = json.loads(text_result[json_start:json_end])
                normalized_query = str(payload.get("query") or cleaned_query).strip() or cleaned_query
        except Exception:
            pass

        # 2. Rewrite
        rewrite_cfg = _require_mapping(prompts.get("query_rewrite"), "prompts.query_rewrite")
        keyword_prompt = str(rewrite_cfg.get("system_prompt", "")).strip()
        if not keyword_prompt:
             raise RuntimeError("workflow config missing: prompts.query_rewrite.system_prompt")

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
            text_result = re.sub(r"(?is)<think>.*?</think>", "", rewrite_content).strip()
            if "{" in text_result and "}" in text_result:
                json_start = text_result.find("{")
                json_end = text_result.rfind("}") + 1
                payload = json.loads(text_result[json_start:json_end])
                raw_keywords = payload.get("keywords", []) if isinstance(payload, dict) else []
                keywords = [str(item).strip() for item in raw_keywords if str(item).strip()]
        except Exception:
            pass
        log("keyword rewrite parsed: " + json.dumps(keywords, ensure_ascii=False), level="debug")
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
                # Strip trailing slash to avoid double-slash issues with proxies
                client_kwargs["base_url"] = self.base_url.rstrip("/")
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


class OpenAIEmbeddingClient:
    def __init__(self, *, api_key: str, model: str, base_url: str) -> None:
        self.api_key = api_key.strip() or "lm-studio"
        self.model = model.strip()
        self.base_url = base_url.strip()
        self._client: Any | None = None

    def _client_instance(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            client_kwargs: dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                # Strip trailing slash to avoid double-slash issues with proxies
                client_kwargs["base_url"] = self.base_url.rstrip("/")
            self._client = OpenAI(**client_kwargs)
        return self._client

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        cleaned_texts = [str(text or "").strip() for text in texts if str(text or "").strip()]
        if not cleaned_texts:
            return []
        response = self._client_instance().embeddings.create(
            model=self.model,
            input=cleaned_texts,
        )
        return [list(item.embedding) for item in response.data]


class StorageService:
    def __init__(self, config: StorageConfig, use_cli: bool = True) -> None:
        normalized = StorageConfig(**config.__dict__)
        
        # Final fail-safe: if identity is 'default', try to pull from global environment
        # to handle cases where nested components reload config incorrectly.
        if normalized.user_id == "default":
            env_id = os.environ.get("PTT_USER_ID", "").strip()
            if env_id and env_id != "default":
                normalized.user_id = env_id
        
        if normalized.mem0_user_id == "default":
             env_id = os.environ.get("PTT_USER_ID", "").strip()
             if env_id and env_id != "default":
                normalized.mem0_user_id = env_id
        h_path = str(normalized.history_db_path or "").strip()
        r_path = str(normalized.remember_db_path or "").strip()

        # If only one is provided, use it for both. If neither, use defaults.
        if h_path and not r_path:
            normalized.remember_db_path = h_path
        elif r_path and not h_path:
            normalized.history_db_path = r_path
        elif not h_path and not r_path:
            normalized.history_db_path = str(DEFAULT_HISTORY_DB_PATH)
            normalized.remember_db_path = str(DEFAULT_REMEMBER_DB_PATH)
        
        # All Peewee models share the same global 'db' instance, so paths MUST match
        h_path_abs = Path(normalized.history_db_path).expanduser().resolve()
        r_path_abs = Path(normalized.remember_db_path).expanduser().resolve()
        
        if h_path_abs != r_path_abs:
            log(f"Warning: history_db_path and remember_db_path are different. Using {r_path_abs} for all storage.", level="warning")
            normalized.history_db_path = str(r_path_abs)
            normalized.remember_db_path = str(r_path_abs)
        else:
            # Update paths to resolved absolute strings
            normalized.history_db_path = str(h_path_abs)
            normalized.remember_db_path = str(r_path_abs)
            
        self.config = normalized

        # Initialize Peewee database
        db_path = Path(self.config.remember_db_path).expanduser()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db.init(str(db_path))
        db.connect(reuse_if_open=True)
        db.create_tables([User, APIToken, SessionHistory, RememberEntry])
        self._initialize_users()

        self._remember_provider: BaseRememberStore | None = None
        self._remember_store: BaseRememberStore | None = None
        if use_cli:
            self._history_store = CLIHistoryStore(
                user_id=self.config.user_id,
                api_key=self.config.user_token,
            )
            self._remember_store = CLIRememberStore(
                user_id=self.config.user_id,
                api_key=self.config.user_token,
                summary_extractor=self._get_or_build_remember_provider,
            )
            return
        self._history_store = PeeweeHistoryStore(self.config.user_id)

    def _initialize_users(self) -> None:
        """Ensure all user_ids in api_tokens exist in users table."""
        try:
            with db.connection_context():
                # Get unique user_ids from APIToken
                token_user_ids = {str(t.user_id) for t in APIToken.select(APIToken.user_id)}
                # Also include current config user_id
                if self.config.user_id:
                    token_user_ids.add(str(self.config.user_id))
                
                for uid in token_user_ids:
                    if not uid or uid == "None":
                        continue
                    # Create if not exists, set default nickname as user_id
                    User.get_or_create(user_id=uid, defaults={"nickname": uid})
        except Exception as e:
            log(f"Failed to initialize users: {e}", level="error")

    def _get_or_build_remember_provider(self) -> BaseRememberStore:
        if self._remember_provider is None:
            self._remember_provider = self._build_remember_provider()
        return self._remember_provider

    def _build_remember_provider(self) -> BaseRememberStore:
        from .providers import get_remember_provider_class
        
        provider_cls = get_remember_provider_class(self.config.backend)
        return provider_cls.from_config(
            self.config,
            keyword_rewriter=self.keyword_rewriter(),
            embedding_client=self.embedding_client(),
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

    def embedding_client(self) -> EmbeddingClient | None:
        if not self.config.embedding_search_enabled:
            return None
        if not self.config.embedding_model.strip() or not self.config.embedding_base_url.strip():
            return None
        return OpenAIEmbeddingClient(
            api_key=self.config.embedding_api_key,
            model=self.config.embedding_model,
            base_url=self.config.embedding_base_url,
        )

    def build_export_target_store(self, provider_name: str) -> BaseRememberStore:
        """Build a remember store specifically for export, using user_id from config and no app_id."""
        from .providers import get_remember_provider_class
        
        provider_cls = get_remember_provider_class(provider_name)
        return provider_cls.from_config(
            self.config,
            app_id="",  # Special instruction for export
            keyword_rewriter=self.keyword_rewriter(),
            embedding_client=self.embedding_client(),
        )

    def remember_store(self) -> BaseRememberStore:
        if self._remember_store is None:
            self._remember_store = self._get_or_build_remember_provider()
        return self._remember_store

    def history_store(self) -> BaseHistoryStore:
        return self._history_store

    def get_user_nickname(self) -> str:
        """Fetch user nickname from database, fallback to user_id."""
        log(f"DEBUG get_user_nickname: config.user_id={repr(self.config.user_id)}", level="debug")
        try:
            with db.connection_context():
                user = User.get_or_none(User.user_id == self.config.user_id)
                if user:
                    log(f"DEBUG get_user_nickname: found user={user.user_id} nickname={repr(user.nickname)}", level="debug")
                    if user.nickname:
                        nick = str(user.nickname).strip()
                        if nick and nick != "None" and nick != "default":
                            return nick
                else:
                    log(f"DEBUG get_user_nickname: user not found for id {self.config.user_id}", level="debug")
        except Exception as e:
            log(f"Failed to fetch user nickname: {e}", level="error")
        
        # Final fallbacks
        base_id = str(self.config.user_id or "default")
        res = "大王" if base_id == "default" else base_id
        log(f"DEBUG get_user_nickname: returning fallback {repr(res)}", level="debug")
        return res
