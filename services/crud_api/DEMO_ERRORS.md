# DEMO_ERRORS.md — Guía de Demostración de Errores Sembrados

> **Documento maestro de payloads para la demo del Agente de Auto-reparación.**
>
> Cada Lambda CRUD contiene errores intencionales y permanentes. Este documento
> describe cómo disparar cada uno de forma determinista e independiente.

---

## Obtener la API Key

```bash
aws apigateway get-api-keys --include-values --region eu-west-1 --query "items[].{name:name,value:value}" --output table
```

```bash
aws apigateway get-api-key --api-key zqjw57ws12 --include-value --region eu-west-1 --query value --output text
```

Usa el valor obtenido en el placeholder `<API_KEY>` de los comandos siguientes.

**API Base URL:** `https://xr6uq6n947.execute-api.eu-west-1.amazonaws.com/prod`

---

## Estado Operativo de la API

| Operación | ¿Camino feliz funciona? | Notas |
|---|---|---|
| **POST /tasks** (create) | ✅ Sí (con title válido string, sin priority float ni task_id numérico) | SE-1 y SE-2 son silenciosos |
| **GET /tasks** (list) | ✅ Sí (sin query params limit/next) | SE-4 silencioso con tabla pequeña |
| **PUT /tasks/{id}** (update) | ✅ Solo para `title` (string) | SE-16 impide actualizar `completed` |
| **GET /tasks/{id}** (get) | ❌ **500 SIEMPRE** (SE-13 incondicional) | task['completed_at'] no existe |
| **DELETE /tasks/{id}** (delete) | ❌ **500 SIEMPRE** (SE-18/SE-19 incondicional) | PK incorrecta siempre |

---

## Tabla Resumen para el Jurado

| ID | Lambda | Endpoint | Payload de disparo | HTTP esperado | Dispara `ERROR:` | Agente detecta |
|---|---|---|---|---|---|---|
| SE-1 | create_task | POST /tasks | `{"title": "   "}` | 201 (debería ser 400) | ❌ No | Solo inspección |
| SE-2 | create_task | POST /tasks | `{"title": "Cualquier texto"}` | 201 (timestamp=1970) | ❌ No | Solo inspección |
| SE-3 | get_task | GET /tasks/{id} | pathParameters ausente (solo via `aws lambda invoke`) | 500 | ✅ Sí (SE-9 reducido) | ✅ Automático |
| SE-4 | list_tasks | GET /tasks | Tabla con >1000 items | 200 (todos) | ❌ No (tabla pequeña) | Solo inspección |
| SE-5 | update_task | PUT /tasks/{id} | `{}` (body vacío) | 500 | ✅ Sí | ✅ Automático |
| SE-6 | update_task | PUT /tasks/{id} | `{"title": "Nuevo"}` | 200 (updated_at congelado) | ❌ No | Solo inspección |
| SE-7 | delete_task | DELETE /tasks/{id} | *(ENMASCARADO por SE-18)* | — | — | — |
| SE-8 | create_task | POST /tasks | body no JSON | 502 Bad Gateway | ❌ No (ValidationError) | Solo inspección |
| SE-9 | get_task | GET /tasks/{id} | ID inexistente | 404 (sin log ERROR:) | ❌ No (print en 404) | Solo inspección |
| SE-10 | create_task | POST /tasks | `{"title":"x","priority":3.5}` | 500 | ✅ Sí | ✅ Automático |
| SE-11 | create_task | POST /tasks | `{"title":"x","task_id":123}` | 500 | ✅ Sí | ✅ Automático |
| SE-12 | get_task | GET /tasks/{id}?fields=status | query param `fields=status` | 500 | ✅ Sí | ✅ Automático |
| SE-13 | get_task | GET /tasks/{id} | Cualquier GET válido | 500 (SIEMPRE) | ✅ Sí | ✅ Automático |
| SE-14 | list_tasks | GET /tasks?limit=10 | query param `limit` (cualquier valor) | 500 | ✅ Sí | ✅ Automático |
| SE-15 | list_tasks | GET /tasks?next=abc | query param `next` (cualquier valor) | 500 | ✅ Sí | ✅ Automático |
| SE-16 | update_task | PUT /tasks/{id} | `{"completed":true}` | 400 | ✅ Sí | ✅ Automático |
| SE-17 | update_task | PUT /tasks/{id} | `{"title":"x","priority":2.5}` | 500 | ✅ Sí | ✅ Automático |
| SE-18 | delete_task | DELETE /tasks/{id} | Cualquier task_id no numérico | 500 (SIEMPRE) | ✅ Sí | ✅ Automático |
| SE-19 | delete_task | DELETE /tasks/{id} | task_id solo dígitos (ej: "12345") | 500 | ✅ Sí | ✅ Automático |

