"""
Endpoint /v1/accounts — gestión de cuentas.

Replica la API del Core Vault para que el Spring Boot use EXACTAMENTE
la misma ruta y payload al migrar al entorno real. Expone:

  POST /v1/accounts        → crea una cuenta (típicamente en PENDING)
  GET  /v1/accounts/{id}   → consulta la cuenta
  GET  /v1/accounts        → lista cuentas (útil para la demo)

El payload sigue el formato oficial de Thought Machine:
  {
    "request_id": "<uuid para idempotencia>",
    "account": {
      "id": "...",
      "product_id": "...",
      "product_version_id": "...",
      "status": "ACCOUNT_STATUS_PENDING",
      "stakeholder_ids": ["..."],
      "permitted_denominations": ["COP"],
      "instance_param_vals": { ... }
    }
  }
"""
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import require_auth_token
from app.store import STORE

router = APIRouter(prefix="/v1", tags=["Accounts"])

# SCHEMAS fieles al formato oficial del Core API

class Account(BaseModel):
    """Cuenta bancaria tal como la modela Thought Machine."""
    id: str = Field(..., description="Account ID único")
    product_id: str = Field(..., description="ID del smart contract asociado")
    product_version_id: str = Field("1", description="Versión del contract")
    status: str = Field("ACCOUNT_STATUS_PENDING",
                        description="PENDING, OPEN, CLOSED, etc.")
    stakeholder_ids: List[str] = Field(default_factory=list,
                                       description="Customers asociados (ej: cédula)")
    permitted_denominations: List[str] = Field(default_factory=lambda: ["COP"])
    instance_param_vals: Dict[str, Any] = Field(default_factory=dict)


class CreateAccountRequest(BaseModel):
    request_id: str = Field(..., description="UUID para idempotencia")
    account: Account


class CreateAccountResponse(BaseModel):
    account: Account


class ListAccountsResponse(BaseModel):
    accounts: List[Account]
    total: int

# ENDPOINTS

@router.post(
    "/accounts",
    response_model=CreateAccountResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_auth_token)],
    summary="Crea una cuenta en Vault (típicamente en estado PENDING)",
)
def create_account(req: CreateAccountRequest) -> CreateAccountResponse:
    """
    Crea una cuenta persistiendo su estado en el store.
    Idempotencia: si el mismo request_id llega dos veces, devuelve la cuenta
    existente en lugar de fallar (comportamiento estándar del Core API).
    """
    # Idempotencia por request_id
    existing = next(
        (a for a in STORE["accounts"].values() if a.get("_request_id") == req.request_id),
        None,
    )
    if existing:
        return CreateAccountResponse(account=Account(**{
            k: v for k, v in existing.items() if not k.startswith("_")
        }))

    # Validación: account.id no debe colisionar con uno existente
    if req.account.id in STORE["accounts"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Account {req.account.id} ya existe",
        )

    # Persistir
    account_dict = req.account.model_dump()
    account_dict["_request_id"] = req.request_id
    account_dict["_created_at"] = datetime.now(timezone.utc).isoformat()
    STORE["accounts"][req.account.id] = account_dict

    return CreateAccountResponse(account=req.account)


@router.get(
    "/accounts/{account_id}",
    response_model=Account,
    dependencies=[Depends(require_auth_token)],
    summary="Consulta una cuenta por su ID",
)
def get_account(account_id: str) -> Account:
    if account_id not in STORE["accounts"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account {account_id} no encontrada",
        )
    data = STORE["accounts"][account_id]
    return Account(**{k: v for k, v in data.items() if not k.startswith("_")})


@router.get(
    "/accounts",
    response_model=ListAccountsResponse,
    dependencies=[Depends(require_auth_token)],
    summary="Lista todas las cuentas (útil para la demo del pitch)",
)
def list_accounts() -> ListAccountsResponse:
    accounts = [
        Account(**{k: v for k, v in data.items() if not k.startswith("_")})
        for data in STORE["accounts"].values()
    ]
    return ListAccountsResponse(accounts=accounts, total=len(accounts))