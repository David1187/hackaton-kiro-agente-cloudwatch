# Requirements Document

## Introduction

Esta funcionalidad define una API CRUD para gestionar una lista de tareas (to-do list). La API se implementa con funciones AWS Lambda escritas en Python que persisten los datos directamente en Amazon DynamoDB, y se expone públicamente mediante un Amazon API Gateway (REST API) definido con OpenAPI y protegido con Usage Plan + API Key.

El diseño prioriza la programación defensiva: cada handler y cada operación de I/O contra DynamoDB debe validar sus entradas y capturar sus errores. Todos los fallos, controlados o no controlados, se registran mediante el módulo estándar `logging` de Python con el prefijo `ERROR:` seguido del stack trace, de modo que un futuro CloudWatch Metric Filter (patrón `ERROR:`) pueda detectarlos. El alcance de este spec cubre exclusivamente el CRUD de tareas y su exposición; la infraestructura del Agente de auto-reparación queda fuera de este documento.

### Objetivo de Hackathon: errores sembrados de forma controlada

El objetivo final de este proyecto (ver steering `architecture-guide.md`) es demostrar un Agente de auto-reparación en Amazon Bedrock que detecta un error de ejecución en las Lambdas CRUD y propone una corrección vía Pull Request. Para que el Agente disponga de algo que detectar y reparar, el código de las Lambdas CRUD incluye, de forma **deliberada, controlada y documentada**, los errores más comunes de las operaciones CRUD (el catálogo definido en los Requisitos 10 y 11).

> **Decisión de alcance vigente (actualizada):** el Código_Sembrado **no** es un estado inicial transitorio alojado en una carpeta aparte. Por decisión explícita del usuario, los errores sembrados están **fusionados dentro de los handlers desplegados** (`services/crud_api/handlers/`) y deben permanecer desplegados de forma **permanente**, para que el jurado del Hackathon pueda dispararlos en vivo contra el API Gateway real. La consecuencia asumida es que la API pública **no se comporta correctamente** para uso normal mientras dure la demo. Los payloads exactos de disparo están documentados en `services/crud_api/DEMO_ERRORS.md`, que es la fuente de verdad del catálogo realmente implementado.

Es importante entender la relación entre los requisitos de este documento para no interpretarlos como contradictorios:

- Los **Requisitos 1 a 9** describen el **Comportamiento_Objetivo**: el comportamiento correcto y defensivo al que el Agente debe llevar el código. Representan el **estado final** esperado tras la reparación. **No** describen el estado desplegado.
- Los **Requisitos 10 y 11** describen el **Código_Sembrado**: el estado defectuoso intencional, actualmente desplegado, que sirve de punto de partida para la demostración de auto-reparación.

Los errores sembrados no son defectos de diseño: son un artefacto intencional del Hackathon. Cada error sembrado debe ser reproducible y disparable de forma determinista para que la demo sea repetible.

## Glossary

- **Todo_API**: Sistema completo compuesto por el API Gateway REST, las funciones Lambda CRUD y su capa de persistencia en DynamoDB.
- **API_Gateway**: Amazon API Gateway (REST API) definido con OpenAPI que expone públicamente las operaciones CRUD.
- **Task**: Elemento de la lista de tareas persistido en DynamoDB. Contiene al menos un identificador único (`task_id`), un título (`title`), un estado de completitud (`completed`) y marcas de tiempo de creación y actualización.
- **task_id**: Clave de partición (partition key) que identifica de forma única a una Task en DynamoDB. Es un valor de tipo string.
- **Create_Handler**: Función Lambda que procesa la creación de una Task.
- **Get_Handler**: Función Lambda que procesa la consulta de una Task individual por `task_id`.
- **List_Handler**: Función Lambda que procesa el listado de Tasks.
- **Update_Handler**: Función Lambda que procesa la actualización de una Task existente.
- **Delete_Handler**: Función Lambda que procesa la eliminación de una Task existente.
- **CRUD_Handler**: Cualquiera de los handlers Lambda anteriores (Create, Get, List, Update, Delete).
- **Payload_Validator**: Componente lógico dentro de cada CRUD_Handler que valida existencia y tipo de los parámetros de entrada antes de procesar la operación.
- **Persistence_Layer**: Componente lógico dentro de cada CRUD_Handler que ejecuta las operaciones de I/O contra DynamoDB.
- **Error_Logger**: Mecanismo de registro basado en el módulo estándar `logging` de Python que emite mensajes con el prefijo `ERROR:` y el stack trace asociado.
- **API_Key**: Credencial estática enviada en la cabecera de la petición que el API_Gateway valida contra un Usage Plan.
- **Comportamiento_Objetivo**: Comportamiento correcto y defensivo definido en los Requisitos 1 a 9, que representa el estado final al que el Agente de auto-reparación debe llevar el código de las Lambdas CRUD.
- **Seeded_Error** (Error_Sembrado): Defecto introducido de forma deliberada, controlada y documentada en el código de un CRUD_Handler, cuyo propósito es servir de punto de partida para la demostración del Agente de auto-reparación. No constituye un defecto de diseño.
- **Seeded_Codebase** (Código_Sembrado): Versión del código de las Lambdas CRUD que contiene los Seeded_Errors del catálogo (Requisitos 10 y 11). **Es el código actualmente desplegado** en las 5 Lambdas (`services/crud_api/handlers/`), de forma permanente y deliberada para la demo del Hackathon, no un estado transitorio previo al despliegue.
- **Metric_Filter**: CloudWatch Metric Filter configurado con el patrón `ERROR:` sobre el Log Group de las Lambdas CRUD, responsable de detectar los fallos registrados y disparar la CloudWatch Alarm que activa al Agente.

