"""
setup_alerts.py — one-shot alert wiring for the RCA pipeline.

Does everything from the checklist in one run:
  1. Creates the SNS topic TicketBuddy_Alerts (idempotent)
  2. Subscribes your email (idempotent — AWS ignores duplicates)
  3. Updates the TicketBuddy_RCA Lambda's environment so it publishes
     to this topic and calls the Groq LLM

Usage:
  python3 setup_alerts.py --email you@example.com --groq-key gsk_xxxxx

After running: CLICK THE CONFIRMATION LINK in the email AWS sends you,
then run the smoke test.
"""

import argparse
import sys
import time

import boto3

REGION = "us-east-1"
TOPIC_NAME = "TicketBuddy_Alerts"
RCA_FUNCTION = "TicketBuddy_RCA"

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

sns = boto3.client("sns", region_name=REGION)
lam = boto3.client("lambda", region_name=REGION)


def ensure_topic():
    resp = sns.create_topic(Name=TOPIC_NAME)  # idempotent
    arn = resp["TopicArn"]
    print(f"[1/3] Topic ready: {arn}")
    return arn


def subscribe_email(topic_arn, email):
    subs = sns.list_subscriptions_by_topic(TopicArn=topic_arn).get(
        "Subscriptions", [])
    for s in subs:
        if s.get("Endpoint") == email:
            status = s.get("SubscriptionArn", "")
            if status == "PendingConfirmation":
                print(f"[2/3] {email} already subscribed but NOT CONFIRMED "
                      f"— check your inbox for the AWS confirmation email.")
            else:
                print(f"[2/3] {email} already subscribed and confirmed.")
            return
    sns.subscribe(TopicArn=topic_arn, Protocol="email",
                  Endpoint=email)
    print(f"[2/3] Subscription created for {email} — AWS has sent a "
          f"confirmation email. YOU MUST CLICK THE LINK IN IT.")


def update_rca_lambda(topic_arn, groq_key):
    try:
        cfg = lam.get_function_configuration(FunctionName=RCA_FUNCTION)
    except lam.exceptions.ResourceNotFoundException:
        print(f"[3/3] ERROR: Lambda {RCA_FUNCTION} not found — run "
              f"setup_experiment.py first.")
        sys.exit(1)

    env = cfg.get("Environment", {}).get("Variables", {})
    env.update({
        "SNS_TOPIC_ARN": topic_arn,
        "LLM_PROVIDER": "openai",
        "LLM_BASE_URL": GROQ_URL,
        "LLM_MODEL": GROQ_MODEL,
    })
    if groq_key:
        env["OPENAI_API_KEY"] = groq_key
    elif not env.get("OPENAI_API_KEY"):
        print("[3/3] WARNING: no --groq-key given and none already set — "
              "LLM calls will fail until you provide one.")

    # Lambda may briefly be in 'InProgress' state after a recent update.
    for attempt in range(6):
        try:
            lam.update_function_configuration(
                FunctionName=RCA_FUNCTION,
                Environment={"Variables": env},
            )
            break
        except lam.exceptions.ResourceConflictException:
            time.sleep(5)
    else:
        print("[3/3] ERROR: Lambda busy — retry in a minute.")
        sys.exit(1)

    print(f"[3/3] {RCA_FUNCTION} updated: topic + Groq "
          f"({GROQ_MODEL}) configured.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--email", required=True,
                   help="address to receive RCA alert emails")
    p.add_argument("--groq-key", default="",
                   help="Groq API key (gsk_...); omit to keep existing")
    args = p.parse_args()

    arn = ensure_topic()
    subscribe_email(arn, args.email)
    update_rca_lambda(arn, args.groq_key)

    print("\nDONE. Next steps:")
    print("  1. Click the confirmation link in the AWS email (if new sub).")
    print("  2. Optionally set SNS_TOPIC_ARN=" + arn)
    print("     in Elastic Beanstalk env properties (restores booking emails).")
    print("  3. Smoke test:")
    print("     python3 inject_fault.py activate --target "
          "TicketBuddy_BookTicket --type exception")
    print("     python3 traffic_generator.py --base-url "
          "http://YOUR-EB-URL --duration 180")