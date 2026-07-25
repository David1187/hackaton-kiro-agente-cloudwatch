# Requirements Document

## Introduction

Esta funcionalidad define el **Agente de Auto-reparación** (Self-Healing Agent): un componente 100% serverless que se activa automáticamente cuando una de las Lambdas CRUD del proyecto (definidas en la spec `todo-crud-api`) registra un error de ejecución. El agente analiza los logs, identifica la causa, genera un parche de código y abre un Pull Request en el repositorio de GitHub correspondiente para que un humano lo revise y apruebe. En ningún caso el agente hace merge automático.

El flujo de detección y remediación es el siguiente:

1. Una Lambda CRUD registra un fallo con el prefijo `ERROR:` en su Log Group de CloudWatch.
2. Un **Metric Filter** (patrón `ERROR:`) incrementa una métrica que dispara una **CloudWatch Alarm**.
3. El cambio de estado de la alarma se publica en **Amazon EventBridge**, que invoca al agente.
4. El agente, alojado en **Amazon Bedrock AgentCore Runtime** y desarrollado con **Strands-Agents (Python)**, consulta CloudWatch Logs para obtener el stack trace real (el evento de EventBridge solo trae metadata).
5. El agente lee el tag `github-repo` de la Lambda afectada para resolver el repositorio objetivo (`owner/repo`).
6. El agente, usando el modelo **Qwen3 Coder 30B A3B** (`qwen.qwen3-coder-30b-a3b-instruct`, configurable vía variable de entorno), genera el parche.
7. A través de **AgentCore Gateway** conectado al **MCP remoto oficial de GitHub** (con un PAT almacenado en **AWS Secrets Manager** e inyectado de forma efímera por el Gateway), el agente crea una rama `fix/auto-heal-{lambda}-{timestamp}` desde `main`, escribe el parche y abre un Pull Request.
8. El Pull Request queda pendiente de revisión y aprobación humana obligatoria.

### Relación con la spec `todo-crud-api`

La spec `todo-crud-api` define en su diseño la infraestructura de observabilidad (Log Group, Metric Filter con patrón `ERROR:` y CloudWatch Alarm), pero declara explícitamente que la conexión de la alarma con EventBridge y el agente queda **fuera de su alcance**. Esta feature es responsable de:

- Asociar la CloudWatch Alarm existente a una regla de EventBridge que invoque al agente.
- Crear el Metric Filter y la CloudWatch Alarm **únicamente si no han sido creados** por la spec `todo-crud-api`, garantizando que no se dupliquen recursos.
- Implementar y desplegar todo el resto de componentes del agente (AgentCore Runtime, AgentCore Gateway, integración MCP de GitHub, Secrets Manager).

### Restricciones de arquitectura (steering `architecture-guide.md`)

- Toda la infraestructura se despliega **exclusivamente con AWS CDK en Python**.
- El diseño es **100% serverless**. Queda prohibido usar ECS, Fargate, EC2 o ECR.
- El `model_id` del LLM debe ser configurable vía variable de entorno, nunca hardcodeado.
- El PAT de GitHub jamás es accesible en texto plano por el código del agente; el AgentCore Gateway lo inyecta en tránsito.
- El repositorio objetivo se resuelve por el tag `github-repo` de la Lambda afectada, nunca por escaneo de repositorios.
- El merge automático de Pull Requests está prohibido sin excepciones.

## Glossary

