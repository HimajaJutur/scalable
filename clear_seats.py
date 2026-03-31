# clear_seats.py
import boto3

dynamo = boto3.resource("dynamodb", region_name="us-east-1")
table = dynamo.Table("TicketBuddy_Seats")

# Scan all items
resp = table.scan()
items = resp.get("Items", [])

print(f"Found {len(items)} items to delete...")

for item in items:
    table.delete_item(
        Key={
            "route_id": item["route_id"],
            "seat_id":  item["seat_id"]
        }
    )
    print(f"Deleted: {item['route_id']} / {item['seat_id']}")

print("Done! Table is now clean.")