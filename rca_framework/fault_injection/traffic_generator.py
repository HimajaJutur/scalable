"""
traffic_generator.py — synthetic workload driver for RCA trials.

Generates a steady stream of realistic activity so that active faults
actually manifest in CloudWatch logs. Two channels:

  1. HTTP GETs against the Elastic Beanstalk site (public pages).
  2. Direct boto3 invocations of the backend Lambdas with synthetic
     payloads (username 'loadtest-user' so records are identifiable
     and cleanable).

Usage:
  python traffic_generator.py --base-url http://ridereseve.us-east-1.elasticbeanstalk.com \
      --duration 300 --interval 2

Requires: pip install requests  (boto3 already present in Cloud9)
"""

import argparse
import json
import random
import time
import uuid
from datetime import datetime, timedelta

import boto3

try:
    import requests
except ImportError:
    requests = None

lambda_client = boto3.client("lambda", region_name="us-east-1")

HTTP_PATHS = ["/", "/destinations/", "/schedules/", "/contact/"]

ROUTES = [
    ("Dublin", "Cork"), ("Galway", "Cork"), ("Dublin", "Galway"),
    ("Limerick", "Dublin"), ("Waterford", "Dublin"),
]


def hit_http(base_url):
    if requests is None:
        return "SKIP(no requests lib)"
    path = random.choice(HTTP_PATHS)
    try:
        r = requests.get(base_url.rstrip("/") + path, timeout=10)
        return f"GET {path} -> {r.status_code}"
    except Exception as e:
        return f"GET {path} -> ERROR {type(e).__name__}"


def invoke(fn, payload):
    try:
        resp = lambda_client.invoke(
            FunctionName=fn,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload),
        )
        body = resp["Payload"].read().decode()[:80]
        return f"{fn} -> {body}"
    except Exception as e:
        return f"{fn} -> ERROR {type(e).__name__}: {e}"


def synthetic_booking():
    src, dst = random.choice(ROUTES)
    date = (datetime.utcnow()
            + timedelta(days=random.randint(1, 30))).strftime("%Y-%m-%d")
    seat = str(random.randint(1, 4))
    payload = {
        "username": "loadtest-user",
        "from": src, "to": dst,
        "passengers": "1",
        "departure_date": date,
        "ticket_type": "One Way",
        "seats": [seat],
        "fare": 25.0,
        "route": f"{src[:3].upper()}-{dst[:3].upper()}-01",
        "departure_time": "09:00",
        "arrival_time": "12:00",
        "booking_id": f"loadtest-{uuid.uuid4().hex[:8]}",
    }
    return invoke("TicketBuddy_BookTicket", payload)


def synthetic_reads():
    src, dst = random.choice(ROUTES)
    out = [invoke("TicketBuddy_GetSchedules", {"from": src, "to": dst})]
    out.append(invoke("TicketBuddy_GetHistory", {"username": "loadtest-user"}))
    out.append(invoke("TicketBuddy_TaxCalculator",
                      {"price": round(random.uniform(10, 90), 2),
                       "country_code": "IE"}))
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", required=True)
    p.add_argument("--duration", type=int, default=300,
                   help="seconds to run (default 300)")
    p.add_argument("--interval", type=float, default=2.0,
                   help="seconds between activity bursts (default 2)")
    args = p.parse_args()

    end = time.time() + args.duration
    n = 0
    print(f"Traffic run: {args.duration}s against {args.base_url}")
    while time.time() < end:
        n += 1
        lines = [hit_http(args.base_url)]
        lines += synthetic_reads()
        if n % 3 == 0:                       # a booking every 3rd burst
            lines.append(synthetic_booking())
        stamp = datetime.utcnow().strftime("%H:%M:%S")
        for line in lines:
            print(f"[{stamp}] {line}")
        time.sleep(args.interval)
    print(f"Done: {n} bursts.")


if __name__ == "__main__":
    main()