## Requirements

### Requirement 1: Crear una tarea

**User Story:** Como cliente de la API, quiero crear una nueva tarea, para poder registrar un elemento pendiente en mi lista.

#### Acceptance Criteria

1. WHEN el Create_Handler recibe una petición con un cuerpo JSON válido que contiene el atributo `title` de tipo string cuyo contenido, tras eliminar los espacios en blanco iniciales y finales, tiene una longitud de entre 1 y 255 caracteres, THE Create_Handler SHALL generar un `task_id` único, persistir la Task en DynamoDB y devolver, en un tiempo máximo de 3 segundos, una respuesta con código de estado 201 y la Task creada.
2. WHEN el Create_Handler persiste una Task y el cuerpo de la petición no incluye el atributo `completed`, THE Create_Handler SHALL asignar al atributo `completed` el valor booleano `false`.
3. WHEN el Create_Handler persiste una Task, THE Create_Handler SHALL asignar las marcas de tiempo `created_at` y `updated_at` con el mismo instante actual expresado en formato ISO 8601 con precisión de milisegundos y zona horaria UTC.
4. IF el cuerpo de la petición no es un JSON válido, THEN THE Create_Handler SHALL rechazar la petición sin persistir ninguna Task y devolver una respuesta con código de estado 400 y un mensaje de error que indique que el cuerpo no es un JSON válido.
5. IF el cuerpo de la petición no contiene el atributo `title`, o `title` no es de tipo string, o su contenido tras eliminar los espacios en blanco iniciales y finales está vacío o supera los 255 caracteres, THEN THE Create_Handler SHALL rechazar la petición sin persistir ninguna Task y devolver una respuesta con código de estado 400 y un mensaje de error que indique el atributo inválido y el motivo del rechazo.
6. IF el cuerpo de la petición incluye el atributo `completed` con un valor que no es de tipo booleano, THEN THE Create_Handler SHALL rechazar la petición sin persistir ninguna Task y devolver una respuesta con código de estado 400 y un mensaje de error que indique que `completed` debe ser un valor booleano.
7. IF la operación de escritura en DynamoDB falla, THEN THE Create_Handler SHALL registrar el error mediante el Error_Logger con el prefijo `ERROR:` y el stack trace, no dejar ninguna Task parcialmente persistida y devolver una respuesta con código de estado 500 y un mensaje de error que indique que la creación no pudo completarse.

### Requirement 2: Consultar una tarea

**User Story:** Como cliente de la API, quiero consultar una tarea por su identificador, para poder ver su detalle actual.

#### Acceptance Criteria

