import boto3
import json
import logging
from boto3.dynamodb.conditions import Key
from datetime import datetime
import traceback
from fault_injector import apply_fault
logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamo = boto3.resource("dynamodb")
TABLE = dynamo.Table("TicketBuddy_Seats")


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
              "Get seats Lambda started",
              request_id=request_id,
              seats_table="TicketBuddy_Seats")

    # Extract route_id from event or body
    route_id = event.get("route_id")

    if not route_id and "body" in event:
        try:
            body = json.loads(event["body"])
            route_id = body.get("route_id")
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

    # Validate route_id presence
    if not route_id:
        log_event("ERROR", "MISSING_ROUTE_ID",
                  "Request received with no route_id field",
                  request_id=request_id,
                  received_keys=list(event.keys()),
                  http_status=400)
        return {
            "statusCode": 400,
            "status": "error",
            "message": "Missing route_id"
        }

    # Query DynamoDB for all seats on this route
    log_event("INFO", "SEAT_QUERY_STARTED",
              "Querying all seats for route",
              request_id=request_id,
              route_id=route_id)

    try:
        resp = TABLE.query(
            KeyConditionExpression=Key("route_id").eq(route_id)
        )
    except dynamo.meta.client.exceptions.ProvisionedThroughputExceededException as e:
        log_event("ERROR", "DYNAMODB_THROTTLE_ERROR",
                  "DynamoDB query throttled — provisioned throughput exceeded",
                  request_id=request_id,
                  route_id=route_id,
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
                  table="TicketBuddy_Seats",
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=500)
        return {
            "statusCode": 500,
            "status": "error",
            "message": "Internal configuration error"
        }

    except Exception as e:
        log_event("ERROR", "DYNAMODB_QUERY_ERROR",
                  "Unexpected error querying seats from DynamoDB",
                  request_id=request_id,
                  route_id=route_id,
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=500)
        return {
            "statusCode": 500,
            "status": "error",
            "message": "Failed to retrieve seats"
        }

    # Extract items from response
    seats = resp.get("Items", [])

    if not seats:
        log_event("WARNING", "NO_SEATS_FOUND",
                  "Query returned no seats for the given route",
                  request_id=request_id,
                  route_id=route_id,
                  http_status=200)
    else:
        log_event("INFO", "SEATS_RETRIEVED",
                  "Seats retrieved successfully",
                  request_id=request_id,
                  route_id=route_id,
                  seats_count=len(seats),
                  http_status=200)

    return {
        "statusCode": 200,
        "status": "success",
        "seats": seats
    }