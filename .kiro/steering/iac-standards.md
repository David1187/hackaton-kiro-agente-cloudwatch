---
name: iac-standards
inclusion: fileMatch
fileMatchPattern: "infra/**/*.py"
description: "Estándares de Infraestructura como Código (AWS CDK en Python) para este proyecto."
---

# Estándares de IaC (infra/)

Esta guía aplica a todo el código bajo `infra/` (AWS CDK en Python). Complementa a `architecture-guide.md`, que fija las decisiones de arquitectura de más alto nivel (IaC = CDK/Python, prohibición de contenedores autogestionados, etc.).

## 1. Nivel de Abstracción de Constructs

- Usar **constructs L2** (`aws_dynamodb.Table`, `aws_lambda.Function`, `aws_apigateway.SpecRestApi`, etc.) como estándar por defecto en todo el proyecto.
- Reservar **L1** (`Cfn*`) únicamente para propiedades no soportadas por el L2 correspondiente, accediendo vía `construct.node.default_child` o `add_property_override` como escape hatch, nunca como primera opción.
- Usar métodos `grant_*()` de los L2 (ej. `table.grant_read_write_data(lambda_fn)`) para permisos IAM en vez de escribir políticas manuales. Esto genera políticas de mínimo privilegio automáticamente.

## 2. DynamoDB

- **Billing mode:** `BillingMode.PAY_PER_REQUEST` (on-demand) en todas las tablas. No usar `PROVISIONED`.
- **Removal policy:** `RemovalPolicy.DESTROY` en todas las tablas, para permitir limpieza completa del entorno de hackathon con `cdk destroy`.
- **Point-in-Time Recovery:** desactivado (no configurar `point_in_time_recovery_specification`). Coherente con el carácter desechable de los datos en este proyecto.
- **Termination protection del stack:** `termination_protection=False` en el `Stack` que contiene la tabla, para no requerir un paso manual antes de poder destruir el stack completo.

```python
from aws_cdk import aws_dynamodb as dynamodb, RemovalPolicy

table = dynamodb.Table(
    self, "ItemsTable",
    partition_key=dynamodb.Attribute(name="id", type=dynamodb.AttributeType.STRING),
    billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
    removal_policy=RemovalPolicy.DESTROY,
)
```

## 3. API Gateway y OpenAPI

- El API Gateway REST debe definirse mediante `apigateway.SpecRestApi` con `ApiDefinition.from_asset("api/openapi.yaml")`. El stack de CDK **no** debe definir rutas/métodos de forma programática (`add_resource`/`add_method`): el `openapi.yaml` es la única fuente de verdad del contrato de la API.
- Cada una de las 5 operaciones CRUD (`create`, `get`, `update`, `delete`, `list`) debe mantenerse como una **Lambda independiente**. El `openapi.yaml` debe declarar, para cada combinación path/método, su propia extensión `x-amazon-apigateway-integration` apuntando al ARN de invocación de la Lambda correspondiente. Queda prohibido usar un recurso proxy genérico (`{proxy+}` + método `ANY`) que enrute todo a una única Lambda, ya que rompería la separación de responsabilidades ya fijada en la estructura de carpetas.
- Los ARNs de las Lambdas no pueden hardcodearse en el YAML (se generan en cada despliegue). El stack de CDK debe resolver el `openapi.yaml` como plantilla, sustituyendo los ARNs mediante tokens de CDK (ej. `CfnInclude` con transformaciones, o generando el documento a partir de un template con placeholders) antes de pasarlo a `ApiDefinition`.
- Autenticación vía Usage Plan + API Key (ver `architecture-guide.md` sección 4), configurada en el propio `SpecRestApi` (`api_key_source_type`) y no en el YAML.

## 4. Testing de Infraestructura

- Usar el módulo `aws_cdk.assertions` para tests de los stacks: `Template.from_stack(stack)` + `template.has_resource_properties(...)`, siguiendo la práctica recomendada por la documentación oficial de CDK.
- Cada stack debe tener al menos un test que verifique:
  - Las propiedades críticas de seguridad/configuración (ej. `BillingMode`, `RemovalPolicy` reflejada en `DeletionPolicy` del template sintetizado).
  - Que los IDs lógicos de recursos con estado (DynamoDB) permanecen estables entre cambios no relacionados, para evitar reemplazos accidentales.
- Framework: `pytest`, igual que el backend, para mantener consistencia de herramientas en todo el repo.

## 5. Organización de Stacks

- Mantener la separación de carpetas ya acordada: `infra/` (CDK) como árbol independiente de `services/` (código de runtime), a pesar de que la guía genérica de CDK sugiere mantener infra y runtime juntos por construct. Esta separación se mantiene deliberadamente para que los `fileMatchPattern` de este steering y de `backend-standards.md` no se solapen.
- Separar stacks con estado (`DynamoDB`) de stacks sin estado (API Gateway, Lambdas, Agente), según la práctica recomendada por AWS, para poder desplegar/destruir de forma independiente cuando sea necesario.
- No hardcodear nombres físicos de recursos (`table_name`, `function_name`, etc.). Dejar que CDK genere los nombres y propagarlos vía referencias de CDK (`table.table_name`) o variables de entorno de la Lambda, nunca como literal.
- Tomar decisiones de configuración (entornos, flags) en tiempo de síntesis usando condicionales de Python, no `CfnParameter`/`CfnCondition` de CloudFormation.

