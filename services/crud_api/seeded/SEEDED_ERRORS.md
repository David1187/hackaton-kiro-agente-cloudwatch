# SEEDED_ERRORS.md — Catálogo de Errores Sembrados

> **Estado inicial intencional y controlado.**
>
> Este directorio (`services/crud_api/seeded/`) contiene variantes deliberadamente
> defectuosas de los handlers CRUD.  Los errores son intencionales, reproducibles y
> aislados: cada módulo implementa exactamente un defecto.  Su único propósito es
> servir como el «código inicial con bugs» que el Agente de Auto-reparación
> (Bedrock AgentCore + strands) detectará, analizará y corregirá mediante un Pull
> Request con revisión humana obligatoria.
>
> **Los handlers correctos permanecen intactos en `services/crud_api/handlers/`.**
> Ningún cambio en `seeded/` afecta a los handlers de producción.

---

## Convenciones del catálogo

| Campo | Descripción |
|---|---|
| **ID** | Identificador del error (SE-1 … SE-9). |
| **Módulo** | Archivo Python en `seeded/`. |
| **Operación** | Operación CRUD afectada. |
| **Entrada disparadora** | Evento mínimo que activa el defecto de forma determinista. |
| **Excepción / comportamiento** | Lo que ocurre exactamente al disparar el error. |
| **Detectable por `ERROR:`** | Si el CloudWatch Metric Filter `ERROR:` captura el defecto. |
| **Requisito violado** | Número del Comportamiento_Objetivo (1–9) del `design.md`. |

---

## SE-1 — Create sin validación de `title`

| Campo | Valor |
|---|---|
| **Módulo** | `se1_create_no_title_validation.py` |
| **Operación** | POST /tasks (Create) |
| **Entrada disparadora** | `{"title": "   "}` (solo espacios) o `{"title": ""}` (cadena vacía) |
| **Excepción / comportamiento** | No se lanza excepción. Responde 201 con `title=""` o `title="   "` almacenado en DynamoDB. El handler omite `Payload_Validator.validate_title()` y usa el valor crudo. |
| **Detectable por `ERROR:`** | No directamente (no produce excepción). El agente lo detecta por inspección de código: ausencia de `validate_title()`. |
| **Requisito violado** | **1.2** — El título debe rechazarse con 400 `INVALID_TITLE` si está vacío o es sólo espacios en blanco. |

---

## SE-2 — Create con timestamp hardcoded

| Campo | Valor |
|---|---|
| **Módulo** | `se2_create_hardcoded_timestamp.py` |
| **Operación** | POST /tasks (Create) |
| **Entrada disparadora** | Cualquier `POST /tasks` válido: `{"title": "Mi tarea"}` |
| **Excepción / comportamiento** | No se lanza excepción. Responde 201 pero `created_at` y `updated_at` siempre valen `"1970-01-01T00:00:00.000+00:00"` sin importar cuándo se crea. El handler usa la constante `_HARDCODED_TIMESTAMP` en lugar de `datetime.now(timezone.utc)`. |
| **Detectable por `ERROR:`** | No directamente. El agente lo detecta por inspección de código: constante literal en vez de llamada dinámica a `datetime.now()`. |
| **Requisito violado** | **1.5** — `created_at` y `updated_at` deben ser el instante UTC real en formato ISO 8601 con milisegundos. |

---

## SE-3 — Get sin manejo de `task_id` ausente (KeyError)

| Campo | Valor |
|---|---|
| **Módulo** | `se3_get_missing_task_id.py` |
| **Entrada disparadora** | Evento sin `pathParameters`: `{"httpMethod": "GET", "path": "/tasks"}` |
| **Operación** | GET /tasks/{task_id} (Get) |
| **Excepción / comportamiento** | `KeyError: 'pathParameters'` (o `KeyError: 'task_id'` si `pathParameters` existe pero vacío). El `except Exception` lo captura → devuelve 500 y registra: `ERROR: fallo no controlado en se3_get` con traceback completo. |
| **Detectable por `ERROR:`** | ✅ Sí. El `except Exception` llama `logger.error("ERROR: ...")`, activando el Metric Filter. |
| **Requisito violado** | **2.2** — El sistema debe devolver 400 `INVALID_TASK_ID` cuando `task_id` está ausente en lugar de propagar un `KeyError`. |

