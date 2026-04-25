from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from ..storage.models import APIToken

security = HTTPBearer()

async def get_user_id(auth: HTTPAuthorizationCredentials = Security(security)) -> str:
    token_str = auth.credentials
    try:
        token_obj = APIToken.get(APIToken.token == token_str)
        return token_obj.user_id
    except APIToken.DoesNotExist:
        raise HTTPException(status_code=401, detail="Invalid token")
