from django.shortcuts import render, redirect
from django.contrib import messages
from .cognito_auth import cognito_signup, cognito_confirm, cognito_login
from .cognito_auth import (
    cognito_signup, cognito_confirm, cognito_login,
    cognito_forgot_password, cognito_confirm_new_password
)
import json
import urllib.request
from buddy.utils.pdf_generator import generate_ticket_pdf, upload_ticket_pdf, get_presigned_url
from datetime import datetime
from decimal import Decimal
import os
import boto3


AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

dynamo = boto3.resource("dynamodb", region_name=AWS_REGION)
sns = boto3.client("sns", region_name=AWS_REGION)
tickets_table = dynamo.Table("TicketBuddy_Tickets")
seats_table = dynamo.Table("TicketBuddy_Seats")

SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:943886678149:TicketBuddy_Alerts"

TAX_API_URL  = "https://nxk175t5ol.execute-api.us-east-1.amazonaws.com/prod/tax_calculator"
FARE_API_URL = "https://0v2jl32vw0.execute-api.us-east-1.amazonaws.com/PROD/fare-calculator"


def get_lambda_client():
    return boto3.client("lambda", region_name="us-east-1")


# ── Fare helper ───────────────────────────────────────────────────────────────
def fetch_fare(source, destination):
    """Call the RideReserve fare calculator API and return fare_raw."""
    try:
        payload = json.dumps({"from": source, "to": destination}).encode()
        req = urllib.request.Request(
            FARE_API_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return round(float(data.get("fare_raw", 0)), 2)
    except Exception as e:
        print(f"Fare API error ({source}→{destination}): {e}")
        return None


# ── Tax helper ────────────────────────────────────────────────────────────────
def fetch_tax(price, country_code="IE"):
    """Call the tax calculator API. Returns a safe fallback if the call fails."""
    try:
        payload = json.dumps({"price": round(float(price), 2), "country_code": country_code}).encode()
        req = urllib.request.Request(
            TAX_API_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"Tax API error: {e}")
        return {
            "original_price": float(price),
            "tax_rate": 0,
            "tax_amount": 0.0,
            "final_price": float(price),
            "currency": "EUR"
        }


def send_booking_email(username, subject, message):
    full_message = f"Hello {username},\n\n{message}\n\n— TicketBuddy"
    sns.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=subject,
        Message=full_message
    )


def index(request):
    username = request.session.get("username")
    if not username:
        return redirect("login")
    return render(request, "buddy/index.html", {"username": username})


def register_view(request):
    if request.method == "POST":
        username = request.POST["username"]
        email = request.POST["email"]
        password = request.POST["password"]
        res = cognito_signup(username, email, password)
        if "error" in res:
            messages.error(request, res["error"])
            return redirect("register")
        request.session["pending_username"] = username
        return redirect("confirm")
    return render(request, "buddy/register.html")


def confirm_view(request):
    if request.method == "POST":
        username = request.session.get("pending_username")
        code = request.POST["code"]
        res = cognito_confirm(username, code)
        if "error" in res:
            messages.error(request, res["error"])
            return redirect("confirm")
        messages.success(request, "Account confirmed! Login now.")
        return redirect("login")
    return render(request, "buddy/confirm.html")


def login_view(request):
    if request.method == "POST":
        username = request.POST["username"]
        password = request.POST["password"]
        res = cognito_login(username, password)
        if "error" in res:
            messages.error(request, res["error"])
            return render(request, "buddy/login.html")
        tokens = res["AuthenticationResult"]
        request.session["id_token"] = tokens["IdToken"]
        request.session["access_token"] = tokens["AccessToken"]
        request.session["username"] = username
        return redirect("index")
    return render(request, "buddy/login.html")


def logout_view(request):
    request.session.flush()
    return redirect("login")


def forgot_password_view(request):
    if request.method == "POST":
        username = request.POST["username"]
        res = cognito_forgot_password(username)
        if "error" in res:
            messages.error(request, res["error"])
            return redirect("forgot-password")
        request.session["reset_username"] = username
        messages.success(request, "OTP sent to your email.")
        return redirect("reset-password")
    return render(request, "buddy/forgot_password.html")