### Notas sobre transformaciones y enmascaramientos

- **SE-3**: Solo disparable invocando la Lambda directamente con `aws lambda invoke`. API Gateway siempre inyecta `pathParameters` con `task_id` en `/tasks/{task_id}`, por lo que por HTTP es inalcanzable. Tras la reducción de SE-9, el KeyError SÍ se loguea con `ERROR:`.
- **SE-7 ENMASCARADO**: El código de SE-7 (delete sin ConditionExpression) sigue presente pero NUNCA se alcanza. SE-19 (task_id numérico) y SE-18 (PK incorrecta) se evalúan ANTES y siempre producen ClientError, impidiendo que se ejecute el delete original.
- **SE-8 TRANSFORMADO**: Pasó de "sin try-except" a "manejo de errores incompleto". Ahora captura ClientError/ParamValidationError/Exception con logger.error(), pero NO captura ValidationError → sigue propagándose (502).
- **SE-9 REDUCIDO**: Ahora usa print() SOLO en el except NotFoundError (404 invisible). Los demás except (ClientError, ParamValidationError, Exception) usan logger.error() → SÍ disparan Metric Filter.

---

## Errores Detallados



### SE-1 — Create sin validación de `title`

| Campo | Valor |
|---|---|
| **Lambda/Handler** | `create_task` / `handlers/create_task.py` |
| **Endpoint** | `POST /tasks` |
| **Bug** | No llama a `Payload_Validator.validate_title()` — acepta títulos vacíos |
| **Dispara `ERROR:`** | ❌ No |

```bash
curl -X POST "https://xr6uq6n947.execute-api.eu-west-1.amazonaws.com/prod/tasks" \
  -H "Content-Type: application/json" -H "x-api-key: <API_KEY>" \
  -d '{"title": "   "}'
```

**Respuesta:** `HTTP 201` con `"title": "   "` (debería ser 400 INVALID_TITLE).

---

### SE-2 — Create con timestamp hardcoded

| Campo | Valor |
|---|---|
| **Lambda/Handler** | `create_task` / `handlers/create_task.py` |
| **Endpoint** | `POST /tasks` |
| **Bug** | `created_at` y `updated_at` siempre `"1970-01-01T00:00:00.000+00:00"` |
| **Dispara `ERROR:`** | ❌ No |

```bash
curl -X POST "https://xr6uq6n947.execute-api.eu-west-1.amazonaws.com/prod/tasks" \
  -H "Content-Type: application/json" -H "x-api-key: <API_KEY>" \
  -d '{"title": "Mi tarea de prueba"}'
```

**Respuesta:** `HTTP 201` con timestamps `1970-01-01T00:00:00.000+00:00`.

---

### SE-3 — Get sin manejo de `task_id` ausente (KeyError)

| Campo | Valor |
|---|---|
| **Lambda/Handler** | `get_task` / `handlers/get_task.py` |
| **Endpoint** | `GET /tasks/{task_id}` |
| **Bug** | Accede a `event["pathParameters"]["task_id"]` directamente → `KeyError` |
| **Dispara `ERROR:`** | ✅ Sí (tras reducción de SE-9, except Exception usa logger.error()) |

> **⚠️ SOLO disparable via `aws lambda invoke`** — API Gateway siempre inyecta pathParameters.

```bash
aws lambda invoke \
  --function-name <NOMBRE_FUNCION_GET_TASK> \
  --region eu-west-1 \
  --payload '{"httpMethod": "GET", "path": "/tasks"}' \
  --cli-binary-format raw-in-base64-out \
  /dev/stdout
```

**Respuesta:** `HTTP 500` `{"error":{"code":"INTERNAL_ERROR","message":"Error interno inesperado."}}`

---

### SE-4 — List sin límite en el scan

