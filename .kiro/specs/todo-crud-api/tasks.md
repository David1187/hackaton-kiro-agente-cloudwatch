# Implementation Plan: todo-crud-api

## Overview

Este plan convierte el diseño de `todo-crud-api` en pasos de código incrementales en **Python 3.13**, desplegados 100% serverless con **AWS CDK en Python**. El orden construye primero la capa común compartida (`services/crud_api/common/`), luego los cinco handlers CRUD del Comportamiento_Objetivo (Requisitos 1–9), después el Código_Sembrado intencional (Requisitos 10–11) y, por último, la infraestructura CDK (tabla DynamoDB, 5 Lambdas con tag `github-repo` y mínimo privilegio, API Gateway REST con Usage Plan + API Key, y la observabilidad Log Group → Metric Filter `ERROR:` → Alarm) que integra todo. Cada bloque termina cableando sus piezas para que no quede código huérfano.

Las pruebas basadas en propiedades (`hypothesis` + `moto`) implementan las 17 Correctness Properties del diseño y se colocan junto al código que validan. Las sub-tareas de test están marcadas con `*` (opcionales para un MVP rápido).

## Tasks

- [x] 1. Configurar estructura del proyecto y dependencias
  - [x] 1.1 Crear el esqueleto del paquete `services/crud_api` y los manifiestos de dependencias
    - Crear la estructura de directorios: `services/crud_api/{common,handlers,seeded,tests}/` con sus `__init__.py`
    - Crear `services/crud_api/requirements.txt` con `boto3` fijado a versión exacta (`==`), resolviendo la versión estable más reciente en el momento de implementar
    - Crear `services/crud_api/requirements-dev.txt` con `pytest`, `moto`, `hypothesis` y `bandit` fijados a versión exacta (`==`), verificando compatibilidad de `moto` con la versión de `boto3`
    - Fijar Python 3.13 como runtime objetivo
    - _Requirements: 9.1, 7.3_

- [x] 2. Implementar la capa común compartida (`services/crud_api/common/`)
  - [x] 2.1 Implementar `DecimalEncoder` (`encoding.py`)
    - Subclase de `json.JSONEncoder` que convierte `decimal.Decimal` a `int` (si es entero) o `float`
    - _Requirements: 2.1, 3.1_

  - [x] 2.2 Implementar helpers de respuesta (`responses.py`)
    - `success_response(status_code, payload)` y `error_response(status_code, code, message)` con el envelope estándar `{statusCode, headers, body}`
    - Serializar el body con `DecimalEncoder`; nunca incluir stack trace, nombres de tabla ni ARNs
    - _Requirements: 7.5, 3.4_

  - [x] 2.3 Implementar `Error_Logger` (`logging_config.py`)
    - `configure_logger(name)` que garantiza que cada registro de error comience por el prefijo `ERROR:` sin caracteres previos, usando el módulo estándar `logging` (no `print()`)
    - Emitir exactamente un registro por excepción con `exc_info=True`
    - _Requirements: 7.1, 7.2, 7.3_

  - [x] 2.4 Implementar `Payload_Validator` (`validation.py`)
    - `parse_json_body`, `validate_title`, `validate_completed`, `validate_task_id` y la excepción `ValidationError(code, message)` con `code` en `UPPER_SNAKE_CASE`
    - Rechazar body ausente/vacío/mal formado, `title` no-string o fuera de 1–255 tras `strip`, `completed` no booleano estricto, y `task_id` ausente/vacío/whitespace/>256
    - _Requirements: 1.4, 1.5, 1.6, 2.2, 2.3, 4.3, 4.4, 4.5, 4.6, 5.2, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

  - [x]* 2.5 Escribir property test de validación de `title`
    - **Property 4: Validación de `title`**
    - **Validates: Requirements 1.5, 4.5**

  - [x]* 2.6 Escribir property test de validación de `completed`
    - **Property 5: Validación de `completed`**
    - **Validates: Requirements 1.6, 4.6**

  - [x]* 2.7 Escribir property test de validación de `task_id`
    - **Property 6: Validación de `task_id`**
    - **Validates: Requirements 2.2, 2.3, 4.3, 5.2**

  - [x]* 2.8 Escribir property test de no filtración en respuestas de error
    - **Property 16: Las respuestas de error no filtran detalles internos**
    - **Validates: Requirements 7.4, 7.5**

  - [x] 2.9 Implementar `TaskRepository` (`repository.py`)
    - Instanciar `boto3.resource("dynamodb")` y `Table(os.environ["TABLE_NAME"])` a nivel de módulo
    - Métodos `create`, `get`, `list(limit=1000)`, `update`, `delete`; encapsular cada I/O en try-except; traducir `ConditionalCheckFailedException` a `NotFoundError` (→404) y otros `ClientError` a log `ERROR:` + propagación (→500); devolver `task_id` en escrituras confirmadas
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

  - [x]* 2.10 Escribir property test de traducción de errores del repositorio
    - **Property 17: La Persistence_Layer traduce los errores del cliente DynamoDB de forma controlada**
    - **Validates: Requirements 9.2, 9.5**

  - [x]* 2.11 Escribir unit tests de `Error_Logger` y `DecimalEncoder`
    - Verificar prefijo `ERROR:` sin caracteres previos y un único registro por excepción; verificar serialización de `Decimal` a `int`/`float`
    - _Requirements: 7.1, 7.2, 3.1_

