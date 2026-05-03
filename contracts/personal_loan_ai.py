"""
Personal Loan AI — Smart Contract estilo Thought Machine Vault Core.

Define las reglas ejecutables de un producto de préstamo personal.
Se activa en dos momentos clave del ciclo de vida:

  1. pre_posting_code    → valida la apertura (reglas duras de política)
  2. scheduled_code      → devenga intereses diarios + aplica cuota mensual

Parámetros expuestos al orquestador (instance level):
  - principal              : monto desembolsado
  - annual_interest_rate   : tasa efectiva anual
  - loan_term_months       : plazo en meses (6 - 120)
  - risk_score             : score IA 0-1000 (obligatorio al apertura)
  - customer_age           : edad del cliente

Parámetros globales del producto (template level):
  - denomination           : COP
  - min_risk_score         : score mínimo exigido (default: 500)
  - max_principal          : tope de monto (default: 100.000.000 COP)

NOTAS:
  * Este contract usa la sintaxis pública de Contract Language v3 de
    Thought Machine, visible en sus tutoriales Lab3.
  * Objetos vault.* se inyectan en tiempo de ejecución por el mock engine.
  * Migración a Vault real: se sube este archivo tal cual vía el endpoint
    /v1/product-versions del Core API, y el motor real lo ejecuta.
"""

api = "3.0.0"
version = "1.0.0"
display_name = "Personal Loan AI"
summary = "Préstamo personal con aprobación asistida por IA"
tside = "ASSET"                         # préstamo = activo para el banco
supported_denominations = ["COP"]

#  DEFINICIÓN DE PARÁMETROS
# En Vault real, cada entrada es un objeto Parameter(...) con Shape tipado.
# Aquí los declaramos como dict para que el mock engine los lea sin
# depender del SDK completo de Thought Machine.

parameters = [
    # Template-level: iguales para todas las instancias del producto
    {
        "name": "denomination", "level": "TEMPLATE",
        "shape": "DenominationShape", "default_value": "COP",
    },
    {
        "name": "min_risk_score", "level": "TEMPLATE",
        "shape": "NumberShape", "min_value": 0, "max_value": 1000,
        "default_value": 500,
        "description": "Score mínimo IA para aprobar el crédito",
    },
    {
        "name": "max_principal", "level": "TEMPLATE",
        "shape": "NumberShape", "min_value": 500_000, "max_value": 500_000_000,
        "default_value": 100_000_000,
        "description": "Monto máximo autorizado por política",
    },

    # Instance-level: únicos por cada préstamo otorgado
    {
        "name": "principal", "level": "INSTANCE",
        "shape": "NumberShape", "min_value": 500_000, "max_value": 100_000_000,
        "description": "Monto desembolsado al cliente",
    },
    {
        "name": "annual_interest_rate", "level": "INSTANCE",
        "shape": "NumberShape", "min_value": 0.0, "max_value": 0.50,
        "default_value": 0.18,
        "description": "Tasa efectiva anual (ej: 0.185 = 18.5%)",
    },
    {
        "name": "loan_term_months", "level": "INSTANCE",
        "shape": "NumberShape", "min_value": 6, "max_value": 120,
        "description": "Plazo del préstamo en meses",
    },
    {
        "name": "risk_score", "level": "INSTANCE",
        "shape": "NumberShape", "min_value": 0, "max_value": 1000,
        "description": "Score de riesgo IA 0-1000 (mayor = mejor)",
    },
    {
        "name": "customer_age", "level": "INSTANCE",
        "shape": "NumberShape", "min_value": 18, "max_value": 75,
        "description": "Edad del solicitante",
    },
]


#  HOOK: pre_posting_code — validación al momento de apertura
# Se invoca ANTES de aceptar cualquier posting (desembolso inicial, pagos).
# Si lanza Rejected(...), la operación se anula y no queda rastro en el ledger.
#
# Este es el "guardián de la política": aquí aterrizan las reglas duras
# que NO deben delegarse al modelo de IA (regulatorias, legales, de producto).
#
# En tu orquestador Spring Boot, el adapter llama a un endpoint
# /v1/smart-contracts:validate que ejecuta ESTE hook con los parámetros
# propuestos antes de crear la cuenta. Si rechaza, el orquestador
# responde RECHAZADO al Angular sin llegar a abrir la cuenta.

def pre_posting_code(postings, effective_date):
    """Valida que la solicitud cumpla las reglas de política del producto."""

    # Cargamos parámetros relevantes usando la API del objeto vault
    score      = vault.get_parameter_timeseries(name="risk_score").latest()
    min_score  = vault.get_parameter_timeseries(name="min_risk_score").latest()
    age        = vault.get_parameter_timeseries(name="customer_age").latest()
    principal  = vault.get_parameter_timeseries(name="principal").latest()
    max_princ  = vault.get_parameter_timeseries(name="max_principal").latest()
    term       = vault.get_parameter_timeseries(name="loan_term_months").latest()

    # --- Regla 1: score mínimo de IA ---
    if score < min_score:
        raise Rejected(
            f"Score de riesgo {score} inferior al mínimo exigido {min_score}",
            reason_code="RISK_SCORE_BELOW_THRESHOLD",
        )

    # --- Regla 2: edad dentro del rango operativo ---
    # (18-70 para asegurar capacidad legal + término del préstamo antes de los 75)
    if age < 18 or age > 70:
        raise Rejected(
            f"Edad {age} fuera del rango permitido (18-70 años)",
            reason_code="AGE_OUT_OF_RANGE",
        )

    # --- Regla 3: monto no excede el tope de política ---
    if principal > max_princ:
        raise Rejected(
            f"Monto {principal:,.0f} excede el máximo autorizado {max_princ:,.0f}",
            reason_code="PRINCIPAL_EXCEEDS_LIMIT",
        )

    # --- Regla 4: plazo + edad no pueden superar los 75 años ---
    # (regla común en banca colombiana: deuda liquidada antes de los 75)
    edad_final = age + (term / 12)
    if edad_final > 75:
        raise Rejected(
            f"El cliente cumpliría {edad_final:.0f} años al liquidar el préstamo",
            reason_code="MATURITY_AGE_EXCEEDED",
        )

    # Si llegamos aquí, todas las reglas aprobaron. Vault procede
    # con la apertura de cuenta y el desembolso inicial.


#  HOOK: derived_parameters — cálculos on-the-fly
# Vault los ejecuta cuando alguien consulta la cuenta. Son valores
# calculados en vivo (no almacenados), típicos para saldos pendientes,
# próxima fecha de pago, intereses acumulados sin capitalizar, etc.
#
# Este hook no lo usamos ahora, pero lo dejamos declarado para que
# cuando migres a Vault real solo tengas que rellenar el cuerpo.

def derived_parameters(effective_date):
    """Devuelve parámetros calculados (ej: saldo pendiente actual)."""
    return {
        "product_id": "personal_loan_ai",
        "remaining_term_months": vault.get_parameter_timeseries(
            name="loan_term_months").latest(),
    }