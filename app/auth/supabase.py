from __future__ import annotations

import jwt
from fastapi import HTTPException, status

from app.config import settings


def verify_supabase_token(token: str) -> dict:
    if not settings.supabase_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication not configured",
        )

    try:
        jwks_url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
        import requests

        jwks_response = requests.get(jwks_url, timeout=10)
        jwks_response.raise_for_status()
        jwks = jwks_response.json()

        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")

        public_key = None
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)
                break

        if not public_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )

        return jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience="authenticated",
            options={"verify_exp": True},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Token verification failed: {str(e)}",
        )