- [x] 3. Checkpoint - Asegurar que pasan los tests de la capa común
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implementar los handlers CRUD (Comportamiento_Objetivo)
  - [x] 4.1 Implementar `Create_Handler` (`handlers/create_task.py`)
    - Parsear y validar el body vía la capa común; generar `task_id` (`uuid4`); fijar `created_at`/`updated_at` al mismo instante ISO 8601 UTC con milisegundos; `completed` default `false`; persistir vía `TaskRepository`; responder 201 con la Task; jerarquía de `try-except` con red de seguridad
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

  - [x]* 4.2 Escribir property test de creación válida y persistida
    - **Property 1: Creación produce una Task válida y persistida**
    - **Validates: Requirements 1.1, 1.2, 9.3**

  - [x]* 4.3 Escribir property test de igualdad y formato de marcas de tiempo
    - **Property 2: Igualdad y formato de marcas de tiempo en creación**
    - **Validates: Requirements 1.3**

  - [x]* 4.4 Escribir property test de rechazo de body no-JSON
    - **Property 3: El cuerpo no-JSON o inválido se rechaza sin persistir**
    - **Validates: Requirements 1.4, 4.4, 6.3**

  - [x] 4.5 Implementar `Get_Handler` (`handlers/get_task.py`)
    - Validar `task_id`; `get_item` vía repositorio; 404 si no existe; 200 con la Task serializada con `DecimalEncoder`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x]* 4.6 Escribir property test de round trip create → get
    - **Property 7: Round trip create → get**
    - **Validates: Requirements 2.1**

  - [x]* 4.7 Escribir property test de consulta de `task_id` inexistente
    - **Property 8: Consultar un `task_id` inexistente devuelve 404**
    - **Validates: Requirements 2.4**

  - [x] 4.8 Implementar `List_Handler` (`handlers/list_tasks.py`)
    - `scan` con límite de 1000; 200 con `{tasks, count}` (colección vacía si no hay Tasks); serializar con `DecimalEncoder`
    - _Requirements: 3.1, 3.2, 3.4_

  - [x]* 4.9 Escribir property test de listado que refleja el conjunto persistido
    - **Property 9: El listado refleja el conjunto persistido**
    - **Validates: Requirements 3.1, 3.2**

  - [x] 4.10 Implementar `Update_Handler` (`handlers/update_task.py`)
    - Validar `task_id` y body (al menos uno de `title`/`completed`); construir `UpdateExpression` solo con atributos presentes + `updated_at`; `ConditionExpression attribute_exists(task_id)` para 404; 200 con la Task actualizada
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8_

  - [x]* 4.11 Escribir property test de actualización parcial
    - **Property 10: La actualización parcial modifica solo los atributos presentes**
    - **Validates: Requirements 4.1**

  - [x]* 4.12 Escribir property test de refresco de `updated_at` y preservación de `created_at`
    - **Property 11: La actualización refresca `updated_at` y preserva `created_at`**
    - **Validates: Requirements 4.2**

  - [x] 4.13 Implementar `Delete_Handler` (`handlers/delete_task.py`)
    - Validar `task_id` no vacío; `delete_item` con `ConditionExpression attribute_exists(task_id)` para 404; 200 con confirmación que incluye el `task_id` eliminado
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [x]* 4.14 Escribir property test de round trip create → delete → get
    - **Property 13: Round trip create → delete → get**
    - **Validates: Requirements 5.1**

  - [x]* 4.15 Escribir property test de operación condicional sobre Task inexistente
    - **Property 12: Operación condicional sobre Task inexistente devuelve 404**
    - **Validates: Requirements 4.7, 5.3, 9.4**

  - [x]* 4.16 Escribir property test de que la entrada inválida nunca alcanza la persistencia
    - **Property 14: La entrada inválida nunca alcanza la persistencia; la válida sí**
    - **Validates: Requirements 6.1, 6.2, 6.4, 6.5, 6.6**

  - [x]* 4.17 Escribir property test de registro único con prefijo `ERROR:`
    - **Property 15: Todo error se registra exactamente una vez con el prefijo `ERROR:`**
    - **Validates: Requirements 7.1, 7.2**

  - [x]* 4.18 Escribir unit tests de las ramas de error de I/O de los handlers
    - Mocks que fuerzan `ClientError` en cada handler; verificar respuesta 500 sin detalles internos y registro `ERROR:`
    - _Requirements: 1.7, 2.5, 3.4, 4.8, 5.4_

