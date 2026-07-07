import boto3
import os
import zipfile
import botocore.exceptions

LAMBDA_ROLE = "arn:aws:iam::085848612043:role/LabRole"
LAMBDA_DIR = "../lambda_deploy"

FUNCTIONS = {
    "TicketBuddy_BookTicket": "book_ticket.py",
    "TicketBuddy_GetHistory": "get_history.py",
    "TicketBuddy_CreateAlert": "create_alert.py",
    "TicketBuddy_CancelTicket": "cancel_ticket.py",
    "TicketBuddy_GetSchedules": "get_schedules.py",
    "TicketBuddy_SeedSeats": "seed_seats.py",
    "TicketBuddy_GetSeats": "get_seats.py",
    "TicketBuddy_UpdateSeat": "update_seat.py",
    "TicketBuddy_GetSeatStatus": "get_seat_status.py",
    "TicketBuddy_TaxCalculator": "tax_calculator.py",
}

# Functions that need a public function URL (called over HTTP by the app)
FUNCTION_URLS = ["TicketBuddy_TaxCalculator"]

lambda_client = boto3.client("lambda")


def zip_lambda(py_file):
    zip_name = py_file.replace(".py", ".zip")
    py_path = os.path.join(LAMBDA_DIR, py_file)
    zip_path = os.path.join(LAMBDA_DIR, zip_name)
    print(f"Zipping {py_file} → {zip_name}")
    with zipfile.ZipFile(zip_path, 'w') as z:
        z.write(py_path, arcname=py_file)
    return zip_path


def deploy_lambda(function_name, file_name):
    zip_path = zip_lambda(file_name)
    with open(zip_path, "rb") as f:
        zip_bytes = f.read()
    try:
        print(f"Creating Lambda: {function_name} ...")
        lambda_client.create_function(
            FunctionName=function_name,
            Runtime="python3.9",
            Role=LAMBDA_ROLE,
            Handler=file_name.replace(".py", "") + ".lambda_handler",
            Code={"ZipFile": zip_bytes},
            Timeout=15
        )
        print(f"✔ CREATED {function_name}")
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == "ResourceConflictException":
            print(f"Updating Lambda: {function_name} ...")
            lambda_client.update_function_code(
                FunctionName=function_name,
                ZipFile=zip_bytes
            )
            print(f"✔ UPDATED {function_name}")
        else:
            raise e


def ensure_function_url(function_name):
    """Create a public function URL for the given Lambda (idempotent)."""
    try:
        resp = lambda_client.create_function_url_config(
            FunctionName=function_name,
            AuthType="NONE",
        )
        url = resp["FunctionUrl"]
        print(f"✔ CREATED function URL for {function_name}: {url}")
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == "ResourceConflictException":
            resp = lambda_client.get_function_url_config(
                FunctionName=function_name
            )
            url = resp["FunctionUrl"]
            print(f"✔ EXISTING function URL for {function_name}: {url}")
        else:
            raise e

    # Allow public invocation of the URL (idempotent)
    try:
        lambda_client.add_permission(
            FunctionName=function_name,
            StatementId="public-url",
            Action="lambda:InvokeFunctionUrl",
            Principal="*",
            FunctionUrlAuthType="NONE",
        )
        print(f"✔ PUBLIC access granted for {function_name} URL")
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] != "ResourceConflictException":
            raise e

    return url


if __name__ == "__main__":
    print("Deploying TicketBuddy Lambda Functions...\n")
    for fn, file in FUNCTIONS.items():
        deploy_lambda(fn, file)

    print("\nConfiguring function URLs...\n")
    for fn in FUNCTION_URLS:
        url = ensure_function_url(fn)

    print("\n✔ ALL LAMBDAS DEPLOYED OR UPDATED SUCCESSFULLY")
    print("→ Set TAX_API_URL in Elastic Beanstalk environment properties "
          "to the TicketBuddy_TaxCalculator URL printed above.")