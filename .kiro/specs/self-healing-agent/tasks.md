# Implementation Plan: Self-Healing Agent

## Overview

Este plan convierte el diseño del `self-healing-agent` en pasos de código incrementales. Se implementa en **Python 3.13** (según el diseño): primero la lógica pura y testeable del runtime del agente (Strands), luego el acceso a CloudWatch Logs y las herramientas MCP vía Gateway, después la orquestación ReAct (`handle_event`) que integra todo, y finalmente la infraestructura AWS CDK (constructs + stack + wiring de EventBridge) que despliega el agente 100% serverless (sin ECS/Fargate/EC2/ECR).

Cada tarea construye sobre las anteriores y termina cableada al conjunto; no queda código huérfano. Las sub-tareas de test están marcadas con `*` (opcionales) y se sitúan cerca de la implementación que validan.

## Tasks

- [x] 1. Preparar la estructura del servicio del agente y sus dependencias
  - [x] 1.1 Crear la estructura de `services/self_healing_agent` y ficheros de dependencias con pins exactos
    - Crear `services/self_healing_agent/` con `__init__.py` y el paquete `tests/`
    - Crear `requirements.txt` con pins exactos: `strands-agents`, `bedrock-agentcore`, `boto3`
    - Crear `requirements-dev.txt` con pins exactos: `pytest`, `hypothesis`
    - Añadir módulos vacíos como placeholders: `config.py`, `repo_tag.py`, `branch_naming.py`, `pr_body.py`, `logs_client.py`, `mcp_tools.py`, `agent.py`
    - _Requirements: 3.2_

- [x] 2. Implementar la lógica pura del runtime del agente
  - [x] 2.1 Implementar la resolución del identificador de modelo en `config.py`
    - Implementar `resolve_model_id(env: Mapping[str, str]) -> str` que devuelva `env["Model_Id_Variable"]` si está presente y no vacío, y en cualquier otro caso el default `qwen.qwen3-coder-30b-a3b-v1:0`
    - No incluir ningún otro literal de modelo en el código
    - _Requirements: 4.1, 4.2, 4.3_

  - [x]* 2.2 Escribir property test para la resolución del modelo
    - **Property 1: Resolución del identificador del modelo**
    - **Validates: Requirements 4.1, 4.2**
    - En `tests/test_properties.py`, usar `hypothesis` (`@settings(max_examples=100)`), etiquetar con `# Feature: self-healing-agent, Property 1: ...`

  - [x] 2.3 Implementar el parseo y validación del tag `github-repo` en `repo_tag.py`
    - Definir `RepoRef` (owner, repo) y `InvalidRepoTagError`
    - Implementar `parse_repo_tag(tags: dict) -> RepoRef`: exige exactamente un `/`, `owner` y `repo` no vacíos, sin espacios; lanza `InvalidRepoTagError` si falta el tag o el formato es inválido
    - _Requirements: 6.1, 6.2, 6.5_

  - [x]* 2.4 Escribir property test para el tag `github-repo`
    - **Property 2: Round-trip y rechazo del tag `github-repo`**
    - **Validates: Requirements 6.2, 6.5**
    - En `tests/test_properties.py`, `hypothesis` con `max_examples>=100`, etiquetar con `# Feature: self-healing-agent, Property 2: ...`

  - [x]* 2.5 Escribir unit tests de ejemplo/edge case para `repo_tag`
    - En `tests/test_repo_tag.py`: tag ausente, cadena vacía, sin `/`, múltiples `/`, `owner`/`repo` vacíos
    - _Requirements: 6.5_

  - [x] 2.6 Implementar el naming de la Fix_Branch en `branch_naming.py`
    - Implementar `build_fix_branch_name(lambda_name: str, timestamp: datetime) -> str` que produzca `fix/auto-heal-{lambda}-{timestamp}`, saneando caracteres no válidos para refs de Git y usando timestamp UTC compacto (p. ej. `20250115T103045Z`)
    - _Requirements: 9.1_

  - [x]* 2.7 Escribir property test para el nombre de la Fix_Branch
    - **Property 4: Formato y validez del nombre de la Fix_Branch**
    - **Validates: Requirements 9.1**
    - En `tests/test_properties.py`, `hypothesis` con `max_examples>=100`, etiquetar con `# Feature: self-healing-agent, Property 4: ...`

  - [x]* 2.8 Escribir unit tests de ejemplo/edge case para `branch_naming`
    - En `tests/test_branch_naming.py`: saneo de caracteres, unicidad por timestamp, prefijo `fix/auto-heal-`
    - _Requirements: 9.1_

  - [x] 2.9 Implementar el constructor del cuerpo del Pull_Request en `pr_body.py`
    - Implementar `build_pr_description(lambda_name: str, error_summary: str) -> str` (función pura) que incluya el nombre de la Affected_Lambda y el resumen del error detectado; añadir `build_pr_title(lambda_name)` para el título
    - _Requirements: 9.4_

  - [x]* 2.10 Escribir property test para el cuerpo del Pull_Request
    - **Property 5: El cuerpo del Pull_Request referencia la Affected_Lambda y el error**
    - **Validates: Requirements 9.4**
    - En `tests/test_properties.py`, `hypothesis` con `max_examples>=100`, etiquetar con `# Feature: self-healing-agent, Property 5: ...`

