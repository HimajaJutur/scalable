"""
STEP 2 — Triggers the Glue job immediately and waits for it to finish
Run: python3 step2_run_glue_once.py
"""

import boto3
import time

AWS_REGION = "us-east-1"
JOB_NAME   = "RideReserveAnalyticsJob"

glue = boto3.client("glue", region_name=AWS_REGION)

print(f"Starting Glue job: {JOB_NAME}...")
try:
    response = glue.start_job_run(JobName=JOB_NAME)
    run_id   = response["JobRunId"]
    print(f"  ✅ Job started — Run ID: {run_id}")
except Exception as e:
    print(f"  ❌ Failed to start job: {e}")
    exit(1)

print("  ⏳ Waiting for job to complete (takes ~2-4 mins)...")
print("     Checking every 15 seconds...\n")

while True:
    time.sleep(15)
    status_resp = glue.get_job_run(JobName=JOB_NAME, RunId=run_id)
    state       = status_resp["JobRun"]["JobRunState"]
    elapsed     = status_resp["JobRun"].get("ExecutionTime", 0)
    print(f"     Status: {state}  ({elapsed}s elapsed)")
    if state in ("SUCCEEDED", "FAILED", "ERROR", "STOPPED"):
        break

if state == "SUCCEEDED":
    print("\n" + "="*60)
    print("✅ STEP 2 COMPLETE — Analytics generated!")
    print("="*60)
    print("  Saved at:")
    print("  s3://ticketbuddy-tickets-943886678148/analytics/dashboard.json")
    print("\n👉 Now run: python3 step3_update_django.py")
    print("="*60)
else:
    error = status_resp["JobRun"].get("ErrorMessage", "Unknown error")
    print(f"\n❌ Job {state}: {error}")
    print("Check: AWS Console -> Glue -> Jobs -> RideReserveAnalyticsJob -> Run history")