import json
import boto3
import uuid
import logging
from datetime import datetime
import traceback

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamo = boto3.resource("dynamodb")
TABLE = dynamo.Table("TicketBuddy_Alerts")


def log_event(log_level, error_type, message, **kwargs):
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "level": log_level,
        "error_type": error_type,
        "message": message,
        **kwargs
    }
    if log_level == "ERROR":
        logger.error(json.dumps(entry))
    elif log_level == "WARNING":
        logger.warning(json.dumps(entry))
    else:
        logger.info(json.dumps(entry))


def lambda_handler(event, context):
    request_id = context.aws_request_id if context else "unknown"

    # Startup log — confirms Lambda initialised and target table in CloudWatch
    log_event("INFO", "LAMBDA_INITIALISED",
              "Alerts Lambda started",
              request_id=request_id,
              alerts_table="TicketBuddy_Alerts")

    # Validate that a body exists in the event
    if "body" not in event or not event["body"]:
        log_event("ERROR", "MISSING_REQUEST_BODY",
                  "Event contains no body field",
                  request_id=request_id,
                  received_keys=list(event.keys()),
                  http_status=400)
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Request body is missing"})
        }

    # Parse the request body
    try:
        body = json.loads(event["body"])
    except (json.JSONDecodeError, TypeError) as e:
        log_event("ERROR", "JSON_PARSE_ERROR",
                  "Request body could not be parsed as JSON",
                  request_id=request_id,
                  raw_body=str(event.get("body", ""))[:200],
                  exception=str(e),
                  http_status=400)
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Invalid JSON body"})
        }

    # Validate required fields
    username = body.get("username")
    message  = body.get("message")

    missing_fields = []
    if not username:
        missing_fields.append("username")
    if not message:
        missing_fields.append("message")

    if missing_fields:
        log_event("ERROR", "MISSING_REQUIRED_FIELDS",
                  "One or more required fields are missing from the request",
                  request_id=request_id,
                  missing_fields=missing_fields,
                  received_keys=list(body.keys()),
                  http_status=400)
        return {
            "statusCode": 400,
            "body": json.dumps({"error": f"Missing required fields: {missing_fields}"})
        }

    # Validate alert level value
    alert_level  = body.get("level", "info")
    valid_levels = {"info", "warning", "error", "critical"}
    if alert_level not in valid_levels:
        log_event("WARNING", "INVALID_ALERT_LEVEL",
                  "Provided alert level is not a recognised value — defaulting to info",
                  request_id=request_id,
                  username=username,
                  provided_level=alert_level,
                  valid_levels=list(valid_levels))
        alert_level = "info"

    # Build the alert item
    alert_id   = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()

    item = {
        "alert_id":   alert_id,
        "username":   username,
        "message":    message,
        "created_at": created_at,
        "level":      alert_level,
        "request_id": request_id
    }

    # Write alert to DynamoDB
    try:
        TABLE.put_item(Item=item)
    except dynamo.meta.client.exceptions.ProvisionedThroughputExceededException as e:
        log_event("ERROR", "DYNAMODB_THROTTLE_ERROR",
                  "DynamoDB write throttled — provisioned throughput exceeded",
                  request_id=request_id,
                  username=username,
                  alert_id=alert_id,
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=503)
        return {
            "statusCode": 503,
            "body": json.dumps({"error": "Service temporarily unavailable"})
        }

    except dynamo.meta.client.exceptions.ResourceNotFoundException as e:
        log_event("ERROR", "DYNAMODB_TABLE_NOT_FOUND",
                  "DynamoDB table not found — check table name and region",
                  request_id=request_id,
                  table="TicketBuddy_Alerts",
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=500)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal configuration error"})
        }

    except Exception as e:
        log_event("ERROR", "DYNAMODB_WRITE_ERROR",
                  "Unexpected error writing alert to DynamoDB",
                  request_id=request_id,
                  username=username,
                  alert_id=alert_id,
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=500)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Failed to store alert"})
        }

    # Success
    log_event("INFO", "ALERT_STORED",
              "Alert stored successfully",
              request_id=request_id,
              alert_id=alert_id,
              username=username,
              alert_level=alert_level,
              http_status=200)

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Alert stored", "alert_id": alert_id})
    }