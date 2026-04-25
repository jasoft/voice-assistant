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
        # Auto-create user and token if not found (facilitates integration with 3rd party agents)
        try:
            APIToken.create(
                token=token_str,
                user_id=token_str,
                description="Auto-created for third-party integration"
            )
            return token_str
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Invalid token and auto-creation failed: {e}")