| Campo | Valor |
|---|---|
| **Lambda/Handler** | `list_tasks` / `handlers/list_tasks.py` |
| **Endpoint** | `GET /tasks` |
| **Bug** | `table.scan()` sin `Limit` cuando no hay query params |
| **Dispara `ERROR:`** | ❌ No (tabla pequeña) / ✅ Sí (tabla grande → throttling) |

```bash
curl -X GET "https://xr6uq6n947.execute-api.eu-west-1.amazonaws.com/prod/tasks" \
  -H "x-api-key: <API_KEY>"
```

**Respuesta:** `HTTP 200` con todos los items (observable por inspección del código).

---

### SE-5 — Update sin validación de campos presentes (body vacío)

| Campo | Valor |
|---|---|
| **Lambda/Handler** | `update_task` / `handlers/update_task.py` |
| **Endpoint** | `PUT /tasks/{task_id}` |
| **Bug** | No valida que body contenga `title` o `completed` → body `{}` llega a DynamoDB |
| **Dispara `ERROR:`** | ✅ **Sí** — ParamValidationError |

```bash
# Crear tarea primero, luego:
curl -X PUT "https://xr6uq6n947.execute-api.eu-west-1.amazonaws.com/prod/tasks/<TASK_ID>" \
  -H "Content-Type: application/json" -H "x-api-key: <API_KEY>" \
  -d '{}'
```

**Respuesta:** `HTTP 500` `{"error":{"code":"DDB_PARAM_ERROR","message":"Error de parametros en la base de datos."}}`



---

### SE-6 — Update sin actualizar `updated_at`

| Campo | Valor |
|---|---|
| **Lambda/Handler** | `update_task` / `handlers/update_task.py` |
| **Endpoint** | `PUT /tasks/{task_id}` |
| **Bug** | No añade `updated_at` a attrs → campo congelado |
| **Dispara `ERROR:`** | ❌ No |

```bash
curl -X PUT "https://xr6uq6n947.execute-api.eu-west-1.amazonaws.com/prod/tasks/<TASK_ID>" \
  -H "Content-Type: application/json" -H "x-api-key: <API_KEY>" \
  -d '{"title": "Titulo modificado"}'
```

**Respuesta:** `HTTP 200` con `updated_at` congelado en valor de creación.

---

### SE-7 — Delete sin ConditionExpression (ENMASCARADO)

| Campo | Valor |
|---|---|
| **Lambda/Handler** | `delete_task` / `handlers/delete_task.py` |
| **Endpoint** | `DELETE /tasks/{task_id}` |
| **Bug** | `delete_item()` sin `ConditionExpression` — PERO nunca se alcanza |
| **Dispara `ERROR:`** | — (enmascarado por SE-18/SE-19) |

> **⚠️ ENMASCARADO:** El código de SE-7 existe pero SE-18 y SE-19 se ejecutan antes y siempre lanzan ClientError, impidiendo que el flujo llegue al delete original sin condición. Para observar SE-7 aislado habría que corregir SE-18/SE-19.

---

### SE-8 — Manejo de errores incompleto (TRANSFORMADO)

| Campo | Valor |
|---|---|
| **Lambda/Handler** | `create_task` / `handlers/create_task.py` |
| **Endpoint** | `POST /tasks` |
| **Bug** | Captura ClientError/ParamValidationError/Exception, pero NO ValidationError |
| **Dispara `ERROR:`** | ❌ No (ValidationError se propaga al runtime → `[ERROR]` sin `:`) |

> **TRANSFORMADO:** Originalmente era "sin try-except". Ahora tiene try-except parcial: ValidationError no se captura → se propaga → 502 en API Gateway.

```bash
curl -X POST "https://xr6uq6n947.execute-api.eu-west-1.amazonaws.com/prod/tasks" \
  -H "Content-Type: application/json" -H "x-api-key: <API_KEY>" \
  -d 'esto no es json'
```

**Respuesta:** `HTTP 502` `{"message": "Internal server error"}`

---

### SE-9 — print() en NotFoundError (REDUCIDO)

| Campo | Valor |
|---|---|
| **Lambda/Handler** | `get_task` / `handlers/get_task.py` |
| **Endpoint** | `GET /tasks/{task_id}` |
| **Bug** | Usa `print()` SOLO en except NotFoundError → 404 invisible para alarmas |
| **Dispara `ERROR:`** | ❌ No (solo en el 404; los demás except ahora SÍ usan logger.error()) |