1. WHEN el Get_Handler recibe una petición con un `task_id` de formato válido que corresponde a una Task existente, THE Get_Handler SHALL devolver, en un tiempo máximo de 3 segundos, una respuesta con código de estado 200 y la Task solicitada con todos sus atributos almacenados sin modificar.
2. IF la petición no incluye el parámetro `task_id`, THEN THE Get_Handler SHALL devolver una respuesta con código de estado 400 y un mensaje de error que indique que el `task_id` es requerido.
3. IF el `task_id` recibido está vacío o supera los 256 caracteres, THEN THE Get_Handler SHALL devolver una respuesta con código de estado 400 y un mensaje de error que indique que el formato del `task_id` es inválido.
4. IF el `task_id` tiene formato válido pero no corresponde a ninguna Task existente, THEN THE Get_Handler SHALL devolver una respuesta con código de estado 404 y un mensaje de error que indique que la Task no fue encontrada.
5. IF la operación de lectura en DynamoDB falla, THEN THE Get_Handler SHALL registrar el error mediante el Error_Logger con el prefijo `ERROR:` y el stack trace, y devolver una respuesta con código de estado 500 y un mensaje que indique un fallo interno, sin alterar los datos almacenados.

### Requirement 3: Listar tareas

**User Story:** Como cliente de la API, quiero listar las tareas existentes, para poder revisar el conjunto de elementos de mi lista.

#### Acceptance Criteria

1. WHEN el List_Handler recibe una petición que incluye una API Key válida asociada a un Usage Plan activo, THE List_Handler SHALL devolver, en un tiempo máximo de 3 segundos, una respuesta con código de estado 200 y una colección que contenga todas las Tasks existentes, hasta un máximo de 1000 elementos por respuesta.
2. WHILE no existan Tasks persistidas en DynamoDB, THE List_Handler SHALL devolver una respuesta con código de estado 200 y una colección vacía (con 0 elementos).
3. IF la petición no incluye una API Key o la API Key es inválida, THEN THE List_Handler SHALL rechazar la petición devolviendo una respuesta con código de estado 403 y un mensaje que indique que la autenticación falló, sin ejecutar la operación de lectura en DynamoDB.
4. IF la operación de lectura en DynamoDB falla, THEN THE List_Handler SHALL registrar el error mediante el Error_Logger con el prefijo `ERROR:` y el stack trace detallado, y devolver una respuesta con código de estado 500 y un mensaje que indique que la lectura de las Tasks no pudo completarse, sin exponer detalles internos de la excepción al cliente.

### Requirement 4: Actualizar una tarea

**User Story:** Como cliente de la API, quiero actualizar una tarea existente, para poder modificar su título o su estado de completitud.

#### Acceptance Criteria

1. WHEN el Update_Handler recibe una petición con un `task_id` que corresponde a una Task existente y un cuerpo JSON válido que incluye al menos uno de los atributos actualizables (`title` o `completed`) con valores válidos, THE Update_Handler SHALL actualizar únicamente los atributos presentes en la Task en DynamoDB y devolver una respuesta con código de estado 200 y la Task actualizada.
2. WHEN el Update_Handler actualiza una Task, THE Update_Handler SHALL asignar la marca de tiempo `updated_at` con el instante actual en formato ISO 8601 con zona horaria UTC y precisión de milisegundos.
3. IF la petición no incluye el parámetro `task_id`, THEN THE Update_Handler SHALL devolver una respuesta con código de estado 400 y un mensaje de error que indique que el `task_id` es requerido, sin modificar ninguna Task.
4. IF el cuerpo de la petición está ausente, contiene JSON inválido o no incluye ninguno de los atributos actualizables (`title` o `completed`), THEN THE Update_Handler SHALL devolver una respuesta con código de estado 400 y un mensaje de error que indique la causa del rechazo, sin modificar ninguna Task.
5. IF el cuerpo de la petición incluye el atributo `title` con un valor que no es de tipo string o cuyo contenido, tras eliminar los espacios en blanco iniciales y finales, está vacío o supera los 255 caracteres, THEN THE Update_Handler SHALL devolver una respuesta con código de estado 400 y un mensaje de error que indique el motivo del rechazo, sin modificar ninguna Task.
6. IF el cuerpo de la petición incluye el atributo `completed` con un valor que no es de tipo booleano, THEN THE Update_Handler SHALL devolver una respuesta con código de estado 400 y un mensaje de error que indique que `completed` debe ser un valor booleano, sin modificar ninguna Task.
7. IF el `task_id` recibido no corresponde a ninguna Task existente, THEN THE Update_Handler SHALL devolver una respuesta con código de estado 404 y un mensaje de error que indique que la Task no fue encontrada.
8. IF la operación de escritura en DynamoDB falla, THEN THE Update_Handler SHALL registrar el error mediante el Error_Logger con el prefijo `ERROR:` y el stack trace, no dejar la Task parcialmente modificada, y devolver una respuesta con código de estado 500 y un mensaje que indique un fallo interno.