def reset_password_view(request):
    if request.method == "POST":
        username = request.session.get("reset_username")
        code = request.POST["code"]
        new_password = request.POST["password"]
        res = cognito_confirm_new_password(username, code, new_password)
        if "error" in res:
            messages.error(request, res["error"])
            return redirect("reset-password")
        messages.success(request, "Password reset successful! You can now login.")
        return redirect("login")
    return render(request, "buddy/reset_password.html")


def dashboard(request):
    return render(request, "buddy/index.html")


def book_ticket_page(request):
    lambda_client = get_lambda_client()

    if request.method == "POST":
        seats_str = request.POST.get("selected_seats", "")
        selected_seats = [s for s in seats_str.split(",") if s]
        route = request.POST.get("route") or request.POST.get("route_id")
        ticket_type = request.POST.get("ticket_type")
        is_return = ticket_type == "Return"
        return_date = request.POST.get("return_date")
        departure_date = request.POST.get("departure_date")

        book_payload = {
            "username": request.session.get("username"),
            "from": request.POST.get("from"),
            "to": request.POST.get("to"),
            "passengers": request.POST.get("passengers"),
            "departure_date": departure_date,
            "return_date": return_date,
            "ticket_type": ticket_type,
            "seats": selected_seats,
            "fare": request.POST.get("fare"),
            "route": route,
            "departure_time": request.POST.get("departure_time"),
            "arrival_time": request.POST.get("arrival_time"),
            "is_student": bool(request.POST.get("is_student")),
            "has_refund_protection": bool(request.POST.get("refund_protection")),
        }

        request.session["pending_booking"] = book_payload
        request.session["pending_booking_ts"] = str(__import__("time").time())

        if is_return:
            return redirect(
                f"/return-seat?from={book_payload['to']}"
                f"&to={book_payload['from']}"
                f"&date={book_payload['return_date']}"
                f"&fare={book_payload['fare']}"
                f"&route={book_payload['route']}"
                f"&departure_time={book_payload['departure_time']}"
                f"&arrival_time={book_payload['arrival_time']}"
                f"&total_seats={request.POST.get('total_seats', 4)}"
            )

        return redirect(
            f"/payment?from={book_payload['from']}&to={book_payload['to']}"
            f"&date={book_payload['departure_date']}&fare={book_payload['fare']}"
            f"&route={book_payload['route']}&departure_time={book_payload['departure_time']}"
            f"&arrival_time={book_payload['arrival_time']}"
        )

    prefill = {
        "from":           request.GET.get("from", ""),
        "to":             request.GET.get("to", ""),
        "route":          request.GET.get("route", ""),
        "fare":           request.GET.get("fare", ""),
        "departure_time": request.GET.get("time", ""),
        "arrival_time":   request.GET.get("arrival", ""),
        "date":           request.GET.get("date", ""),
        "return_date":    request.GET.get("return_date", ""),
        "car_type":       request.GET.get("car_type", ""),
        "driver_name":    request.GET.get("driver_name", ""),
        "total_seats":    request.GET.get("total_seats", "4"),
    }

    seats = []
    booked = []
    if prefill.get("route"):
        try:
            seat_resp = lambda_client.invoke(
                FunctionName="TicketBuddy_GetSeatStatus",
                InvocationType="RequestResponse",
                Payload=json.dumps({
                    "route_id":       prefill["route"],
                    "departure_time": prefill["departure_time"]
                })
            )
            seat_result = json.loads(seat_resp["Payload"].read())
            seats  = seat_result.get("seats", [])
            booked = seat_result.get("booked_seats", [])
        except Exception:
            total = int(float(prefill.get("total_seats") or 4))
            seats = [{"seat_id": str(i), "status": "AVAILABLE"} for i in range(1, total + 1)]

    return render(request, "buddy/booking.html", {
        "prefill": prefill,
        "seats":   seats,
        "booked":  booked
    })