> **REDUCIDO:** Originalmente todos los except usaban print(). Ahora solo NotFoundError usa print(); ClientError/ParamValidationError/Exception usan logger.error().

```bash
curl -X GET "https://xr6uq6n947.execute-api.eu-west-1.amazonaws.com/prod/tasks/tarea-fantasma-xyz" \
  -H "x-api-key: <API_KEY>"
```

**Respuesta:** `HTTP 500` (por SE-13, no 404) — ver SE-13. Si SE-13 no existiera, sería 404 sin ERROR:.

---

### SE-10 — Create con priority float sin convertir a Decimal

| Campo | Valor |
|---|---|
| **Lambda/Handler** | `create_task` / `handlers/create_task.py` |
| **Endpoint** | `POST /tasks` |
| **Bug** | Si body trae `priority` float, se copia sin Decimal() → TypeError |
| **Dispara `ERROR:`** | ✅ **Sí** — TypeError capturado por except Exception |

```bash
curl -X POST "https://xr6uq6n947.execute-api.eu-west-1.amazonaws.com/prod/tasks" \
  -H "Content-Type: application/json" -H "x-api-key: <API_KEY>" \
  -d '{"title": "Tarea con prioridad", "priority": 3.5}'
```

**Respuesta:** `HTTP 500` `{"error":{"code":"INTERNAL_ERROR","message":"Error interno inesperado."}}`

**CloudWatch Logs:** `ERROR: fallo no controlado en create_task` + traceback con `TypeError: Float types are not supported. Use Decimal types instead.`

---

### SE-11 — Create con task_id numérico del body

| Campo | Valor |
|---|---|
| **Lambda/Handler** | `create_task` / `handlers/create_task.py` |
| **Endpoint** | `POST /tasks` |
| **Bug** | Si body trae `task_id`, se usa sin validar tipo. Con valor numérico → ClientError |
| **Dispara `ERROR:`** | ✅ **Sí** — ClientError capturado |

```bash
curl -X POST "https://xr6uq6n947.execute-api.eu-west-1.amazonaws.com/prod/tasks" \
  -H "Content-Type: application/json" -H "x-api-key: <API_KEY>" \
  -d '{"title": "Tarea con ID numerico", "task_id": 123}'
```

**Respuesta:** `HTTP 500` `{"error":{"code":"DDB_ERROR","message":"Error al acceder a la base de datos."}}`

**CloudWatch Logs:** `ERROR: ClientError en create_task` + traceback con type mismatch (PK esperada S, recibida N).



---

### SE-12 — Get con ProjectionExpression sin escapar (palabra reservada)

| Campo | Valor |
|---|---|
| **Lambda/Handler** | `get_task` / `handlers/get_task.py` |
| **Endpoint** | `GET /tasks/{task_id}?fields=status` |
| **Bug** | Pasa query param `fields` como ProjectionExpression sin ExpressionAttributeNames |
| **Dispara `ERROR:`** | ✅ **Sí** — ClientError capturado |

```bash
curl -X GET "https://xr6uq6n947.execute-api.eu-west-1.amazonaws.com/prod/tasks/<TASK_ID>?fields=status" \
  -H "x-api-key: <API_KEY>"
```

**Respuesta:** `HTTP 500` `{"error":{"code":"DDB_ERROR","message":"Error al acceder a la base de datos."}}`

**CloudWatch Logs:** `ERROR: ClientError en get_task` + traceback con `Invalid ProjectionExpression: Attribute name is a reserved keyword; status`.

> **Nota:** Este error se produce ANTES de SE-13 solo si `fields` está presente. Sin `fields`, SE-13 se activa primero.

---

### SE-13 — Get accede a task['completed_at'] inexistente (INCONDICIONAL)

| Campo | Valor |
|---|---|
| **Lambda/Handler** | `get_task` / `handlers/get_task.py` |
| **Endpoint** | `GET /tasks/{task_id}` |
| **Bug** | Accede a `task['completed_at']` — atributo que nunca existe → KeyError → 500 |
| **Dispara `ERROR:`** | ✅ **Sí** — KeyError capturado por except Exception |

