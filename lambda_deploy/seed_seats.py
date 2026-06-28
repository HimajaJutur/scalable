import boto3
import json
import logging
from datetime import datetime
import traceback

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamo = boto3.resource("dynamodb")
SEAT_TABLE = dynamo.Table("TicketBuddy_Seats")
SCHEDULE_TABLE = dynamo.Table("TicketBuddy_Schedules")

ROWS  = ["A", "B", "C", "D", "E"]
COUNT = 8


def log_event(log_level, error_type, message, **kwargs):
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "level": log_level,
        "error_type": error_type,
        "message": message,
        **kwargs
    }
    if log_level == "ERROR":
        logger.error(json.dumps(entry))
    elif log_level == "WARNING":
        logger.warning(json.dumps(entry))
    else:
        logger.info(json.dumps(entry))


def _chunks(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def lambda_handler(event, context):
    request_id = context.aws_request_id if context else "unknown"

    # Startup log — confirms Lambda initialised and target tables in CloudWatch
    log_event("INFO", "LAMBDA_INITIALISED",
              "Seat generation Lambda started",
              request_id=request_id,
              seats_table="TicketBuddy_Seats",
              schedules_table="TicketBuddy_Schedules",
              rows=ROWS,
              seats_per_row=COUNT)

    # Fetch all routes from schedules table
    try:
        routes = SCHEDULE_TABLE.scan()["Items"]
    except dynamo.meta.client.exceptions.ProvisionedThroughputExceededException as e:
        log_event("ERROR", "DYNAMODB_THROTTLE_ERROR",
                  "DynamoDB scan throttled while fetching routes from schedules table",
                  request_id=request_id,
                  table="TicketBuddy_Schedules",
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=503)
        return {
            "statusCode": 503,
            "status": "error",
            "message": "Service temporarily unavailable"
        }

    except dynamo.meta.client.exceptions.ResourceNotFoundException as e:
        log_event("ERROR", "DYNAMODB_TABLE_NOT_FOUND",
                  "Schedules table not found — check table name and region",
                  request_id=request_id,
                  table="TicketBuddy_Schedules",
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=500)
        return {
            "statusCode": 500,
            "status": "error",
            "message": "Internal configuration error"
        }

    except Exception as e:
        log_event("ERROR", "SCHEDULE_FETCH_ERROR",
                  "Unexpected error fetching routes from schedules table",
                  request_id=request_id,
                  table="TicketBuddy_Schedules",
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=500)
        return {
            "statusCode": 500,
            "status": "error",
            "message": "Failed to fetch routes"
        }

    # Validate that routes exist before generating seats
    if not routes:
        log_event("WARNING", "NO_ROUTES_FOUND",
                  "Schedules table scan returned no routes — no seats will be generated",
                  request_id=request_id,
                  table="TicketBuddy_Schedules",
                  http_status=200)
        return {
            "statusCode": 200,
            "status": "success",
            "message": "No routes found — no seats generated",
            "total_seats": 0
        }

    log_event("INFO", "ROUTES_FETCHED",
              "Routes fetched successfully from schedules table",
              request_id=request_id,
              routes_count=len(routes))

    # Build batch write request list
    client   = boto3.client("dynamodb")
    requests = []

    try:
        for r in routes:
            route_id = r.get("route_id")

            if not route_id:
                log_event("WARNING", "MISSING_ROUTE_ID",
                          "Route record is missing route_id field — skipping",
                          request_id=request_id,
                          route_record=str(r))
                continue

            for row in ROWS:
                for n in range(1, COUNT + 1):
                    seat_no = f"{row}{n}"
                    requests.append({
                        "PutRequest": {
                            "Item": {
                                "route_id": {"S": route_id},
                                "seat_no":  {"S": seat_no},
                                "is_booked": {"BOOL": False}
                            }
                        }
                    })
    except Exception as e:
        log_event("ERROR", "SEAT_BUILD_ERROR",
                  "Unexpected error while building seat request list",
                  request_id=request_id,
                  routes_count=len(routes),
                  exception=str(e),
                  trace=traceback.format_exc(),
                  http_status=500)
        return {
            "statusCode": 500,
            "status": "error",
            "message": "Failed to build seat list"
        }

    log_event("INFO", "SEAT_BATCH_STARTED",
              "Starting batch write of seats to DynamoDB",
              request_id=request_id,
              total_seats=len(requests),
              total_chunks=len(requests) // 25 + (1 if len(requests) % 25 else 0))

    # Batch write in chunks of 25
    chunks_written  = 0
    chunks_failed   = 0

    for chunk in _chunks(requests, 25):
        try:
            client.batch_write_item(
                RequestItems={
                    "TicketBuddy_Seats": chunk
                }
            )
            chunks_written += 1
        except client.exceptions.ProvisionedThroughputExceededException as e:
            chunks_failed += 1
            log_event("ERROR", "BATCH_WRITE_THROTTLE_ERROR",
                      "Batch write throttled — provisioned throughput exceeded",
                      request_id=request_id,
                      chunk_index=chunks_written + chunks_failed,
                      chunk_size=len(chunk),
                      exception=str(e),
                      trace=traceback.format_exc(),
                      http_status=503)

        except client.exceptions.ResourceNotFoundException as e:
            chunks_failed += 1
            log_event("ERROR", "DYNAMODB_TABLE_NOT_FOUND",
                      "Seats table not found during batch write — check table name and region",
                      request_id=request_id,
                      table="TicketBuddy_Seats",
                      chunk_index=chunks_written + chunks_failed,
                      exception=str(e),
                      trace=traceback.format_exc(),
                      http_status=500)

        except Exception as e:
            chunks_failed += 1
            log_event("ERROR", "BATCH_WRITE_ERROR",
                      "Unexpected error during batch write chunk",
                      request_id=request_id,
                      chunk_index=chunks_written + chunks_failed,
                      chunk_size=len(chunk),
                      exception=str(e),
                      trace=traceback.format_exc(),
                      http_status=500)

    # Report partial failure if any chunks failed
    if chunks_failed > 0 and chunks_written == 0:
        log_event("ERROR", "SEAT_GENERATION_FAILED",
                  "All batch write chunks failed — no seats were written",
                  request_id=request_id,
                  chunks_failed=chunks_failed,
                  total_seats=len(requests),
                  http_status=500)
        return {
            "statusCode": 500,
            "status": "error",
            "message": "Seat generation failed — no seats were written"
        }

    if chunks_failed > 0:
        log_event("WARNING", "SEAT_GENERATION_PARTIAL",
                  "Seat generation completed with some batch write failures",
                  request_id=request_id,
                  chunks_written=chunks_written,
                  chunks_failed=chunks_failed,
                  total_seats=len(requests),
                  http_status=207)
        return {
            "statusCode": 207,
            "status": "partial_success",
            "message": f"Seat generation completed with {chunks_failed} failed chunks",
            "chunks_written": chunks_written,
            "chunks_failed": chunks_failed,
            "total_seats": len(requests)
        }

    # Success
    log_event("INFO", "SEAT_GENERATION_COMPLETE",
              "All seats generated and written successfully",
              request_id=request_id,
              routes_count=len(routes),
              total_seats=len(requests),
              chunks_written=chunks_written,
              http_status=200)

    return {
        "statusCode": 200,
        "status": "success",
        "message": f"Seats generated for {len(routes)} routes",
        "total_seats": len(requests)
    }