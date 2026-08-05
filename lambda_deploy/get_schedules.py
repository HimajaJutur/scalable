import json
import boto3
import logging
from boto3.dynamodb.conditions import Attr
from decimal import Decimal
from datetime import datetime
import traceback
from fault_injector import apply_fault
logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamo = boto3.resource("dynamodb")
TABLE = dynamo.Table("TicketBuddy_Schedules")


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


def d2f(obj):
    if isinstance(obj, list):
        return [d2f(i) for i in obj]
    if isinstance(obj, dict):
        return {k: d2f(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return float(obj)
    return obj


def lambda_handler(event, context):
    request_id = context.aws_request_id if context else "unknown"
    
    fault = apply_fault("TicketBuddy_GetSchedules")
    if fault and fault.get("fault_type") == "api_failure":
        return {"statusCode": 502, "status": "error",
                "message": "Upstream API failure"}

    # Startup log — confirms Lambda initialised and target table in CloudWatch
    log_event("INFO", "LAMBDA_INITIALISED",
              "Schedules Lambda started",
              request_id=request_id,
              schedules_table="TicketBuddy_Schedules")

    # Parse request body
    try:
        if "body" in event and isinstance(event["body"], str):
            body = json.loads(event["body"])
        else:
            body = event
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

    # Extract filter parameters
    source      = body.get("from") or body.get("source")
    destination = body.get("to") or body.get("destination")

    # MODE 1: Filter by source and destination
    if source and destination:
        log_event("INFO", "SCHEDULE_SEARCH_STARTED",
                  "Scanning schedules filtered by source and destination",
                  request_id=request_id,
                  source=source,
                  destination=destination)

        try:
            resp = TABLE.scan(
                FilterExpression=Attr("source").eq(source) &
                                 Attr("destination").eq(destination)
            )
        except dynamo.meta.client.exceptions.ProvisionedThroughputExceededException as e:
            log_event("ERROR", "DYNAMODB_THROTTLE_ERROR",
                      "DynamoDB scan throttled — provisioned throughput exceeded",
                      request_id=request_id,
                      source=source,
                      destination=destination,
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
                      table="TicketBuddy_Schedules",
                      exception=str(e),
                      trace=traceback.format_exc(),
                      http_status=500)
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "Internal configuration error"})
            }

        except Exception as e:
            log_event("ERROR", "DYNAMODB_SCAN_ERROR",
                      "Unexpected error scanning schedules by source and destination",
                      request_id=request_id,
                      source=source,
                      destination=destination,
                      exception=str(e),
                      trace=traceback.format_exc(),
                      http_status=500)
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "Failed to retrieve schedules"})
            }

        # Convert Decimal types
        try:
            items = d2f(resp.get("Items", []))
        except Exception as e:
            log_event("ERROR", "DECIMAL_CONVERSION_ERROR",
                      "Failed to convert DynamoDB Decimal types to native types",
                      request_id=request_id,
                      source=source,
                      destination=destination,
                      exception=str(e),
                      trace=traceback.format_exc(),
                      http_status=500)
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "Failed to process schedules"})
            }

        if not items:
            log_event("WARNING", "NO_SCHEDULES_FOUND",
                      "Scan completed but no schedules found for the given route",
                      request_id=request_id,
                      source=source,
                      destination=destination,
                      http_status=200)
        else:
            log_event("INFO", "SCHEDULES_RETRIEVED",
                      "Schedules retrieved successfully for route",
                      request_id=request_id,
                      source=source,
                      destination=destination,
                      schedules_count=len(items),
                      http_status=200)

        return {
            "statusCode": 200,
            "body": json.dumps(items)
        }

    # MODE 2: Return all schedules
    log_event("INFO", "ALL_SCHEDULES_REQUESTED",
              "No route filter provided — scanning all schedules",
              request_id=request_id)

    try:
        resp = TABLE.scan()
    except dynamo.meta.client.exceptions.ProvisionedThroughputExceededException as e:
        log_event("ERROR", "DYNAMODB_THROTTLE_ERROR",
                  "DynamoDB full table scan throttled — provisioned throughput exceeded",
                  request_id=request_id,
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
                  table="TicketBuddy_Schedules",
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=500)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal configuration error"})
        }

    except Exception as e:
        log_event("ERROR", "DYNAMODB_SCAN_ERROR",
                  "Unexpected error scanning all schedules from DynamoDB",
                  request_id=request_id,
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=500)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Failed to retrieve schedules"})
        }

    # Convert Decimal types
    try:
        items = d2f(resp.get("Items", []))
    except Exception as e:
        log_event("ERROR", "DECIMAL_CONVERSION_ERROR",
                  "Failed to convert DynamoDB Decimal types to native types",
                  request_id=request_id,
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=500)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Failed to process schedules"})
        }

    if not items:
        log_event("WARNING", "NO_SCHEDULES_FOUND",
                  "Full table scan completed but no schedules exist in the table",
                  request_id=request_id,
                  http_status=200)
    else:
        log_event("INFO", "ALL_SCHEDULES_RETRIEVED",
                  "All schedules retrieved successfully",
                  request_id=request_id,
                  schedules_count=len(items),
                  http_status=200)

    return {
        "statusCode": 200,
        "body": json.dumps(items)
    }