```bash
curl -X GET "https://xr6uq6n947.execute-api.eu-west-1.amazonaws.com/prod/tasks/<TASK_ID>" \
  -H "x-api-key: <API_KEY>"
```

**Respuesta:** `HTTP 500` `{"error":{"code":"INTERNAL_ERROR","message":"Error interno inesperado."}}`

**CloudWatch Logs:** `ERROR: fallo no controlado en get_task` + traceback con `KeyError: 'completed_at'`.

> **⚠️ INCONDICIONAL:** TODO GET devuelve 500. No hay camino feliz para esta operación.

---

### SE-14 — List con limit string sin castear a int

| Campo | Valor |
|---|---|
| **Lambda/Handler** | `list_tasks` / `handlers/list_tasks.py` |
| **Endpoint** | `GET /tasks?limit=10` |
| **Bug** | Pasa el query param `limit` como string a scan(Limit=...) sin int() |
| **Dispara `ERROR:`** | ✅ **Sí** — ParamValidationError |

```bash
curl -X GET "https://xr6uq6n947.execute-api.eu-west-1.amazonaws.com/prod/tasks?limit=10" \
  -H "x-api-key: <API_KEY>"
```

**Respuesta:** `HTTP 500` `{"error":{"code":"DDB_PARAM_ERROR","message":"Error de parametros en la base de datos."}}`

**CloudWatch Logs:** `ERROR: ParamValidationError en list_tasks` + traceback.

---

### SE-15 — List con next token string sin decodificar a dict

| Campo | Valor |
|---|---|
| **Lambda/Handler** | `list_tasks` / `handlers/list_tasks.py` |
| **Endpoint** | `GET /tasks?next=abc` |
| **Bug** | Pasa el query param `next` como string a scan(ExclusiveStartKey=...) sin decodificar |
| **Dispara `ERROR:`** | ✅ **Sí** — ParamValidationError |

```bash
curl -X GET "https://xr6uq6n947.execute-api.eu-west-1.amazonaws.com/prod/tasks?next=some_token" \
  -H "x-api-key: <API_KEY>"
```

**Respuesta:** `HTTP 500` `{"error":{"code":"DDB_PARAM_ERROR","message":"Error de parametros en la base de datos."}}`

**CloudWatch Logs:** `ERROR: ParamValidationError en list_tasks` + traceback.

---

### SE-16 — Update valida 'completed' con validate_title (copy-paste)

| Campo | Valor |
|---|---|
| **Lambda/Handler** | `update_task` / `handlers/update_task.py` |
| **Endpoint** | `PUT /tasks/{task_id}` |
| **Bug** | Para `completed` llama a `validate_title()` en vez de `validate_completed()` |
| **Dispara `ERROR:`** | ✅ **Sí** — ValidationError logueado en except |

```bash
curl -X PUT "https://xr6uq6n947.execute-api.eu-west-1.amazonaws.com/prod/tasks/<TASK_ID>" \
  -H "Content-Type: application/json" -H "x-api-key: <API_KEY>" \
  -d '{"completed": true}'
```

**Respuesta:** `HTTP 400` `{"error":{"code":"INVALID_TYPE","message":"El campo 'title' debe ser una cadena de texto (string)."}}`

**CloudWatch Logs:** `ERROR: validacion fallida en update_task: El campo 'title' debe ser una cadena de texto (string).`

> **Consecuencia:** El campo `completed` ya no se puede actualizar nunca vía la API.

---

### SE-17 — Update con priority float sin convertir a Decimal

| Campo | Valor |
|---|---|
| **Lambda/Handler** | `update_task` / `handlers/update_task.py` |
| **Endpoint** | `PUT /tasks/{task_id}` |
| **Bug** | Si body trae `priority` float, se añade a attrs sin Decimal() → TypeError |
| **Dispara `ERROR:`** | ✅ **Sí** — TypeError capturado por except Exception |

```bash
curl -X PUT "https://xr6uq6n947.execute-api.eu-west-1.amazonaws.com/prod/tasks/<TASK_ID>" \
  -H "Content-Type: application/json" -H "x-api-key: <API_KEY>" \
  -d '{"title": "ok", "priority": 2.5}'
```

**Respuesta:** `HTTP 500` `{"error":{"code":"INTERNAL_ERROR","message":"Error interno inesperado."}}`