def payment_page(request):
    pending_out = request.session.get("pending_booking")
    pending_ret = request.session.get("pending_return_booking")

    if pending_out and pending_out.get("ticket_type") == "One Way":
        request.session.pop("pending_return_booking", None)
        pending_ret = None

    context = {
        "from": "", "to": "", "date": "", "fare": 0, "seats": "",
        "route": "", "departure_time": "", "arrival_time": "",
        "ticket_type": "", "outbound_fare": 0, "return_fare": 0,
        "total_fare": 0, "outbound_seats": "", "return_seats": "",
        "outbound_route": "", "return_route": "",
        "outbound_departure_time": "", "return_departure_time": "",
        "outbound_arrival_time": "", "return_arrival_time": "",
    }

    if pending_out:
        context["from"] = pending_out.get("from")
        context["to"] = pending_out.get("to")
        context["date"] = pending_out.get("departure_date")
        context["outbound_fare"] = float(pending_out.get("fare") or 0)
        context["outbound_seats"] = ", ".join(pending_out.get("seats", []))
        context["outbound_route"] = pending_out.get("route")
        context["outbound_departure_time"] = pending_out.get("departure_time")
        context["outbound_arrival_time"] = pending_out.get("arrival_time")
        context["ticket_type"] = pending_out.get("ticket_type")
        context["fare"] = context["outbound_fare"]

    if pending_ret and pending_out and pending_out.get("ticket_type") == "Return":
        context["return_fare"] = float(pending_ret.get("fare") or 0)
        context["return_seats"] = ", ".join(pending_ret.get("seats", []))
        context["return_route"] = pending_ret.get("route")
        context["return_departure_time"] = pending_ret.get("departure_time")
        context["return_arrival_time"] = pending_ret.get("arrival_time")

    context["total_fare"] = context["outbound_fare"] + context["return_fare"]
    return render(request, "buddy/payment.html", context)


def history_page(request):
    lambda_client = get_lambda_client()
    username = request.session.get("username")

    response = lambda_client.invoke(
        FunctionName="TicketBuddy_GetHistory",
        InvocationType="RequestResponse",
        Payload=json.dumps({"username": username})
    )
    result = json.loads(response['Payload'].read())
    bookings = result.get("bookings", []) if result.get("status") == "success" else []

    def parse_date(d):
        try:
            return datetime.strptime(d, "%Y-%m-%d")
        except:
            return datetime.min

    grouped = {}
    for b in bookings:
        parent_id = b.get("parent_booking_id")
        if parent_id:
            grouped.setdefault(parent_id, {"outbound": None, "returns": []})
            grouped[parent_id]["returns"].append(b)
        else:
            bid = b["booking_id"]
            if bid not in grouped:
                grouped[bid] = {"outbound": b, "returns": []}
            else:
                grouped[bid]["outbound"] = b

    final_list = []
    for parent_id, data in grouped.items():
        outbound = data["outbound"]
        returns = data["returns"]
        if outbound:
            final_list.append({
                "outbound": outbound,
                "returns": sorted(
                    returns,
                    key=lambda x: parse_date(x.get("departure_date", ""))
                )
            })

    final_list = sorted(
        final_list,
        key=lambda x: parse_date(x["outbound"].get("departure_date", "")),
        reverse=True
    )

    for group in final_list:
        outbound = group["outbound"]
        if outbound.get("pdf_url"):
            outbound["pdf_url"] = get_presigned_url(outbound["pdf_url"])
        for r in group["returns"]:
            if r.get("pdf_url"):
                r["pdf_url"] = get_presigned_url(r["pdf_url"])

    return render(request, "buddy/history.html", {"groups": final_list})