- [x] 5. Checkpoint - Asegurar que pasan los tests de handlers y propiedades
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implementar el Código_Sembrado (errores controlados, desplegados de forma permanente)
  - [x] 6.1 Implementar las variantes sembradas de los handlers (`services/crud_api/seeded/`)
    - Reproducir de forma aislada y determinista los Seeded_Errors SE-1…SE-9 del catálogo, cada uno disparable por su entrada documentada
    - Mantener cada Seeded_Error aislado en su módulo de referencia
    - **Retirado en la tarea 9:** el paquete se eliminó por redundante una vez los errores quedaron fusionados en los handlers desplegados (6.4)
    - _Requirements: 10.1, 10.3, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9_

  - [x] 6.2 Documentar el catálogo en `services/crud_api/seeded/SEEDED_ERRORS.md`
    - Declarar el código sembrado como intencional y controlado; por cada Seeded_Error documentar operación, entrada disparadora y el número de requisito del Comportamiento_Objetivo (1–9)
    - **Retirado en la tarea 9:** la documentación de referencia del catálogo pasó a la sección 6 de `design.md`
    - _Requirements: 10.2_

  - [x]* 6.3 Escribir tests de ejemplo deterministas del Código_Sembrado
    - Por cada SE-1…SE-9: ejecutar el handler con la entrada documentada y verificar el comportamiento defectuoso esperado y su detectabilidad (o no) por el marcador `ERROR:`
    - **Retirado en la tarea 9:** `tests/test_seeded.py` se eliminó junto al paquete; la cobertura se consolidó en `tests/test_handlers.py`
    - _Requirements: 10.3, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9_

  - [x] 6.4 Fusionar los Seeded_Errors en los handlers desplegados (`services/crud_api/handlers/`)
    - Distribuir el catálogo completo en las 5 Lambdas que se despliegan: Create → SE-1, SE-2, SE-8; Get → SE-3, SE-9; List → SE-4; Update → SE-5, SE-6; Delete → SE-7 (ampliado en la tarea 9 hasta SE-19)
    - Marcar cada bug en el código con un comentario localizable (`# [SE-n] BUG INTENCIONAL: ...`) indicando el comportamiento correcto esperado
    - Adaptar los tests para que afirmen el comportamiento defectuoso (es el contrato real desplegado)
    - _Requirements: 10.1, 10.3, 10.6_

  - [x] 6.5 Documentar los payloads de disparo en `services/crud_api/DEMO_ERRORS.md`
    - Por cada SE-1…SE-19: endpoint real, comando `curl` completo, respuesta HTTP esperada, log resultante y si activa o no el Metric_Filter
    - Identificar explícitamente el subconjunto silencioso y los enmascaramientos entre errores del mismo handler
    - _Requirements: 10.5, 10.6, 10.7_

