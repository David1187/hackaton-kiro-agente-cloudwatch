---
name: backend-standards
inclusion: fileMatch
fileMatchPattern: "services/**/*.py"
description: "Estándares de código backend para las Lambdas CRUD y el Agente strands de auto-reparación."
---

# Estándares de Backend (services/)

Esta guía aplica a todo el código bajo `services/`: las Lambdas CRUD (`services/crud_api/`) y el Agente de auto-reparación (`services/self_healing_agent/`). Complementa a `architecture-guide.md`, que sigue siendo la referencia de arquitectura global.

> ## ⚠️ LEER ANTES DE EDITAR `services/crud_api/handlers/`
>
> Los 5 handlers CRUD desplegados contienen **errores sembrados intencionales y permanentes** (SE-1 … SE-19), por decisión explícita del usuario para la demo del Hackathon. Ver `architecture-guide.md` sección 3 para la excepción completa.
>
> - **NO apliques los estándares de la sección 1 (manejo de errores), 2 (formato de respuesta) ni 4 (validación de payloads) a `services/crud_api/handlers/`.** Ese código está roto a propósito.
> - **NO "corrijas" un bug que encuentres ahí**, ni siquiera si es evidente, salvo que el usuario lo pida de forma explícita.
> - Las secciones de esta guía describen el **Comportamiento_Objetivo**: el estado al que el Agente de Auto-reparación debe llevar el código vía Pull Request.
> - Catálogo real y payloads de disparo: `services/crud_api/DEMO_ERRORS.md`.
> - Los tests de `services/crud_api/tests/` afirman el comportamiento **defectuoso** a propósito.
>
> Esta advertencia **no** aplica a `services/self_healing_agent/` ni a `services/crud_api/common/`: ahí los estándares de esta guía se aplican con normalidad.

## 1. Manejo de Errores (boto3 / DynamoDB)

- Capturar siempre excepciones **específicas** antes de cualquier catch-all genérico:
  - `botocore.exceptions.ClientError` para errores devueltos por el servicio AWS (throttling, `ConditionalCheckFailedException`, `ResourceNotFoundException`, etc.). Inspeccionar `error.response["Error"]["Code"]` cuando se necesite lógica distinta por tipo de error.
  - `botocore.exceptions.ParamValidationError` para parámetros inválidos antes de llegar al servicio.
- Un `except Exception` genérico solo se permite como última red de seguridad, después de los catches específicos, y siempre debe registrar el error con `logging.error(..., exc_info=True)` antes de devolver una respuesta 500.
- Nunca silenciar una excepción sin loguearla. Nunca hacer `except: pass`.

```python
import logging
import botocore

logger = logging.getLogger(__name__)

try:
    table.put_item(Item=item)
except botocore.exceptions.ClientError as error:
    logger.error("ERROR: fallo al escribir en DynamoDB", exc_info=True)
    return _error_response(500, "DDB_WRITE_ERROR", "No se pudo guardar el item.")
except botocore.exceptions.ParamValidationError as error:
    logger.error("ERROR: parámetros inválidos para DynamoDB", exc_info=True)
    return _error_response(400, "INVALID_PARAMS", "Parámetros de la petición inválidos.")
except Exception:
    logger.error("ERROR: fallo no controlado", exc_info=True)
    return _error_response(500, "INTERNAL_ERROR", "Error interno inesperado.")
```

## 2. Formato de Respuesta HTTP (Lambdas CRUD)

Toda Lambda CRUD detrás de API Gateway (integración Lambda proxy) debe devolver siempre este formato:

```python
{
    "statusCode": 200,
    "headers": {"Content-Type": "application/json"},
    "body": json.dumps(payload)
}
```

En caso de error, el `body` debe serializar el siguiente envelope estándar:

```python
{
    "error": {
        "code": "RESOURCE_NOT_FOUND",
        "message": "El item solicitado no existe."
    }
}
```

- `code`: identificador corto en `UPPER_SNAKE_CASE`, estable y documentable (no cambia entre idiomas ni versiones).
- `message`: texto descriptivo para debugging/logging del cliente de la API. No debe filtrar detalles internos (stack traces, nombres de tabla, ARNs).
- El `statusCode` debe reflejar semánticamente el error (`400` validación, `404` no encontrado, `500` error interno, etc.).

Centralizar la construcción de estas respuestas en un helper compartido (`services/crud_api/common/`) para evitar duplicar el formato en cada handler.

## 3. Inicialización de Clientes boto3

Instanciar el cliente/resource de DynamoDB (y cualquier otro cliente boto3) **a nivel de módulo**, fuera del handler, para reutilizar la conexión entre invocaciones en warm starts:

```python
import boto3
import os

# Nivel de módulo: se ejecuta una vez por contenedor de ejecución, no por invocación
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["TABLE_NAME"])

def handler(event, context):
    ...
```

Nunca instanciar `boto3.client(...)` o `boto3.resource(...)` dentro del cuerpo del handler.

## 4. Validación de Payloads

Antes de operar sobre el evento, validar explícitamente la presencia y tipo de los parámetros esperados (`partition_key`, atributos del body, etc.) para evitar `KeyError`/`TypeError` no controlados. Usar validación explícita (`if key not in body`) o un esquema (ej. `pydantic`) de forma consistente en todos los handlers de un mismo servicio.