---

## SE-4 — List sin límite en el scan

| Campo | Valor |
|---|---|
| **Módulo** | `se4_list_no_limit.py` |
| **Operación** | GET /tasks (List) |
| **Entrada disparadora** | Cualquier `GET /tasks` con tabla grande (> 1000 items). Con tabla pequeña el bug es observable inspeccionando el código: `table.scan()` sin `Limit`. |
| **Excepción / comportamiento** | Con tabla grande: posible `ReadThrottled` (`ClientError`) o timeout de Lambda → `ERROR: ClientError en se4_list` en CloudWatch. Con tabla pequeña: responde 200 con todos los items sin restricción. |
| **Detectable por `ERROR:`** | ✅ Con tabla grande (en producción). En demo con tabla pequeña: detectable por inspección de código. |
| **Requisito violado** | **3.2** — El scan debe limitarse a 1000 elementos para prevenir timeouts y consumo excesivo de capacidad. |

---

## SE-5 — Update sin validación de campos presentes (body vacío)

| Campo | Valor |
|---|---|
| **Módulo** | `se5_update_no_field_validation.py` |
| **Operación** | PUT /tasks/{task_id} (Update) |
| **Entrada disparadora** | `PUT /tasks/{task_id}` con body `{}` (objeto JSON vacío) |
| **Excepción / comportamiento** | El handler omite la comprobación `if not present: raise ValidationError(...)`. Con `body={}`, `attrs` queda vacío → el repositorio construye un `UpdateExpression` vacío → DynamoDB lanza `ParamValidationError` o `ClientError(ValidationException)` → registra `ERROR: ParamValidationError en se5_update` activando el Metric Filter. |
| **Detectable por `ERROR:`** | ✅ Sí. El `except ParamValidationError` llama `logger.error("ERROR: ...")`. |
| **Requisito violado** | **4.3** — El sistema debe rechazar con 400 `MISSING_FIELD` si el body no contiene ningún campo actualizable. |

---

## SE-6 — Update sin actualizar `updated_at`

| Campo | Valor |
|---|---|
| **Módulo** | `se6_update_no_updated_at.py` |
| **Operación** | PUT /tasks/{task_id} (Update) |
| **Entrada disparadora** | `PUT /tasks/{task_id}` con body `{"title": "Nuevo título"}` sobre una tarea existente |
| **Excepción / comportamiento** | No se lanza excepción. Responde 200 con los atributos actualizados, pero `updated_at` permanece con el valor original de `created_at`. El campo nunca se incluye en `attrs`. |
| **Detectable por `ERROR:`** | No directamente. El agente lo detecta por inspección de código: ausencia de la línea `attrs["updated_at"] = timestamp`. |
| **Requisito violado** | **4.7** — El sistema debe refrescar `updated_at` con la hora UTC actual en cada operación de actualización. |

---

## SE-7 — Delete sin ConditionExpression (no da 404 si no existe)

| Campo | Valor |
|---|---|
| **Módulo** | `se7_delete_no_condition.py` |
| **Operación** | DELETE /tasks/{task_id} (Delete) |
| **Entrada disparadora** | `DELETE /tasks/tarea-inexistente` (un `task_id` que no existe en la tabla) |
| **Excepción / comportamiento** | No se lanza excepción. DynamoDB acepta el `delete_item` sin el `ConditionExpression` y devuelve éxito aunque el item no existiera. El handler responde 200 `{"deleted": true}` cuando debería responder 404. |
| **Detectable por `ERROR:`** | No directamente. El agente lo detecta por inspección de código: ausencia de `ConditionExpression`. |
| **Requisito violado** | **5.3** — El sistema debe devolver 404 `RESOURCE_NOT_FOUND` al intentar eliminar una tarea inexistente. |

---

## SE-8 — Handler sin try-except

