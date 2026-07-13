"""
inject_fault.py — experiment control CLI.

Activates/deactivates faults and records ground truth (what fault was
active, where, and exactly when) in TicketBuddy_GroundTruth. Detection
latency and accuracy are later computed against these records.

Usage:
  python inject_fault.py activate  --target TicketBuddy_BookTicket --type exception
  python inject_fault.py activate  --target TicketBuddy_TaxCalculator --type api_failure
  python inject_fault.py activate  --target TicketBuddy_BookTicket --type timeout --intensity 30
  python inject_fault.py deactivate --target TicketBuddy_BookTicket
  python inject_fault.py status
  python inject_fault.py control --minutes 10        # record a no-fault control trial

Fault types: api_failure | timeout | cpu_overload | exception | dynamodb_failure
"""

import argparse
import uuid
from datetime import datetime, timezone

import boto3

dynamo = boto3.resource("dynamodb")
FAULTS = dynamo.Table("TicketBuddy_FaultConfig")
TRUTH = dynamo.Table("TicketBuddy_GroundTruth")

VALID_TYPES = ["api_failure", "timeout", "cpu_overload",
               "exception", "dynamodb_failure"]


def now():
    return datetime.now(timezone.utc).isoformat()


def activate(target, ftype, intensity):
    trial_id = str(uuid.uuid4())[:8]
    FAULTS.put_item(Item={
        "target": target,
        "fault_type": ftype,
        "intensity": intensity,
        "active": True,
        "trial_id": trial_id,
        "activated_at": now(),
    })
    TRUTH.put_item(Item={
        "trial_id": trial_id,
        "target": target,
        "fault_type": ftype,
        "intensity": intensity,
        "injected_at": now(),
        "cleared_at": "",
    })
    print(f"ACTIVATED  trial={trial_id}  {ftype} on {target} "
          f"(intensity={intensity})")
    print(f"Ground truth recorded at {now()}")


def deactivate(target):
    resp = FAULTS.get_item(Key={"target": target})
    item = resp.get("Item")
    if not item or not item.get("active"):
        print(f"No active fault on {target}")
        return
    trial_id = item.get("trial_id", "")
    FAULTS.update_item(
        Key={"target": target},
        UpdateExpression="SET active = :f",
        ExpressionAttributeValues={":f": False},
    )
    if trial_id:
        TRUTH.update_item(
            Key={"trial_id": trial_id},
            UpdateExpression="SET cleared_at = :t",
            ExpressionAttributeValues={":t": now()},
        )
    print(f"DEACTIVATED  trial={trial_id} on {target} at {now()}")


def control(minutes):
    """Record a no-fault control window (needed for false-positive rate)."""
    trial_id = str(uuid.uuid4())[:8]
    TRUTH.put_item(Item={
        "trial_id": trial_id,
        "target": "NONE",
        "fault_type": "none",
        "intensity": 0,
        "injected_at": now(),
        "cleared_at": "",
        "control_minutes": minutes,
    })
    print(f"CONTROL trial={trial_id} started at {now()} "
          f"— run traffic for {minutes} min with no fault, then note any "
          f"RCA alerts fired in this window count as false positives.")


def status():
    items = FAULTS.scan().get("Items", [])
    active = [i for i in items if i.get("active")]
    if not active:
        print("No active faults.")
    for i in active:
        print(f"ACTIVE  {i['fault_type']} on {i['target']} "
              f"(trial {i.get('trial_id')}, since {i.get('activated_at')})")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("action",
                   choices=["activate", "deactivate", "status", "control"])
    p.add_argument("--target", default="")
    p.add_argument("--type", dest="ftype", choices=VALID_TYPES)
    p.add_argument("--intensity", type=int, default=30)
    p.add_argument("--minutes", type=int, default=10)
    args = p.parse_args()

    if args.action == "activate":
        if not args.target or not args.ftype:
            p.error("activate requires --target and --type")
        activate(args.target, args.ftype, args.intensity)
    elif args.action == "deactivate":
        if not args.target:
            p.error("deactivate requires --target")
        deactivate(args.target)
    elif args.action == "control":
        control(args.minutes)
    else:
        status()
