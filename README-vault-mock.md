# vault-mock

> Simulador local del **Core API de Thought Machine (Vault Core)**.
> Replica los endpoints, headers, payloads y **Smart Contracts en Python**
> de la plataforma real para permitir desarrollo offline hasta que
> llegue la VPN productiva.

Puerto: **9000** · Python 3.12 · FastAPI

---

## ¿Por qué existe este servicio?

Vault Core es el core bancario de Thought Machine. En producción, Lumen
apunta a la instancia real:

```
https://core-api.tm.blx-demo.com/
```

…pero el acceso requiere **VPN + token de servicio + contrato firmado**.
Esos llegan después del pitch.

Mientras tanto, necesitamos desarrollar contra **una réplica fiel** del
contrato HTTP de Vault: mismos endpoints, mismos headers de auth, mismos
códigos de respuesta, mismas estructuras de payload. Eso es este
servicio.

Cuando llegue la VPN, el cambio es **una variable de entorno** en el
orquestador:

```diff
- APP_VAULT_BASE_URL=http://localhost:9000
+ APP_VAULT_BASE_URL=https://core-api.tm.blx-demo.com
- APP_VAULT_AUTH_TOKEN=mock-dev-token
+ APP_VAULT_AUTH_TOKEN=<token productivo>
```

Cero código modificado. Esa es la promesa del mock: fidelidad al contrato.

---

## Fidelidad al Core API real

El mock replica deliberadamente estos detalles del Vault Core real:

| Detalle | Implementación |
|---|---|
| **Prefijo de rutas** | `/v1/...` igual que la plataforma |
| **Auth** | Header `X-Auth-Token` (NO `Authorization: Bearer`, NO JWT) |
| **Códigos de error** | Mismos status + estructura `{"detail": "..."}` |
| **Account states** | `ACCOUNT_STATUS_PENDING`, `ACCOUNT_STATUS_OPEN`, `ACCOUNT_STATUS_CLOSED` |
| **Idempotencia** | `request_id` es única — crea o retorna la existente |
| **Payload shape** | `stakeholder_ids`, `permitted_denominations`, `instance_param_vals` igual que TM |
| **Smart Contracts** | **Python real**, no JSON de configuración |

---

## La parte única: Smart Contracts como código Python

Este es el aspecto más distintivo de Vault Core y lo que lo separa de
un core legado: **las reglas de producto son código Python**, no filas
en una tabla de configuración.

Un Smart Contract en Vault Core es un archivo `.py` que define:
- Parámetros del producto (plazo mín/máx, monto mín/máx, tasa base)
- `pre_posting_code` — validación antes de registrar un movimiento
- `post_posting_code` — lógica después del movimiento (cobros, intereses)
- `derived_parameters` — cálculos derivados a exponer

El mock incluye un **mini Contract Execution Engine** (~80 líneas de
Python) que carga dinámicamente el contract, lo ejecuta en un namespace
aislado, e interpreta los `Rejected` exceptions como rechazos de
validación — igual que Vault Core real.

Ejemplo del contract actual (`contracts/personal_loan_ai.py`):

```python
"""
Smart Contract: personal_loan_ai — Préstamo personal asistido por IA.

Reglas de producto:
  1. Score mínimo: 500
  2. Edad: 18-70 años al originar
  3. Monto: <= 100M COP
  4. Edad + plazo <= 75 años (sale antes de riesgo de mortalidad)
"""

product_id = "personal_loan_ai"
version = "1.0.0"

parameters = [
    Parameter("principal",           "Monto del préstamo en COP"),
    Parameter("loan_term_months",    "Plazo en meses"),
    Parameter("annual_interest_rate", "Tasa EA ajustada por riesgo"),
    Parameter("risk_score",          "Score 0-1000 calculado por IA"),
    Parameter("customer_age",        "Edad del titular"),
]


def pre_posting_code(vault, postings):
    score      = int(vault.get_parameter("risk_score"))
    age        = int(vault.get_parameter("customer_age"))
    principal  = float(vault.get_parameter("principal"))
    term       = int(vault.get_parameter("loan_term_months"))

    if score < 500:
        raise Rejected(
            f"Score de riesgo {score} inferior al mínimo exigido 500",
            reason_code="RISK_SCORE_BELOW_THRESHOLD",
        )

    if age < 18 or age > 70:
        raise Rejected(
            f"Edad {age} fuera del rango permitido 18-70",
            reason_code="AGE_OUT_OF_RANGE",
        )

    if principal > 100_000_000:
        raise Rejected(
            f"Monto {principal:,.0f} excede el límite de 100M COP",
            reason_code="PRINCIPAL_EXCEEDED",
        )

    if age + (term / 12) > 75:
        raise Rejected(
            f"Edad al término ({age + term // 12}) excede el límite 75",
            reason_code="AGE_AT_MATURITY_EXCEEDED",
        )
```

