import csv
import boto3
from decimal import Decimal, InvalidOperation

REGION = "us-east-1"

GT_FILE = "ground_truth_extended.csv"
RCA_FILE = "rca_results_extended.csv"

dynamodb = boto3.resource("dynamodb", region_name=REGION)

gt_table = dynamodb.Table("TicketBuddy_GroundTruth")
rca_table = dynamodb.Table("TicketBuddy_RCAResults")


def clean(value):
    """Convert empty/NaN-like CSV values to empty strings."""
    if value is None:
        return ""

    value = str(value).strip()

    if value.lower() in {"nan", "none", "null"}:
        return ""

    return value


def decimal_value(value):
    value = clean(value)

    if not value:
        return Decimal("0")

    try:
        return Decimal(value)
    except InvalidOperation:
        return Decimal("0")


# ============================================================
# Ground Truth
# Original CSV = 19 rows
# Extended CSV = 39 rows
# Therefore insert rows after first 19.
# ============================================================

with open(GT_FILE, newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

new_gt = rows[19:]

print(f"GroundTruth new records: {len(new_gt)}")

for row in new_gt:

    item = {
        "trial_id": clean(row["trial_id"]),
        "cleared_at": clean(row["cleared_at"]),
        "fault_type": clean(row["fault_type"]),
        "injected_at": clean(row["injected_at"]),
        "intensity": decimal_value(row["intensity"]),
        "target": clean(row["target"]),
    }

    gt_table.put_item(Item=item)

    print(
        f"GT  {item['trial_id']} | "
        f"{item['fault_type']} | "
        f"{item['target']}"
    )


# ============================================================
# RCA Results
# Original CSV = 26 rows
# Extended CSV = 46 rows
# Therefore insert rows after first 26.
# ============================================================

with open(RCA_FILE, newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

new_rca = rows[26:]

print(f"\nRCA new records: {len(new_rca)}")

for row in new_rca:

    item = {
        "result_id": clean(row["result_id"]),
        "affected_component": clean(row["affected_component"]),
        "alarm_name": clean(row["alarm_name"]),
        "alarm_time": clean(row["alarm_time"]),

        # Your existing RCA table stores these as strings.
        "analysis_seconds": clean(row["analysis_seconds"]),
        "confidence": clean(row["confidence"]),

        "evidence": clean(row["evidence"]),
        "llm_error": clean(row["llm_error"]),
        "llm_model": clean(row["llm_model"]),
        "remediation": clean(row["remediation"]),
        "root_cause": clean(row["root_cause"]),
        "root_cause_category": clean(row["root_cause_category"]),
    }

    rca_table.put_item(Item=item)

    print(
        f"RCA {item['result_id']} | "
        f"{item['root_cause_category']} | "
        f"{item['affected_component']}"
    )


print("\n====================================")
print("INSERT COMPLETE")
print("GroundTruth inserted:", len(new_gt))
print("RCAResults inserted :", len(new_rca))
print("====================================")
