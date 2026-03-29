# fix_all_pdfs.py
import boto3
import sys
import os

sys.path.insert(0, '/home/ec2-user/environment/New Folder/scalable/ticketbuddy_new')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ticketbuddy_new.settings')

import django
django.setup()

from buddy.utils.pdf_generator import generate_ticket_pdf, upload_ticket_pdf

dynamo = boto3.resource("dynamodb", region_name="us-east-1")
tickets_table = dynamo.Table("TicketBuddy_Tickets")

response = tickets_table.scan()
items = response.get("Items", [])

while "LastEvaluatedKey" in response:
    response = tickets_table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
    items.extend(response.get("Items", []))

print(f"Total tickets found: {len(items)}")

fixed = 0
skipped = 0
failed = 0

for ticket in items:
    booking_id = ticket["booking_id"]
    pdf_url = ticket.get("pdf_url")  # ✅ None-safe

    # ✅ Fixed: check None first before calling .startswith()
    if not pdf_url or pdf_url.startswith("http"):
        try:
            pdf_buffer = generate_ticket_pdf(ticket)
            filename = f"tickets/{booking_id}.pdf"
            pdf_key = upload_ticket_pdf(pdf_buffer, filename)

            tickets_table.update_item(
                Key={"booking_id": booking_id},
                UpdateExpression="SET pdf_url = :p",
                ExpressionAttributeValues={":p": pdf_key}
            )
            print(f"✅ Fixed: {booking_id}")
            fixed += 1
        except Exception as e:
            print(f"❌ Failed: {booking_id} → {e}")
            failed += 1
    else:
        print(f"⏭  Skipped (already has key): {booking_id}")
        skipped += 1

print(f"\nDone! Fixed: {fixed} | Skipped: {skipped} | Failed: {failed}")