def payment_success(request):
    lambda_client = get_lambda_client()
    pending_out = request.session.get("pending_booking")
    pending_ret = request.session.get("pending_return_booking")

    if not pending_out:
        messages.error(request, "Session expired. Please book again.")
        return redirect("book-ticket")

    username = pending_out.get("username") or request.session.get("username")
    outbound_route = pending_out.get("route")
    outbound_seats = pending_out.get("seats", [])

    if not outbound_route or not outbound_seats:
        messages.error(request, "Missing outbound route or seats.")
        return redirect("book-ticket")

    try:
        seat_payload_out = {
            "route_id": outbound_route,
            "departure_time": pending_out.get("departure_time"),
            "seats": outbound_seats
        }
        seat_resp = lambda_client.invoke(
            FunctionName="TicketBuddy_UpdateSeat",
            InvocationType="RequestResponse",
            Payload=json.dumps(seat_payload_out)
        )
        seat_result = json.loads(seat_resp["Payload"].read())

        if seat_result.get("status") != "success":
            messages.error(request, seat_result.get("message"))
            return redirect("book-ticket")

        outbound_seat_booking_id = seat_result.get("booking_id")

    except Exception:
        messages.error(request, "Failed to lock outbound seats.")
        return redirect("book-ticket")

    # ── Apply bulk discount ───────────────────────────────────────────────────
    try:
        from ticketdiscount.discount import apply_bulk_discount
    except Exception as e:
        print("ticketdiscount import failed:", e)
        apply_bulk_discount = None

    seat_count = len(outbound_seats) or 0
    try:
        fare_per_seat = float(pending_out.get("fare", 0)) or 0.0
    except Exception:
        fare_per_seat = 0.0

    total_fare = fare_per_seat * seat_count

    if apply_bulk_discount:
        new_total, discount_amount, applied = apply_bulk_discount(total_fare, seat_count)
    else:
        new_total, discount_amount, applied = total_fare, 0.0, False

    final_per_seat_fare = round(new_total / seat_count, 2) if seat_count > 0 else round(fare_per_seat, 2)

    if applied:
        messages.success(request, f"Bulk discount applied: €{discount_amount:.2f} off total.")

    # ── Call Tax API for outbound ─────────────────────────────────────────────
    outbound_tax = fetch_tax(final_per_seat_fare * seat_count)

    book_payload_out = {
        "username": username,
        "from": pending_out.get("from"),
        "to": pending_out.get("to"),
        "passengers": pending_out.get("passengers"),
        "departure_date": pending_out.get("departure_date"),
        "ticket_type": pending_out.get("ticket_type"),
        "seats": outbound_seats,
        "fare": final_per_seat_fare,
        "tax_rate":    outbound_tax.get("tax_rate", 0),
        "tax_amount":  round(outbound_tax.get("tax_amount", 0.0), 2),
        "final_price": round(outbound_tax.get("final_price", final_per_seat_fare * seat_count), 2),
        "route": outbound_route,
        "departure_time": pending_out.get("departure_time"),
        "arrival_time": pending_out.get("arrival_time"),
    }

    if outbound_seat_booking_id:
        book_payload_out["booking_id"] = outbound_seat_booking_id

    try:
        resp = lambda_client.invoke(
            FunctionName="TicketBuddy_BookTicket",
            InvocationType="RequestResponse",
            Payload=json.dumps(book_payload_out)
        )
        result = json.loads(resp["Payload"].read())

        if result.get("status") != "success":
            messages.error(request, f"Error: {result}")
            return redirect("book-ticket")

    except Exception:
        messages.error(request, "Outbound booking failed.")
        return redirect("book-ticket")

    outbound_item = result["item"]
    outbound_item["tax_rate"]    = book_payload_out["tax_rate"]
    outbound_item["tax_amount"]  = book_payload_out["tax_amount"]
    outbound_item["final_price"] = book_payload_out["final_price"]
    outbound_id = outbound_item["booking_id"]

    try:
        tickets_table.update_item(
            Key={"booking_id": outbound_id},
            UpdateExpression="SET tax_rate = :tr, tax_amount = :ta, final_price = :fp",
            ExpressionAttributeValues={
                ":tr": Decimal(str(book_payload_out["tax_rate"])),
                ":ta": Decimal(str(book_payload_out["tax_amount"])),
                ":fp": Decimal(str(book_payload_out["final_price"])),
            }
        )
    except Exception as e:
        print(f"Failed to save tax fields for outbound: {e}")

    try:
        pdf_buffer = generate_ticket_pdf(outbound_item)
        filename = f"tickets/{outbound_id}.pdf"
        pdf_key = upload_ticket_pdf(pdf_buffer, filename)
        tickets_table.update_item(
            Key={"booking_id": outbound_id},
            UpdateExpression="SET pdf_url = :p",
            ExpressionAttributeValues={":p": pdf_key},
        )
    except Exception:
        pdf_key = ""

    try:
        send_booking_email(
            username,
            "Your TicketBuddy Ticket is Confirmed!",
            f"Booking ID: {outbound_id}\n"
            f"Route: {outbound_item.get('source')} → {outbound_item.get('destination')}\n"
            f"Date: {outbound_item.get('departure_date')}\n"
            f"Seats: {', '.join(outbound_seats)}\n"
            f"Base Fare: €{final_per_seat_fare * seat_count:.2f}\n"
            f"VAT ({book_payload_out['tax_rate']}%): €{book_payload_out['tax_amount']:.2f}\n"
            f"Total incl. VAT: €{book_payload_out['final_price']:.2f}\n\n"
            f"Download your ticket from the TicketBuddy app."
        )
    except Exception:
        pass

    # ── Return booking ────────────────────────────────────────────────────────
    if pending_ret:
        return_seats = pending_ret.get("seats", [])
        return_route = pending_ret.get("route")

        if return_route and return_seats:
            try:
                seat_payload_ret = {
                    "route_id": return_route,
                    "departure_time": pending_ret.get("departure_time"),
                    "seats": return_seats
                }
                seat_resp_ret = lambda_client.invoke(
                    FunctionName="TicketBuddy_UpdateSeat",
                    InvocationType="RequestResponse",
                    Payload=json.dumps(seat_payload_ret)
                )
                seat_result_ret = json.loads(seat_resp_ret["Payload"].read())

                if seat_result_ret.get("status") != "success":
                    messages.error(request, seat_result_ret.get("message"))
                    return redirect("history")

                return_seat_booking_id = seat_result_ret.get("booking_id")

            except Exception:
                messages.error(request, "Failed to lock return seats.")
                return redirect("history")

            try:
                return_fare_raw = float(pending_ret.get("fare", 0)) * len(return_seats)
            except Exception:
                return_fare_raw = 0.0

            return_tax = fetch_tax(return_fare_raw)

            book_payload_ret = {
                "username": username,
                "from": pending_ret.get("from"),
                "to": pending_ret.get("to"),
                "passengers": pending_ret.get("passengers"),
                "departure_date": pending_ret.get("departure_date"),
                "ticket_type": "Return",
                "seats": return_seats,
                "fare": pending_ret.get("fare"),
                "tax_rate":    return_tax.get("tax_rate", 0),
                "tax_amount":  round(return_tax.get("tax_amount", 0.0), 2),
                "final_price": round(return_tax.get("final_price", return_fare_raw), 2),
                "route": return_route,
                "departure_time": pending_ret.get("departure_time"),
                "arrival_time": pending_ret.get("arrival_time"),
                "parent_booking_id": outbound_id
            }

            if return_seat_booking_id:
                book_payload_ret["booking_id"] = return_seat_booking_id

            try:
                resp_ret = lambda_client.invoke(
                    FunctionName="TicketBuddy_BookTicket",
                    InvocationType="RequestResponse",
                    Payload=json.dumps(book_payload_ret)
                )
                result_ret = json.loads(resp_ret["Payload"].read())
            except Exception:
                result_ret = {}

            if result_ret.get("status") == "success":
                return_item = result_ret["item"]
                return_id = return_item["booking_id"]

                return_item["tax_rate"]    = book_payload_ret["tax_rate"]
                return_item["tax_amount"]  = book_payload_ret["tax_amount"]
                return_item["final_price"] = book_payload_ret["final_price"]

                try:
                    tickets_table.update_item(
                        Key={"booking_id": return_id},
                        UpdateExpression="SET tax_rate = :tr, tax_amount = :ta, final_price = :fp",
                        ExpressionAttributeValues={
                            ":tr": Decimal(str(book_payload_ret["tax_rate"])),
                            ":ta": Decimal(str(book_payload_ret["tax_amount"])),
                            ":fp": Decimal(str(book_payload_ret["final_price"])),
                        }
                    )
                except Exception as e:
                    print(f"Failed to save tax fields for return: {e}")

                try:
                    pdf_buffer_ret = generate_ticket_pdf(return_item)
                    filename_ret = f"tickets/{return_id}.pdf"
                    pdf_key_ret = upload_ticket_pdf(pdf_buffer_ret, filename_ret)
                    tickets_table.update_item(
                        Key={"booking_id": return_id},
                        UpdateExpression="SET pdf_url = :p",
                        ExpressionAttributeValues={":p": pdf_key_ret},
                    )
                except Exception:
                    pdf_key_ret = ""

                try:
                    send_booking_email(
                        username,
                        "Your RETURN Ticket is Confirmed!",
                        f"Return ID: {return_id}\nOutbound: {outbound_id}\n"
                        f"Total incl. VAT: €{book_payload_ret['final_price']:.2f}\n\n"
                        f"Download your ticket from the TicketBuddy app."
                    )
                except Exception:
                    pass

    request.session.pop("pending_booking", None)
    request.session.pop("pending_return_booking", None)

    messages.success(request, "Payment complete! Ticket(s) booked successfully.")
    return redirect("history")