- **Self_Healing_Agent**: Agente autónomo alojado en Amazon Bedrock AgentCore Runtime, desarrollado con Strands-Agents en Python, que analiza errores de las Lambdas CRUD y abre Pull Requests con propuestas de corrección.
- **AgentCore_Runtime**: Amazon Bedrock AgentCore Runtime, entorno serverless administrado donde se ejecuta el Self_Healing_Agent.
- **AgentCore_Gateway**: Componente de AWS que actúa como intermediario autenticado entre el Self_Healing_Agent y el GitHub_MCP, responsable de inyectar el GitHub_PAT de forma efímera en tránsito.
- **GitHub_MCP**: Servidor Model Context Protocol remoto oficial de GitHub que expone herramientas para leer archivos, crear ramas y abrir Pull Requests.
- **LLM_Model**: Modelo de lenguaje utilizado por el Self_Healing_Agent para analizar errores y generar parches. Por defecto Qwen3 Coder 30B A3B (identificador Bedrock `qwen.qwen3-coder-30b-a3b-instruct`), gestionado/serverless en Amazon Bedrock.
- **Model_Id_Variable**: Variable de entorno del AgentCore_Runtime que contiene el identificador del LLM_Model, permitiendo cambiarlo sin redeploy de código.
- **Metric_Filter**: CloudWatch Metric Filter configurado con el patrón `ERROR:` sobre el Log_Group de una Lambda CRUD, que incrementa una métrica de conteo de errores.
- **CloudWatch_Alarm**: Alarma de CloudWatch sobre la métrica del Metric_Filter que cambia a estado `ALARM` cuando se detecta al menos un error en el periodo de evaluación.
- **EventBridge_Rule**: Regla de Amazon EventBridge que reacciona al cambio de estado de la CloudWatch_Alarm hacia `ALARM` e invoca al Self_Healing_Agent.
- **Log_Group**: Grupo de logs de CloudWatch asociado a una Lambda CRUD, donde se registran los mensajes con el prefijo `ERROR:` y el stack trace.
- **Affected_Lambda**: Función Lambda CRUD cuyo error disparó la CloudWatch_Alarm y cuyo código debe ser reparado.
- **Github_Repo_Tag**: Tag con clave `github-repo` presente en cada Affected_Lambda, cuyo valor sigue el formato `owner/repo` e identifica el Target_Repository.
- **Target_Repository**: Repositorio de GitHub, resuelto a partir del Github_Repo_Tag, sobre el que el Self_Healing_Agent opera para leer código y abrir el Pull_Request.
- **GitHub_PAT**: Personal Access Token de GitHub almacenado en AWS Secrets Manager, usado por el AgentCore_Gateway para autenticarse contra el GitHub_MCP.
- **Secrets_Manager**: AWS Secrets Manager, servicio donde se almacena de forma cifrada el GitHub_PAT.
- **Fix_Branch**: Rama de corrección creada por el Self_Healing_Agent con el patrón de nombre `fix/auto-heal-{lambda}-{timestamp}`, siempre a partir de `main`.
- **Pull_Request**: Solicitud de incorporación de cambios abierta por el Self_Healing_Agent en el Target_Repository, sujeta a revisión y aprobación humana obligatoria.
- **Stack_Trace**: Traza de error completa obtenida por el Self_Healing_Agent desde el Log_Group mediante FilterLogEvents o Logs Insights.
- **IaC_Stack**: Conjunto de recursos de infraestructura definidos con AWS CDK en Python que despliega esta feature.

## Requirements

### Requirement 1: Detección de errores mediante Metric Filter y Alarm

**User Story:** Como ingeniero de operaciones, quiero que los errores registrados por las Lambdas CRUD se detecten automáticamente, para que el Self_Healing_Agent pueda activarse sin intervención humana.

#### Acceptance Criteria

1. WHERE el Metric_Filter con el patrón `ERROR:` sobre el Log_Group de una Affected_Lambda no ha sido creado por la spec `todo-crud-api`, THE IaC_Stack SHALL crear un Metric_Filter con `filterPattern` igual al literal `ERROR:` que incremente una métrica de conteo de errores.
2. WHERE la CloudWatch_Alarm sobre la métrica de errores no ha sido creada por la spec `todo-crud-api`, THE IaC_Stack SHALL crear una CloudWatch_Alarm que pase a estado `ALARM` cuando el conteo de errores sea mayor o igual a 1 en el periodo de evaluación.
3. WHERE el Metric_Filter y la CloudWatch_Alarm ya han sido creados por la spec `todo-crud-api`, THE IaC_Stack SHALL reutilizar dichos recursos existentes sin crear recursos duplicados.
4. WHEN el conteo de la métrica de errores alcanza o supera el umbral definido, THE CloudWatch_Alarm SHALL cambiar a estado `ALARM`.

### Requirement 2: Enrutamiento del evento a través de EventBridge

**User Story:** Como arquitecto del sistema, quiero que el cambio de estado de la alarma se enrute a través de EventBridge, para invocar al Self_Healing_Agent de forma desacoplada y serverless.

#### Acceptance Criteria

1. THE IaC_Stack SHALL crear una EventBridge_Rule que reaccione al evento nativo de cambio de estado de la CloudWatch_Alarm hacia el estado `ALARM`.
2. WHEN la CloudWatch_Alarm cambia a estado `ALARM`, THE EventBridge_Rule SHALL invocar al Self_Healing_Agent alojado en el AgentCore_Runtime.
3. WHEN la EventBridge_Rule invoca al Self_Healing_Agent, THE EventBridge_Rule SHALL transmitir la metadata del evento de alarma que identifique la Affected_Lambda y su Log_Group.
4. IF la CloudWatch_Alarm cambia a un estado distinto de `ALARM`, THEN THE EventBridge_Rule SHALL NOT invocar al Self_Healing_Agent.

### Requirement 3: Alojamiento serverless del agente

**User Story:** Como responsable de la arquitectura, quiero que el agente se ejecute en un entorno serverless administrado, para cumplir la restricción de no usar contenedores autogestionados.

