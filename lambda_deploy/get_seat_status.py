import json
import boto3
from boto3.dynamodb.conditions import Key

dynamo = boto3.resource("dynamodb")
SEATS = dynamo.Table("TicketBuddy_Seats")

def lambda_handler(event, context):
    route = event.get("route_id")
    dep_time = event.get("departure_time")
    dep_date = event.get("departure_date")

    if not route and "body" in event:
        try:
            body = json.loads(event["body"])
            route = body.get("route_id")
            dep_time = body.get("departure_time")
            dep_date = body.get("departure_date")
        except Exception:
            pass

    if not route or not dep_time or not dep_date:
        return {"status": "error",
                "message": "Missing route_id, departure_time or departure_date"}

    try:
        resp = SEATS.query(
            KeyConditionExpression=(
                Key("route_id").eq(route)
                & Key("departure_time_seat").begins_with(f"{dep_date}#{dep_time}#")
            )
        )
        booked = [item["seat_no"] for item in resp.get("Items", [])
                  if item.get("status") == "BOOKED"]
        return {"status": "success", "booked_seats": booked}

    except Exception as e:
        return {"status": "error", "message": str(e)}