## 5. Convención de Variables de Entorno

- Formato `UPPER_SNAKE_CASE`, sin prefijo de aplicación (ej. `TABLE_NAME`, `MODEL_ID`, `GITHUB_TAG_KEY`), tal como se fija en `architecture-guide.md` para `model_id` de Bedrock.
- Nunca leer secretos (tokens, credenciales) desde variables de entorno. Los secretos se gestionan exclusivamente según la sección 2.2 de `architecture-guide.md`.

## 6. Agente Strands (`services/self_healing_agent/`)

### 6.1. Integración MCP

Pasar el `MCPClient` directamente al constructor de `Agent`, dejando que la librería gestione el ciclo de vida de la conexión automáticamente. No gestionar manualmente `stdio_client`/streams de conexión:

```python
from strands import Agent
from strands.tools.mcp import MCPClient

mcp_client = MCPClient(lambda: streamable_http_client(github_mcp_url))

# Correcto: conexión gestionada automáticamente por el SDK
agent = Agent(tools=[mcp_client])
```

### 6.2. Modelo Bedrock

Configurar `BedrockModel` con el `model_id` leído desde la variable de entorno `MODEL_ID` (nunca hardcodeado):

```python
import os
from strands.models import BedrockModel

bedrock_model = BedrockModel(
    model_id=os.environ["MODEL_ID"],
    temperature=0.3,
)
```

### 6.3. Empaquetado y Despliegue

- El Agente se despliega en Amazon Bedrock AgentCore Runtime mediante **direct code deployment (.zip)**, usando el SDK `bedrock-agentcore` con el decorador `@app.entrypoint`. Queda prohibido el despliegue mediante contenedor Docker/ECR para este proyecto: aunque AgentCore Runtime en modo contenedor también es serverless en cuanto a cómputo, introduce Docker/ECR en el flujo de build, lo cual contradice el estándar "sin contenedores ni ECR" fijado en `architecture-guide.md` sección 3, y AWS recomienda direct code deployment como opción por defecto cuando `uv` está disponible (que es el caso en este proyecto).
- El entrypoint debe implementar el contrato de AgentCore Runtime: usar `@app.entrypoint` del SDK `bedrock-agentcore`, o exponer manualmente `/invocations` (POST) y `/ping` (GET) si no se usa el decorador.
- El paquete `.zip` debe compilarse para arquitectura **arm64** (único ISA soportado por AgentCore Runtime), respetando el límite de 250 MB comprimido / 750 MB descomprimido.

## 7. Testing

- Framework: `pytest`.
- Mock de DynamoDB en tests unitarios de las Lambdas CRUD: `moto` (decorador `@mock_aws` o fixture equivalente), nunca contra una tabla real. `moto` depende directamente de la versión de `boto3`/`botocore` instalada: al fijar `moto` en `requirements-dev.txt`, verificar compatibilidad con la versión de `boto3` resuelta en `requirements.txt` (consultar Context7/changelog de `moto` si hay dudas), para evitar fallos de test por desincronización entre ambas.
- Cada handler debe tener al menos un test de camino feliz y un test por cada rama de error controlada (`ClientError`, `ParamValidationError`, validación de payload).
- Los tests del Agente strands deben mockear las llamadas MCP y Bedrock; no deben invocar servicios reales de AWS ni GitHub.

## 8. Versionado del Stack

- **Python:** 3.13 fijo (LTS, runtime soportado por AWS Lambda y arquitectura arm64 de AgentCore Runtime). No usar una versión distinta salvo decisión explícita.
- **Librerías de producción** (`boto3`, `strands-agents`, `bedrock-agentcore`): estas evolucionan con mucha frecuencia. No hardcodear una versión de memoria: antes de fijar el pin en `requirements.txt`, resolver la versión estable más reciente vía el power de Context7 o el MCP `bedrock-agentcore-mcp-server`/`aws-docs` en el momento de implementar, y fijarla como versión exacta (`==`), nunca como rango abierto.
- **Dependencia transitiva a vigilar:** `strands-agents` y el SDK `bedrock-agentcore` dependen de `pydantic` v2. No añadir ninguna otra librería que requiera `pydantic` v1 en el mismo entorno: es un conflicto de instalación, no una preferencia de estilo.
- **Herramientas de desarrollo/testing** (`pytest`, `bandit`): declarar sus versiones en un `requirements-dev.txt` por servicio (`services/crud_api/requirements-dev.txt`, `services/self_healing_agent/requirements-dev.txt`), separado del `requirements.txt` de runtime. Fijar versión exacta (`==`), igual que las de producción, para que `backend-agent` y `reviewer-agent` ejecuten siempre la misma versión y no haya diferencias de resultado entre quien escribe el test y quien lo audita.
- **Herramientas de auditoría de seguridad** (`pip-audit`, `gitleaks`/`detect-secrets`): estas son responsabilidad de `reviewer-agent`, no de `backend-agent`, y **nunca deben fijarse a una versión pinneada**. Su utilidad depende de tener las reglas/base de datos de vulnerabilidades más recientes en el momento de la ejecución; fijarlas a una versión antigua las volvería ciegas a hallazgos posteriores. Ejecutar siempre la última versión disponible en el momento de la auditoría.
