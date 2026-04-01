"""
STEP 1 — Run this FIRST on your EC2 instance
Uses LabRole + existing S3 bucket
Field names matched exactly to your DynamoDB table
Run: python3 step1_setup_aws.py
"""

import boto3
import json
import time

AWS_REGION   = "us-east-1"
BUCKET_NAME  = "ticketbuddy-tickets-943886678148"
GLUE_SCRIPT  = "analytics/glue_job.py"
JOB_NAME     = "RideReserveAnalyticsJob"
TRIGGER_NAME = "RideReserveHourlyTrigger"

s3   = boto3.client("s3",   region_name=AWS_REGION)
glue = boto3.client("glue", region_name=AWS_REGION)
sts  = boto3.client("sts",  region_name=AWS_REGION)

# ─────────────────────────────────────────────────────────────────────────────
# 1. Get LabRole ARN automatically
# ─────────────────────────────────────────────────────────────────────────────
print("Fetching LabRole ARN from your session...")
try:
    identity    = sts.get_caller_identity()
    account_id  = identity["Account"]
    LABROLE_ARN = f"arn:aws:iam::{account_id}:role/LabRole"
    print(f"  ✅ Account ID  : {account_id}")
    print(f"  ✅ LabRole ARN : {LABROLE_ARN}")
except Exception as e:
    print(f"  ❌ Could not get identity: {e}")
    exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# 2. Glue PySpark script — field names match your real DynamoDB table exactly
#    Fields used:
#      booking_id, source, destination, departure_date, departure_time,
#      arrival_time, ticket_type, seats, fare, final_price,
#      tax_amount, tax_rate, status, username
# ─────────────────────────────────────────────────────────────────────────────
GLUE_SCRIPT_CONTENT = '''
import sys
import json
import boto3
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.utils import getResolvedOptions
from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType
from datetime import datetime

args = getResolvedOptions(sys.argv, ["JOB_NAME"])
sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session

BUCKET_NAME = "ticketbuddy-tickets-943886678148"

# ── Read TicketBuddy_Tickets from DynamoDB ───────────────────────────────────
tickets_dyf = glueContext.create_dynamic_frame.from_options(
    connection_type="dynamodb",
    connection_options={"dynamodb.input.tableName": "TicketBuddy_Tickets"}
)
df = tickets_dyf.toDF()

# ── Cast numeric fields (DynamoDB stores as Decimal/String sometimes) ─────────
df = df.withColumn("final_price", F.col("final_price").cast(DoubleType()))
df = df.withColumn("fare",        F.col("fare").cast(DoubleType()))
df = df.withColumn("tax_amount",  F.col("tax_amount").cast(DoubleType()))

# Only count CONFIRMED bookings
df = df.filter(F.col("status") == "CONFIRMED")
df.cache()

# ── 1. Top 5 most booked routes ──────────────────────────────────────────────
# Uses: source, destination, booking_id
routes_df = df.groupBy("source", "destination") \\
    .agg(F.count("booking_id").alias("total_bookings")) \\
    .orderBy(F.desc("total_bookings")) \\
    .limit(5)
routes_df = routes_df.withColumn(
    "route", F.concat(F.col("source"), F.lit(" -> "), F.col("destination"))
)
top_routes = [row.asDict() for row in routes_df.collect()]

# ── 2. Most popular ticket type (One Way vs Return) ──────────────────────────
# Uses: ticket_type, booking_id
ticket_type_df = df.groupBy("ticket_type") \\
    .agg(F.count("booking_id").alias("count")) \\
    .orderBy(F.desc("count"))
ticket_types = [row.asDict() for row in ticket_type_df.collect()]

# ── 3. Revenue by month ──────────────────────────────────────────────────────
# Uses: departure_date (format: 2026-03-31), final_price
revenue_df = df.withColumn("month", F.substring("departure_date", 1, 7)) \\
    .groupBy("month") \\
    .agg(F.round(F.sum("final_price"), 2).alias("total_revenue")) \\
    .orderBy("month")
revenue = [row.asDict() for row in revenue_df.collect()]

# ── 4. Busiest departure times ───────────────────────────────────────────────
# Uses: departure_time, booking_id
time_df = df.groupBy("departure_time") \\
    .agg(F.count("booking_id").alias("total_bookings")) \\
    .orderBy(F.desc("total_bookings"))
busy_times = [row.asDict() for row in time_df.collect()]

# ── 5. Top destinations (most arrived at) ────────────────────────────────────
# Uses: destination, booking_id (replaces car_type since not in your table)
dest_df = df.groupBy("destination") \\
    .agg(F.count("booking_id").alias("total_bookings")) \\
    .orderBy(F.desc("total_bookings"))
top_destinations = [row.asDict() for row in dest_df.collect()]

# ── Summary stats ─────────────────────────────────────────────────────────────
total_bookings = df.count()
total_revenue  = df.agg(F.round(F.sum("final_price"), 2).alias("total")).collect()[0]["total"] or 0
total_tax      = df.agg(F.round(F.sum("tax_amount"),  2).alias("total")).collect()[0]["total"] or 0

# ── Bundle and save to S3 ─────────────────────────────────────────────────────
analytics = {
    "top_routes":        top_routes,
    "ticket_types":      ticket_types,
    "revenue":           revenue,
    "busy_times":        busy_times,
    "top_destinations":  top_destinations,
    "summary": {
        "total_bookings": total_bookings,
        "total_revenue":  float(total_revenue),
        "total_tax":      float(total_tax),
    },
    "last_updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
}

s3 = boto3.client("s3")
s3.put_object(
    Bucket=BUCKET_NAME,
    Key="analytics/dashboard.json",
    Body=json.dumps(analytics, default=str),
    ContentType="application/json"
)
print("Analytics saved to S3 successfully.")
'''

