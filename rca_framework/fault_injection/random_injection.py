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
  python inject_fault.py deactivate --trial_id abcd1234   # blind-safe: no target shown
  python inject_fault.py status
  python inject_fault.py control --minutes 10        # record a no-fault control trial

  # Blind mode: picks a random target/type/intensity, activates it, and
  # writes full ground truth to DynamoDB — but does NOT print what it did.
  # Use this to keep yourself blinded while watching the RCA/detection system.
  python inject_fault.py blind
  python inject_fault.py blind --targets TicketBuddy_BookTicket TicketBuddy_TaxCalculator
  python inject_fault.py blind --types exception timeout api_failure
  python inject_fault.py reveal --trial_id abcd1234   # unblind after you've logged your guess

Fault types: api_failure | timeout | cpu_overload | exception | dynamodb_failure
"""

import argparse
import random
import uuid
from datetime import datetime, timezone

import boto3

dynamo = boto3.resource("dynamodb")
FAULTS = dynamo.Table("TicketBuddy_FaultConfig")
TRUTH = dynamo.Table("TicketBuddy_GroundTruth")

VALID_TYPES = ["api_failure", "timeout", "cpu_overload",
               "exception", "dynamodb_failure"]

# Default pool of injectable targets for blind mode. Override with --targets.
DEFAULT_TARGETS = [
    "TicketBuddy_BookTicket",
    "TicketBuddy_TaxCalculator",
]

# Default intensity range (inclusive) for blind mode when a fault type uses
# intensity (timeout/cpu_overload). Override with --min-intensity/--max-intensity.
DEFAULT_INTENSITY_RANGE = (10, 60)


def now():
    return datetime.now(timezone.utc).isoformat()


def activate(target, ftype, intensity, quiet=False):
    """Core activation logic. Returns the trial_id.

    quiet=True suppresses printing which fault/target/intensity was chosen —
    used by blind mode so the operator doesn't learn ground truth from stdout.
    """
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
    if quiet:
        print(f"ACTIVATED  trial={trial_id}  (details withheld — blind trial)")
        print(f"Ground truth recorded at {now()}. Use `reveal --trial_id {trial_id}` "
              f"after you've logged your detection guess.")
    else:
        print(f"ACTIVATED  trial={trial_id}  {ftype} on {target} "
              f"(intensity={intensity})")
        print(f"Ground truth recorded at {now()}")
    return trial_id


def blind(targets, types, min_intensity, max_intensity):
    """Randomly choose target/type/intensity and activate without revealing
    the choice. Ground truth is still fully recorded in DynamoDB so it can
    be scored later."""
    target = random.choice(targets)
    ftype = random.choice(types)
    intensity = random.randint(min_intensity, max_intensity)
    trial_id = activate(target, ftype, intensity, quiet=True)
    return trial_id


def reveal(trial_id):
    """Unblind a previously activated blind trial by trial_id."""
    resp = TRUTH.get_item(Key={"trial_id": trial_id})
    item = resp.get("Item")
    if not item:
        print(f"No ground truth found for trial {trial_id}")
        return
    print(f"trial={trial_id}  target={item.get('target')}  "
          f"type={item.get('fault_type')}  intensity={item.get('intensity')}  "
          f"injected_at={item.get('injected_at')}  cleared_at={item.get('cleared_at') or '(still active)'}")


def deactivate(target=None, trial_id=None):
    """Deactivate a fault by --target (as before) or by --trial_id.

    --trial_id is the blind-safe path: it looks up the target from
    TicketBuddy_FaultConfig internally without ever printing it, so you can
    end a blind trial without learning what was injected.
    """
    if trial_id:
        items = FAULTS.scan().get("Items", [])
        match = next((i for i in items if i.get("trial_id") == trial_id and i.get("active")), None)
        if not match:
            print(f"No active fault found for trial {trial_id}")
            return
        target = match["target"]
        FAULTS.update_item(
            Key={"target": target},
            UpdateExpression="SET active = :f",
            ExpressionAttributeValues={":f": False},
        )
        TRUTH.update_item(
            Key={"trial_id": trial_id},
            UpdateExpression="SET cleared_at = :t",
            ExpressionAttributeValues={":t": now()},
        )
        print(f"DEACTIVATED  trial={trial_id} at {now()} (target withheld — blind trial)")
        return

    resp = FAULTS.get_item(Key={"target": target})
    item = resp.get("Item")
    if not item or not item.get("active"):
        print(f"No active fault on {target}")
        return
    tid = item.get("trial_id", "")
    FAULTS.update_item(
        Key={"target": target},
        UpdateExpression="SET active = :f",
        ExpressionAttributeValues={":f": False},
    )
    if tid:
        TRUTH.update_item(
            Key={"trial_id": tid},
            UpdateExpression="SET cleared_at = :t",
            ExpressionAttributeValues={":t": now()},
        )
    print(f"DEACTIVATED  trial={tid} on {target} at {now()}")


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
        # Note: this intentionally still reveals fault_type/target for any
        # active fault, including ones started in blind mode. If you want
        # `status` to stay blind too, run it from a separate operator who
        # isn't the one calling `activate`, or don't run `status` during
        # a blind trial.
        print(f"ACTIVE  {i['fault_type']} on {i['target']} "
              f"(trial {i.get('trial_id')}, since {i.get('activated_at')})")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("action",
                   choices=["activate", "deactivate", "status", "control",
                            "blind", "reveal"])
    p.add_argument("--target", default="")
    p.add_argument("--type", dest="ftype", choices=VALID_TYPES)
    p.add_argument("--intensity", type=int, default=30)
    p.add_argument("--minutes", type=int, default=10)
    p.add_argument("--trial_id", default="")
    p.add_argument("--targets", nargs="+", default=DEFAULT_TARGETS,
                   help="Pool of targets to choose from in blind mode")
    p.add_argument("--types", nargs="+", default=VALID_TYPES, choices=VALID_TYPES,
                   help="Pool of fault types to choose from in blind mode")
    p.add_argument("--min-intensity", type=int, default=DEFAULT_INTENSITY_RANGE[0])
    p.add_argument("--max-intensity", type=int, default=DEFAULT_INTENSITY_RANGE[1])
    args = p.parse_args()

    if args.action == "activate":
        if not args.target or not args.ftype:
            p.error("activate requires --target and --type")
        activate(args.target, args.ftype, args.intensity)
    elif args.action == "deactivate":
        if not args.target and not args.trial_id:
            p.error("deactivate requires --target or --trial_id")
        deactivate(target=args.target or None, trial_id=args.trial_id or None)
    elif args.action == "control":
        control(args.minutes)
    elif args.action == "blind":
        blind(args.targets, args.types, args.min_intensity, args.max_intensity)
    elif args.action == "reveal":
        if not args.trial_id:
            p.error("reveal requires --trial_id")
        reveal(args.trial_id)
    else:
        status()