import boto3

dynamo = boto3.resource("dynamodb", region_name="us-east-1")
table = dynamo.Table("TicketBuddy_Schedules")

carpool_data = {
    "R1": {"car_type": "Toyota Camry",    "driver_name": "Liam Murphy",   "total_seats": 5},
    "R2": {"car_type": "Honda Civic",     "driver_name": "Sean O'Brien",  "total_seats": 5},
    "R3": {"car_type": "Ford Focus",      "driver_name": "Aoife Kelly",   "total_seats": 6},
    "R4": {"car_type": "Volkswagen Golf", "driver_name": "Ciarán Walsh",  "total_seats": 5},
    "R5": {"car_type": "Nissan Leaf",     "driver_name": "Niamh Ryan",    "total_seats": 5},
    "R6": {"car_type": "Hyundai Tucson",  "driver_name": "Patrick Doyle", "total_seats": 6},
    "R7": {"car_type": "Toyota Corolla",  "driver_name": "Siobhan Burke", "total_seats": 5},
    "R8": {"car_type": "Skoda Octavia",   "driver_name": "Declan Byrne",  "total_seats": 6},
}

for route_id, data in carpool_data.items():
    table.update_item(
        Key={"route_id": route_id},
        UpdateExpression="SET car_type = :c, driver_name = :d, total_seats = :t",
        ExpressionAttributeValues={
            ":c": data["car_type"],
            ":d": data["driver_name"],
            ":t": int(data["total_seats"])
        }
    )
    print(f"✅ Updated {route_id}: {data['car_type']} · {data['driver_name']} ({data['total_seats']} seats)")

print("Done!")