print("\nUploading Glue PySpark script to S3...")
try:
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=GLUE_SCRIPT,
        Body=GLUE_SCRIPT_CONTENT.encode("utf-8"),
        ContentType="text/x-python"
    )
    print(f"  ✅ Uploaded: s3://{BUCKET_NAME}/{GLUE_SCRIPT}")
except Exception as e:
    print(f"  ❌ Upload failed: {e}")
    exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# 3. Create Glue Job using LabRole
# ─────────────────────────────────────────────────────────────────────────────
print(f"\nCreating Glue Job: {JOB_NAME}...")
try:
    glue.create_job(
        Name=JOB_NAME,
        Role=LABROLE_ARN,
        Command={
            "Name": "glueetl",
            "ScriptLocation": f"s3://{BUCKET_NAME}/{GLUE_SCRIPT}",
            "PythonVersion": "3"
        },
        DefaultArguments={
            "--job-language": "python",
            "--TempDir": f"s3://{BUCKET_NAME}/temp/",
            "--enable-metrics": "",
        },
        GlueVersion="4.0",
        NumberOfWorkers=2,
        WorkerType="G.1X",
        MaxRetries=1,
        Timeout=30,
    )
    print(f"  ✅ Glue job created: {JOB_NAME}")
except glue.exceptions.AlreadyExistsException:
    # Job exists — update the script location in case it changed
    glue.update_job(
        JobName=JOB_NAME,
        JobUpdate={
            "Role": LABROLE_ARN,
            "Command": {
                "Name": "glueetl",
                "ScriptLocation": f"s3://{BUCKET_NAME}/{GLUE_SCRIPT}",
                "PythonVersion": "3"
            },
        }
    )
    print(f"  ✅ Glue job already exists — script updated")
except Exception as e:
    print(f"  ❌ Glue job error: {e}")
    exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# 4. Schedule Glue Job every hour
# ─────────────────────────────────────────────────────────────────────────────
print(f"\nCreating hourly trigger: {TRIGGER_NAME}...")
try:
    glue.create_trigger(
        Name=TRIGGER_NAME,
        Type="SCHEDULED",
        Schedule="cron(0 * * * ? *)",
        Actions=[{"JobName": JOB_NAME}],
        StartOnCreation=True,
    )
    print(f"  ✅ Trigger created: runs every hour")
except glue.exceptions.AlreadyExistsException:
    print(f"  ✅ Trigger already exists")
except Exception as e:
    print(f"  ⚠️  Trigger: {e}")

print("\n" + "="*60)
print("✅ STEP 1 COMPLETE")
print("="*60)
print(f"  S3 Bucket   : s3://{BUCKET_NAME}/")
print(f"  Glue Script : s3://{BUCKET_NAME}/{GLUE_SCRIPT}")
print(f"  LabRole ARN : {LABROLE_ARN}")
print(f"  Glue Job    : {JOB_NAME}")
print(f"  Schedule    : Every 1 hour")
print("\n👉 Now run: python3 step2_run_glue_once.py")
print("="*60)