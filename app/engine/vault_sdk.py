"""
Vault SDK Mock — simulación del objeto `vault` y excepciones que los
smart contracts esperan encontrar en su namespace cuando se ejecutan.

En Vault real, estas clases y funciones las inyecta el motor de Contract
Execution Engine (Python embebido) antes de ejecutar cada hook. Aquí
las reproducimos con fidelidad mínima para que nuestros contracts corran
offline.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List
import uuid


# =====================================================================
#  EXCEPCIONES DEL DSL
# =====================================================================

class Rejected(Exception):
    """
    Se lanza desde un hook (pre_posting_code, pre_parameter_change) para
    rechazar la operación. El motor de Vault la captura y responde al
    cliente con el mensaje y el reason_code.
    """
    def __init__(self, message: str, reason_code: str = "AGAINST_TNC"):
        super().__init__(message)
        self.message = message
        self.reason_code = reason_code


# =====================================================================
#  TIMESERIES — estructura que devuelve get_parameter_timeseries()
# =====================================================================
# En Vault real esto es más rico (historial temporal de valores con
# fechas de vigencia). Para el mock, basta con un wrapper que guarde
# el valor actual y exponga .latest().

@dataclass
class ParamTimeseries:
    """Representa el historial de valores de un parámetro. Mock: solo 'latest'."""
    value: Any

    def latest(self):
        return self.value


@dataclass
class BalanceEntry:
    """Una entrada de saldo con su valor neto."""
    net: Decimal


class BalanceTimeseries:
    """Representa el historial de saldos. Mock: devuelve el estado actual."""
    def __init__(self, balances: Dict[tuple, Decimal]):
        self._balances = balances

    def latest(self) -> Dict[tuple, BalanceEntry]:
        return {k: BalanceEntry(net=Decimal(str(v)))
                for k, v in self._balances.items()}


# =====================================================================
#  OBJETO VAULT — lo que el contract ve como `vault.*`
# =====================================================================

class MockVault:
    """
    Reemplaza el objeto `vault` inyectado por el motor de Thought Machine.

    Expone la misma API: get_parameter_timeseries, get_balance_timeseries,
    instruct_posting_batch, make_internal_transfer_instructions, account_id,
    get_hook_execution_id. Suficiente para ejecutar hooks simples.

    Los valores de parámetros y balances se cargan al construir la instancia
    (desde los instance_param_vals recibidos por el endpoint REST) y no
    cambian durante la ejecución del hook, lo que es aceptable para validación.
    """

    def __init__(
        self,
        account_id: str,
        parameters: Dict[str, Any],
        balances: Dict[tuple, Decimal] = None,
    ):
        self.account_id = account_id
        self._parameters = parameters
        self._balances = balances or {}
        self._pending_postings: List[dict] = []

    # --- Parámetros ---
    def get_parameter_timeseries(self, name: str) -> ParamTimeseries:
        if name not in self._parameters:
            raise KeyError(f"Parámetro '{name}' no definido para la cuenta {self.account_id}")
        return ParamTimeseries(value=self._parameters[name])

    # --- Saldos ---
    def get_balance_timeseries(self) -> BalanceTimeseries:
        return BalanceTimeseries(self._balances)

    # --- Instrucción de postings (movimientos contables) ---
    def instruct_posting_batch(self, posting_instructions, effective_date=None, **kw):
        """
        En Vault real, esto envía un batch al ledger. Aquí lo guardamos
        en memoria para que el endpoint REST pueda devolverlos como eco.
        """
        self._pending_postings.extend(posting_instructions or [])

    def make_internal_transfer_instructions(self, **kw) -> List[dict]:
        """Construye una instrucción de transferencia entre cuentas internas."""
        return [kw]

    # --- Utilidades ---
    def get_hook_execution_id(self) -> str:
        return str(uuid.uuid4())

    @property
    def pending_postings(self) -> List[dict]:
        return list(self._pending_postings)