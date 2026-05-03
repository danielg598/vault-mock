"""
Endpoint /v1/smart-contracts:validate

Este es el corazón del mock: recibe una solicitud del orquestador Spring Boot
con los parámetros propuestos para una cuenta, carga el smart contract
correspondiente, ejecuta su hook pre_posting_code y devuelve si acepta o
rechaza (y por qué).

NOTA arquitectónica:
  En Vault real el flujo es ligeramente distinto: el Core API expone
  /v1/contracts:simulate para probar hooks sin efecto real, o bien se
  valida via pre_posting_code al momento de crear la cuenta en estado
  PENDING. Este endpoint custom /v1/smart-contracts:validate encapsula
  esa validación en una sola llamada, más conveniente para el MVP.

  Cuando migres a Vault real, el CoreBankingPort.validateLoan() del
  Spring Boot apunta al endpoint real (o hace createPendingAccount y
  captura el Rejected). Un solo cambio en VaultCoreAdapter, no en la
  lógica del servicio.
"""
from decimal import Decimal
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import require_auth_token
from app.engine.contract_loader import load_contract
from app.engine.vault_sdk import MockVault, Rejected

router = APIRouter(prefix="/v1", tags=["Smart Contracts"])

#  SCHEMAS

class ValidateRequest(BaseModel):
    """
    Payload que envía el orquestador. Idéntico al que usarías contra Vault
    real para /v1/contracts:simulate (con adaptación menor).
    """
    product_id: str = Field(..., description="ID del smart contract a validar",
                            examples=["personal_loan_ai"])
    instance_param_vals: Dict[str, str] = Field(
        ..., description="Parámetros de instancia como strings",
        examples=[{
            "principal": "20000000",
            "loan_term_months": "36",
            "risk_score": "742",
            "customer_age": "32",
        }])


class ValidateResponse(BaseModel):
    accepted: bool
    reason: str | None = None
    reason_code: str | None = None
    contract_version: str | None = None


#  UTILIDADES

def _coerce_params(raw: Dict[str, str]) -> Dict[str, Any]:
    """
    Los parámetros llegan como strings (estándar de Vault: son opacos al
    transport). Los convertimos a int/Decimal/str según el valor, para que
    el contract pueda compararlos con sus defaults tipados.
    """
    coerced: Dict[str, Any] = {}
    for k, v in raw.items():
        try:
            coerced[k] = int(v)
            continue
        except (ValueError, TypeError):
            pass
        try:
            coerced[k] = Decimal(str(v))
            continue
        except Exception:
            pass
        coerced[k] = v
    return coerced


def _merge_with_defaults(contract_module, instance_vals: Dict[str, Any]) -> Dict[str, Any]:
    """
    Combina los parámetros recibidos con los defaults declarados en el
    contract (para los TEMPLATE-level que el orquestador no envía).
    """
    merged = dict(instance_vals)
    for param in getattr(contract_module, "parameters", []):
        name = param.get("name")
        default = param.get("default_value")
        if name and name not in merged and default is not None:
            merged[name] = default
    return merged

#  ENDPOINT

@router.post(
    "/smart-contracts/validate",
    response_model=ValidateResponse,
    dependencies=[Depends(require_auth_token)],
    summary="Valida una solicitud contra el pre_posting_code de un smart contract",
)
def validate_smart_contract(req: ValidateRequest) -> ValidateResponse:
    """
    Ejecuta el hook pre_posting_code del contract indicado con los parámetros
    propuestos y retorna si la operación sería aceptada o rechazada.
    """
    # 1. Cargar el contract (inyectando vault y Rejected en su namespace)
    try:
        params = _coerce_params(req.instance_param_vals)
        # Instancia temporal de MockVault con los parámetros propuestos
        temp_account_id = "VALIDATION_TEMP"
        vault_stub = MockVault(account_id=temp_account_id, parameters=params)
        contract = load_contract(req.product_id, vault_stub)
    except FileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    # 2. Combinar con defaults del contract (cubre los TEMPLATE-level faltantes)
    full_params = _merge_with_defaults(contract, params)
    vault_stub._parameters = full_params  # refresh

    # 3. Ejecutar pre_posting_code y capturar el Rejected si procede
    hook = getattr(contract, "pre_posting_code", None)
    if hook is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Contract {req.product_id} no define pre_posting_code",
        )

    try:
        hook(postings=[], effective_date=None)
    except Rejected as r:
        return ValidateResponse(
            accepted=False,
            reason=r.message,
            reason_code=r.reason_code,
            contract_version=getattr(contract, "version", "unknown"),
        )
    except Exception as e:
        # Error inesperado ejecutando el contract: NO aprobamos por precaución
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error ejecutando contract {req.product_id}: {e}",
        )

    # 4. Si llegamos aquí, el contract aceptó la operación
    return ValidateResponse(
        accepted=True,
        reason=None,
        reason_code=None,
        contract_version=getattr(contract, "version", "unknown"),
    )