#### Acceptance Criteria

1. THE Self_Healing_Agent SHALL ejecutarse en Amazon Bedrock AgentCore Runtime como entorno de ejecución serverless administrado.
2. THE Self_Healing_Agent SHALL implementarse con el SDK de Strands-Agents en Python.
3. THE IaC_Stack SHALL desplegar el Self_Healing_Agent exclusivamente mediante AWS CDK en Python.
4. THE IaC_Stack SHALL NOT definir recursos basados en Amazon ECS, AWS Fargate, Amazon EC2 ni Amazon ECR para el Self_Healing_Agent.

### Requirement 4: Configuración del modelo LLM

**User Story:** Como operador del agente, quiero poder cambiar el modelo LLM sin redesplegar código, para adaptar el agente a la disponibilidad de modelos en Bedrock.

#### Acceptance Criteria

1. THE Self_Healing_Agent SHALL obtener el identificador del LLM_Model desde la Model_Id_Variable de entorno del AgentCore_Runtime.
2. WHERE la Model_Id_Variable no está definida, THE Self_Healing_Agent SHALL usar el valor por defecto `qwen.qwen3-coder-30b-a3b-instruct`.
3. THE Self_Healing_Agent SHALL NOT contener el identificador del LLM_Model codificado de forma fija en el código fuente fuera de la lectura de la Model_Id_Variable y su valor por defecto.
4. WHEN el Self_Healing_Agent analiza un error y genera un parche, THE Self_Healing_Agent SHALL invocar el LLM_Model identificado por la Model_Id_Variable a través de Amazon Bedrock.

### Requirement 5: Análisis del error mediante consulta a CloudWatch Logs

**User Story:** Como agente de auto-reparación, quiero obtener el stack trace real del error desde CloudWatch Logs, para poder diagnosticar la causa a partir de información completa y no solo de la metadata de la alarma.

#### Acceptance Criteria

1. WHEN el Self_Healing_Agent recibe la invocación de la EventBridge_Rule, THE Self_Healing_Agent SHALL determinar el Log_Group de la Affected_Lambda a partir de la metadata del evento.
2. WHEN el Self_Healing_Agent identifica el Log_Group, THE Self_Healing_Agent SHALL consultar CloudWatch Logs mediante FilterLogEvents o Logs Insights para obtener el registro de error más reciente que contenga el prefijo `ERROR:` y su Stack_Trace completo.
3. IF el Self_Healing_Agent no encuentra ningún registro de error con el prefijo `ERROR:` en el Log_Group, THEN THE Self_Healing_Agent SHALL registrar la ausencia de stack trace y finalizar la ejecución sin abrir ningún Pull_Request.
4. IF la consulta a CloudWatch Logs falla, THEN THE Self_Healing_Agent SHALL registrar el fallo con el prefijo `ERROR:` y finalizar la ejecución sin abrir ningún Pull_Request.

### Requirement 6: Resolución del repositorio objetivo por tag

**User Story:** Como responsable de seguridad, quiero que el agente opere solo sobre el repositorio indicado por el tag de la Lambda afectada, para limitar el radio de exposición del PAT.

#### Acceptance Criteria

1. WHEN el Self_Healing_Agent identifica la Affected_Lambda, THE Self_Healing_Agent SHALL leer el valor del Github_Repo_Tag de dicha Affected_Lambda.
2. WHEN el Self_Healing_Agent lee el Github_Repo_Tag, THE Self_Healing_Agent SHALL interpretar su valor con el formato `owner/repo` para determinar el Target_Repository.
3. THE Self_Healing_Agent SHALL operar únicamente sobre el Target_Repository resuelto a partir del Github_Repo_Tag.
4. THE Self_Healing_Agent SHALL NOT escanear ni enumerar repositorios accesibles por el GitHub_PAT distintos del Target_Repository.
5. IF la Affected_Lambda carece del Github_Repo_Tag o su valor no cumple el formato `owner/repo`, THEN THE Self_Healing_Agent SHALL registrar el fallo con el prefijo `ERROR:` y finalizar la ejecución sin abrir ningún Pull_Request.

### Requirement 7: Conectividad segura con GitHub mediante AgentCore Gateway y MCP

**User Story:** Como responsable de seguridad, quiero que el token de GitHub nunca sea accesible por el código del agente, para evitar la exposición de credenciales en texto plano.

#### Acceptance Criteria

