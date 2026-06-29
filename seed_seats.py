import boto3

dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
table = dynamodb.Table("TicketBuddy_Seats")

routes = [
    ("R1001", "08:00", 5),
    ("R1002", "14:00", 5),
    ("R2001", "09:00", 5),
    ("R2002", "17:00", 5),
    ("R3001", "07:30", 5),
    ("R3002", "15:00", 5),
    ("R4001", "11:00", 5),
    ("R4002", "18:30", 5),
    ("R5001", "09:00", 5),
    ("R5002", "17:00", 5),
    ("R6001", "07:45", 5),
    ("R6002", "16:00", 5),
    ("R7001", "10:00", 5),
    ("R7002", "18:00", 5),
    ("R8001", "08:00", 5),
    ("R8002", "17:00", 5),
    ("R9001", "08:00", 5),
    ("R9002", "15:00", 5),
    ("R9003", "09:30", 5),
    ("R9101", "07:45", 5),
    ("R9102", "17:15", 5),
    ("R9103", "11:00", 5),
    ("R9201", "08:00", 5),
    ("R9202", "14:00", 5),
    ("R9301", "09:00", 5),
    ("R9302", "17:30", 5),
    ("R9401", "10:00", 5),
    ("R9402", "14:30", 5),
]

count = 0

for route_id, departure_time, total_seats in routes:
    for seat in range(1, total_seats + 1):
        table.put_item(
            Item={
                "route_id": route_id,
                "departure_time_seat": f"{departure_time}#{seat}",
                "departure_time": departure_time,
                "seat_no": str(seat),
                "status": "AVAILABLE",
                "booking_id": ""
            }
        )
        count += 1

print(f"Inserted {count} seats successfully.")