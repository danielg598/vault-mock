"""
Vault Core Mock — punto de entrada.

Simula el Core API de Thought Machine. Expone los mismos endpoints
(rutas, headers y payloads) que la plataforma real, de modo que el
microservicio orquestador Spring Boot pueda apuntar indistintamente a:

  - http://localhost:9000             (este mock, desarrollo)
  - https://core-api.tm.blx-demo.com/ (Vault real, producción con VPN)

El cambio se hace únicamente por variable de entorno. Cero código modificado.
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import accounts, contracts
from app.store import store_summary

# --- Logging básico ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# --- App ---
app = FastAPI(
    title="Vault Core Mock",
    description="Simulador local del Core API de Thought Machine para desarrollo offline",
    version="1.0.0",
)

# CORS middleware
# Permite que el dashboard Angular (localhost:4200) consuma el Vault Mock
# directamente desde el navegador. Sin esto, Chrome bloquea las peticiones
# con error "No 'Access-Control-Allow-Origin' header".
# En producción (Vault real), no se necesita porque el Spring Boot orquestador
# es quien habla con Vault, no el navegador.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4200",   # Angular dev server
        "http://localhost:3000",   # React/otros dev servers
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],           # incluye X-Auth-Token y Content-Type
    expose_headers=["X-Request-Id"],
    max_age=3600,                  # cachea el preflight 1 hora
)

# Routers
app.include_router(contracts.router)
app.include_router(accounts.router)


# Endpoints utilitarios

@app.get("/health")
def health_check():
    """Health check simple para verificar que el servidor está vivo."""
    return {"status": "ok", "service": "vault-mock", "version": "1.0.0"}


@app.get("/")
def root():
    """Landing que lista los endpoints disponibles."""
    return {
        "service": "Vault Core Mock",
        "docs": "/docs",
        "store_summary": store_summary(),
        "endpoints": [
            "GET  /health",
            "POST /v1/smart-contracts/validate",
            "POST /v1/accounts",
            "GET  /v1/accounts/{account_id}",
            "GET  /v1/accounts",
        ],
    }