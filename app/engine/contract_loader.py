"""
Contract Loader — carga dinámica de smart contracts.

En Vault real, los contracts viven en la base de datos y el Contract
Execution Engine los carga e inyecta los símbolos del DSL (vault, Rejected,
Parameter, NumberShape, etc.) antes de ejecutar cada hook.

Aquí reproducimos ese comportamiento: cargamos el archivo .py desde disco,
lo compilamos en un módulo aislado, e inyectamos las clases de nuestro
vault_sdk en su namespace. Así el contract puede usar `vault.get_parameter_timeseries(...)`
y `raise Rejected(...)` sin importar nada.
"""
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from app.engine.vault_sdk import MockVault, Rejected


# Directorio donde viven los contracts (hermano de app/)
CONTRACTS_DIR = Path(__file__).resolve().parent.parent.parent / "contracts"


def load_contract(product_id: str, vault_obj: MockVault) -> ModuleType:
    """
    Carga el contract {product_id}.py desde contracts/, inyecta el DSL
    simulado en su namespace y devuelve el módulo listo para invocar hooks.

    Args:
        product_id: nombre del archivo sin .py (ej: "personal_loan_ai").
        vault_obj:  instancia de MockVault con los parámetros del contract.

    Returns:
        El módulo Python del contract con `vault` y `Rejected` ya inyectados.

    Raises:
        FileNotFoundError: si no existe contracts/{product_id}.py.
    """
    contract_path = CONTRACTS_DIR / f"{product_id}.py"

    if not contract_path.exists():
        raise FileNotFoundError(
            f"Smart contract '{product_id}' no encontrado en {CONTRACTS_DIR}"
        )

    # Creamos un módulo Python aislado con nombre único (evita colisiones)
    module_name = f"contracts.{product_id}"
    spec = importlib.util.spec_from_file_location(module_name, contract_path)
    module = importlib.util.module_from_spec(spec)

    # --- INYECCIÓN DEL DSL ---
    # Antes de ejecutar el contract, poblamos su namespace con los símbolos
    # que espera encontrar (como si fuera el motor real de Thought Machine).
    module.vault = vault_obj
    module.Rejected = Rejected

    # Stubs mínimos para los "Shapes" y "Level" usados en las definiciones
    # de parámetros. El mock no los usa activamente (validamos a nivel REST),
    # pero evitan NameError al evaluar las listas de parameters.
    class _ShapeStub:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    module.NumberShape = _ShapeStub
    module.StringShape = _ShapeStub
    module.DenominationShape = _ShapeStub
    module.AccountIdShape = _ShapeStub

    class _Level:
        GLOBAL = "GLOBAL"
        TEMPLATE = "TEMPLATE"
        INSTANCE = "INSTANCE"

    class _Tside:
        ASSET = "ASSET"
        LIABILITY = "LIABILITY"

    module.Level = _Level
    module.Tside = _Tside
    module.Parameter = lambda **kw: kw

    # Decorador @requires(parameters=True, balances=...) — en Vault real
    # declara qué datos precargar. En el mock es un no-op.
    def requires(**kw):
        def _decorator(fn):
            return fn
        return _decorator
    module.requires = requires

    # Registrar y ejecutar el módulo
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    return module