import json
import boto3
import uuid
import logging
from datetime import datetime
from botocore.exceptions import ClientError
import traceback

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamo = boto3.resource("dynamodb")
SEATS = dynamo.Table("TicketBuddy_Seats")


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
              "Update seat Lambda started",
              request_id=request_id,
              seats_table="TicketBuddy_Seats")

    # Extract fields from event
    route      = event.get("route_id")
    dep_time   = event.get("departure_time")
    seats      = event.get("seats", [])
    booking_id = event.get("booking_id") or str(uuid.uuid4())

    # Validate required fields
    missing_fields = []
    if not route:
        missing_fields.append("route_id")
    if not dep_time:
        missing_fields.append("departure_time")

    if missing_fields:
        log_event("ERROR", "MISSING_REQUIRED_FIELDS",
                  "One or more required fields are missing from the request",
                  request_id=request_id,
                  missing_fields=missing_fields,
                  received_keys=list(event.keys()),
                  http_status=400)
        return {
            "statusCode": 400,
            "status": "error",
            "message": f"Missing required fields: {missing_fields}"
        }

    if not seats:
        log_event("ERROR", "MISSING_SEATS",
                  "Request received with no seats to book",
                  request_id=request_id,
                  route_id=route,
                  departure_time=dep_time,
                  http_status=400)
        return {
            "statusCode": 400,
            "status": "error",
            "message": "Missing seats"
        }

    if not isinstance(seats, list):
        log_event("ERROR", "INVALID_SEATS_FORMAT",
                  "Seats field is not a list",
                  request_id=request_id,
                  route_id=route,
                  departure_time=dep_time,
                  seats_value=str(seats),
                  http_status=400)
        return {
            "statusCode": 400,
            "status": "error",
            "message": "Seats must be provided as a list"
        }

    log_event("INFO", "SEAT_BOOKING_STARTED",
              "Starting seat conflict check and booking",
              request_id=request_id,
              route_id=route,
              departure_time=dep_time,
              booking_id=booking_id,
              seats_requested=seats)

    # Check each seat for conflicts before booking any
    for seat in seats:
        composite = f"{dep_time}#{seat}"

        try:
            resp = SEATS.get_item(
                Key={
                    "route_id": route,
                    "departure_time_seat": composite
                }
            )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]

            if error_code == "ProvisionedThroughputExceededException":
                log_event("ERROR", "DYNAMODB_THROTTLE_ERROR",
                          "DynamoDB get_item throttled during conflict check",
                          request_id=request_id,
                          route_id=route,
                          departure_time=dep_time,
                          seat=seat,
                          composite_key=composite,
                          exception=str(e),
                          trace=traceback.format_exc(),
                          http_status=503)
                return {
                    "statusCode": 503,
                    "status": "error",
                    "message": "Service temporarily unavailable"
                }

            if error_code == "ResourceNotFoundException":
                log_event("ERROR", "DYNAMODB_TABLE_NOT_FOUND",
                          "DynamoDB table not found during conflict check",
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

            log_event("ERROR", "DYNAMODB_GET_ERROR",
                      "Unexpected DynamoDB error during seat conflict check",
                      request_id=request_id,
                      route_id=route,
                      departure_time=dep_time,
                      seat=seat,
                      composite_key=composite,
                      error_code=error_code,
                      exception=str(e),
                      trace=traceback.format_exc(),
                      http_status=500)
            return {
                "statusCode": 500,
                "status": "error",
                "message": "Failed to check seat availability"
            }

        except Exception as e:
            log_event("ERROR", "SEAT_CHECK_ERROR",
                      "Unexpected error during seat conflict check",
                      request_id=request_id,
                      route_id=route,
                      departure_time=dep_time,
                      seat=seat,
                      composite_key=composite,
                      exception=str(e),
                      trace=traceback.format_exc(),
                      http_status=500)
            return {
                "statusCode": 500,
                "status": "error",
                "message": "Failed to check seat availability"
            }

        # Seat conflict detected
        if "Item" in resp and resp["Item"].get("status") == "BOOKED":
            log_event("WARNING", "SEAT_CONFLICT",
                      "Seat is already booked — booking rejected",
                      request_id=request_id,
                      route_id=route,
                      departure_time=dep_time,
                      booking_id=booking_id,
                      conflicting_seat=seat,
                      composite_key=composite,
                      http_status=409)
            return {
                "statusCode": 409,
                "status": "error",
                "message": f"Seat already booked: {seat}",
                "conflict": seat
            }

    # No conflicts — book all seats
    booked_seats  = []
    failed_seats  = []

    for seat in seats:
        composite = f"{dep_time}#{seat}"

        try:
            SEATS.put_item(
                Item={
                    "route_id":             route,
                    "departure_time_seat":  composite,
                    "departure_time":       dep_time,
                    "seat_no":              seat,
                    "status":               "BOOKED",
                    "booking_id":           booking_id
                }
            )
            booked_seats.append(seat)
            log_event("INFO", "SEAT_BOOKED",
                      "Seat booked successfully",
                      request_id=request_id,
                      route_id=route,
                      departure_time=dep_time,
                      booking_id=booking_id,
                      seat=seat,
                      composite_key=composite)

        except ClientError as e:
            failed_seats.append(seat)
            error_code = e.response["Error"]["Code"]

            if error_code == "ProvisionedThroughputExceededException":
                log_event("ERROR", "DYNAMODB_THROTTLE_ERROR",
                          "DynamoDB put_item throttled during seat booking",
                          request_id=request_id,
                          route_id=route,
                          departure_time=dep_time,
                          booking_id=booking_id,
                          seat=seat,
                          composite_key=composite,
                          exception=str(e),
                          trace=traceback.format_exc(),
                          http_status=503)

            else:
                log_event("ERROR", "DYNAMODB_PUT_ERROR",
                          "Unexpected DynamoDB error while booking seat",
                          request_id=request_id,
                          route_id=route,
                          departure_time=dep_time,
                          booking_id=booking_id,
                          seat=seat,
                          composite_key=composite,
                          error_code=error_code,
                          exception=str(e),
                          trace=traceback.format_exc(),
                          http_status=500)

        except Exception as e:
            failed_seats.append(seat)
            log_event("ERROR", "SEAT_WRITE_ERROR",
                      "Unexpected error while writing seat booking to DynamoDB",
                      request_id=request_id,
                      route_id=route,
                      departure_time=dep_time,
                      booking_id=booking_id,
                      seat=seat,
                      composite_key=composite,
                      exception=str(e),
                      trace=traceback.format_exc(),
                      http_status=500)

    # Report partial failure if any seats failed to write
    if failed_seats and not booked_seats:
        log_event("ERROR", "SEAT_BOOKING_FAILED",
                  "All seat writes failed — no seats were booked",
                  request_id=request_id,
                  route_id=route,
                  departure_time=dep_time,
                  booking_id=booking_id,
                  failed_seats=failed_seats,
                  http_status=500)
        return {
            "statusCode": 500,
            "status": "error",
            "message": "Seat booking failed — no seats were written"
        }

    if failed_seats:
        log_event("WARNING", "SEAT_BOOKING_PARTIAL",
                  "Seat booking completed with some write failures",
                  request_id=request_id,
                  route_id=route,
                  departure_time=dep_time,
                  booking_id=booking_id,
                  booked_seats=booked_seats,
                  failed_seats=failed_seats,
                  http_status=207)
        return {
            "statusCode": 207,
            "status": "partial_success",
            "message": "Some seats could not be booked",
            "booking_id": booking_id,
            "booked": booked_seats,
            "failed": failed_seats
        }

    # Full success
    log_event("INFO", "SEAT_BOOKING_COMPLETE",
              "All seats booked successfully",
              request_id=request_id,
              route_id=route,
              departure_time=dep_time,
              booking_id=booking_id,
              booked_seats=booked_seats,
              http_status=200)

    return {
        "statusCode": 200,
        "status": "success",
        "booking_id": booking_id,
        "booked": booked_seats
    }