### Requirement 5: Eliminar una tarea

**User Story:** Como cliente de la API, quiero eliminar una tarea existente, para poder retirarla de mi lista.

#### Acceptance Criteria

1. WHEN el Delete_Handler recibe una petición con un `task_id` no vacío que corresponde a una Task existente, THE Delete_Handler SHALL eliminar la Task de DynamoDB y devolver, en un tiempo máximo de 3 segundos, una respuesta con código de estado 200 y un cuerpo que confirme la eliminación incluyendo el `task_id` eliminado.
2. IF la petición no incluye el parámetro `task_id` o el `task_id` está vacío o contiene únicamente espacios en blanco, THEN THE Delete_Handler SHALL rechazar la petición sin ejecutar ninguna operación de eliminación en DynamoDB y devolver una respuesta con código de estado 400 y un mensaje de error que indique que el `task_id` es requerido.
3. IF el `task_id` recibido tiene formato válido pero no corresponde a ninguna Task existente en DynamoDB, THEN THE Delete_Handler SHALL devolver una respuesta con código de estado 404 y un mensaje de error que indique que la Task no fue encontrada, sin modificar el estado de DynamoDB.
4. IF la operación de eliminación en DynamoDB falla por un error no controlado, THEN THE Delete_Handler SHALL registrar el error mediante el Error_Logger con el prefijo `ERROR:` y el stack trace, preservar sin cambios el estado de la Task en DynamoDB, y devolver una respuesta con código de estado 500 y un mensaje de error que indique que ocurrió un fallo interno.

### Requirement 6: Validación defensiva de payloads

**User Story:** Como responsable de la fiabilidad del servicio, quiero que cada handler valide sus entradas antes de procesarlas, para evitar fallos no controlados del tipo `KeyError` o `TypeError`.

#### Acceptance Criteria

1. WHEN un CRUD_Handler recibe una petición, THE Payload_Validator SHALL verificar la existencia de todos los parámetros requeridos por la operación (como mínimo `task_id` para las operaciones que lo requieran y los atributos del body definidos para esa operación) antes de ejecutar cualquier operación contra DynamoDB.
2. WHEN un CRUD_Handler recibe una petición, THE Payload_Validator SHALL verificar que cada parámetro requerido corresponde al tipo esperado por la operación antes de ejecutar cualquier operación contra DynamoDB.
3. IF el cuerpo de la petición está ausente, vacío o contiene JSON con formato inválido, THEN THE CRUD_Handler SHALL devolver una respuesta con código de estado 400, sin invocar la Persistence_Layer, y un mensaje de error que identifique la causa del rechazo (cuerpo ausente, vacío o formato JSON inválido).
4. IF el Payload_Validator detecta un parámetro requerido ausente, THEN THE CRUD_Handler SHALL devolver una respuesta con código de estado 400, sin invocar la Persistence_Layer, y un mensaje de error que identifique el nombre del parámetro ausente.
5. IF el Payload_Validator detecta un parámetro requerido con tipo distinto al esperado, THEN THE CRUD_Handler SHALL devolver una respuesta con código de estado 400, sin invocar la Persistence_Layer, y un mensaje de error que identifique el nombre del parámetro y el tipo esperado.
6. WHEN el Payload_Validator confirma que todos los parámetros requeridos existen y tienen el tipo esperado, THE CRUD_Handler SHALL proceder a ejecutar la operación correspondiente contra DynamoDB.

### Requirement 7: Registro de errores para observabilidad

**User Story:** Como ingeniero de operaciones, quiero que todos los fallos se registren en un formato consistente, para que un futuro CloudWatch Metric Filter pueda detectar anomalías.

#### Acceptance Criteria

1. WHEN un CRUD_Handler captura una excepción controlada o no controlada, THE Error_Logger SHALL registrar el fallo mediante el módulo estándar `logging` de Python invocando `logging.error` con `exc_info=True` exactamente una vez por cada excepción capturada.
2. WHEN el Error_Logger registra un fallo, THE Error_Logger SHALL emitir un único registro cuyo contenido comience con el prefijo `ERROR:` seguido del stack trace completo asociado a la excepción, sin caracteres previos al prefijo, de forma que el patrón `ERROR:` sea detectable por un CloudWatch Metric Filter.
3. THE Todo_API SHALL usar el módulo estándar `logging` de Python para toda emisión de trazas de diagnóstico y SHALL NOT usar `print()` para dicha emisión.
4. IF un CRUD_Handler encuentra una excepción no controlada durante su ejecución, THEN THE CRUD_Handler SHALL registrar el fallo mediante el Error_Logger y devolver una respuesta con código de estado 500 que contenga un mensaje que indique un error interno del servidor.
5. IF un CRUD_Handler devuelve una respuesta de error al cliente, THEN THE CRUD_Handler SHALL excluir del cuerpo de la respuesta el stack trace y cualquier detalle interno de la excepción, registrando dichos detalles únicamente mediante el Error_Logger.