## 6. Región de Despliegue y Confirmación Previa

- Todos los despliegues de este proyecto se realizan en la región **`eu-west-1` (Irlanda)**. El entorno del `Stack` (`env=Environment(region="eu-west-1", ...)`) debe fijar esta región explícitamente; no depender de la región configurada por defecto en el entorno de quien ejecuta `cdk deploy`.
- El despliegue usa el **perfil de AWS por defecto** (`default`) del entorno donde se ejecuta, no un perfil con nombre hardcodeado en el código.
- Antes de cualquier despliegue real (`cdk deploy`), es obligatorio mostrar al usuario, y esperar confirmación explícita, de:
  1. Cuenta de AWS y región de destino (`eu-west-1`).
  2. Lista de recursos afectados y la acción sobre cada uno (crear, actualizar o eliminar), obtenida de `cdk diff` o del changeset de CloudFormation antes de ejecutar el despliegue.
- Tras el despliegue, verificar que el stack quedó en un estado exitoso (`CREATE_COMPLETE`/`UPDATE_COMPLETE`). Si el despliegue falla, se debe diagnosticar la causa raíz (eventos de la stack, CloudTrail) e informar al usuario con el detalle del fallo, sin reintentar automáticamente ni revertir sin confirmación.

## 7. Versionado del Stack

- **Python:** 3.13 fijo, igual que el runtime de las Lambdas/Agente (ver `backend-standards.md`), para evitar divergencia de versión entre el entorno de síntesis de CDK y el código que despliega.
- **AWS CDK:** familia **CDK v2** (`aws-cdk-lib`) fija. No usar CDK v1 (en fin de soporte) bajo ninguna circunstancia. La versión exacta de `aws-cdk-lib` dentro de la serie 2.x debe resolverse vía Context7/MCP `aws-iac-mcp-server` en el momento de implementar (evoluciona con frecuencia), y fijarse como versión exacta (`==`) en el `requirements.txt` de la raíz.
- **Node.js (runtime de la CLI de CDK):** no se fija una versión concreta. La CLI `cdk` corre sobre Node.js/jsii por debajo del código Python, pero se considera una herramienta de entorno de quien ejecuta los comandos, no una librería/dependencia del proyecto en sí. Decisión consciente: no forma parte del stack versionado de este steering.
- **Herramientas de auditoría de seguridad** (`cdk-nag`, consumido vía `check_cloudformation_template_compliance` del MCP `aws-iac-mcp-server`): responsabilidad de `reviewer-agent`, no de `iac-agent`. Nunca fijar a una versión pinneada; usar siempre la más reciente disponible en el momento de la auditoría, por la misma razón que en `backend-standards.md` sección 8 (reglas de compliance se actualizan con el tiempo).

## 8. Política de Tagging

### 8.1. Tag funcional obligatorio: `github-repo`

- Cada Lambda CRUD (`services/crud_api/**`) debe llevar el tag `github-repo` con el valor exacto **`David1187/hackaton-kiro-agente-cloudwatch`** (ver `architecture-guide.md` sección 2.3). Como todas las Lambdas de este proyecto apuntan al mismo repositorio, este valor es constante en las 5 Lambdas, no varía por función.
- Se aplica directamente vía el parámetro/propiedad de tags del propio construct al definir cada Lambda (ej. `tags={"github-repo": "David1187/hackaton-kiro-agente-cloudwatch"}` en `lambda.Function`, o `Tags.of(lambda_fn).add("github-repo", "David1187/hackaton-kiro-agente-cloudwatch")` justo después de crearla), no mediante un Aspect de validación en tiempo de síntesis. No es necesario un mecanismo de verificación adicional dado que el valor es fijo y compartido por todas las Lambdas del proyecto.

### 8.2. Tags transversales de gestión y coste

Aplicar a nivel de `App`/`Stack` (`Tags.of(app).add(...)` en `app.py`), para que CloudFormation los propague automáticamente a todos los recursos que soportan tags, sin repetirlos recurso por recurso:

| Tag | Valor | Propósito |
|---|---|---|
| `Project` | `hackaton-kiro-agente-cloudwatch` | Identificar todos los recursos de este proyecto en la cuenta de AWS. |
| `Environment` | `hackathon` | Distinguir de cualquier entorno futuro (dev/prod) si el proyecto evoluciona más allá del hackathon. |
| `ManagedBy` | `cdk` | Señalar que el recurso se gestiona por IaC y no debe modificarse manualmente desde la consola. |

Estos tags transversales son independientes del tag funcional `github-repo`: los primeros sirven para gestión/coste de la cuenta AWS, el segundo es leído en tiempo de ejecución por el Agente de auto-reparación para resolver el repositorio a corregir.
