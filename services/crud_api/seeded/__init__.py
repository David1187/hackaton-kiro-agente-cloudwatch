"""
services/crud_api/seeded — Variantes sembradas con errores intencionales.

Este paquete contiene handlers deliberadamente defectuosos para la demo del
Agente de Auto-reparación.  Cada módulo implementa exactamente un Seeded_Error
(SE-1 … SE-9) del catálogo documentado en SEEDED_ERRORS.md.

ADVERTENCIA: Estos handlers NO deben desplegarse como endpoints de producción.
Su único propósito es ser el «código inicial con errores» que el Agente
detectará, analizará, y propondrá corregir mediante un Pull Request.

Módulos disponibles:
  se1_create_no_title_validation   — SE-1: Create acepta títulos vacíos/whitespace.
  se2_create_hardcoded_timestamp   — SE-2: Create usa timestamp hardcoded.
  se3_get_missing_task_id          — SE-3: Get lanza KeyError si task_id ausente.
  se4_list_no_limit                — SE-4: List scan sin Limit (riesgo timeout).
  se5_update_no_field_validation   — SE-5: Update acepta body vacío.
  se6_update_no_updated_at         — SE-6: Update no refresca updated_at.
  se7_delete_no_condition          — SE-7: Delete sin ConditionExpression (no 404).
  se8_no_try_except                — SE-8: Handler sin try-except.
  se9_print_instead_of_logging     — SE-9: Logging vía print() (no detectado por Metric Filter).
"""