### Requirement 8: Exposición pública mediante API Gateway

**User Story:** Como cliente externo, quiero acceder a las operaciones CRUD a través de una API pública autenticada, para poder gestionar tareas de forma remota.

#### Acceptance Criteria

1. THE API_Gateway SHALL exponer las operaciones de creación, consulta individual, listado, actualización y eliminación de Tasks a través de un Amazon API Gateway REST API definido con OpenAPI.
2. IF una petición al API_Gateway no incluye una API_Key o incluye una API_Key no asociada a un Usage Plan activo, THEN THE API_Gateway SHALL rechazar la petición con código de estado 403 sin enrutarla a ningún CRUD_Handler.
3. WHEN el API_Gateway recibe una petición con una API_Key válida cuyo método y ruta coinciden con una operación definida, THE API_Gateway SHALL enrutar la petición al CRUD_Handler correspondiente con una latencia de enrutamiento máxima de 500 milisegundos.
4. IF el API_Gateway recibe una petición con una API_Key válida cuyo método o ruta no coinciden con ninguna operación definida, THEN THE API_Gateway SHALL rechazar la petición sin invocar ningún CRUD_Handler.
5. THE API_Gateway SHALL aplicar a cada API_Key el throttling definido en el Usage Plan con una tasa de 100 peticiones por segundo, un burst de 200 peticiones y una cuota diaria de 10.000 peticiones.
6. IF una API_Key supera el límite de tasa o la cuota diaria definidos en el Usage Plan, THEN THE API_Gateway SHALL rechazar la petición con código de estado 429, sin afectar el servicio de las demás API_Keys.

### Requirement 9: Persistencia en DynamoDB

**User Story:** Como responsable de los datos, quiero que las tareas se persistan de forma fiable en DynamoDB, para garantizar la durabilidad de la información.

#### Acceptance Criteria

1. THE Persistence_Layer SHALL usar el atributo `task_id` como clave de partición única de la tabla de DynamoDB, de forma que no puedan coexistir dos Tasks con el mismo valor de `task_id`.
2. WHEN la Persistence_Layer ejecuta una operación de I/O contra DynamoDB, THE Persistence_Layer SHALL encapsular dicha operación en un bloque de manejo de errores que capture las excepciones del cliente de DynamoDB sin permitir su propagación no controlada.
3. WHEN una operación de escritura contra DynamoDB se confirma como exitosa, THE Persistence_Layer SHALL devolver al CRUD_Handler el identificador `task_id` de la Task persistida como indicación de éxito.
4. IF la Persistence_Layer recibe un error de condición desde DynamoDB al escribir sobre una Task inexistente, THEN THE Persistence_Layer SHALL propagar la condición para que el CRUD_Handler devuelva un código de estado 404.
5. IF la Persistence_Layer captura una excepción del cliente de DynamoDB distinta de un error de condición, THEN THE Persistence_Layer SHALL registrar el fallo mediante el módulo `logging` con el prefijo `ERROR:` y el stack trace completo, y SHALL propagar el fallo para que el CRUD_Handler devuelva un código de estado 500 sin persistir datos parciales.

### Requirement 10: Estado inicial sembrado con errores controlados

**User Story:** Como responsable de la demostración del Hackathon, quiero que el estado inicial del código de las Lambdas CRUD contenga errores comunes sembrados de forma controlada y determinista, para que el Agente de auto-reparación de Amazon Bedrock tenga fallos reales que detectar y corregir.

#### Acceptance Criteria

