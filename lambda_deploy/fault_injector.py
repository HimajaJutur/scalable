

import json
import os
import time
from datetime import datetime, timezone

import boto3

FAULT_TABLE_NAME = os.getenv("FAULT_CONFIG_TABLE", "TicketBuddy_FaultConfig")

_table = None


def _fault_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(FAULT_TABLE_NAME)
    return _table


def get_active_fault(target):
    """Return the active fault item for this target (or GLOBAL), else None."""
    try:
        for key in (target, "GLOBAL"):
            resp = _fault_table().get_item(Key={"target": key})
            item = resp.get("Item")
            if item and item.get("active"):
                return item
    except Exception:
        # Fault table missing/unreachable == no fault. Injection must never
        # itself break the application outside of experiments.
        return None
    return None


def _log_marker(target, fault):
    """Structured ground-truth marker written to CloudWatch Logs."""
    print(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "WARNING",
        "error_type": "FAULT_ACTIVE",
        "message": "Injected fault active for this invocation",
        "fault_target": target,
        "fault_type": fault.get("fault_type"),
        "trial_id": fault.get("trial_id", ""),
    }))


def apply_fault(target):
    """
    Check for and apply an active fault.

    Behaviour by type:
      timeout / cpu_overload  -> blocks inside this call
      exception               -> raises RuntimeError
      dynamodb_failure        -> raises botocore ClientError (real AWS error)
      api_failure             -> returns the fault dict; the CALLER must
                                 return an error response

    Returns the fault item (dict) if one is active, else None.
    """
    fault = get_active_fault(target)
    if not fault:
        return None

    _log_marker(target, fault)
    ftype = fault.get("fault_type")

    if ftype == "api_failure":
        print(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "ERROR",
            "error_type": "API_REQUEST_FAILURE",
            "message": "INJECTED_FAULT: upstream API failure (HTTP 502) in "
                       + str(target) + " (trial "
                       + str(fault.get("trial_id", "?")) + ")",
            "fault_target": target,
        }))
    intensity = int(fault.get("intensity", 30))

    if ftype == "timeout":
        print(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "ERROR",
            "error_type": "LAMBDA_TIMEOUT",
            "message": "INJECTED_FAULT: operation timed out in " + str(target)
                       + " (trial " + str(fault.get("trial_id", "?")) + ")",
            "fault_target": target,
        }))
        # Sleep past the Lambda's configured timeout (15 s by default).
        time.sleep(intensity)

    elif ftype == "cpu_overload":
        print(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "ERROR",
            "error_type": "HIGH_RESOURCE_USAGE",
            "message": "INJECTED_FAULT: high CPU utilisation in " + str(target)
                       + " (trial " + str(fault.get("trial_id", "?")) + ")",
            "fault_target": target,
        }))
        # Busy-loop: saturate the vCPU for `intensity` seconds.
        end = time.time() + intensity
        x = 0
        while time.time() < end:
            x = (x * 1103515245 + 12345) % (2 ** 31)

    elif ftype == "exception":
        raise RuntimeError(
            f"INJECTED_FAULT exception in {target} "
            f"(trial {fault.get('trial_id', '?')}): simulated data-access crash"
        )

    elif ftype == "dynamodb_failure":
        # Real ResourceNotFoundException from DynamoDB — indistinguishable
        # in the logs from a genuine misconfigured/failed-over table.
        boto3.resource("dynamodb").Table(
            "TicketBuddy_NonExistent_Table"
        ).get_item(Key={"booking_id": "fault-probe"})

    # api_failure (and anything unrecognised) falls through: caller decides.
    return fault
