"""
rca_lambda.py — RCA diagnosis engine (Groq-tuned, errors-pinned).
"""

import json
import os
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone

import boto3

logs_client = boto3.client("logs")
sns_client = boto3.client("sns")
dynamo = boto3.resource("dynamodb")

DEFAULT_LOG_GROUPS = [
    "/aws/lambda/TicketBuddy_BookTicket",
    "/aws/lambda/TicketBuddy_CancelTicket",
    "/aws/lambda/TicketBuddy_UpdateSeat",
    "/aws/lambda/TicketBuddy_GetSchedules",
    "/aws/lambda/TicketBuddy_GetHistory",
    "/aws/lambda/TicketBuddy_TaxCalculator",
]

FAULT_CATEGORIES = [
    "API_REQUEST_FAILURE",
    "LAMBDA_TIMEOUT",
    "HIGH_RESOURCE_USAGE",
    "LAMBDA_EXCEPTION",
    "DYNAMODB_FAILURE",
    "NO_FAULT",
]

ERROR_FILTER = '?"ERROR" ?"Traceback" ?"Task timed out" ?"INJECTED_FAULT" ?"ResourceNotFoundException"'

SYSTEM_PROMPT = """You are an expert Site Reliability Engineer performing \
automated root cause analysis for TicketBuddy, a ticket-booking application \
on AWS (Django on Elastic Beanstalk -> Lambda functions -> DynamoDB, SNS, S3).

You receive a CloudWatch alarm and log excerpts. The section 'ERROR / FAULT \
LOG LINES' contains the most diagnostic evidence — focus there. Log lines \
containing 'INJECTED_FAULT' or 'FAULT_ACTIVE' are experiment markers: IGNORE \
those markers themselves, but the surrounding real error (the traceback, the \
exception type, the timeout, the DynamoDB error) IS the fault to diagnose.

Map the evidence to exactly one category:
- LAMBDA_EXCEPTION: an unhandled exception / RuntimeError / traceback in a function
- LAMBDA_TIMEOUT: "Task timed out"
- HIGH_RESOURCE_USAGE: very long durations / CPU exhaustion
- DYNAMODB_FAILURE: ResourceNotFoundException or DynamoDB errors
- API_REQUEST_FAILURE: upstream API 502 / API_REQUEST_FAILURE errors
- NO_FAULT: only if there is genuinely no error evidence at all

Respond with ONLY a JSON object, no markdown, no preamble:
{
  "root_cause_category": one of the six above,
  "affected_component": "<function/service most affected>",
  "root_cause": "<1-2 sentence diagnosis>",
  "remediation": "<concrete fix, 1-3 steps>",
  "confidence": <float 0.0-1.0>,
  "evidence": "<key log lines supporting the diagnosis>"
}"""


def collect_logs(lookback_minutes):
    groups = [g.strip() for g in os.getenv(
        "LOG_GROUPS", ",".join(DEFAULT_LOG_GROUPS)).split(",") if g.strip()]
    start = int((time.time() - lookback_minutes * 60) * 1000)
    error_lines = []
    context_lines = []
    for group in groups:
        short = group.split("/")[-1]
        try:
            errs = logs_client.filter_log_events(
                logGroupName=group, startTime=start,
                filterPattern=ERROR_FILTER, limit=20).get("events", [])
            for e in errs:
                error_lines.append("[" + short + "] " + e["message"].strip()[:250])
            ctx = logs_client.filter_log_events(
                logGroupName=group, startTime=start,
                limit=8).get("events", [])
            for e in ctx:
                context_lines.append("[" + short + "] " + e["message"].strip()[:150])
        except logs_client.exceptions.ResourceNotFoundException:
            continue
        except Exception as e:
            context_lines.append("[" + short + "] collect error: " + str(e))

    ctx_block = "\n".join(context_lines[-25:])
    err_block = "\n".join(error_lines[-25:])
    text = ("CONTEXT LOGS:\n" + ctx_block +
            "\n\nERROR / FAULT LOG LINES (most important):\n" + err_block)
    return text[-6000:] if len(text) > 6000 else text