def alerts_page(request):
    return render(request, "buddy/alerts.html")


def profile_view(request):
    return render(request, "buddy/profile.html")


def cancel_ticket(request, booking_id):
    lambda_client = get_lambda_client()
    response = lambda_client.invoke(
        FunctionName="TicketBuddy_CancelTicket",
        InvocationType="RequestResponse",
        Payload=json.dumps({"booking_id": booking_id})
    )
    result = json.loads(response["Payload"].read())
    if result.get("status") != "success":
        messages.error(request, "Failed to cancel ticket.")
        return redirect("history")
    messages.success(request, "Ticket cancelled successfully.")
    return redirect("history")


def schedules_page(request):
    lambda_client = get_lambda_client()
    schedules = []
    date = request.GET.get("date", "")
    return_date = request.GET.get("return_date", "")

    if request.method == "POST":
        source      = request.POST.get("from")
        destination = request.POST.get("to")
        date        = request.POST.get("date")
        return_date = request.POST.get("return_date", "")

        response = lambda_client.invoke(
            FunctionName="TicketBuddy_GetSchedules",
            InvocationType="RequestResponse",
            Payload=json.dumps({"from": source, "to": destination})
        )
        result = json.loads(response["Payload"].read())
        schedules = json.loads(result.get("body", "[]"))

        # Override fare on every schedule with live API fare
        fare_cache = {}
        for s in schedules:
            src  = s.get("source", "")
            dest = s.get("destination", "")
            key  = (src, dest)
            if key not in fare_cache:
                fare_cache[key] = fetch_fare(src, dest)
            live_fare = fare_cache[key]
            if live_fare is not None:
                s["fare"] = live_fare

    return render(request, "buddy/schedules.html", {
        "schedules":   schedules,
        "date":        date,
        "return_date": return_date,
    })


