import json
import boto3
import uuid
from datetime import datetime
from decimal import Decimal
import traceback
dynamo = boto3.resource("dynamodb")
TICKETS = dynamo.Table("TicketBuddy_Tickets")

def to_decimal(v, default="0"):
    try:
        return Decimal(str(v))
    except:
        return Decimal(default)

def lambda_handler(event, context):
    try:
        body = event if isinstance(event, dict) else json.loads(event.get("body","{}"))
        username = body.get("username")
        source = body.get("from") or body.get("source")
        destination = body.get("to") or body.get("destination")
        passengers = body.get("passengers", 1)
        seats = body.get("seats", [])
        
        #  USE DISCOUNTED PER-SEAT FARE FROM DJANGO
        final_fare_per_seat = to_decimal(body.get("fare", 0))
        passengers_dec = to_decimal(passengers)

        # ️ CALCULATE TOTAL FARE CORRECTLY
        total = (final_fare_per_seat * passengers_dec).quantize(Decimal("0.01"))

        booking_id = str(uuid.uuid4())
        created_at = datetime.utcnow().isoformat()
        
        parent_booking_id = body.get("parent_booking_id")
        
        #  STORE BOTH PER-SEAT AND TOTAL FARE
        item = {
            "booking_id": booking_id,
            "username": username,
            "source": source,
            "destination": destination,
            "passengers": passengers_dec,
            "seats": seats,
            "fare_per_seat": final_fare_per_seat,  
            "fare": total,                          
            "departure_time": body.get("departure_time",""),
            "arrival_time": body.get("arrival_time",""),
            "departure_date": body.get("departure_date",""),
            "return_date": body.get("return_date",""),
            "ticket_type": body.get("ticket_type","One Way"),
            "status": "CONFIRMED",
            "created_at": created_at
        }
        if parent_booking_id:
            item["parent_booking_id"] = parent_booking_id

        TICKETS.put_item(Item=item)
        return {"status": "success", "booking_id": booking_id, "item": item}
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "trace": traceback.format_exc()
        }