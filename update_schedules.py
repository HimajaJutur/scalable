
import json
import boto3

REGION = "us-east-1"
TABLE_NAME = "TicketBuddy_Schedules"

dynamodb = boto3.resource("dynamodb", region_name=REGION)
client = boto3.client("dynamodb", region_name=REGION)


# Create table if it doesn't exist

try:
    client.describe_table(TableName=TABLE_NAME)
    print(f" {TABLE_NAME} already exists.")
except client.exceptions.ResourceNotFoundException:
    print("Creating table...")

    table = dynamodb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {
                "AttributeName": "route_id",
                "KeyType": "HASH"
            }
        ],
        AttributeDefinitions=[
            {
                "AttributeName": "route_id",
                "AttributeType": "S"
            }
        ],
        BillingMode="PAY_PER_REQUEST"
    )

    table.wait_until_exists()
    print(" Table created.")

table = dynamodb.Table(TABLE_NAME)


# Sample cars/drivers


cars = [
    "Toyota Camry",
    "Honda Civic",
    "Ford Focus",
    "Volkswagen Golf",
    "Toyota Corolla",
    "Skoda Octavia",
    "Hyundai Tucson",
    "Nissan Leaf"
]

drivers = [
    "Liam Murphy",
    "Aoife Kelly",
    "Sean O'Brien",
    "Patrick Doyle",
    "Niamh Ryan",
    "Ciarán Walsh",
    "Siobhan Burke",
    "Declan Byrne",
    "Emma Kelly",
    "John Ryan",
    "Sarah Murphy",
    "Mark Byrne",
    "Laura Walsh",
    "David Doyle"
]


# Files to import


FILES = [
    "schedule_part1.json",
    "schedule_part2.json"
]

index = 0

for filename in FILES:

    print(f"\nReading {filename}")

    with open(filename) as f:
        data = json.load(f)

    routes = data["TicketBuddy_Schedules"]

    with table.batch_writer() as batch:

        for route in routes:

            item = route["PutRequest"]["Item"]

            batch.put_item(
                Item={
                    "route_id": item["route_id"]["S"],
                    "source": item["source"]["S"],
                    "destination": item["destination"]["S"],
                    "departure_time": item["departure_time"]["S"],
                    "arrival_time": item["arrival_time"]["S"],
                    "fare": int(item["fare"]["N"]),

                    # Extra fields used by Django
                    "car_type": cars[index % len(cars)],
                    "driver_name": drivers[index % len(drivers)],
                    "total_seats": 5 if index % 2 == 0 else 6
                }
            )

            print("Inserted", item["route_id"]["S"])

            index += 1

print("\n=================================")
print(f"Imported {index} schedules successfully.")
print("=================================")