**CloudWatch Logs:** `ERROR: fallo no controlado en update_task` + traceback con TypeError.

---

### SE-18 — Delete con nombre de PK incorrecto ('id' en vez de 'task_id')

| Campo | Valor |
|---|---|
| **Lambda/Handler** | `delete_task` / `handlers/delete_task.py` |
| **Endpoint** | `DELETE /tasks/{task_id}` |
| **Bug** | Usa `Key={'id': task_id}` (PK incorrecta) → ClientError key schema mismatch |
| **Dispara `ERROR:`** | ✅ **Sí** — ClientError capturado |

```bash
curl -X DELETE "https://xr6uq6n947.execute-api.eu-west-1.amazonaws.com/prod/tasks/cualquier-uuid-aqui" \
  -H "x-api-key: <API_KEY>"
```

**Respuesta:** `HTTP 500` `{"error":{"code":"DDB_ERROR","message":"Error al acceder a la base de datos."}}`

**CloudWatch Logs:** `ERROR: ClientError en delete_task` + traceback con key schema mismatch.

> **⚠️ INCONDICIONAL** para cualquier task_id que no sea solo dígitos. TODO DELETE de UUIDs falla.

---

### SE-19 — Delete con task_id numérico convertido a int

| Campo | Valor |
|---|---|
| **Lambda/Handler** | `delete_task` / `handlers/delete_task.py` |
| **Endpoint** | `DELETE /tasks/{task_id}` |
| **Bug** | Si task_id.isdigit(), usa `Key={'task_id': int(task_id)}` → ClientError type mismatch |
| **Dispara `ERROR:`** | ✅ **Sí** — ClientError capturado |

```bash
curl -X DELETE "https://xr6uq6n947.execute-api.eu-west-1.amazonaws.com/prod/tasks/12345" \
  -H "x-api-key: <API_KEY>"
```

**Respuesta:** `HTTP 500` `{"error":{"code":"DDB_ERROR","message":"Error al acceder a la base de datos."}}`

**CloudWatch Logs:** `ERROR: ClientError en delete_task` + traceback con type mismatch (PK esperada S, recibida N).

---

## Secuencia Recomendada para la Demo

### Errores que disparan el Agente (✅ alarma automática):

1. **SE-5** (más fácil): `PUT /tasks/{id}` con body `{}`
2. **SE-13** (incondicional): cualquier `GET /tasks/{id}` 
3. **SE-18/SE-19** (incondicional): cualquier `DELETE /tasks/{id}`
4. **SE-10**: `POST /tasks` con `{"title":"x","priority":3.5}`
5. **SE-11**: `POST /tasks` con `{"title":"x","task_id":123}`
6. **SE-14**: `GET /tasks?limit=10`
7. **SE-15**: `GET /tasks?next=abc`
8. **SE-16**: `PUT /tasks/{id}` con `{"completed":true}`
9. **SE-17**: `PUT /tasks/{id}` con `{"title":"x","priority":2.5}`
10. **SE-12**: `GET /tasks/{id}?fields=status`

### Secuencia rápida para demo al jurado:

```bash
API="https://xr6uq6n947.execute-api.eu-west-1.amazonaws.com/prod"
KEY="<API_KEY>"

# 1. Crear tarea (funciona, muestra SE-1+SE-2 silenciosos)
curl -s -X POST "$API/tasks" -H "Content-Type: application/json" -H "x-api-key: $KEY" -d '{"title": "Demo"}'

# 2. GET siempre 500 (SE-13)
curl -s -X GET "$API/tasks/<TASK_ID>" -H "x-api-key: $KEY"

# 3. DELETE siempre 500 (SE-18)
curl -s -X DELETE "$API/tasks/<TASK_ID>" -H "x-api-key: $KEY"

# 4. List con limit 500 (SE-14)  
curl -s -X GET "$API/tasks?limit=10" -H "x-api-key: $KEY"

# 5. Update completed imposible (SE-16)
curl -s -X PUT "$API/tasks/<TASK_ID>" -H "Content-Type: application/json" -H "x-api-key: $KEY" -d '{"completed":true}'
```

Esperar ~1-5 minutos para que las alarmas pasen a ALARM → EventBridge invoca al Agente.
