# Updated script with static parameters (example placeholders filled)

import json
import time
import boto3
import os
from pathlib import Path

# -------------------------------------------------------------------
# Static configuration (replace with your actual values)
# -------------------------------------------------------------------
REGION = "us-east-1"  # Example: Ireland region
EB_APPLICATION_NAME = "RideReserve"
EB_ENVIRONMENT_NAME = "Ridereserveenvi"
EB_CNAME_PREFIX = "RideReseve"
EB_SERVICE_ROLE = "LabRole"
EB_INSTANCE_PROFILE = "LabInstanceProfile"

# (config.json removed — using static parameters only)

# -------------------------------------------------------------------
# Get Elastic Beanstalk client
# -------------------------------------------------------------------
def get_eb(region):
    return boto3.client("elasticbeanstalk", region_name=region)


# -------------------------------------------------------------------
# Ensure application exists
# -------------------------------------------------------------------
def ensure_application(eb, app_name):
    print(f"Checking if application '{app_name}' exists...")
    resp = eb.describe_applications(ApplicationNames=[app_name])

    if resp.get("Applications"):
        print(f"✔ Application exists: {app_name}")
        return

    print(f"Creating application '{app_name}'...")
    eb.create_application(
        ApplicationName=app_name,
        Description="EasyCart Elastic Beanstalk Application"
    )
    print("✔ Application created")


# -------------------------------------------------------------------
# Get latest Python platform
# -------------------------------------------------------------------
def get_latest_platform(eb):
    print("Finding latest Python platform...")

    resp = eb.list_platform_versions(
        Filters=[
            {"Type": "PlatformName", "Operator": "contains", "Values": ["Python"]}
        ]
    )

    platforms = resp.get("PlatformSummaryList", [])
    if not platforms:
        raise RuntimeError("❌ No Python EB platforms found!")

    ready = [p for p in platforms if p.get("PlatformStatus") in (None, "Ready")]
    ready.sort(key=lambda p: p["PlatformArn"], reverse=True)

    latest = ready[0]
    print(f"✔ Using platform: {latest['PlatformArn']}")
    return latest["PlatformArn"]


# -------------------------------------------------------------------
# Ensure EB environment
# -------------------------------------------------------------------
def ensure_environment(eb):
    app_name = EB_APPLICATION_NAME
    env_name = EB_ENVIRONMENT_NAME
    cname_prefix = EB_CNAME_PREFIX
    service_role = EB_SERVICE_ROLE
    instance_profile = EB_INSTANCE_PROFILE

    print(f"Checking if environment '{env_name}' exists...")
    resp = eb.describe_environments(
        ApplicationName=app_name,
        EnvironmentNames=[env_name],
        IncludeDeleted=False
    )

    envs = [e for e in resp.get("Environments", []) if e["Status"] != "Terminated"]

    if envs:
        env = envs[0]
        print(f"✔ Environment exists: {env_name}")
        print(f"   URL: http://{env.get('CNAME')}")
        return

    platform_arn = get_latest_platform(eb)

    print(f"Creating environment '{env_name}'...")

    resp = eb.create_environment(
        ApplicationName=app_name,
        EnvironmentName=env_name,
        CNAMEPrefix=cname_prefix,
        PlatformArn=platform_arn,
        Tier={"Name": "WebServer", "Type": "Standard"},
        OptionSettings=[
            {"Namespace": "aws:elasticbeanstalk:environment", "OptionName": "EnvironmentType", "Value": "SingleInstance"},
            {"Namespace": "aws:elasticbeanstalk:environment", "OptionName": "ServiceRole", "Value": service_role},
            {"Namespace": "aws:autoscaling:launchconfiguration", "OptionName": "IamInstanceProfile", "Value": instance_profile}
        ]
    )

    print(f"🚀 Environment creation started: {resp['EnvironmentId']}")


# -------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------
def main():
    print("🚀 Running EB deployment with static parameters")

    # No config.json — using static params only

    eb = get_eb(REGION)

    ensure_application(eb, EB_APPLICATION_NAME)
    ensure_environment(eb)

    print("✔ EB deployment script finished with static parameters.")


if __name__ == "__main__":
    main()