def select_seat_page(request):
    lambda_client = get_lambda_client()
    route_id = request.GET.get("route")
    response = lambda_client.invoke(
        FunctionName="TicketBuddy_GetSeats",
        InvocationType="RequestResponse",
        Payload=json.dumps({"route_id": route_id})
    )
    result = json.loads(response["Payload"].read())
    seats = result.get("seats", [])
    seats = [s for s in seats if s.get("seat_no") not in ["A1", "Seat 1"]]
    return render(request, "buddy/select_seat.html", {
        "route_id": route_id,
        "seats": seats
    })


def destinations_page(request):
    lambda_client = get_lambda_client()

    response = lambda_client.invoke(
        FunctionName="TicketBuddy_GetSchedules",
        InvocationType="RequestResponse",
        Payload=json.dumps({})
    )
    result = json.loads(response["Payload"].read())
    schedules = json.loads(result.get("body", "[]"))

    fare_cache = {}
    for s in schedules:
        src  = s.get("source", "")
        dest = s.get("destination", "")
        key  = (src, dest)
        if key not in fare_cache:
            fare_cache[key] = fetch_fare(src, dest)
        live_fare = fare_cache[key]
        if live_fare is not None:
            s["fare"] = live_fare

    city_coords = {
        "Dublin":    {"lat": 53.3498, "lng": -6.2603},
        "Cork":      {"lat": 51.8985, "lng": -8.4756},
        "Galway":    {"lat": 53.2707, "lng": -9.0568},
        "Limerick":  {"lat": 52.6638, "lng": -8.6267},
        "Waterford": {"lat": 52.2593, "lng": -7.1101},
        "Belfast":   {"lat": 54.5973, "lng": -5.9301},
    }

    return render(request, "buddy/destinations.html", {
        "schedules": schedules,
        "city_coords_json": json.dumps(city_coords),
    })


def contact_page(request):
    return render(request, "buddy/contact.html")