**Cuando vayamos a producción** este mismo contract se sube al Vault
real (con sintaxis ajustada a su DSL específico). No hay que rescribir
la lógica de negocio.

---

## Arquitectura interna

```
┌──────────────────────────────────────────────────────────┐
│  vault-mock (puerto 9000)                                │
│                                                          │
│  ┌───────────────┐    ┌──────────────────┐              │
│  │ api/contracts │───▶│ engine/          │              │
│  │ POST /validate│    │ contract_loader  │              │
│  └───────────────┘    │ (ejecuta .py)    │              │
│                       └────────┬─────────┘              │
│                                │                         │
│                                ▼                         │
│                       ┌──────────────────┐              │
│                       │ engine/vault_sdk │              │
│                       │ (MockVault API)  │              │
│                       └──────────────────┘              │
│                                                          │
│  ┌───────────────┐    ┌──────────────────┐              │
│  │ api/accounts  │───▶│ store (in-memory)│              │
│  │ POST /accounts│    │ accounts_by_id   │              │
│  │ GET  /accounts│    │ postings_by_acc  │              │
│  └───────────────┘    └──────────────────┘              │
│                                                          │
│  ┌──────────────────────────────────────────┐           │
│  │ auth (middleware)                         │           │
│  │ verifica X-Auth-Token en cada request    │           │
│  └──────────────────────────────────────────┘           │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

---

## Estructura del proyecto

```
vault-mock/
├── app/
│   ├── main.py                     FastAPI app + CORS + routers
│   ├── auth.py                     Dependency require_auth_token
│   ├── store.py                    Dict en memoria (accounts, postings)
│   │
│   ├── engine/
│   │   ├── vault_sdk.py            MockVault + Rejected + ParamTimeseries
│   │   └── contract_loader.py      Contract Execution Engine casero
│   │
│   └── api/
│       ├── contracts.py            POST /v1/smart-contracts/validate
│       └── accounts.py             POST/GET /v1/accounts
│
├── contracts/
│   └── personal_loan_ai.py         El Smart Contract actual
│
├── requirements.txt
└── README.md                       (este archivo)
```

---

## Stack técnico

| Componente | Librería | Rol |
|---|---|---|
| Framework | FastAPI 0.115+ | ASGI, OpenAPI auto-generado |
| Server | Uvicorn 0.32+ | ASGI server con hot reload |
| Validación | Pydantic 2.9+ | Schemas tipados de accounts y contracts |

Tres librerías totales. Cero dependencias de pago. Cero base de datos.
El proyecto entero arranca en <1 segundo.

---

## Setup desde cero

```bash
cd vault-mock

python -m venv venv
.\venv\Scripts\Activate.ps1        # Windows
# source venv/bin/activate         # Linux/Mac

pip install -r requirements.txt
uvicorn app.main:app --reload --port 9000
```

Verificar:

```bash
curl http://localhost:9000/health
# {"status":"ok","service":"vault-mock","version":"1.0.0"}
```

Swagger UI: http://localhost:9000/docs

---

## Endpoints

### `POST /v1/smart-contracts/validate`

Valida una solicitud contra el `pre_posting_code` del Smart Contract
sin crear la cuenta. Es lo que el orquestador llama **antes** de
materializar una aprobación.

**Headers**: `X-Auth-Token: mock-dev-token`

**Request**:

```json
{
  "product_id": "personal_loan_ai",
  "instance_param_vals": {
    "principal": "20000000",
    "loan_term_months": "36",
    "risk_score": "755",
    "customer_age": "32"
  }
}
```

**Response — aprobado** (200):

```json
{
  "accepted": true,
  "reason": null,
  "reason_code": null,
  "contract_version": "1.0.0"
}
```

**Response — rechazado** (200):

```json
{
  "accepted": false,
  "reason": "Score de riesgo 300 inferior al mínimo exigido 500",
  "reason_code": "RISK_SCORE_BELOW_THRESHOLD",
  "contract_version": "1.0.0"
}
```

Nótese que **un rechazo no es un HTTP 4xx** — es un `200 OK` con
`accepted: false`. Así funciona Vault Core real: el contract corrió
exitosamente, simplemente rechazó la solicitud.

### `POST /v1/accounts`

Crea una cuenta (estado inicial `ACCOUNT_STATUS_PENDING`).

**Headers**: `X-Auth-Token: mock-dev-token`, `X-Request-Id: <uuid>`

**Request**:

```json
{
  "request_id": "req-abc123",
  "account": {
    "product_id": "personal_loan_ai",
    "product_version_id": "1",
    "stakeholder_ids": ["1023456789"],
    "permitted_denominations": ["COP"],
    "instance_param_vals": {
      "principal": "20000000",
      "loan_term_months": "36",
      "annual_interest_rate": "0.2233",
      "risk_score": "755",
      "customer_age": "32"
    }
  }
}
```

**Response** (201 o 200 con idempotencia):

```json
{
  "id": "LOAN-1023456789-1776650001047",
  "product_id": "personal_loan_ai",
  "product_version_id": "1",
  "status": "ACCOUNT_STATUS_PENDING",
  "stakeholder_ids": ["1023456789"],
  "permitted_denominations": ["COP"],
  "instance_param_vals": {...},
  "opening_timestamp": "2026-04-19T20:36:00Z"
}
```

**Idempotencia**: si el mismo `request_id` llega dos veces, el store
devuelve la cuenta existente. Igual que Vault Core real, para tolerar
reintentos del cliente ante network blips.

### `GET /v1/accounts`

Lista todas las cuentas creadas.

```json
{
  "accounts": [...],
  "total": 3
}
```

### `GET /v1/accounts/{account_id}`

Consulta una cuenta específica. 404 si no existe.

---

## Auth: cómo funciona `X-Auth-Token`

```python
# app/auth.py
VALID_TOKENS = {"mock-dev-token", "mock-test-token"}

