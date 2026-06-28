import boto3
import json
import logging
from boto3.dynamodb.conditions import Attr
from decimal import Decimal
from datetime import datetime
import traceback

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamo = boto3.resource("dynamodb")
TICKETS = dynamo.Table("TicketBuddy_Tickets")


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


def dec_to_native(obj):
    if isinstance(obj, list):
        return [dec_to_native(i) for i in obj]
    if isinstance(obj, dict):
        return {k: dec_to_native(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return float(obj)
    return obj


def lambda_handler(event, context):
    request_id = context.aws_request_id if context else "unknown"

    # Startup log — confirms Lambda initialised and target table in CloudWatch
    log_event("INFO", "LAMBDA_INITIALISED",
              "Get bookings Lambda started",
              request_id=request_id,
              tickets_table="TicketBuddy_Tickets")

    # Extract username from event or body
    username = None
    try:
        username = event.get("username")
        if not username and event.get("body"):
            body = json.loads(event.get("body"))
            username = body.get("username")
    except (json.JSONDecodeError, TypeError) as e:
        log_event("ERROR", "JSON_PARSE_ERROR",
                  "Request body could not be parsed as JSON",
                  request_id=request_id,
                  raw_body=str(event.get("body", ""))[:200],
                  exception=str(e),
                  http_status=400)
        return {
            "statusCode": 400,
            "status": "error",
            "message": "Invalid JSON body"
        }

    # Validate username presence
    if not username:
        log_event("ERROR", "MISSING_USERNAME",
                  "Request received with no username field",
                  request_id=request_id,
                  received_keys=list(event.keys()),
                  http_status=400)
        return {
            "statusCode": 400,
            "status": "error",
            "message": "Missing username"
        }

    # Scan DynamoDB for bookings belonging to this user
    try:
        resp = TICKETS.scan(
            FilterExpression=Attr("username").eq(username)
        )
    except dynamo.meta.client.exceptions.ProvisionedThroughputExceededException as e:
        log_event("ERROR", "DYNAMODB_THROTTLE_ERROR",
                  "DynamoDB scan throttled — provisioned throughput exceeded",
                  request_id=request_id,
                  username=username,
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=503)
        return {
            "statusCode": 503,
            "status": "error",
            "message": "Service temporarily unavailable"
        }

    except dynamo.meta.client.exceptions.ResourceNotFoundException as e:
        log_event("ERROR", "DYNAMODB_TABLE_NOT_FOUND",
                  "DynamoDB table not found — check table name and region",
                  request_id=request_id,
                  table="TicketBuddy_Tickets",
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=500)
        return {
            "statusCode": 500,
            "status": "error",
            "message": "Internal configuration error"
        }

    except Exception as e:
        log_event("ERROR", "DYNAMODB_SCAN_ERROR",
                  "Unexpected error scanning bookings from DynamoDB",
                  request_id=request_id,
                  username=username,
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=500)
        return {
            "statusCode": 500,
            "status": "error",
            "message": "Failed to retrieve bookings"
        }

    # Convert Decimal types to native Python types for JSON serialisation
    items = resp.get("Items", [])

    try:
        items = [dec_to_native(item) for item in items]
    except Exception as e:
        log_event("ERROR", "DECIMAL_CONVERSION_ERROR",
                  "Failed to convert DynamoDB Decimal types to native types",
                  request_id=request_id,
                  username=username,
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=500)
        return {
            "statusCode": 500,
            "status": "error",
            "message": "Failed to process bookings"
        }

    # Warn if no bookings were found for this user
    if not items:
        log_event("WARNING", "NO_BOOKINGS_FOUND",
                  "Scan completed but no bookings found for user",
                  request_id=request_id,
                  username=username,
                  http_status=200)
    else:
        log_event("INFO", "BOOKINGS_RETRIEVED",
                  "Bookings retrieved successfully",
                  request_id=request_id,
                  username=username,
                  bookings_count=len(items),
                  http_status=200)

    return {
        "statusCode": 200,
        "status": "success",
        "bookings": items
    }