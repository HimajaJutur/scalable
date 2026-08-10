

from datetime import datetime
import boto3

REGION = "us-east-1"
dynamo = boto3.resource("dynamodb", region_name=REGION)
GT = dynamo.Table("TicketBuddy_GroundTruth")
RCA = dynamo.Table("TicketBuddy_RCAResults")

GRACE_SECONDS = 600  # how long after injection a diagnosis may still count


def parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def main():
    faults = GT.scan().get("Items", [])
    results = RCA.scan().get("Items", [])

    # Parse RCA result times
    rca_times = []
    for r in results:
        t = parse(r.get("alarm_time", ""))
        if t:
            rca_times.append((t, r.get("root_cause_category", "?")))

    detected = []
    no_detection = []

    for f in faults:
        inj = parse(f.get("injected_at", ""))
        clr = parse(f.get("cleared_at", ""))
        if not inj:
            continue
        # window: from injection to cleared (or +grace if never cleared)
        window_end = clr if clr else inj
        # a diagnosis matches if it falls between injection and cleared+grace
        matched = None
        for (rt, cat) in rca_times:
            if inj <= rt <= (window_end.timestamp() and
                             datetime.fromtimestamp(
                                 window_end.timestamp() + GRACE_SECONDS,
                                 tz=inj.tzinfo)):
                matched = cat
                break
        row = {
            "trial_id": f.get("trial_id", "?"),
            "fault_type": f.get("fault_type", "?"),
            "target": f.get("target", "?"),
            "injected_at": f.get("injected_at", "?"),
        }
        if matched:
            row["diagnosed_as"] = matched
            detected.append(row)
        else:
            no_detection.append(row)

    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("Total faults injected :", len(faults))
    print("Detected (diagnosed)  :", len(detected))
    print("NO_DETECTION          :", len(no_detection))
    print()

    print("=" * 70)
    print("NO_DETECTION FAULTS (injected but no diagnosis recorded)")
    print("=" * 70)
    if not no_detection:
        print("None — every fault got a diagnosis.")
    else:
        for r in no_detection:
            print(f"  trial={r['trial_id']}  {r['fault_type']:18s} "
                  f"on {r['target']:28s}  at {r['injected_at']}")

    print()
    print("=" * 70)
    print("DETECTED FAULTS (for reference)")
    print("=" * 70)
    for r in detected:
        print(f"  trial={r['trial_id']}  {r['fault_type']:18s} "
              f"-> {r.get('diagnosed_as','?')}")


if __name__ == "__main__":
    main()