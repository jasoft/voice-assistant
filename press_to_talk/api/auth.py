import os
import secrets
import hmac
import httpx
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from ..utils.logging import log

# HTTPBearer renders a simple "paste token" Authorize button in Swagger UI,
# unlike OAuth2PasswordBearer which expects a /token endpoint we don't have.
bearer_scheme = HTTPBearer()

def uses_harness_backend() -> bool:
    backend = os.environ.get("PTT_QUERY_BACKEND", "legacy").strip().lower()
    return backend in {"harness", "deepseek-harness"}


# Kept as a module-local alias for existing API callers/tests.
_uses_harness_backend = uses_harness_backend


def _pb_api_url() -> str:
    return os.environ.get("PTT_PB_URL", "http://127.0.0.1:18090").rstrip("/") + "/api"


def _harness_user_id(token: str) -> str:
    configured_token = os.environ.get("PTT_API_KEY", "").strip()
    if configured_token and not hmac.compare_digest(token, configured_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return os.environ.get("PTT_USER_ID", "soj").strip() or "soj"

async def get_user_id(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    token = credentials.credentials
    if _uses_harness_backend():
        return _harness_user_id(token)

    try:
        # 针对 PocketBase api_tokens 集合进行查询
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{_pb_api_url()}/collections/api_tokens/records",
                params={"filter": f"token = '{token}'"}
            )
            data = res.json()
            items = data.get("items", [])

            if items:
                return items[0]["user_id"]

            # 自动创建逻辑（适配豆包智能体等场景）
            user_id = token
            log(f"Token not found, auto-creating user for token: {token[:8]}...", level="info")
            create_res = await client.post(
                f"{_pb_api_url()}/collections/api_tokens/records",
                json={
                    "token": token,
                    "user_id": user_id,
                    "description": f"Auto-generated for agent uid: {token[:8]}..."
                }
            )
            create_res.raise_for_status()

            # 同步创建 user 记录（默认 nickname 为"大人"）
            try:
                user_check = await client.get(
                    f"{_pb_api_url()}/collections/users/records",
                    params={"filter": f"user_id = '{user_id}'", "fields": "id"}
                )
                if user_check.status_code == 200 and not user_check.json().get("items"):
                    random_pw = secrets.token_urlsafe(16)
                    user_res = await client.post(
                        f"{_pb_api_url()}/collections/users/records",
                        json={
                            "user_id": user_id,
                            "nickname": "大人",
                            "username": user_id,
                            "password": random_pw,
                            "passwordConfirm": random_pw,
                        }
                    )
                    if user_res.status_code in (200, 201):
                        log(f"Created user record for {user_id} with default nickname", level="info")
                    else:
                        log(f"Failed to create user record for {user_id}", level="warn")
            except Exception as e:
                log(f"Warning: failed to create user record: {e}", level="warn")

            return user_id

    except Exception as e:
        log(f"Authentication system error: {e}", level="error")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

async def get_optional_user_id(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)) -> Optional[str]:
    """Extract the bearer token without enforcing authentication.

    Returns None when no credentials are provided; callers decide whether
    that is acceptable. Core validation lives in ``get_user_id``.
    """
    if credentials is None:
        return None
    return credentials.credentials
