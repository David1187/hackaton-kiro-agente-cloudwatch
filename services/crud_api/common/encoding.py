"""
encoding.py — DecimalEncoder for JSON serialisation of DynamoDB Decimal values.

DynamoDB returns numeric attributes as decimal.Decimal.  The standard
json.dumps() raises TypeError on Decimal.  DecimalEncoder converts:
  - Decimal with no fractional part  → int
  - Decimal with fractional part     → float
"""

import decimal
import json


class DecimalEncoder(json.JSONEncoder):
    """JSONEncoder subclass that handles decimal.Decimal from DynamoDB."""

    def default(self, obj):  # noqa: ANN001
        if isinstance(obj, decimal.Decimal):
            # Preserve integer semantics when there is no fractional part
            if obj % 1 == 0:
                return int(obj)
            return float(obj)
        return super().default(obj)