def require_auth_token(x_auth_token: str = Header(...)):
    if x_auth_token not in VALID_TOKENS:
        raise HTTPException(status_code=401, detail="Invalid X-Auth-Token")
```

Los 2 tokens hardcodeados son solo para desarrollo. En producción Vault
real usa tokens de servicio emitidos por el Vault Admin API.

Lo importante es que el **contrato del header coincide** con Vault real:
`X-Auth-Token`, no `Authorization: Bearer`. Ese detalle es el tipo de
cosa que te preguntarán en el pitch: *"¿y cómo se autentica?"* y
respondés con el header exacto.

---

## El Contract Execution Engine (lo más interesante)

En `app/engine/contract_loader.py` vive un mini-ejecutor de ~80 líneas
que carga dinámicamente un archivo `.py` de la carpeta `contracts/`,
lo compila, y lo ejecuta en un namespace aislado donde le inyectamos:

- `Rejected`: la clase de excepción del SDK
- `vault`: una instancia de `MockVault` que simula los getters del
  runtime real (`vault.get_parameter("principal")`)
- `Parameter`, `ParamTimeseries`, etc.

```python
def load_contract(product_id: str):
    contract_path = Path("contracts") / f"{product_id}.py"
    source = contract_path.read_text(encoding="utf-8")

    namespace = {
        "Rejected":        Rejected,
        "Parameter":       Parameter,
        "ParamTimeseries": ParamTimeseries,
        "BalanceTimeseries": BalanceTimeseries,
    }
    compiled = compile(source, str(contract_path), "exec")
    exec(compiled, namespace)
    return namespace


def execute_pre_posting(contract_ns: dict, vault: MockVault) -> None:
    """Ejecuta pre_posting_code. Si lanza Rejected, se captura en el router."""
    fn = contract_ns["pre_posting_code"]
    fn(vault=vault, postings=[])
```

Esta arquitectura permite escribir **nuevos Smart Contracts** (credit card,
mortgage, savings) sin tocar una línea del mock — solo crear un `.py`
nuevo en `contracts/`.

---

## Persistencia: por qué in-memory

El store es un `dict` en memoria (`app/store.py`):

```python
STORE = {
    "accounts": {},      # account_id -> Account dict
    "postings": {},      # account_id -> [Posting, ...]
    "customers": {},     # cedula -> Customer
}
```

**Trade-off consciente**: cada reinicio de uvicorn borra todo. Perfecto
para desarrollo (reset limpio), inaceptable para producción.

**En producción, Vault Core real persiste en** su propio storage
distribuido (múltiples nodos con Raft). **No necesitamos portar el
store a Postgres** en este MVP — cuando migremos al Vault real, el
tema desaparece (el core persiste por nosotros).

---

## CORS

Habilitado para que el dashboard Angular pueda consultar las cuentas
directamente (tab "Cuentas aprobadas"):

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],           # incluye X-Auth-Token
    max_age=3600,
)
```

**Advertencia**: en producción, el navegador NUNCA debería hablar
directo con Vault — siempre pasar por el orquestador (que es quien
tiene el token productivo). El CORS aquí es para ahorrar tiempo de
desarrollo.

---

