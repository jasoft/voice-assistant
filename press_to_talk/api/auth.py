import os
import httpx
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from ..utils.logging import log

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
PB_API_URL = os.environ.get("PTT_PB_URL", "http://127.0.0.1:18090") + "/api"

async def get_user_id(token: str = Depends(oauth2_scheme)):
    try:
        # 针对 PocketBase api_tokens 集合进行查询
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{PB_API_URL}/collections/api_tokens/records",
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
                f"{PB_API_URL}/collections/api_tokens/records",
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
                    f"{PB_API_URL}/collections/users/records",
                    params={"filter": f"user_id = '{user_id}'", "fields": "id"}
                )
                if user_check.status_code == 200 and not user_check.json().get("items"):
                    await client.post(
                        f"{PB_API_URL}/collections/users/records",
                        json={"user_id": user_id, "nickname": "大人"}
                    )
                    log(f"Created user record for {user_id} with default nickname", level="info")
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

def get_optional_user_id(token: Optional[str] = Depends(oauth2_scheme)) -> Optional[str]:
    # 这是一个同步方法，但在 FastAPI 依赖中通常建议用 async
    # 为了保持原有签名暂不改动，但内部需处理异步或改为 async
    return None # 暂时返回 None，核心逻辑已迁往 async get_user_id