- [x] 3. Implementar el acceso a CloudWatch Logs y las herramientas MCP
  - [x] 3.1 Implementar la derivación del Log_Group y la obtención del Stack_Trace en `logs_client.py`
    - Implementar `derive_log_group(function_name: str) -> str` que produzca exactamente `/aws/lambda/{function_name}` (función pura)
    - Implementar `derive_function_name(event: dict) -> str` a partir de la metadata de la alarma
    - Implementar `get_latest_stack_trace(log_group: str) -> str | None` con `boto3` (`filter_log_events`, patrón `ERROR:`, más reciente), envuelto en `try-except`: si no hay registros devuelve `None`; si la consulta falla, `logging.error("ERROR: ...", exc_info=True)` y propaga un fallo controlado
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [x]* 3.2 Escribir property test para la derivación del Log_Group
    - **Property 3: Derivación del Log_Group desde la metadata de la alarma**
    - **Validates: Requirements 5.1**
    - En `tests/test_properties.py`, `hypothesis` con `max_examples>=100`, etiquetar con `# Feature: self-healing-agent, Property 3: ...`

  - [x] 3.3 Implementar los wrappers de herramientas del GitHub_MCP vía AgentCore_Gateway en `mcp_tools.py`
    - Definir wrappers (Strands tools) que invoquen exclusivamente el GitHub_MCP a través del Gateway: `get_file_contents`, `create_branch` (desde `main`), `create_or_update_file`, `create_pull_request`
    - No exponer ninguna herramienta de merge/approve ni flag que la habilite; el agente no recibe el PAT
    - Cada wrapper envuelto en `try-except` que registra `ERROR:` ante fallo (incluye fallo de credencial del Gateway)
    - _Requirements: 7.1, 7.4, 8.1, 9.2, 9.3, 10.2, 10.3, 10.4_

- [x] 4. Cablear la orquestación ReAct y el entrypoint del agente
  - [x] 4.1 Implementar el entrypoint de AgentCore y el ciclo ReAct en `agent.py`
    - Exponer el contrato del AgentCore Runtime (`/invocations` POST, `/ping` GET) con `bedrock-agentcore` + `strands-agents`
    - Implementar `handle_event(event)` integrando todos los módulos en orden: derivar Log_Group (`logs_client`), obtener Stack_Trace, leer tag y `parse_repo_tag` (`repo_tag`), leer archivo vía MCP (`mcp_tools`), invocar el LLM con `resolve_model_id` (`config`) para generar el parche, construir `build_fix_branch_name`, escribir el parche y abrir el PR con `build_pr_description`/`build_pr_title`
    - Cada paso de I/O en `try-except`: ante cualquier fallo, `logging.error("ERROR: ...")` y terminar sin abrir/dejar a medias un Pull_Request; nunca merge/approve
    - _Requirements: 3.1, 4.4, 5.1, 5.2, 5.3, 5.4, 6.1, 6.3, 6.4, 7.1, 7.4, 8.2, 8.3, 9.1, 9.2, 9.3, 9.4, 9.5, 10.1_

  - [x]* 4.2 Escribir unit tests de flujo y caminos de error del agente
    - En `tests/test_agent_flow.py` con mocks de `logs_client`, cliente de tags, cliente Bedrock y tools MCP: sin `ERROR:` (5.3), fallo de consulta a Logs (5.4), tag inválido/ausente (6.5), fallo de credencial del Gateway (7.5), fallo al crear rama/PR (9.5), ausencia de merge/approve (10.x), no enumeración de repos (6.4)
    - _Requirements: 5.3, 5.4, 6.4, 6.5, 7.5, 9.5, 10.1, 10.2, 10.3, 10.4_