def call_anthropic(prompt):
    body = json.dumps({
        "model": os.getenv("LLM_MODEL", "claude-sonnet-4-6"),
        "max_tokens": 1000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "User-Agent": "TicketBuddy-RCA/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return "".join(b.get("text", "") for b in data.get("content", []))


def call_openai(prompt):
    body = json.dumps({
        "model": os.getenv("LLM_MODEL", "gpt-4o"),
        "max_tokens": 1000,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }).encode()
    req = urllib.request.Request(
        os.getenv("LLM_BASE_URL", "https://api.openai.com/v1/chat/completions"),
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
            "User-Agent": "TicketBuddy-RCA/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def call_llm(prompt):
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    fn = call_openai if provider == "openai" else call_anthropic
    last = None
    for attempt in range(3):
        try:
            return fn(prompt)
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                time.sleep(20 * (attempt + 1))
                continue
            raise
    raise last


def parse_llm_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON in LLM response: " + text[:200])
    result = json.loads(text[start:end + 1], strict=False)
    if result.get("root_cause_category") not in FAULT_CATEGORIES:
        result["root_cause_category"] = "NO_FAULT"
    result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0))))
    return result


def lambda_handler(event, context):
    alarm_time = datetime.now(timezone.utc).isoformat()
    t0 = time.time()

    detail = event.get("detail", {})
    alarm_name = detail.get("alarmName", "unknown-alarm")
    alarm_reason = detail.get("state", {}).get("reason", "")

    lookback = int(os.getenv("LOOKBACK_MINUTES", "5"))
    log_text = collect_logs(lookback)

    prompt = (
        "CloudWatch alarm fired: " + alarm_name + "\n"
        "Alarm reason: " + alarm_reason + "\n"
        "Time (UTC): " + alarm_time + "\n\n"
        + log_text + "\n\n"
        "Produce the JSON diagnosis."
    )

    try:
        raw = call_llm(prompt)
        result = parse_llm_json(raw)
        llm_error = ""
    except Exception as e:
        result = {
            "root_cause_category": "NO_FAULT",
            "affected_component": "unknown",
            "root_cause": "RCA pipeline error: " + str(e),
            "remediation": "Inspect RCA Lambda logs.",
            "confidence": 0.0,
            "evidence": "",
        }
        llm_error = str(e)

    analysis_seconds = round(time.time() - t0, 2)
    result_id = str(uuid.uuid4())[:8]

    try:
        dynamo.Table(os.getenv("RESULTS_TABLE", "TicketBuddy_RCAResults")).put_item(
            Item={
                "result_id": result_id,
                "alarm_name": alarm_name,
                "alarm_time": alarm_time,
                "analysis_seconds": str(analysis_seconds),
                "root_cause_category": result["root_cause_category"],
                "affected_component": result.get("affected_component", ""),
                "root_cause": result.get("root_cause", ""),
                "remediation": result.get("remediation", ""),
                "confidence": str(result.get("confidence", 0)),
                "evidence": result.get("evidence", "")[:1000],
                "llm_error": llm_error,
                "llm_model": os.getenv("LLM_MODEL", ""),
            }
        )
    except Exception as e:
        print("Failed to store RCA result: " + str(e))

    message = (
        "AUTOMATED ROOT CAUSE ANALYSIS - TicketBuddy\n"
        + "=" * 46 + "\n"
        "Alarm:      " + alarm_name + "\n"
        "Time:       " + alarm_time + "\n"
        "Analysis:   " + str(analysis_seconds) + "s\n\n"
        "CATEGORY:   " + result["root_cause_category"] + "\n"
        "COMPONENT:  " + result.get("affected_component", "") + "\n"
        "CONFIDENCE: " + str(round(result.get("confidence", 0) * 100)) + "%\n\n"
        "ROOT CAUSE:\n" + result.get("root_cause", "") + "\n\n"
        "REMEDIATION:\n" + result.get("remediation", "") + "\n\n"
        "EVIDENCE:\n" + result.get("evidence", "") + "\n\n"
        "(result_id: " + result_id + ")"
    )
    topic = os.getenv("SNS_TOPIC_ARN", "")
    if topic:
        try:
            sns_client.publish(
                TopicArn=topic,
                Subject="[RCA] " + result["root_cause_category"] + " - " + alarm_name,
                Message=message,
            )
        except Exception as e:
            print("SNS publish failed: " + str(e))

    print(json.dumps({"result_id": result_id, **result,
                      "analysis_seconds": analysis_seconds}))
    return {"statusCode": 200, "result_id": result_id, "result": result}