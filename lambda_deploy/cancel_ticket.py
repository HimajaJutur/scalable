import boto3
import json
import logging
from datetime import datetime
import traceback
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamo = boto3.resource("dynamodb")
tickets_table = dynamo.Table("TicketBuddy_Tickets")
seats_table = dynamo.Table("TicketBuddy_Seats")
sns = boto3.client("sns")

TOPIC_ARN = os.getenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:943886678149:TicketBuddy_Alerts")


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


def lambda_handler(event, context):
    request_id = context.aws_request_id if context else "unknown"

    # Startup log — confirms Lambda initialised and shows configuration in CloudWatch
    log_event("INFO", "LAMBDA_INITIALISED",
              "Cancellation Lambda started",
              request_id=request_id,
              tickets_table="TicketBuddy_Tickets",
              seats_table="TicketBuddy_Seats",
              sns_topic=TOPIC_ARN)

    # Validate booking_id presence
    booking_id = event.get("booking_id")
    if not booking_id:
        log_event("ERROR", "MISSING_BOOKING_ID",
                  "Request received with no booking_id field",
                  request_id=request_id,
                  received_keys=list(event.keys()),
                  http_status=400)
        return {"statusCode": 400, "status": "error", "message": "Missing booking_id"}

    # Fetch the ticket from DynamoDB
    try:
        resp = tickets_table.get_item(Key={"booking_id": booking_id})
        ticket = resp.get("Item")
    except Exception as e:
        log_event("ERROR", "DYNAMODB_FETCH_ERROR",
                  "Failed to fetch booking from DynamoDB",
                  request_id=request_id,
                  booking_id=booking_id,
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=500)
        return {"statusCode": 500, "status": "error", "message": "Failed to retrieve booking"}

    if not ticket:
        log_event("WARNING", "BOOKING_NOT_FOUND",
                  "No booking record found for the provided booking_id",
                  request_id=request_id,
                  booking_id=booking_id,
                  http_status=404)
        return {"statusCode": 404, "status": "error", "message": "Booking not found"}

    # Guard against cancelling an already cancelled booking
    current_status = ticket.get("status")
    if current_status == "CANCELLED":
        log_event("WARNING", "ALREADY_CANCELLED",
                  "Cancellation attempted on a booking that is already cancelled",
                  request_id=request_id,
                  booking_id=booking_id,
                  current_status=current_status,
                  http_status=409)
        return {"statusCode": 409, "status": "error", "message": "Booking is already cancelled"}

    # Extract seat release fields
    route    = ticket.get("route")
    dep_time = ticket.get("departure_time")
    seats    = ticket.get("seats", [])

    if not route or not dep_time:
        log_event("WARNING", "MISSING_SEAT_METADATA",
                  "Ticket is missing route or departure_time — seat release will be skipped",
                  request_id=request_id,
                  booking_id=booking_id,
                  route=route,
                  departure_time=dep_time)

    # Release seats back to AVAILABLE
    if route and dep_time and seats:
        for seat in seats:
            composite = f"{dep_time}#{seat}"
            try:
                seats_table.update_item(
                    Key={
                        "route_id": route,
                        "departure_time_seat": composite
                    },
                    UpdateExpression="SET #s = :a",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":a": "AVAILABLE"}
                )
                log_event("INFO", "SEAT_RELEASED",
                          "Seat successfully released",
                          request_id=request_id,
                          booking_id=booking_id,
                          route=route,
                          seat=seat,
                          composite_key=composite)
            except Exception as e:
                log_event("ERROR", "SEAT_RELEASE_ERROR",
                          "Failed to release seat — seat may remain in BOOKED state",
                          request_id=request_id,
                          booking_id=booking_id,
                          route=route,
                          seat=seat,
                          composite_key=composite,
                          exception=str(e),
                          trace=traceback.format_exc(),
                          http_status=500)

    elif seats and (not route or not dep_time):
        log_event("WARNING", "SEAT_RELEASE_SKIPPED",
                  "Seats exist on booking but could not be released due to missing metadata",
                  request_id=request_id,
                  booking_id=booking_id,
                  seats=seats,
                  route=route,
                  departure_time=dep_time)

    # Update ticket status to CANCELLED
    try:
        tickets_table.update_item(
            Key={"booking_id": booking_id},
            UpdateExpression="SET #s = :c",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":c": "CANCELLED"}
        )
    except Exception as e:
        log_event("ERROR", "DYNAMODB_STATUS_UPDATE_ERROR",
                  "Failed to update booking status to CANCELLED in DynamoDB",
                  request_id=request_id,
                  booking_id=booking_id,
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=500)
        return {"statusCode": 500, "status": "error", "message": "Failed to cancel booking"}

    # Send cancellation notification via SNS
    message = (
        f"Your TicketBuddy booking has been cancelled.\n\n"
        f"Booking ID: {booking_id}\n"
        f"Route: {ticket.get('source')} to {ticket.get('destination')}\n"
        f"Date: {ticket.get('departure_date')}\n"
        f"Seats: {', '.join(ticket.get('seats', []))}\n"
        f"Status: CANCELLED\n\n"
        f"If this was not you, please contact support."
    )

    try:
        sns.publish(
            TopicArn=TOPIC_ARN,
            Subject="TicketBuddy - Ticket Cancelled",
            Message=message
        )
    except Exception as e:
        log_event("ERROR", "SNS_PUBLISH_ERROR",
                  "Cancellation status updated in DynamoDB but SNS notification failed to send",
                  request_id=request_id,
                  booking_id=booking_id,
                  topic_arn=TOPIC_ARN,
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=500)
        return {
            "statusCode": 207,
            "status": "partial_success",
            "message": "Booking cancelled but notification could not be sent",
            "booking_id": booking_id
        }

    # Success
    log_event("INFO", "BOOKING_CANCELLED",
              "Booking cancelled successfully and notification sent",
              request_id=request_id,
              booking_id=booking_id,
              username=ticket.get("username"),
              source=ticket.get("source"),
              destination=ticket.get("destination"),
              seats_released=seats,
              http_status=200)

    return {"statusCode": 200, "status": "success", "booking_id": booking_id}