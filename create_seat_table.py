import boto3

dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

table = dynamodb.create_table(
    TableName="TicketBuddy_Seats",
    KeySchema=[
        {
            "AttributeName": "route_id",
            "KeyType": "HASH"
        },
        {
            "AttributeName": "departure_time_seat",
            "KeyType": "RANGE"
        }
    ],
    AttributeDefinitions=[
        {
            "AttributeName": "route_id",
            "AttributeType": "S"
        },
        {
            "AttributeName": "departure_time_seat",
            "AttributeType": "S"
        }
    ],
    BillingMode="PAY_PER_REQUEST"
)

print("Creating table...")
table.wait_until_exists()
print("Table created successfully!")