## Agregar un producto nuevo (ejemplo: tarjeta de crédito)

1. Crear `contracts/credit_card_premium.py`:

```python
product_id = "credit_card_premium"
version = "1.0.0"

parameters = [
    Parameter("credit_limit",   "Cupo aprobado"),
    Parameter("minimum_income", "Ingreso mínimo certificable"),
    Parameter("customer_age",   "Edad del titular"),
    Parameter("risk_score",     "Score IA"),
]


def pre_posting_code(vault, postings):
    score  = int(vault.get_parameter("risk_score"))
    income = float(vault.get_parameter("minimum_income"))

    if score < 650:
        raise Rejected(
            f"Score {score} insuficiente para tarjeta Premium (mín 650)",
            reason_code="RISK_SCORE_BELOW_PREMIUM_THRESHOLD",
        )

    if income < 5_000_000:
        raise Rejected(
            "Ingresos certificables inferiores a $5M COP requeridos para Premium",
            reason_code="INCOME_BELOW_PREMIUM_THRESHOLD",
        )
```

2. Llamar al orquestador con `productId=credit_card_premium`.

3. El mock carga dinámicamente el contract y lo ejecuta. **Cero código
   modificado en el mock**.

---

## Testing

```bash
# Smart contract válido
curl -X POST http://localhost:9000/v1/smart-contracts/validate \
  -H "X-Auth-Token: mock-dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "product_id": "personal_loan_ai",
    "instance_param_vals": {
      "principal": "20000000",
      "loan_term_months": "36",
      "risk_score": "755",
      "customer_age": "32"
    }
  }'
# → {"accepted": true, ...}

# Smart contract rechazado
curl -X POST http://localhost:9000/v1/smart-contracts/validate \
  -H "X-Auth-Token: mock-dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "product_id": "personal_loan_ai",
    "instance_param_vals": {
      "principal": "20000000",
      "loan_term_months": "36",
      "risk_score": "300",
      "customer_age": "32"
    }
  }'
# → {"accepted": false, "reason": "Score de riesgo 300 inferior al mínimo exigido 500"}
```

---

## Observabilidad

Uvicorn loguea cada request:

```
INFO: 127.0.0.1:62813 - "POST /v1/smart-contracts/validate HTTP/1.1" 200 OK
INFO: 127.0.0.1:62813 - "POST /v1/accounts HTTP/1.1" 201 Created
INFO: 127.0.0.1:62813 - "GET /v1/accounts HTTP/1.1" 200 OK
```

Para producción se agregaría:
- `python-json-logger` para logs estructurados JSON
- Middleware para inyectar `X-Request-Id` en todos los logs (trazabilidad
  cross-servicio junto con Spring Boot)

---

## Limitaciones conocidas

El mock **deliberadamente NO implementa**:

- **Postings reales** (movimientos contables). Se reciben y se persisten
  en el store pero no hay motor de asientos de doble entrada.
- **Balances**. `BalanceTimeseries` es un stub — siempre devuelve 0.
- **Scheduled events** (cobros automáticos, intereses). No hay scheduler.
- **Kafka publishing** (eventos `ACCOUNT_OPENED`). El Vault real publica
  en `tm.kafka.pih9xm...`.
- **Customers / profile API**. Solo se guarda la `cedula` como stakeholder.
- **Streaming endpoints** (Core Streaming API). El real los usa para alto
  volumen.

Ninguna de estas es necesaria para el flujo Lumen de **aprobación de
crédito**. Cuando migremos a Vault Core real, todas estas features
vienen con el producto.

---

## Referencia: equivalencia con Vault Core real

| Mock | Vault Core real | Notas |
|---|---|---|
| `POST /v1/smart-contracts/validate` | `POST /v1/smart-contracts/{id}:simulateExecution` | Nombre ligeramente distinto |
| `POST /v1/accounts` | `POST /v1/accounts` | Idéntico |
| `GET /v1/accounts` | `POST /v1/accounts:batchGet` | En real se usa batch |
| `X-Auth-Token` header | `X-Auth-Token` header | Idéntico |
| Contract in `contracts/*.py` | Contract en Vault Admin API | Sintaxis similar |
| Store in-memory | Cassandra-compatible storage | Obvio |

---

## Próximos pasos

- [ ] Persistencia opcional en SQLite para sobrevivir restarts
- [ ] Publicar eventos en un Kafka local (Redpanda) para testear el
      consumer del orquestador antes de tocar Vault real
- [ ] Support `batchGet` endpoint igualando el API real exacto
- [ ] Dockerfile + docker-compose para levantar todo el stack con `up`
- [ ] OpenAPI spec exportado a `docs/vault-mock-openapi.yaml`
