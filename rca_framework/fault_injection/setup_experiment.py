"""
setup_experiment.py — one-shot creation of all experiment infrastructure.

Creates:
  1. DynamoDB tables: TicketBuddy_FaultConfig, TicketBuddy_GroundTruth,
     TicketBuddy_RCAResults
  2. The RCA Lambda (TicketBuddy_RCA) from ../rca/rca_lambda.py
  3. CloudWatch metric filters on every monitored Lambda log group
     (errors, timeouts, exceptions -> metric TicketBuddy/ErrorEvents)
  4. A CloudWatch alarm (TicketBuddy_ErrorAlarm) on that metric
  5. An EventBridge rule: alarm -> ALARM state ==> invoke the RCA Lambda

Run AFTER exporting your LLM key:
  export ANTHROPIC_API_KEY=sk-ant-...
  python setup_experiment.py
"""

import json
import os
import time
import zipfile

import boto3

REGION = "us-east-1"
ACCOUNT = boto3.client("sts").get_caller_identity()["Account"]
LAMBDA_ROLE = f"arn:aws:iam::{ACCOUNT}:role/LabRole"
SNS_TOPIC_ARN = os.getenv(
    "SNS_TOPIC_ARN",
    f"arn:aws:sns:{REGION}:{ACCOUNT}:TicketBuddy_Alerts",
)

MONITORED_LOG_GROUPS = [
    "/aws/lambda/TicketBuddy_BookTicket",
    "/aws/lambda/TicketBuddy_CancelTicket",
    "/aws/lambda/TicketBuddy_UpdateSeat",
    "/aws/lambda/TicketBuddy_GetSchedules",
    "/aws/lambda/TicketBuddy_GetHistory",
    "/aws/lambda/TicketBuddy_TaxCalculator",
]

dynamo = boto3.client("dynamodb", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)
cw = boto3.client("cloudwatch", region_name=REGION)
events = boto3.client("events", region_name=REGION)
lam = boto3.client("lambda", region_name=REGION)


def ensure_table(name, key):
    try:
        dynamo.create_table(
            TableName=name,
            KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": key,
                                   "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        print(f"CREATED table {name}")
        dynamo.get_waiter("table_exists").wait(TableName=name)
    except dynamo.exceptions.ResourceInUseException:
        print(f"table {name} already exists")


def deploy_rca_lambda():
    src = os.path.join(os.path.dirname(__file__), "..", "rca",
                       "rca_lambda.py")
    zpath = "/tmp/rca_lambda.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.write(src, arcname="rca_lambda.py")
    with open(zpath, "rb") as f:
        code = f.read()

    env = {
        "Variables": {
            "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
            "LLM_PROVIDER": os.getenv("LLM_PROVIDER", "anthropic"),
            "LLM_MODEL": os.getenv("LLM_MODEL", "claude-sonnet-4-6"),
            "LLM_BASE_URL": os.getenv("LLM_BASE_URL", ""),
            "SNS_TOPIC_ARN": SNS_TOPIC_ARN,
            "LOG_GROUPS": ",".join(MONITORED_LOG_GROUPS),
            "LOOKBACK_MINUTES": "5",
            "RESULTS_TABLE": "TicketBuddy_RCAResults",
        }
    }
    try:
        lam.create_function(
            FunctionName="TicketBuddy_RCA",
            Runtime="python3.12",
            Role=LAMBDA_ROLE,
            Handler="rca_lambda.lambda_handler",
            Code={"ZipFile": code},
            Timeout=120,
            MemorySize=256,
            Environment=env,
        )
        print("CREATED TicketBuddy_RCA lambda")
    except lam.exceptions.ResourceConflictException:
        lam.update_function_code(FunctionName="TicketBuddy_RCA",
                                 ZipFile=code)
        time.sleep(3)
        lam.update_function_configuration(FunctionName="TicketBuddy_RCA",
                                          Environment=env, Timeout=120)
        print("UPDATED TicketBuddy_RCA lambda")
    return lam.get_function(FunctionName="TicketBuddy_RCA")[
        "Configuration"]["FunctionArn"]


def ensure_metric_filters():
    # Matches unhandled exceptions, timeouts, and structured ERROR logs.
    pattern = '?"Task timed out" ?"[ERROR]" ?"INJECTED_FAULT" ?"Traceback" ?"\\"level\\": \\"ERROR\\""'
    for group in MONITORED_LOG_GROUPS:
        try:
            logs.put_metric_filter(
                logGroupName=group,
                filterName="TicketBuddyErrorFilter",
                filterPattern=pattern,
                metricTransformations=[{
                    "metricName": "ErrorEvents",
                    "metricNamespace": "TicketBuddy",
                    "metricValue": "1",
                    "defaultValue": 0,
                }],
            )
            print(f"metric filter on {group}")
        except logs.exceptions.ResourceNotFoundException:
            print(f"SKIP {group} (log group does not exist yet — invoke "
                  f"that lambda once, then re-run this script)")


def ensure_alarm():
    cw.put_metric_alarm(
        AlarmName="TicketBuddy_ErrorAlarm",
        Namespace="TicketBuddy",
        MetricName="ErrorEvents",
        Statistic="Sum",
        Period=60,
        EvaluationPeriods=1,
        Threshold=1,
        ComparisonOperator="GreaterThanOrEqualToThreshold",
        TreatMissingData="notBreaching",
        AlarmDescription="Any error event across TicketBuddy lambdas",
    )
    print("alarm TicketBuddy_ErrorAlarm in place")


def ensure_eventbridge(rca_arn):
    rule_name = "TicketBuddy_RCA_Trigger"
    events.put_rule(
        Name=rule_name,
        EventPattern=json.dumps({
            "source": ["aws.cloudwatch"],
            "detail-type": ["CloudWatch Alarm State Change"],
            "detail": {
                "alarmName": ["TicketBuddy_ErrorAlarm"],
                "state": {"value": ["ALARM"]},
            },
        }),
        State="ENABLED",
        Description="Route TicketBuddy error alarm to the RCA lambda",
    )
    events.put_targets(
        Rule=rule_name,
        Targets=[{"Id": "rca-lambda", "Arn": rca_arn}],
    )
    try:
        lam.add_permission(
            FunctionName="TicketBuddy_RCA",
            StatementId="eventbridge-invoke",
            Action="lambda:InvokeFunction",
            Principal="events.amazonaws.com",
            SourceArn=f"arn:aws:events:{REGION}:{ACCOUNT}:rule/{rule_name}",
        )
    except lam.exceptions.ResourceConflictException:
        pass
    print(f"EventBridge rule {rule_name} -> TicketBuddy_RCA")


if __name__ == "__main__":
    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        print("WARNING: no ANTHROPIC_API_KEY/OPENAI_API_KEY exported — the "
              "RCA lambda will deploy without a key and LLM calls will fail. "
              "Export the key and re-run to update.")
    ensure_table("TicketBuddy_FaultConfig", "target")
    ensure_table("TicketBuddy_GroundTruth", "trial_id")
    ensure_table("TicketBuddy_RCAResults", "result_id")
    arn = deploy_rca_lambda()
    ensure_metric_filters()
    ensure_alarm()
    ensure_eventbridge(arn)
    print("\nSETUP COMPLETE.")
    print("Baseline comparison note: the alarm ALSO feeds the traditional "
          "pipeline (alarm -> SNS) if you add an alarm action; the RCA path "
          "runs in parallel via EventBridge.")
