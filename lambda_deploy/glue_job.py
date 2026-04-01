import sys
import json
import boto3
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.utils import getResolvedOptions
from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType, LongType

args = getResolvedOptions(sys.argv, ['JOB_NAME'])
sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session

BUCKET = "ticketbuddy-tickets-943886678148"

# ── Load from DynamoDB ──────────────────────────────────────────────────────
tickets_dyf = glueContext.create_dynamic_frame.from_options(
    connection_type="dynamodb",
    connection_options={"dynamodb.input.tableName": "TicketBuddy_Tickets"}
)
df = tickets_dyf.toDF()
df.printSchema()

# ── Flatten DynamoDB numeric structs FIRST ──────────────────────────────────
# DynamoDB stores numbers as struct<double:double,long:bigint> — must unwrap
# before any .cast() calls, otherwise Spark throws AnalysisException.

def flatten_numeric(df, col_name):
    """
    Safely extract a numeric value regardless of whether DynamoDB
    returned it as a plain scalar or a struct<double:double,long:bigint>.
    """
    if col_name not in df.columns:
        return df.withColumn(col_name, F.lit(0.0).cast(DoubleType()))

    field_type = dict(df.dtypes).get(col_name, "")

    if "struct" in field_type:
        # DynamoDB number type — try double first, fall back to long
        return df.withColumn(
            col_name,
            F.coalesce(
                F.col(f"{col_name}.double").cast(DoubleType()),
                F.col(f"{col_name}.long").cast(DoubleType()),
            )
        )

    # Already a plain scalar — just normalise to double
    return df.withColumn(col_name, F.col(col_name).cast(DoubleType()))


# Flatten all numeric columns before doing anything else
for c in ["fare", "final_price", "fare_per_seat", "tax_amount", "tax_rate", "passengers"]:
    df = flatten_numeric(df, c)

# passengers should be a whole number
df = df.withColumn("passengers", F.col("passengers").cast(LongType()))

# ── 1. Top 5 routes ─────────────────────────────────────────────────────────
routes_df = (
    df.groupBy("source", "destination")
      .agg(F.count("booking_id").alias("total_bookings"))
      .orderBy(F.desc("total_bookings"))
      .limit(5)
)
routes_df = routes_df.withColumn(
    "route", F.concat(F.col("source"), F.lit(" -> "), F.col("destination"))
)
routes_result = [row.asDict() for row in routes_df.collect()]

# ── 2. Car types ────────────────────────────────────────────────────────────
car_df = (
    df.groupBy("car_type")
      .agg(F.count("booking_id").alias("total_bookings"))
      .orderBy(F.desc("total_bookings"))
)
car_result = [row.asDict() for row in car_df.collect()]

# ── 3. Revenue by month ─────────────────────────────────────────────────────
revenue_df = (
    df.withColumn("month", F.substring("departure_date", 1, 7))
      .groupBy("month")
      .agg(F.round(F.sum("final_price"), 2).alias("total_revenue"))
      .orderBy("month")
)
revenue_result = [row.asDict() for row in revenue_df.collect()]

# ── 4. Ticket type split ────────────────────────────────────────────────────
ticket_type_df = (
    df.groupBy("ticket_type")
      .agg(F.count("booking_id").alias("count"))
)
ticket_type_result = [row.asDict() for row in ticket_type_df.collect()]

# ── 5. Busiest departure times ──────────────────────────────────────────────
time_df = (
    df.groupBy("departure_time")
      .agg(F.count("booking_id").alias("total_bookings"))
      .orderBy(F.desc("total_bookings"))
)
time_result = [row.asDict() for row in time_df.collect()]

# ── Write analytics JSON to S3 ──────────────────────────────────────────────
analytics = {
    "top_routes":   routes_result,
    "car_types":    car_result,
    "revenue":      revenue_result,
    "ticket_types": ticket_type_result,
    "busy_times":   time_result,
}

s3 = boto3.client("s3")
s3.put_object(
    Bucket=BUCKET,
    Key="analytics/dashboard.json",
    Body=json.dumps(analytics, default=str),
    ContentType="application/json"
)

print("✅ Analytics saved to S3 successfully.")
print("Sample routes:", routes_result)