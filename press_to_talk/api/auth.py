import os
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from ..storage.models import APIToken
from ..utils.logging import log

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

async def get_user_id(token: str = Depends(oauth2_scheme)):
    try:
        api_token = APIToken.get_or_none(APIToken.token == token)
        if not api_token:
            # 自动创建逻辑：如果数据库中没有这个 token，则自动创建一个
            # 这适配了豆包等智能体提供的唯一 UID，将 token 和 user_id 都设为该值
            log(f"Token not found, auto-creating user for token: {token[:8]}...", level="info")
            api_token = APIToken.create(
                token=token,
                user_id=token,
                description=f"Auto-generated for agent uid: {token[:8]}..."
            )
        return api_token.user_id
    except Exception as e:
        log(f"Authentication system error: {e}", level="error")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

def get_optional_user_id(token: Optional[str] = Depends(oauth2_scheme)) -> Optional[str]:
    if not token:
        return None
    try:
        api_token = APIToken.get_or_none(APIToken.token == token)
        if api_token:
            return api_token.user_id
        return None
    except:
        return None