1. THE Seeded_Codebase SHALL contener, para cada operación CRUD (creación, consulta individual, listado, actualización y eliminación), al menos dos Seeded_Errors del catálogo definido en el Requisito 11, de los cuales al menos uno SHALL pertenecer al subconjunto **detectable**, de modo que las cinco Alarm de CloudWatch del proyecto sean disparables de forma independiente.
2. THE Seeded_Codebase SHALL declararse de forma explícita en el repositorio como estado inicial intencional y controlado, referenciando el Comportamiento_Objetivo (Requisitos 1 a 9) como el resultado esperado tras la reparación por parte del Agente de auto-reparación.
3. WHERE un Seeded_Error está presente en un CRUD_Handler, THE Seeded_Codebase SHALL permitir dispararlo de forma determinista mediante una entrada documentada y específica, de modo que la misma entrada produzca siempre el mismo tipo de excepción.
4. WHEN un Seeded_Error del subconjunto **detectable** se dispara durante la ejecución de un CRUD_Handler, THE Seeded_Codebase SHALL provocar que el fallo quede registrado en CloudWatch Logs en una forma que contenga el marcador de error `ERROR:` y el stack trace asociado, de modo que sea detectable por el Metric_Filter. THE Seeded_Codebase SHALL emitir dicho registro desde un bloque `except` del propio CRUD_Handler mediante `logging.error(..., exc_info=True)`, y no únicamente desde la Persistence_Layer, para que el stack trace contenga el marco de ejecución del handler y el Agente de auto-reparación identifique como fichero a corregir el handler afectado y no el módulo compartido de persistencia.
5. WHERE un Seeded_Error pertenece al subconjunto **silencioso** (no produce excepción, o registra el fallo sin el prefijo `ERROR:`), THE Seeded_Codebase SHALL documentar explícitamente que dicho error **no** activa el Metric_Filter ni, por tanto, el ciclo autónomo del Agente, y SHALL requerir su detección por inspección de código. La existencia de este subconjunto es intencional: demuestra el límite de una estrategia de observabilidad basada exclusivamente en el patrón `ERROR:`.
6. THE Seeded_Codebase SHALL mantener cada Seeded_Error disparable mediante su entrada documentada. WHERE dos Seeded_Errors coexisten en el mismo CRUD_Handler y uno impide alcanzar o detectar al otro, THE Seeded_Codebase SHALL documentar dicho enmascaramiento de forma explícita en `DEMO_ERRORS.md`. Casos conocidos y asumidos: SE-18 falla siempre antes de que SE-7 pueda manifestar su 200 indebido; SE-13 hace que ninguna consulta individual llegue a devolver 200; y SE-16 impide que el atributo `completed` llegue a actualizarse nunca.
7. THE Seeded_Codebase SHALL documentar, para cada Seeded_Error, la operación CRUD afectada, el endpoint y payload que lo dispara, la respuesta HTTP esperada, si activa o no el Metric_Filter, y el número de requisito del Comportamiento_Objetivo (entre el 1 y el 9) que define su comportamiento correcto.

### Requirement 11: Catálogo de errores comunes forzados en las operaciones CRUD

**User Story:** Como responsable de la demostración del Hackathon, quiero un catálogo explícito de los errores más comunes de las operaciones CRUD forzados en el código desplegado, para que cada tipo de fallo típico quede cubierto y sea reproducible por la demo de auto-reparación.

> **Nota de trazabilidad:** este catálogo refleja los errores **realmente implementados y desplegados** en `services/crud_api/handlers/`. La fuente de verdad operativa (payloads `curl`, respuestas esperadas, logs) es `services/crud_api/DEMO_ERRORS.md`. Distribución por Lambda: Create → SE-1, SE-2, SE-8, SE-10, SE-11; Get → SE-3, SE-9, SE-12, SE-13; List → SE-4, SE-14, SE-15; Update → SE-5, SE-6, SE-16, SE-17; Delete → SE-7, SE-18, SE-19.
>
> **Nota de detectabilidad:** las cinco Lambdas tienen al menos un error detectable por el Metric_Filter (Req 10.1): Create vía SE-10/SE-11, Get vía SE-12/SE-13, List vía SE-14/SE-15, Update vía SE-5/SE-16/SE-17, Delete vía SE-18/SE-19.
>
> **Nota de condicionalidad:** un error **condicional** solo se dispara con un payload o parámetro concreto, por lo que la operación conserva su camino de ejecución normal para el resto de peticiones. Un error **incondicional** rompe la operación en toda petición. Son incondicionales SE-13 (Get) y SE-18/SE-19 (Delete), de modo que `GET /tasks/{task_id}` y `DELETE /tasks/{task_id}` devuelven 500 siempre; esto es una consecuencia aceptada de forma explícita.

#### Acceptance Criteria

