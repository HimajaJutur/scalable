import boto3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

REGION = "us-east-1"

dynamodb = boto3.resource("dynamodb", region_name=REGION)

ground_truth = dynamodb.Table("TicketBuddy_GroundTruth")
rca_results = dynamodb.Table("TicketBuddy_RCAResults")


TESTS = [
    # 5 Lambda timeouts
    ("timeout", "LAMBDA_TIMEOUT", "TicketBuddy_BookTicket"),
    ("timeout", "LAMBDA_TIMEOUT", "TicketBuddy_GetSchedules"),
    ("timeout", "LAMBDA_TIMEOUT", "TicketBuddy_TaxCalculator"),
    ("timeout", "LAMBDA_TIMEOUT", "TicketBuddy_BookTicket"),
    ("timeout", "LAMBDA_TIMEOUT", "TicketBuddy_GetSchedules"),

    # 5 Lambda exceptions
    ("exception", "LAMBDA_EXCEPTION", "TicketBuddy_BookTicket"),
    ("exception", "LAMBDA_EXCEPTION", "TicketBuddy_GetSchedules"),
    ("exception", "LAMBDA_EXCEPTION", "TicketBuddy_TaxCalculator"),
    ("exception", "LAMBDA_EXCEPTION", "TicketBuddy_BookTicket"),
    ("exception", "LAMBDA_EXCEPTION", "TicketBuddy_GetSchedules"),

    # 5 DynamoDB failures
    ("dynamodb_failure", "DYNAMODB_FAILURE", "TicketBuddy_GetSchedules"),
    ("dynamodb_failure", "DYNAMODB_FAILURE", "TicketBuddy_BookTicket"),
    ("dynamodb_failure", "DYNAMODB_FAILURE", "TicketBuddy_GetSchedules"),
    ("dynamodb_failure", "DYNAMODB_FAILURE", "TicketBuddy_BookTicket"),
    ("dynamodb_failure", "DYNAMODB_FAILURE", "TicketBuddy_GetSchedules"),

    # 5 API request failures
    ("api_failure", "API_REQUEST_FAILURE", "TicketBuddy_TaxCalculator"),
    ("api_failure", "API_REQUEST_FAILURE", "TicketBuddy_GetSchedules"),
    ("api_failure", "API_REQUEST_FAILURE", "TicketBuddy_TaxCalculator"),
    ("api_failure", "API_REQUEST_FAILURE", "TicketBuddy_BookTicket"),
    ("api_failure", "API_REQUEST_FAILURE", "TicketBuddy_TaxCalculator"),
]


def get_llm_style_content(fault_type, target):
    """Synthetic RCA text for evaluator/pipeline testing only."""

    if fault_type == "timeout":
        return {
            "root_cause": (
                f"[SYNTHETIC TEST] The Lambda function {target} exceeded "
                "its expected execution time, resulting in a Lambda timeout."
            ),
            "remediation": (
                "[SYNTHETIC TEST] Review downstream latency, optimize function "
                "execution, verify timeout configuration, and inspect resource "
                "allocation."
            ),
        }

    if fault_type == "exception":
        return {
            "root_cause": (
                f"[SYNTHETIC TEST] The Lambda function {target} encountered "
                "an unhandled application exception during request processing."
            ),
            "remediation": (
                "[SYNTHETIC TEST] Inspect the exception stack trace and input "
                "data, improve exception handling, and validate dependencies."
            ),
        }

    if fault_type == "dynamodb_failure":
        return {
            "root_cause": (
                f"[SYNTHETIC TEST] {target} experienced a DynamoDB operation "
                "failure while accessing its persistence layer."
            ),
            "remediation": (
                "[SYNTHETIC TEST] Verify DynamoDB access, table configuration, "
                "IAM permissions, request failures, and retry behavior."
            ),
        }

    if fault_type == "api_failure":
        return {
            "root_cause": (
                f"[SYNTHETIC TEST] Requests associated with {target} failed "
                "at the API request layer."
            ),
            "remediation": (
                "[SYNTHETIC TEST] Inspect API Gateway and Lambda logs, verify "
                "integration configuration, permissions, and backend health."
            ),
        }

    raise ValueError(f"Unsupported fault type: {fault_type}")


# Historical isolated range so these windows don't overlap normal current tests.
BASE_TIME = datetime(2026, 7, 31, 5, 0, 0, tzinfo=timezone.utc)

inserted_gt = []
inserted_rca = []

try:
    for index, (fault_type, category, target) in enumerate(TESTS, start=1):

        trial_id = f"synthetic-gt-{index:03d}"
        result_id = f"synthetic-rca-{index:03d}"

        # Separate trials by 5 minutes.
        injected_at = BASE_TIME + timedelta(minutes=(index - 1) * 5)

        # Simulate different detection latencies.
        detection_seconds = 35 + ((index * 7) % 45)

        alarm_time = injected_at + timedelta(seconds=detection_seconds)
        cleared_at = injected_at + timedelta(minutes=3)

        analysis_seconds = Decimal(
            str(round(2.20 + (index % 7) * 0.13, 2))
        )

        confidence = Decimal(
            str(round(0.88 + (index % 5) * 0.02, 2))
        )

        content = get_llm_style_content(fault_type, target)

        # -----------------------------
        # Ground Truth
        # -----------------------------
        ground_truth.put_item(
            Item={
                "trial_id": trial_id,
                "fault_type": fault_type,
                "target": target,
                "injected_at": injected_at.isoformat(),
                "cleared_at": cleared_at.isoformat(),
                "intensity": Decimal("30"),
            },
            ConditionExpression="attribute_not_exists(trial_id)",
        )

        inserted_gt.append(trial_id)

        # -----------------------------
        # RCA Result
        # -----------------------------
        evidence = (
            f'[SYNTHETIC TEST] [{target}] '
            f'{{"timestamp":"{injected_at.isoformat()}",'
            f'"level":"ERROR",'
            f'"error_type":"{category}",'
            f'"message":"SYNTHETIC_EVALUATOR_TEST '
            f'{fault_type} in {target} (trial {trial_id})",'
            f'"fault_target":"{target}"}}'
        )

        rca_results.put_item(
            Item={
                "result_id": result_id,
                "evidence": evidence,
                "remediation": content["remediation"],
                "alarm_name": "TicketBuddy_ErrorAlarm",
                "root_cause_category": category,

                # Deliberately not claiming a real LLM produced this.
                "llm_model": "synthetic-evaluator-test",

                "confidence": str(confidence),
                "analysis_seconds": str(analysis_seconds),
                "root_cause": content["root_cause"],
                "llm_error": "",
                "alarm_time": alarm_time.isoformat(),
                "affected_component": target,
            },
            ConditionExpression="attribute_not_exists(result_id)",
        )

        inserted_rca.append(result_id)

        print(
            f"{index:02d}. {trial_id} | "
            f"{fault_type:<18} -> {category:<20} | "
            f"{target} | latency={detection_seconds}s"
        )

except Exception:
    print("\nInsertion failed. Records inserted before the failure:")
    print("GroundTruth:", inserted_gt)
    print("RCAResults:", inserted_rca)
    raise


print("\n========================================")
print("20 SYNTHETIC TEST PAIRS INSERTED")
print("========================================")
print("Ground truth :", len(inserted_gt))
print("RCA results  :", len(inserted_rca))
print("Expected     : all 20 classifications correct")