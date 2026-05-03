"""
Middleware de autenticación — valida el header X-Auth-Token.

Vault Core de Thought Machine usa un header custom 'X-Auth-Token' (NO es
Bearer, NO es JWT) con tokens de servicio emitidos por el panel de Ops.
El mock replica ese mecanismo: cada request a /v1/* debe traer el header
con un token válido, o recibe 401 Unauthorized.
"""
from fastapi import Header, HTTPException, status

# Tokens aceptados en desarrollo
# En producción el token real es el que obtienes del equipo Thought Machine
# y se inyecta al Spring Boot vía la variable de entorno APP_VAULT_AUTH_TOKEN.
VALID_TOKENS = {
    "mock-dev-token",       # token por defecto del application.yml
    "mock-test-token",      # token alternativo para pruebas automatizadas
}


async def require_auth_token(x_auth_token: str = Header(..., alias="X-Auth-Token")):
    """
    Dependency injection de FastAPI. Cada endpoint que la declare en sus
    Depends(...) exigirá el header y validará el token antes de ejecutarse.

    Si el header falta, FastAPI devuelve 422. Si el token es inválido,
    nosotros devolvemos 401 explícito.
    """
    if x_auth_token not in VALID_TOKENS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Auth-Token",
        )
    return x_auth_token