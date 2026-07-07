import json

VAT_RATES = {"IE": 23, "GB": 20, "DE": 19, "FR": 20}

def lambda_handler(event, context):
    try:
        body = event.get("body")
        if isinstance(body, str):
            body = json.loads(body)
        elif body is None:
            body = event

        price = float(body.get("price", 0))
        country = str(body.get("country_code", "IE")).upper()
        rate = VAT_RATES.get(country, 23)
        tax_amount = round(price * rate / 100, 2)

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "original_price": price,
                "tax_rate": rate,
                "tax_amount": tax_amount,
                "final_price": round(price + tax_amount, 2),
                "currency": "EUR",
            }),
        }
    except Exception as e:
        return {"statusCode": 400,
                "body": json.dumps({"error": str(e)})}