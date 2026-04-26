from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from ..storage.service import resolve_user_id_from_api_key

security = HTTPBearer()

async def get_user_id(auth: HTTPAuthorizationCredentials = Security(security)) -> str:
    token_str = auth.credentials
    user_id = resolve_user_id_from_api_key(token_str)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user_id