- [x] 5. Checkpoint - Asegurar que el runtime del agente pasa sus tests
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implementar la infraestructura AWS CDK (constructs + stack)
  - [x] 6.1 Implementar el construct de observability wiring en `infra/constructs/observability_wiring.py`
    - Comportamiento idempotente: en modo reuse referenciar Metric_Filter/Alarm existentes de `todo-crud-api` (`from_alarm_arn`/`from_log_group_name`); en modo create (flag de contexto `create_observability=true`) crear Metric_Filter (`FilterPattern.literal('"ERROR:"')`) y Alarm (`threshold=1`, `evaluation_periods=1`, `GREATER_THAN_OR_EQUAL_TO_THRESHOLD`, `treat_missing_data=NOT_BREACHING`)
    - Decisión en tiempo de síntesis (condicional Python), no `CfnCondition`
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 6.2 Implementar el construct del AgentCore Runtime + rol IAM en `infra/constructs/agent_runtime.py`
    - Configurar AgentCore Runtime con **direct code deployment** (zip del agente en S3, ARM64, sin ECR/contenedor); pasar `Model_Id_Variable` como variable de entorno; no definir variable con el PAT
    - Rol de ejecución de mínimo privilegio: `bedrock:InvokeModel`, lectura de Logs limitada a los Log_Groups CRUD, `lambda:ListTags`/`tag:GetResources`; SIN `secretsmanager:GetSecretValue`
    - Si el soporte L2 de CDK no está maduro, usar escape hatch L1 (`Cfn*`)/custom resource y documentarlo
    - _Requirements: 3.1, 3.3, 3.4, 4.1, 11.1, 11.2, 11.4, 11.5_

  - [x] 6.3 Implementar el construct del AgentCore Gateway + Secret en `infra/constructs/agent_gateway.py`
    - Crear `secretsmanager.Secret` del GitHub_PAT (sin hardcodear el valor); configurar el Gateway con target al GitHub_MCP remoto y credencial saliente que referencia el secreto
    - Conceder `grant_read` del secreto solo al rol del Gateway
    - _Requirements: 7.1, 7.2, 7.3, 11.3, 11.5_

  - [x] 6.4 Implementar el `agent_stack.py` ensamblando los constructs y la EventBridge_Rule
    - En `infra/stacks/agent_stack.py` instanciar los tres constructs y crear la `events.Rule` con `EventPattern` (`source=["aws.cloudwatch"]`, `detail-type=["CloudWatch Alarm State Change"]`, `detail.state.value=["ALARM"]`, `alarmName` de las alarmas CRUD) y target al AgentCore Runtime con rol que permita `InvokeAgentRuntime`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.4_

  - [x] 6.5 Registrar el `agent_stack` en `app.py`
    - Añadir el stack sin estado en `app.py` en la región `eu-west-1`, integrándolo con el resto de stacks
    - _Requirements: 3.3_

  - [x]* 6.6 Escribir tests de infraestructura con `aws_cdk.assertions`
    - En `infra/tests/test_agent_stack.py`: EventBridge_Rule con `detail.state.value=["ALARM"]` y target al runtime; existencia del Secret; `GetSecretValue` en el rol del Gateway y ausencia en el rol del agente; permisos del agente (`bedrock:InvokeModel`, Logs limitados, tags); `resource_count_is == 0` para `AWS::ECS::*`, `AWS::EC2::Instance`, `AWS::ECR::*`; modo reuse no duplica Metric_Filter/Alarm y modo create sí
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.4, 3.4, 7.2, 11.1, 11.2, 11.3, 11.4, 11.5_

- [x] 7. Checkpoint final - Asegurar que todos los tests pasan
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Las tareas marcadas con `*` son opcionales (tests) y pueden omitirse para un MVP más rápido, pero se recomienda ejecutarlas.
- Cada tarea referencia requisitos específicos para trazabilidad.
- Los property tests (P1–P5) usan `hypothesis` con `max_examples>=100` y se limitan a la lógica pura del agente; la infraestructura CDK y la integración con servicios externos se validan con assertions/snapshots y tests basados en mocks/ejemplos.
- El agente nunca hace merge/approve y nunca accede al GitHub_PAT en texto plano; el Gateway es la única frontera con el secreto.
- El `model_id` se externaliza vía `Model_Id_Variable` (default `qwen.qwen3-coder-30b-a3b-v1:0`) sin literales adicionales en el código.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "2.3", "2.6", "2.9", "3.1", "3.3", "6.1", "6.2", "6.3"] },
    { "id": 2, "tasks": ["2.2", "2.5", "2.8", "4.1", "6.4"] },
    { "id": 3, "tasks": ["2.4", "4.2", "6.5"] },
    { "id": 4, "tasks": ["2.7", "6.6"] },
    { "id": 5, "tasks": ["2.10"] },
    { "id": 6, "tasks": ["3.2"] }
  ]
}
```
