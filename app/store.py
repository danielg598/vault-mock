"""
Store en memoria para el mock.

Simula la persistencia de Vault Core con estructuras Python:
  - accounts:        cuentas creadas (account_id -> dict)
  - postings:        movimientos contables registrados
  - customers:       clientes (stakeholders) de las cuentas

Se reinicia con cada arranque del servidor. Para el objetivo de la
prueba técnica es suficiente; en la versión "persistente" bastaría
cambiar estos dicts por tablas SQLAlchemy o un SQLite local.
"""
from typing import Any, Dict, List

# Estructura principal
STORE: Dict[str, Dict[str, Any]] = {
    "accounts": {},     # account_id -> account dict
    "postings": {},     # batch_id -> posting batch dict
    "customers": {},    # customer_id -> customer dict
    "decisions": {},    # decision_id -> decision dict (para trazabilidad)
}


def reset_store() -> None:
    """Limpia todo el store. Útil para tests."""
    for k in STORE:
        STORE[k].clear()


def store_summary() -> Dict[str, int]:
    """Retorna conteos de cada tabla (útil para endpoint de diagnóstico)."""
    return {tabla: len(registros) for tabla, registros in STORE.items()}