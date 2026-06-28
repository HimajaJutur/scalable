import json
import boto3
import uuid
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
import traceback

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def log_event(level, error_type, message, **kwargs):
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "level": level,
        "error_type": error_type,
        "message": message,
        **kwargs
    }
    if level == "ERROR":
        logger.error(json.dumps(entry))
    elif level == "WARNING":
        logger.warning(json.dumps(entry))
    else:
        logger.info(json.dumps(entry))

dynamo = boto3.resource("dynamodb")
TICKETS = dynamo.Table("TicketBuddy_Tickets")


def to_decimal(v, default="0"):
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError) as e:
        log_event("ERROR", "DECIMAL_CONVERSION_ERROR",
                  "Failed to convert value to Decimal",
                  value=str(v), exception=str(e))
        return Decimal(default)


def lambda_handler(event, context):
    request_id = context.aws_request_id if context else str(uuid.uuid4())

    # Parse body
    try:
        body = event if isinstance(event, dict) else json.loads(event.get("body", "{}"))
    except (json.JSONDecodeError, TypeError) as e:
        log_event("ERROR", "JSON_PARSE_ERROR",
                  "Request body could not be parsed as JSON",
                  request_id=request_id,
                  raw_body=str(event.get("body", ""))[:200],
                  exception=str(e),
                  http_status=400)
        return {"statusCode": 400, "status": "error",
                "message": "Invalid JSON body", "request_id": request_id}

    # Extract and validate required fields
    username    = body.get("username")
    source      = body.get("from") or body.get("source")
    destination = body.get("to") or body.get("destination")
    passengers  = body.get("passengers", 1)
    seats       = body.get("seats", [])
    fare_raw    = body.get("fare", 0)

    missing_fields = []
    if not username:
        missing_fields.append("username")
    if not source:
        missing_fields.append("source/from")
    if not destination:
        missing_fields.append("destination/to")

    if missing_fields:
        log_event("ERROR", "MISSING_REQUIRED_FIELDS",
                  "One or more required fields are missing",
                  request_id=request_id,
                  missing_fields=missing_fields,
                  received_keys=list(body.keys()),
                  http_status=400)
        return {"statusCode": 400, "status": "error",
                "message": f"Missing required fields: {missing_fields}",
                "request_id": request_id}

    # Numeric conversions
    final_fare_per_seat = to_decimal(fare_raw)
    passengers_dec      = to_decimal(passengers)

    if final_fare_per_seat <= 0:
        log_event("WARNING", "INVALID_FARE_VALUE",
                  "Fare value is zero or negative",
                  request_id=request_id,
                  username=username,
                  fare_raw=str(fare_raw),
                  fare_converted=str(final_fare_per_seat),
                  http_status=422)
        return {"statusCode": 422, "status": "error",
                "message": "Fare must be greater than zero",
                "request_id": request_id}

    # Business logic validation
    if passengers_dec <= 0:
        log_event("ERROR", "INVALID_PASSENGER_COUNT",
                  "Passenger count is zero or negative",
                  request_id=request_id,
                  username=username,
                  passengers_raw=str(passengers),
                  passengers_converted=str(passengers_dec),
                  http_status=422)
        return {"statusCode": 422, "status": "error",
                "message": "Passenger count must be greater than zero",
                "request_id": request_id}

    if seats and len(seats) != int(passengers_dec):
        log_event("WARNING", "SEAT_PASSENGER_MISMATCH",
                  "Number of seats does not match passenger count",
                  request_id=request_id,
                  username=username,
                  passengers=str(passengers_dec),
                  seats_provided=len(seats),
                  http_status=422)
        return {"statusCode": 422, "status": "error",
                "message": "Seat count must match number of passengers",
                "request_id": request_id}

    # Compute total fare
    try:
        total = (final_fare_per_seat * passengers_dec).quantize(Decimal("0.01"))
    except Exception as e:
        log_event("ERROR", "FARE_CALCULATION_ERROR",
                  "Failed to calculate total fare",
                  request_id=request_id,
                  username=username,
                  fare_per_seat=str(final_fare_per_seat),
                  passengers=str(passengers_dec),
                  exception=str(e),
                  http_status=500)
        return {"statusCode": 500, "status": "error",
                "message": "Fare calculation failed", "request_id": request_id}

    # Build item
    booking_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()
    parent_booking_id = body.get("parent_booking_id")

    item = {
        "booking_id":     booking_id,
        "username":       username,
        "source":         source,
        "destination":    destination,
        "passengers":     passengers_dec,
        "seats":          seats,
        "fare_per_seat":  final_fare_per_seat,
        "fare":           total,
        "departure_time": body.get("departure_time", ""),
        "arrival_time":   body.get("arrival_time", ""),
        "departure_date": body.get("departure_date", ""),
        "return_date":    body.get("return_date", ""),
        "ticket_type":    body.get("ticket_type", "One Way"),
        "status":         "CONFIRMED",
        "created_at":     created_at,
        "request_id":     request_id,
    }
    if parent_booking_id:
        item["parent_booking_id"] = parent_booking_id

    # Write to DynamoDB
    try:
        TICKETS.put_item(Item=item)
    except dynamo.meta.client.exceptions.ProvisionedThroughputExceededException as e:
        log_event("ERROR", "DYNAMODB_THROTTLE_ERROR",
                  "DynamoDB write throttled — provisioned throughput exceeded",
                  request_id=request_id,
                  username=username,
                  booking_id=booking_id,
                  exception=str(e),
                  http_status=503)
        return {"statusCode": 503, "status": "error",
                "message": "Service temporarily unavailable", "request_id": request_id}

    except dynamo.meta.client.exceptions.ResourceNotFoundException as e:
        log_event("ERROR", "DYNAMODB_TABLE_NOT_FOUND",
                  "DynamoDB table not found — check table name and region",
                  request_id=request_id,
                  table="TicketBuddy_Tickets",
                  exception=str(e),
                  http_status=500)
        return {"statusCode": 500, "status": "error",
                "message": "Internal configuration error", "request_id": request_id}

    except Exception as e:
        log_event("ERROR", "DYNAMODB_WRITE_ERROR",
                  "Unexpected error writing booking to DynamoDB",
                  request_id=request_id,
                  username=username,
                  booking_id=booking_id,
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=500)
        return {"statusCode": 500, "status": "error",
                "message": "Failed to save booking", "request_id": request_id}

    # Success
    log_event("INFO", "BOOKING_CREATED",
              "Booking created successfully",
              request_id=request_id,
              booking_id=booking_id,
              username=username,
              source=source,
              destination=destination,
              passengers=str(passengers_dec),
              total_fare=str(total),
              ticket_type=item["ticket_type"],
              http_status=200)

    return {
        "statusCode": 200,
        "status": "success",
        "booking_id": booking_id,
        "item": item
    }