def return_seat_page(request):
    if request.method == "POST":
        seats_str = request.POST.get("selected_seats", "")
        seats = [s for s in seats_str.split(",") if s]

        route = request.POST.get("route")
        fare = float(request.POST.get("fare") or 0)
        return_date = request.POST.get("return_date")
        outbound_id = request.POST.get("outbound_id")
        username = request.session.get("username")

        return_payload = {
            "username": username,
            "from": request.POST.get("from"),
            "to": request.POST.get("to"),
            "passengers": int(request.POST.get("passengers") or len(seats) or 1),
            "departure_date": return_date,
            "ticket_type": "Return",
            "seats": seats,
            "fare": fare,
            "route": route,
            "departure_time": request.POST.get("departure_time"),
            "arrival_time": request.POST.get("arrival_time"),
            "parent_outbound_temp": outbound_id or ""
        }

        request.session["pending_return_booking"] = return_payload
        request.session["pending_return_booking_ts"] = str(__import__("time").time())
        return redirect("/payment")

    prefill = {
        "from": request.GET.get("from", ""),
        "to": request.GET.get("to", ""),
        "fare": request.GET.get("fare", ""),
        "route": request.GET.get("route", ""),
        "departure_time": request.GET.get("departure_time", ""),
        "arrival_time": request.GET.get("arrival_time", ""),
        "return_date": request.GET.get("date", ""),
        "outbound_id": request.GET.get("outbound_id", ""),
    }

    seats = []
    booked = []
    if prefill["route"]:
        try:
            lambda_client = get_lambda_client()
            seat_resp = lambda_client.invoke(
                FunctionName="TicketBuddy_GetSeatStatus",
                InvocationType="RequestResponse",
                Payload=json.dumps({
                    "route_id":       prefill["route"],
                    "departure_time": prefill["departure_time"]
                })
            )
            seat_result = json.loads(seat_resp["Payload"].read())
            seats  = seat_result.get("seats", [])
            booked = seat_result.get("booked_seats", [])
        except Exception:
            total = int(float(request.GET.get("total_seats", 4)))
            seats = [{"seat_id": str(i), "status": "AVAILABLE"} for i in range(1, total + 1)]

    return render(request, "buddy/return_seat.html", {
        "prefill": prefill,
        "seats":   seats,
        "booked":  booked
    })


def fare_calculator_api(request):
    import math
    from django.http import JsonResponse

    CITY_COORDS = {
        "Dublin":    {"lat": 53.3498, "lng": -6.2603},
        "Cork":      {"lat": 51.8985, "lng": -8.4756},
        "Galway":    {"lat": 53.2707, "lng": -9.0568},
        "Limerick":  {"lat": 52.6638, "lng": -8.6267},
        "Waterford": {"lat": 52.2593, "lng": -7.1101},
        "Belfast":   {"lat": 54.5973, "lng": -5.9301},
    }

    RATE_PER_KM = 0.10
    source      = request.GET.get("from", "").strip().title()
    destination = request.GET.get("to", "").strip().title()

    if not source or not destination:
        return JsonResponse({"error": "Missing 'from' or 'to' parameter"}, status=400)
    if source not in CITY_COORDS:
        return JsonResponse({"error": f"City '{source}' not supported", "supported_cities": list(CITY_COORDS.keys())}, status=400)
    if destination not in CITY_COORDS:
        return JsonResponse({"error": f"City '{destination}' not supported", "supported_cities": list(CITY_COORDS.keys())}, status=400)
    if source == destination:
        return JsonResponse({"error": "Source and destination cannot be the same"}, status=400)

    def haversine(lat1, lng1, lat2, lng2):
        R = 6371
        d_lat = math.radians(lat2 - lat1)
        d_lng = math.radians(lng2 - lng1)
        a = (math.sin(d_lat/2)**2 +
             math.cos(math.radians(lat1)) *
             math.cos(math.radians(lat2)) *
             math.sin(d_lng/2)**2)
        return round(R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a)), 2)

    c1 = CITY_COORDS[source]
    c2 = CITY_COORDS[destination]
    distance_km = haversine(c1["lat"], c1["lng"], c2["lat"], c2["lng"])
    fare        = round(distance_km * RATE_PER_KM, 2)
    total_mins  = int((distance_km / 100) * 60)
    duration    = f"{total_mins//60}h {total_mins%60}mins" if total_mins >= 60 else f"{total_mins}mins"

    return JsonResponse({
        "from":              source,
        "to":                destination,
        "distance_km":       distance_km,
        "duration_estimate": duration,
        "rate_per_km":       f"€{RATE_PER_KM}",
        "delivery_fee":      f"€{fare}",
        "fare_raw":          fare,
        "powered_by":        "RideReserve Distance & Fare Calculator API"
    })