1. **(SE-1, Create, silencioso)** WHEN el Create_Handler en estado sembrado recibe un cuerpo cuyo `title` está vacío o contiene únicamente espacios en blanco, THE Seeded_Codebase SHALL omitir la validación del título y persistir la Task con el valor crudo, devolviendo 201 en lugar del 400 exigido por el Requisito 1.5.
2. **(SE-2, Create, silencioso)** WHEN el Create_Handler en estado sembrado persiste una Task, THE Seeded_Codebase SHALL asignar a `created_at` y `updated_at` un valor constante hardcodeado en lugar del instante UTC actual, incumpliendo el Requisito 1.3.
3. **(SE-3, Get, inalcanzable vía HTTP)** WHEN el Get_Handler en estado sembrado recibe un evento cuyo mapa `pathParameters` está ausente o vacío, THE Seeded_Codebase SHALL acceder directamente a la clave sin validación previa y provocar un `KeyError`, devolviendo 500 en lugar del 400 exigido por el Requisito 2.2. THE Seeded_Codebase SHALL documentar que este error solo es alcanzable mediante invocación directa de la Lambda, porque el API_Gateway siempre inyecta `pathParameters` en la ruta `/tasks/{task_id}`. Tras la reducción de SE-9, el registro resultante SHALL contener el prefijo `ERROR:`.
4. **(SE-4, List, silencioso salvo volumen)** WHEN el List_Handler en estado sembrado consulta DynamoDB, THE Seeded_Codebase SHALL ejecutar la operación de `scan` sin el parámetro `Limit`, incumpliendo el máximo de 1000 elementos exigido por el Requisito 3.1 y exponiéndose a throttling o timeout con tablas grandes.
5. **(SE-5, Update, DETECTABLE)** WHEN el Update_Handler en estado sembrado recibe un cuerpo JSON válido que no incluye ninguno de los atributos actualizables, THE Seeded_Codebase SHALL construir una expresión de actualización vacía y provocar un `ParamValidationError`/`ClientError` desde DynamoDB, que SHALL quedar registrado con el prefijo `ERROR:` y activar el Metric_Filter. Este es el disparador de referencia del ciclo autónomo completo del Agente.
6. **(SE-6, Update, silencioso)** WHEN el Update_Handler en estado sembrado actualiza una Task, THE Seeded_Codebase SHALL omitir la asignación de `updated_at`, dejando la marca de tiempo congelada e incumpliendo el Requisito 4.2.
7. **(SE-7, Delete, silencioso)** WHEN el Delete_Handler en estado sembrado elimina una Task, THE Seeded_Codebase SHALL ejecutar `delete_item` sin `ConditionExpression`, devolviendo 200 sobre un `task_id` inexistente en lugar del 404 exigido por el Requisito 5.3.
8. **(SE-8, Create, DETECTABLE parcialmente — transformado)** WHEN el Create_Handler en estado sembrado encuentra una excepción durante su ejecución, THE Seeded_Codebase SHALL capturar `ClientError`, `ParamValidationError` y la excepción genérica registrándolas con el prefijo `ERROR:` y devolviendo 500, pero SHALL omitir deliberadamente la captura del error de validación, permitiendo que se propague como error de ejecución no controlado y devuelva 502 al cliente. THE Seeded_Codebase SHALL documentar que el Metric_Filter **no** detecta la rama de validación, dado que el runtime de Lambda emite `[ERROR]` sin el carácter de dos puntos que exige el patrón. Este criterio sustituye la formulación anterior de SE-8 ("ausencia total de `try-except`"), que impedía a la Lambda de creación disparar su Alarm con un stack trace que apuntase al propio handler (Req 10.4).
9. **(SE-9, Get, NO detectable por diseño — reducido)** WHEN el Get_Handler en estado sembrado registra el fallo correspondiente a una Task inexistente, THE Seeded_Codebase SHALL emitirlo mediante `print()` en lugar de `logging.error(..., exc_info=True)`, incumpliendo el Requisito 7.3 y garantizando que ese fallo **no** contenga el prefijo `ERROR:` ni active el Metric_Filter. THE Seeded_Codebase SHALL usar `logging.error(..., exc_info=True)` en el resto de sus bloques `except`. Este criterio sustituye la formulación anterior de SE-9 (`print()` en todos los bloques `except`), que suprimía la detectabilidad de todos los errores de la Lambda de consulta.
10. **(SE-10, Create, DETECTABLE, condicional)** WHEN el Create_Handler en estado sembrado recibe un cuerpo que incluye un atributo numérico no entero, THE Seeded_Codebase SHALL transferirlo a la Persistence_Layer sin convertirlo al tipo decimal exigido por DynamoDB, provocando un `TypeError` que SHALL quedar registrado con el prefijo `ERROR:` y devolver 500.
11. **(SE-11, Create, DETECTABLE, condicional)** WHEN el Create_Handler en estado sembrado recibe un cuerpo que incluye un `task_id` proporcionado por el cliente, THE Seeded_Codebase SHALL usarlo como clave de partición sin generar un identificador propio ni validar su tipo, de modo que un valor numérico provoque un `ClientError` de discordancia de tipo con el esquema de la tabla, registrado con el prefijo `ERROR:` y devolviendo 500. El Comportamiento_Objetivo (Requisito 1.1) exige generar siempre el identificador en el servidor.
12. **(SE-12, Get, DETECTABLE, condicional)** WHEN el Get_Handler en estado sembrado recibe un parámetro de consulta que enumera atributos a proyectar, THE Seeded_Codebase SHALL pasarlo sin validación ni escapado como expresión de proyección a DynamoDB, de modo que una palabra reservada del motor provoque un `ClientError`, registrado con el prefijo `ERROR:` y devolviendo 500.
13. **(SE-13, Get, DETECTABLE, incondicional)** WHEN el Get_Handler en estado sembrado construye la respuesta de una Task recuperada correctamente, THE Seeded_Codebase SHALL acceder a un nombre de atributo que no forma parte del modelo de datos, provocando un `KeyError` que SHALL quedar registrado con el prefijo `ERROR:` y devolver 500 en toda consulta individual, incumpliendo el Requisito 2.1.
14. **(SE-14, List, DETECTABLE, condicional)** WHEN el List_Handler en estado sembrado recibe un parámetro de consulta de límite de resultados, THE Seeded_Codebase SHALL transferirlo a DynamoDB como cadena de texto sin convertirlo a entero, provocando un `ParamValidationError` para cualquier valor, registrado con el prefijo `ERROR:` y devolviendo 500.
15. **(SE-15, List, DETECTABLE, condicional)** WHEN el List_Handler en estado sembrado recibe un parámetro de consulta de continuación de paginación, THE Seeded_Codebase SHALL transferirlo a DynamoDB como cadena de texto sin decodificarlo a la estructura de clave que el servicio espera, provocando un `ParamValidationError` registrado con el prefijo `ERROR:` y devolviendo 500.
16. **(SE-16, Update, DETECTABLE, condicional)** WHEN el Update_Handler en estado sembrado recibe el atributo booleano de estado de completitud, THE Seeded_Codebase SHALL validarlo con el validador del atributo de título en lugar del validador booleano correspondiente, provocando un error de validación que SHALL quedar registrado con el prefijo `ERROR:` y devolver 400. Consecuencia asumida: el atributo de completitud no puede actualizarse por ninguna vía, incumpliendo el Requisito 4.1.
17. **(SE-17, Update, DETECTABLE, condicional)** WHEN el Update_Handler en estado sembrado recibe un atributo numérico no entero, THE Seeded_Codebase SHALL incluirlo en la expresión de actualización sin convertirlo al tipo decimal exigido por DynamoDB, provocando un `TypeError` registrado con el prefijo `ERROR:` y devolviendo 500.
18. **(SE-18, Delete, DETECTABLE, incondicional)** WHEN el Delete_Handler en estado sembrado elimina una Task cuyo identificador no es puramente numérico, THE Seeded_Codebase SHALL construir la clave con un nombre de atributo distinto del definido como clave de partición en el esquema de la tabla, provocando un `ClientError` de discordancia de esquema, registrado con el prefijo `ERROR:` y devolviendo 500 en toda eliminación, incumpliendo el Requisito 5.1.
19. **(SE-19, Delete, DETECTABLE, condicional)** WHEN el Delete_Handler en estado sembrado recibe un identificador compuesto únicamente por dígitos, THE Seeded_Codebase SHALL convertirlo a entero antes de construir la clave, provocando un `ClientError` de discordancia de tipo con el esquema de la tabla, registrado con el prefijo `ERROR:` y devolviendo 500. El Comportamiento_Objetivo (Requisito 6.2) exige verificar el tipo esperado del parámetro antes de operar contra DynamoDB, y el modelo de datos define `task_id` como cadena de texto.