1. THE Self_Healing_Agent SHALL interactuar con el Target_Repository exclusivamente a través de herramientas del GitHub_MCP invocadas por medio del AgentCore_Gateway.
2. THE GitHub_PAT SHALL almacenarse cifrado en Secrets_Manager.
3. WHEN el Self_Healing_Agent invoca una herramienta del GitHub_MCP, THE AgentCore_Gateway SHALL recuperar el GitHub_PAT desde Secrets_Manager e inyectarlo en tránsito en la llamada al GitHub_MCP.
4. THE Self_Healing_Agent SHALL NOT leer, recibir ni acceder al GitHub_PAT en texto plano por ningún medio, incluida la variable de entorno.
5. IF el AgentCore_Gateway no puede recuperar el GitHub_PAT desde Secrets_Manager, THEN THE Self_Healing_Agent SHALL registrar el fallo con el prefijo `ERROR:` y finalizar la ejecución sin abrir ningún Pull_Request.

### Requirement 8: Generación del parche de código

**User Story:** Como agente de auto-reparación, quiero generar un parche de código defensivo a partir del stack trace, para proponer una corrección real del error detectado.

#### Acceptance Criteria

1. WHEN el Self_Healing_Agent dispone del Stack_Trace y del código fuente afectado, THE Self_Healing_Agent SHALL leer el contenido del archivo fuente afectado desde el Target_Repository mediante la herramienta del GitHub_MCP correspondiente.
2. WHEN el Self_Healing_Agent genera un parche, THE Self_Healing_Agent SHALL producir el contenido de código corregido aplicando programación defensiva sin eliminar la lógica CRUD existente.
3. THE Self_Healing_Agent SHALL generar el parche de forma autónoma sin delegar la generación del código de corrección a un tercero externo distinto del LLM_Model.

### Requirement 9: Creación de la rama y el Pull Request

**User Story:** Como revisor humano, quiero que cada corrección llegue como un Pull Request en una rama dedicada, para poder revisarla y aprobarla de forma controlada.

#### Acceptance Criteria

1. WHEN el Self_Healing_Agent dispone de un parche, THE Self_Healing_Agent SHALL crear una Fix_Branch a partir de la rama `main` del Target_Repository con el nombre `fix/auto-heal-{lambda}-{timestamp}`.
2. WHEN el Self_Healing_Agent ha creado la Fix_Branch, THE Self_Healing_Agent SHALL escribir el contenido del parche en la Fix_Branch mediante herramientas del GitHub_MCP.
3. WHEN el parche ha sido escrito en la Fix_Branch, THE Self_Healing_Agent SHALL abrir un Pull_Request desde la Fix_Branch hacia la rama `main` del Target_Repository mediante la herramienta del GitHub_MCP correspondiente.
4. WHEN el Self_Healing_Agent abre un Pull_Request, THE Self_Healing_Agent SHALL incluir en la descripción del Pull_Request la referencia a la Affected_Lambda y un resumen del error detectado.
5. IF la creación de la Fix_Branch, la escritura del parche o la apertura del Pull_Request falla, THEN THE Self_Healing_Agent SHALL registrar el fallo con el prefijo `ERROR:` y finalizar la ejecución sin dejar el Target_Repository en un estado que impida reintentos posteriores.

### Requirement 10: Revisión humana obligatoria

**User Story:** Como responsable del repositorio, quiero que ningún cambio generado por el agente se integre sin revisión humana, para garantizar el control sobre el código de producción.

#### Acceptance Criteria

1. THE Self_Healing_Agent SHALL dejar todo Pull_Request en estado pendiente de revisión y aprobación humana.
2. THE Self_Healing_Agent SHALL NOT aprobar ningún Pull_Request generado por sí mismo.
3. THE Self_Healing_Agent SHALL NOT hacer merge de ningún Pull_Request generado por sí mismo.
4. THE Self_Healing_Agent SHALL NOT exponer ningún mecanismo de configuración que permita habilitar el merge automático de Pull_Requests.

### Requirement 11: Permisos de mínimo privilegio del agente

**User Story:** Como responsable de seguridad, quiero que el agente disponga solo de los permisos estrictamente necesarios, para reducir la superficie de riesgo.

#### Acceptance Criteria

1. THE IaC_Stack SHALL conceder al Self_Healing_Agent permisos de lectura sobre CloudWatch Logs limitados a los Log_Groups de las Lambdas CRUD.
2. THE IaC_Stack SHALL conceder al Self_Healing_Agent permisos de lectura del Github_Repo_Tag de las Lambdas CRUD.
3. THE IaC_Stack SHALL conceder al AgentCore_Gateway permiso de lectura del secreto que contiene el GitHub_PAT en Secrets_Manager.
4. THE IaC_Stack SHALL conceder al Self_Healing_Agent permiso de invocación del LLM_Model en Amazon Bedrock.
5. THE IaC_Stack SHALL NOT conceder al Self_Healing_Agent permiso de lectura directa del secreto que contiene el GitHub_PAT en Secrets_Manager.
