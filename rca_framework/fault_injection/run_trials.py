import argparse
import subprocess
import sys
import time
from datetime import datetime

import boto3

REGION = "us-east-1"
cw = boto3.client("cloudwatch", region_name=REGION)
dynamo = boto3.resource("dynamodb", region_name=REGION)
RESULTS = dynamo.Table("TicketBuddy_RCAResults")
ALARM = "TicketBuddy_ErrorAlarm"

SCENARIOS = {
    "exception":        "TicketBuddy_BookTicket",
    "timeout":          "TicketBuddy_BookTicket",
    "cpu_overload":     "TicketBuddy_GetSchedules",
    "dynamodb_failure": "TicketBuddy_GetSchedules",
    "api_failure":      "TicketBuddy_TaxCalculator",
}
ALL_TARGETS = list(set(SCENARIOS.values()))

RESULT_TIMEOUT = 240
POLL = 10
INTER_TRIAL_GAP = 30


def log(m):
    print("[" + datetime.now().strftime("%H:%M:%S") + "] " + m, flush=True)


def run_cli(args):
    r = subprocess.run([sys.executable] + args, capture_output=True, text=True)
    if r.returncode != 0 and r.stderr:
        log("  ! " + r.stderr.strip().splitlines()[-1][:200])
    return r


def deactivate_all():
    for t in ALL_TARGETS:
        run_cli(["inject_fault.py", "deactivate", "--target", t])


def wait_for_ok(timeout=180):
    
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = cw.describe_alarms(AlarmNames=[ALARM])
        a = r.get("MetricAlarms", [])
        if a and a[0]["StateValue"] == "OK":
            return True
        time.sleep(POLL)
    return False


def count_results():
    return RESULTS.scan(Select="COUNT")["Count"]


def wait_for_new_result(baseline, timeout=RESULT_TIMEOUT):
    log("waiting for RCA diagnosis ...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if count_results() > baseline:
            log("diagnosis recorded")
            return True
        time.sleep(POLL)
    log("no diagnosis within timeout (alarm may not have fired)")
    return False


def activate(target, ftype, intensity):
    args = ["inject_fault.py", "activate", "--target", target, "--type", ftype]
    if intensity:
        args += ["--intensity", str(intensity)]
    run_cli(args)


def drive_traffic(base_url, duration):
    run_cli(["traffic_generator.py", "--base-url", base_url,
             "--duration", str(duration)])


def one_trial(scenario, base_url, traffic, intensity, n, total):
    log("===== trial " + str(n) + "/" + str(total)
        + "  scenario=" + scenario + " =====")
    deactivate_all()
    log("waiting for alarm OK ...")
    wait_for_ok()
    log("alarm ready")

    if scenario == "control":
        run_cli(["inject_fault.py", "control", "--minutes",
                 str(max(2, traffic // 60))])
        baseline = count_results()
        drive_traffic(base_url, traffic)
        time.sleep(90)
        log("control done — false positives: "
            + str(count_results() - baseline))
        return

    target = SCENARIOS[scenario]
    baseline = count_results()
    activate(target, scenario, intensity)
    drive_traffic(base_url, traffic)
    wait_for_new_result(baseline)
    deactivate_all()
    log("trial " + str(n) + " done; gap " + str(INTER_TRIAL_GAP) + "s")
    time.sleep(INTER_TRIAL_GAP)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", required=True)
    p.add_argument("--scenario", choices=list(SCENARIOS) + ["control"])
    p.add_argument("--all", action="store_true")
    p.add_argument("--repeats", type=int, default=10)
    p.add_argument("--traffic", type=int, default=120)
    p.add_argument("--intensity", type=int, default=30)
    args = p.parse_args()

    if args.all:
        plan = []
        for r in range(args.repeats):
            for sc in SCENARIOS:
                plan.append(sc)
    elif args.scenario:
        plan = [args.scenario] * args.repeats
    else:
        p.error("give --scenario X or --all")

    total = len(plan)
    log("STARTING: " + str(total) + " trials")
    start = time.time()
    for i, sc in enumerate(plan, 1):
        try:
            one_trial(sc, args.base_url, args.traffic, args.intensity, i, total)
        except KeyboardInterrupt:
            log("interrupted — deactivating all faults")
            deactivate_all()
            sys.exit(1)
        except Exception as e:
            log("trial " + str(i) + " error: " + str(e) + " — continuing")
            deactivate_all()
    log("COMPLETE: " + str(total) + " trials in "
        + str(round((time.time() - start) / 60, 1)) + " min")
    log("Now: cd ../evaluation && python3 evaluate_results.py")


if __name__ == "__main__":
    main()