| Campo | Valor |
|---|---|
| **Módulo** | `se8_no_try_except.py` |
| **Operación** | POST /tasks (Create) — pero el defecto aplica a cualquier operación |
| **Entrada disparadora** | Cualquier evento que provoque un error de boto3: p.ej., `TABLE_NAME` apuntando a tabla inexistente → `ClientError(ResourceNotFoundException)`. O body sin `title` → `ValidationError` no capturada. |
| **Excepción / comportamiento** | La excepción se propaga al runtime de Lambda sin capturar. Lambda emite en CloudWatch: `[ERROR] <ExceptionType>: <mensaje>` y devuelve a API Gateway un error de función → 502. El repositorio ya habrá emitido `ERROR:` antes de re-lanzar si es un `ClientError` de DynamoDB. |
| **Detectable por `ERROR:`** | ✅ Sí, vía el logger del repositorio que emite `ERROR:` antes de propagar la excepción. Adicionalmente el runtime emite `[ERROR]` (sin dos puntos, no coincide con el Metric Filter `ERROR:`). |
| **Requisito violado** | **Requisito general de manejo de errores** — Todo handler debe capturar excepciones y devolver respuestas HTTP estructuradas (ver `backend-standards.md` § 1). |

---

## SE-9 — Handler con logging vía `print()` (no detectable por Metric Filter)

| Campo | Valor |
|---|---|
| **Módulo** | `se9_print_instead_of_logging.py` |
| **Operación** | GET /tasks/{task_id} (Get) |
| **Entrada disparadora** | `GET /tasks/tarea-inexistente` (un `task_id` que no existe en la tabla) |
| **Excepción / comportamiento** | No se lanza excepción al cliente. El handler devuelve 404, pero el mensaje de error se emite con `print()` en lugar de `logging.error()`. En CloudWatch Logs aparece: `Task not found in se9_get: task_id=...` — sin el prefijo `ERROR:`. |
| **Detectable por `ERROR:`** | ❌ No. El Metric Filter `ERROR:` no lo cuenta porque los mensajes de `print()` no tienen el prefijo requerido. Los errores pasan completamente desapercibidos para el sistema de alertas. |
| **Requisito violado** | **Requisito de logging** (ver `architecture-guide.md` § 3 y `backend-standards.md` § 1) — Todo fallo debe registrarse con `logging.error(..., exc_info=True)` para que el Metric Filter `ERROR:` lo detecte. |

---

## Resumen del catálogo

| ID | Módulo | Operación | Detectable `ERROR:` | Requisito violado |
|---|---|---|---|---|
| SE-1 | `se1_create_no_title_validation.py` | Create | No directo | 1.2 |
| SE-2 | `se2_create_hardcoded_timestamp.py` | Create | No directo | 1.5 |
| SE-3 | `se3_get_missing_task_id.py` | Get | ✅ Sí | 2.2 |
| SE-4 | `se4_list_no_limit.py` | List | ✅ Con tabla grande | 3.2 |
| SE-5 | `se5_update_no_field_validation.py` | Update | ✅ Sí | 4.3 |
| SE-6 | `se6_update_no_updated_at.py` | Update | No directo | 4.7 |
| SE-7 | `se7_delete_no_condition.py` | Delete | No directo | 5.3 |
| SE-8 | `se8_no_try_except.py` | Create | ✅ Sí (vía repo) | Estándar errores |
| SE-9 | `se9_print_instead_of_logging.py` | Get | ❌ No | Estándar logging |

---

## Instrucciones para usar en la demo

1. **No desplegar los handlers de `seeded/` directamente** como Lambdas de producción.
2. Para la demo del agente: copiar temporalmente el handler sembrado elegido al
   path de la Lambda a desplegar, activar la Lambda, disparar el error con la
   entrada documentada, y esperar que CloudWatch Alarm → EventBridge → Agente
   se active.
3. El agente leerá el código del repositorio GitHub (vía tag `github-repo`),
   identificará el defecto comparando con `backend-standards.md` y la spec,
   generará el parche y abrirá un Pull Request en la rama
   `fix/auto-heal-{lambda}-{timestamp}`.
4. La revisión y aprobación del PR es siempre humana y manual.