- [x] 7. Implementar la infraestructura CDK (IaC en Python)
  - [x] 7.1 Crear el esqueleto de la app CDK
    - `app.py`, `cdk.json`, paquete `infra/` y `requirements.txt` raíz para CDK; definir el stack base
    - _Requirements: 8.1_

  - [x] 7.2 Definir la tabla DynamoDB
    - PK `task_id` (String), sin sort key, facturación on-demand (PAY_PER_REQUEST)
    - _Requirements: 9.1_

  - [x] 7.3 Definir las 5 funciones Lambda con IAM de mínimo privilegio y tag `github-repo`
    - Runtime `python3.13`, arquitectura `arm64`, empaquetado zip; variable de entorno `TABLE_NAME`; permisos por operación (Create/Update/Delete → escritura; Get → `GetItem`; List → `Scan`); tag `github-repo` con valor `owner/repo` en cada Lambda
    - _Requirements: 8.3_

  - [x] 7.4 Definir el API Gateway REST (OpenAPI) + Usage Plan + API Key
    - REST API definido con OpenAPI, `apiKeyRequired: true` en cada método; rutas POST/GET `/tasks`, GET/PUT/DELETE `/tasks/{task_id}` integradas (Lambda proxy) con sus handlers; Usage Plan con rate 100/s, burst 200, quota 10.000/día
    - _Requirements: 8.1, 8.2, 8.4, 8.5, 8.6_

  - [x] 7.5 Definir la observabilidad (Log Group, Metric Filter `ERROR:`, Alarm)
    - Un Log Group por Lambda con retención; Metric Filter con `filterPattern` literal `ERROR:` publicando métrica de conteo; CloudWatch Alarm (threshold ≥ 1) cuyo cambio de estado se publica en EventBridge
    - _Requirements: 7.2_

  - [x]* 7.6 Escribir tests de aserción/smoke de CDK
    - `aws_cdk.assertions.Template`: 5 rutas/métodos con `apiKeyRequired`, Usage Plan (rate 100/s, burst 200, quota 10.000/día), tabla con PK `task_id` (String), tag `github-repo` en cada Lambda, y ausencia de `print()` en `services/crud_api` (verificación estática / `bandit`)
    - _Requirements: 8.1, 8.5, 9.1, 7.3_

  - [ ]* 7.7 Escribir tests de integración del API Gateway
    - Peticiones con API Key ausente/inválida (403), válida con enrutamiento correcto, ruta/método no definidos (rechazo sin invocar Lambda) y superación de límites (429); 1–3 ejemplos representativos
    - _Requirements: 8.2, 8.3, 8.4, 8.6, 3.3_

- [x] 8. Checkpoint final - Asegurar que pasan todos los tests
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Ampliar el catálogo de Código_Sembrado para que las 5 alarmas sean disparables
  - Añadidos SE-10 a SE-19 en los 5 handlers, con al menos un error detectable por Metric Filter en cada Lambda. Errores de conversión de tipos hacia DynamoDB (`Decimal` vs `float`), clave de partición incorrecta en nombre y en tipo, parámetros de consulta sin validar (`ProjectionExpression` con palabra reservada, `Limit` sin castear, `ExclusiveStartKey` sin decodificar), acceso a atributos inexistentes del item y validación cruzada del campo equivocado
  - SE-8 transformado (el error de validación deja de capturarse, el resto sí) y SE-9 reducido (`print()` solo en la rama 404), para que Create y Get puedan emitir un stack trace con el marco del propio handler
  - Eliminado el paquete redundante `services/crud_api/seeded/` (9 variantes aisladas, `SEEDED_ERRORS.md`) y `tests/test_seeded.py`; la documentación del catálogo pasa a `design.md` §6 y la operativa de disparo a `DEMO_ERRORS.md`
  - Declarados en `api/openapi.yaml` los query params `limit` y `next` (GET /tasks) y `fields` (GET /tasks/{task_id})
  - Cobertura de comportamiento defectuoso consolidada en `tests/test_handlers.py`
  - _Requirements: 10.1, 10.4, 10.6, 11.8, 11.9, 11.10, 11.11, 11.12, 11.13, 11.14, 11.15, 11.16, 11.17, 11.18, 11.19_

## Notes

- Las sub-tareas marcadas con `*` son opcionales y pueden omitirse para un MVP más rápido.
- Cada tarea referencia requisitos específicos para trazabilidad.
- Los property tests validan las 17 Correctness Properties universales del diseño (`hypothesis` + `moto`, mínimo 100 ejemplos por test).
- Los tests del Código_Sembrado (6.3, 6.4) fijan el comportamiento defectuoso desplegado: su afirmación del bug es intencional. Un test que falle porque el bug ya no se reproduce indica que alguien rompió la demo, no que el código haya mejorado. No "corregir" los handlers de `handlers/` salvo petición explícita del usuario.
- Los checkpoints aseguran validación incremental antes de avanzar.
- Toda I/O contra DynamoDB usa `moto` (`@mock_aws`) en tests; nunca una tabla real.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "2.3", "7.1"] },
    { "id": 2, "tasks": ["2.2", "2.4", "2.9"] },
    { "id": 3, "tasks": ["2.5", "2.6", "2.7", "2.8", "2.10", "2.11", "4.1", "4.5", "4.8", "4.10", "4.13"] },
    { "id": 4, "tasks": ["4.2", "4.3", "4.4", "4.6", "4.7", "4.9", "4.11", "4.12", "4.14", "4.15", "4.16", "4.17", "4.18", "6.1", "6.2", "7.2"] },
    { "id": 5, "tasks": ["6.3", "7.3"] },
    { "id": 6, "tasks": ["7.4"] },
    { "id": 7, "tasks": ["7.5"] },
    { "id": 8, "tasks": ["7.6", "7.7"] }
  ]
}
```
