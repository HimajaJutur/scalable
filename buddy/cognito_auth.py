import boto3
from botocore.exceptions import ClientError
from .cognito_config import AWS_REGION, USER_POOL_ID, CLIENT_ID

client = boto3.client("cognito-idp", region_name=AWS_REGION)

# -----------------------------
# SIGN UP
# -----------------------------
def cognito_signup(username, email, password):
    try:
        return client.sign_up(
            ClientId=CLIENT_ID,
            Username=username,
            Password=password,
            UserAttributes=[
                {'Name': 'email', 'Value': email}
            ]
        )
    except ClientError as e:
        return {"error": e.response["Error"]["Message"]}

# -----------------------------
# CONFIRM SIGN UP (OTP)
# -----------------------------
def cognito_confirm(username, code):
    try:
        return client.confirm_sign_up(
            ClientId=CLIENT_ID,
            Username=username,
            ConfirmationCode=code
        )
    except ClientError as e:
        return {"error": e.response["Error"]["Message"]}

# -----------------------------
# LOGIN
# -----------------------------
def cognito_login(username, password):
    try:
        return client.initiate_auth(
            ClientId=CLIENT_ID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": username,
                "PASSWORD": password
            }
        )
    except ClientError as e:
        return {"error": e.response["Error"]["Message"]}

# -----------------------------
# FORGOT PASSWORD (Send OTP)
# -----------------------------
def cognito_forgot_password(username):
    try:
        return client.forgot_password(
            ClientId=CLIENT_ID,
            Username=username
        )
    except ClientError as e:
        return {"error": e.response["Error"]["Message"]}

# -----------------------------
# CONFIRM NEW PASSWORD (Reset)
# -----------------------------
def cognito_confirm_new_password(username, code, new_password):
    try:
        return client.confirm_forgot_password(
            ClientId=CLIENT_ID,
            Username=username,
            ConfirmationCode=code,
            Password=new_password
        )
    except ClientError as e:
        return {"error": e.response["Error"]["Message"]}