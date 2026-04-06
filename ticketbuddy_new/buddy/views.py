from django.shortcuts import render, redirect
from django.contrib import messages
from .cognito_auth import cognito_signup, cognito_confirm, cognito_login
from django.contrib import messages
import json
from .fares import FARES
from .schedules import SCHEDULES
from buddy.utils.pdf_generator import generate_ticket_pdf, upload_ticket_pdf
from datetime import datetime
from decimal import Decimal
import os
import boto3
from ticketdiscount.discount import apply_bulk_discount
from django.contrib.auth.decorators import login_required


AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Initialize DynamoDB and SNS 
dynamo = boto3.resource("dynamodb", region_name=AWS_REGION)
sns = boto3.client("sns", region_name=AWS_REGION)
tickets_table = dynamo.Table("TicketBuddy_Tickets")
seats_table = dynamo.Table("TicketBuddy_Seats")

SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:943886678149:TicketBuddy_Alerts"

# Helper function to get Lambda client
def get_lambda_client():
    """Returns a boto3 Lambda client with proper region configuration"""
    return boto3.client("lambda", region_name="us-east-1")

def send_booking_email(username, subject, message):
    full_message = f"Hello {username},\n\n{message}\n\n— TicketBuddy"
    
    sns.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=subject,
        Message=full_message
    )


# lambda_client = boto3.client("lambda", region_name="us-east-1")


def lambda_handler(event, context):
    try:
        booking_id = event.get("booking_id")
        if not booking_id:
            return {"status": "error", "message": "Missing booking_id"}

        #  Fetch ticket
        resp = tickets_table.get_item(Key={"booking_id": booking_id})
        ticket = resp.get("Item")

        if not ticket:
            return {"status": "error", "message": "Booking not found"}

        route = ticket.get("route")
        dep_time = ticket.get("departure_time")
        seats = ticket.get("seats", [])
        username = ticket.get("username")
        pdf_url = ticket.get("pdf_url", "")
        source = ticket.get("source")
        destination = ticket.get("destination")
        date = ticket.get("departure_date")

        #  Release seats
        if route and dep_time and seats:
            for seat in seats:
                composite_key = f"{dep_time}#{seat}"

                seats_table.update_item(
                    Key={
                        "route_id": route,
                        "departure_time_seat": composite_key
                    },
                    UpdateExpression="SET #s = :a",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":a": "AVAILABLE"}
                )

        #  Mark ticket cancelled
        tickets_table.update_item(
            Key={"booking_id": booking_id},
            UpdateExpression="SET #s = :c",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":c": "CANCELLED"}
        )
        
        #  Save refund details
        from decimal import Decimal
        tickets_table.update_item(
            Key={"booking_id": booking_id},
            UpdateExpression="SET refund_amount = :r, refund_reason = :rr",
            ExpressionAttributeValues={
                ":r": Decimal(str(refund_amount)),   # Convert float → Decimal
                ":rr": refund_reason
            }
        )

        #  Send Cancellation Email
        message = (
            f"Your TicketBuddy booking has been CANCELLED.\n\n"
            f"Booking ID: {booking_id}\n"
            f"Route: {source} → {destination}\n"
            f"Date: {date}\n"
            f"Seats: {', '.join(seats)}\n"
            f"Status: CANCELLED\n\n"
            f"Ticket PDF (Cancelled Copy):\n{pdf_url}"
        )

        sns.publish(
            TopicArn=TOPIC_ARN,
            Subject="TicketBuddy – Ticket Cancelled",
            Message=message
        )

        return {"status": "success"}

    except Exception as e:
        return {"status": "error", "message": str(e)}


from .cognito_auth import (
    cognito_signup, cognito_confirm, cognito_login,
    cognito_forgot_password, cognito_confirm_new_password
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
            return render(request, "buddy/login.html")  # <-- FIXED

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

       
        # Read POST Data
       
        seats_str = request.POST.get("selected_seats", "")
        selected_seats = [s for s in seats_str.split(",") if s]

        route = request.POST.get("route") or request.POST.get("route_id")

        ticket_type = request.POST.get("ticket_type")        # "One Way" / "Return"
        is_return = ticket_type == "Return"

        return_date = request.POST.get("return_date")        # return date
        departure_date = request.POST.get("departure_date")  # outbound date

        
        #  Create Outbound Ticket
 
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
        
        # Save pending outbound booking in session
        request.session["pending_booking"] = book_payload
        request.session["pending_booking_ts"] = str(__import__("time").time())
        
        # If user selected Return, redirect to return-seat page to choose return seats
        if is_return:
            # pass params for return selection prefill (swap from/to)
            return redirect(
                f"/return-seat?from={book_payload['to']}"
                f"&to={book_payload['from']}"
                f"&date={book_payload['return_date']}"
                f"&fare={book_payload['fare']}"
                f"&route={book_payload['route']}"
                f"&departure_time={book_payload['departure_time']}"
                f"&arrival_time={book_payload['arrival_time']}"
            )
        
        # Otherwise One Way -> go directly to payment
        return redirect(
            f"/payment?from={book_payload['from']}&to={book_payload['to']}"
            f"&date={book_payload['departure_date']}&fare={book_payload['fare']}"
            f"&route={book_payload['route']}&departure_time={book_payload['departure_time']}"
            f"&arrival_time={book_payload['arrival_time']}"
        )

    
    # GET → show booking page
  
    prefill = {
        "from": request.GET.get("from", ""),
        "to": request.GET.get("to", ""),
        "route": request.GET.get("route", ""),
        "fare": request.GET.get("fare", ""),
        "departure_time": request.GET.get("time", ""),
        "arrival_time": request.GET.get("arrival", ""),
        "date": request.GET.get("date", ""),
        "return_date": request.GET.get("return_date", ""),
    }

    
    # Get booked seats for this route
    
    booked = []
    if prefill.get("route"):
        try:
            seat_resp = lambda_client.invoke(
                FunctionName="TicketBuddy_GetSeatStatus",
                InvocationType="RequestResponse",
                Payload=json.dumps({
                    "route_id": prefill["route"],
                    "departure_time": prefill["departure_time"]
                })
            )
            seat_result = json.loads(seat_resp["Payload"].read())

            if seat_result.get("status") == "success":
                booked = seat_result.get("booked_seats", [])

        except Exception:
            booked = []

    return render(request, "buddy/booking.html", {"prefill": prefill, "booked": booked})

def payment_page(request):
    
    # Attempt to fetch pending booking from session

    pending_out = request.session.get("pending_booking")
    pending_ret = request.session.get("pending_return_booking")
    
    context = {
        "from": "",
        "to": "",
        "date": "",
        "fare": 0,
        "seats": "",
        "route": "",
        "departure_time": "",
        "arrival_time": "",
        "ticket_type": "",
        "outbound_fare": 0,
        "return_fare": 0,
        "total_fare": 0,
        "outbound_seats": "",
        "return_seats": "",
        "outbound_route": "",
        "return_route": "",
        "outbound_departure_time": "",
        "return_departure_time": "",
        "outbound_arrival_time": "",
        "return_arrival_time": "",
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

    # If we have a pending return booking, include it
    if pending_ret:
        context["return_fare"] = float(pending_ret.get("fare") or 0)
        context["return_seats"] = ", ".join(pending_ret.get("seats", []))
        context["return_route"] = pending_ret.get("route")
        context["return_departure_time"] = pending_ret.get("departure_time")
        context["return_arrival_time"] = pending_ret.get("arrival_time")

    context["total_fare"] = context["outbound_fare"] + context["return_fare"]
    return render(request, "buddy/payment.html", context)

def history_page(request):
    lambda_client = get_lambda_client()  # ADDED
    username = request.session.get("username")

    # get bookings via Lambda
    response = lambda_client.invoke(
        FunctionName="TicketBuddy_GetHistory",
        InvocationType="RequestResponse",
        Payload=json.dumps({"username": username})
    )
    result = json.loads(response['Payload'].read())
    bookings = result.get("bookings", []) if result.get("status") == "success" else []

    # Convert dates to sortable form
 
    from datetime import datetime

    def parse_date(d):
        try:
            return datetime.strptime(d, "%Y-%m-%d")
        except:
            return datetime.min

    
    # Group: outbound + return
   
    grouped = {}

    for b in bookings:
        parent_id = b.get("parent_booking_id")

        if parent_id:
            # this is return ticket → attach to parent
            grouped.setdefault(parent_id, {"outbound": None, "returns": []})
            grouped[parent_id]["returns"].append(b)
        else:
            # this is outbound ticket
            grouped.setdefault(b["booking_id"], {"outbound": b, "returns": []})

   
    # Convert grouped data into sorted list
 
    final_list = []

    for parent_id, data in grouped.items():
        outbound = data["outbound"]
        returns = data["returns"]

        if outbound:
            final_list.append({
                "outbound": outbound,
                "returns": sorted(
                    returns,
                    key=lambda x: parse_date(x.get("departure_date", ""))  # sort return by date
                )
            })

    # sort all outbound groups by outbound date DESC
    final_list = sorted(
        final_list,
        key=lambda x: parse_date(x["outbound"].get("departure_date", "")),
        reverse=True
    )

    return render(request, "buddy/history.html", {"groups": final_list})


def payment_success(request):
    lambda_client = get_lambda_client() 
    pending_out = request.session.get("pending_booking")
    pending_ret = request.session.get("pending_return_booking")

    if not pending_out:
        messages.error(request, "Session expired. Please book again.")
        return redirect("book-ticket")

    # Extract outbound details
    username = pending_out.get("username") or request.session.get("username")
    outbound_route = pending_out.get("route")
    outbound_seats = pending_out.get("seats", [])

    if not outbound_route or not outbound_seats:
        messages.error(request, "Missing outbound route or seats.")
        return redirect("book-ticket")

    #  Lock outbound seats
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

    #  PREPARE BOOK OUTBOUND PAYLOAD
    book_payload_out = {
        "username": username,
        "from": pending_out.get("from"),
        "to": pending_out.get("to"),
        "passengers": pending_out.get("passengers"),
        "departure_date": pending_out.get("departure_date"),
        "ticket_type": pending_out.get("ticket_type"),
        "seats": outbound_seats,
        # we'll overwrite 'fare' below with discounted per-seat fare
        "fare": pending_out.get("fare"),
        "route": outbound_route,
        "departure_time": pending_out.get("departure_time"),
        "arrival_time": pending_out.get("arrival_time"),
    }

    if outbound_seat_booking_id:
        book_payload_out["booking_id"] = outbound_seat_booking_id


    # APPLY DISCOUNT USING PYPI LIBRARY 

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
    if seat_count > 0:
        final_per_seat_fare = round(new_total / seat_count, 2)
    else:
        final_per_seat_fare = round(fare_per_seat, 2)
    book_payload_out["fare"] = final_per_seat_fare

    if applied:
        messages.success(request, f"Bulk discount applied: €{discount_amount:.2f} off total.")
    else:
        pass

    #  Book outbound ticket (call Lambda)
    try:
        resp = lambda_client.invoke(
            FunctionName="TicketBuddy_BookTicket",
            InvocationType="RequestResponse",
            Payload=json.dumps(book_payload_out)
        )
        result = json.loads(resp["Payload"].read())

        if result.get("status") != "success":
            messages.error(request, "Failed to book outbound ticket.")
            return redirect("book-ticket")

    except Exception:
        messages.error(request, "Outbound booking failed.")
        return redirect("book-ticket")

    outbound_item = result["item"]
    outbound_id = outbound_item["booking_id"]
    
    #  Generate outbound PDF
    try:
        pdf_buffer = generate_ticket_pdf(outbound_item)
        filename = f"tickets/{outbound_id}.pdf"
        pdf_url = upload_ticket_pdf(pdf_buffer, filename)

        tickets_table.update_item(
            Key={"booking_id": outbound_id},
            UpdateExpression="SET pdf_url = :p",
            ExpressionAttributeValues={":p": pdf_url},
        )
    except Exception:
        pdf_url = ""
        
    # Send outbound email
    try:
        send_booking_email(
            username,
            "Your TicketBuddy Ticket is Confirmed!",
            f"Booking ID: {outbound_id}\n"
            f"Route: {outbound_item.get('source')} → {outbound_item.get('destination')}\n"
            f"Date: {outbound_item.get('departure_date')}\n"
            f"Seats: {', '.join(outbound_seats)}\n"
            f"Fare: €{outbound_item.get('fare')}\n\n{pdf_url}"
        )
    except:
        pass

    
    # (E) Handle RETURN booking if exists
    
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

            except:
                messages.error(request, "Failed to lock return seats.")
                return redirect("history")

            # Book return ticket
            book_payload_ret = {
                "username": username,
                "from": pending_ret.get("from"),
                "to": pending_ret.get("to"),
                "passengers": pending_ret.get("passengers"),
                "departure_date": pending_ret.get("departure_date"),
                "ticket_type": "Return",
                "seats": return_seats,
                "fare": pending_ret.get("fare"),
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
            except:
                result_ret = {}

            if result_ret.get("status") == "success":
                return_item = result_ret["item"]
                return_id = return_item["booking_id"]

                # PDF
                try:
                    pdf_buffer_ret = generate_ticket_pdf(return_item)
                    filename_ret = f"tickets/{return_id}.pdf"
                    pdf_url_ret = upload_ticket_pdf(pdf_buffer_ret, filename_ret)

                    tickets_table.update_item(
                        Key={"booking_id": return_id},
                        UpdateExpression="SET pdf_url = :p",
                        ExpressionAttributeValues={":p": pdf_url_ret},
                    )
                except:
                    pdf_url_ret = ""

                # Email
                try:
                    send_booking_email(
                        username,
                        "Your RETURN Ticket is Confirmed!",
                        f"Return ID: {return_id}\nOutbound: {outbound_id}\n\n"
                        f"{pdf_url_ret}"
                    )
                except:
                    pass

    # Clear session
    request.session.pop("pending_booking", None)
    request.session.pop("pending_return_booking", None)

    messages.success(request, "Payment complete! Ticket(s) booked successfully.")
    return redirect("history")

def alerts_page(request):
    return render(request, "buddy/alerts.html")

def profile_view(request):
    return render(request, "buddy/profile.html")
    
def cancel_ticket(request, booking_id):
    """
    Cancels the ticket using Lambda + computes refund using refundengine.
    Stores refund details in DynamoDB and shows message to user.
    """
    lambda_client = get_lambda_client()  # ADDED

    #  Call your existing Lambda to cancel the ticket (release seats)
    data = {"booking_id": booking_id}

    response = lambda_client.invoke(
        FunctionName="TicketBuddy_CancelTicket",
        InvocationType="RequestResponse",
        Payload=json.dumps(data)
    )
    result = json.loads(response["Payload"].read())

    if result.get("status") != "success":
        messages.error(request, "Failed to cancel ticket.")
        return redirect("history")

    #  Fetch ticket from DynamoDB to calculate refund
    resp = tickets_table.get_item(Key={"booking_id": booking_id})
    ticket = resp.get("Item")

    if not ticket:
        messages.error(request, "Ticket not found for refund processing.")
        return redirect("history")

    
    #  Mark ticket CANCELLED
    tickets_table.update_item(
        Key={"booking_id": booking_id},
        UpdateExpression="SET #s = :c",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":c": "CANCELLED"}
    )

    #  Show success (NO refund message)
    messages.success(request, "Ticket cancelled successfully.")

    return redirect("history")



def schedules_page(request):
    lambda_client = get_lambda_client()  
    schedules = []
    date = request.GET.get("date", "")
    return_date = ""

    if request.method == "POST":
        source = request.POST.get("from")
        destination = request.POST.get("to")
        date = request.POST.get("date") 
        

        payload = {
            "from": source,
            "to": destination
        }

        response = lambda_client.invoke(
            FunctionName="TicketBuddy_GetSchedules",
            InvocationType="RequestResponse",
            Payload=json.dumps(payload)
        )

        result = json.loads(response['Payload'].read())

        # Lambda returns array directly inside body
        schedules = json.loads(result.get("body", "[]"))

    return render(request, "buddy/schedules.html", {"schedules": schedules,"date": date,"return_date": return_date})



def select_seat_page(request):
    lambda_client = get_lambda_client() 
    route_id = request.GET.get("route")

    # Call Lambda to fetch seats
    payload = {"route_id": route_id}

    response = lambda_client.invoke(
        FunctionName="TicketBuddy_GetSeats",
        InvocationType="RequestResponse",
        Payload=json.dumps(payload)
    )

    result = json.loads(response["Payload"].read())

    seats = result.get("seats", [])

    return render(request, "buddy/select_seat.html", {
        "route_id": route_id,
        "seats": seats
    })
    
    
def destinations_page(request):
    lambda_client = get_lambda_client()  
    # Call Lambda without any filters → fetch ALL routes
    payload = {}

    response = lambda_client.invoke(
        FunctionName="TicketBuddy_GetSchedules",
        InvocationType="RequestResponse",
        Payload=json.dumps(payload)
    )

    result = json.loads(response["Payload"].read())

    # Lambda returns { statusCode, body }
    schedules = json.loads(result.get("body", "[]"))

    return render(request, "buddy/destinations.html", {"schedules": schedules})


def contact_page(request):
    return render(request, "buddy/contact.html")

def return_seat_page(request):


    lambda_client = get_lambda_client()
    # POST → save pending return booking and redirect to payment
    
    if request.method == "POST":
        seats_str = request.POST.get("selected_seats", "")
        seats = [s for s in seats_str.split(",") if s]

        route = request.POST.get("route")
        fare = float(request.POST.get("fare") or 0)
        return_date = request.POST.get("return_date")
        outbound_id = request.POST.get("outbound_id")  # may be empty because outbound not booked yet

        username = request.session.get("username")

        # Build pending return payload (do NOT lock/book here)
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
            # outbound linkage will be applied during payment_success using outbound id
            "parent_outbound_temp": outbound_id or ""
        }

        # Save pending return in session
        request.session["pending_return_booking"] = return_payload
        request.session["pending_return_booking_ts"] = str(__import__("time").time())

        # Redirect to payment where both fares will be shown
        return redirect("/payment")

   
    # GET  Seat selection page (prefill from query params)
 
    prefill = {
        "from": request.GET.get("from", ""),
        "to": request.GET.get("to", ""),
        "fare": request.GET.get("fare", ""),
        "route": request.GET.get("route", ""),
        "departure_time": request.GET.get("departure_time", ""),
        "arrival_time": request.GET.get("arrival_time", ""),
        "return_date": request.GET.get("date", ""),        # FIXED
        "outbound_id": request.GET.get("outbound_id", ""),
    }

    # Fetch already booked seats for this return route/time
    booked = []
    if prefill["route"]:
        try:
            seat_resp = lambda_client.invoke(
                FunctionName="TicketBuddy_GetSeatStatus",
                InvocationType="RequestResponse",
                Payload=json.dumps({
                    "route_id": prefill["route"],
                    "departure_time": prefill["departure_time"]
                })
            )
            seat_result = json.loads(seat_resp["Payload"].read())
            booked = seat_result.get("booked_seats", [])
        except:
            booked = []

    return render(request, "buddy/return_seat.html", {
        "prefill": prefill,
        "booked": booked
    })
import json
import boto3
from django.http import JsonResponse, HttpResponseServerError

BUCKET = "ticketbuddy-tickets-943886678148"
KEY    = "analytics/dashboard.json"


@login_required(login_url='/login/')
def analytics_page(request):
    """Render the analytics dashboard page."""
    return render(request, "buddy/analytics.html")


@login_required(login_url='/login/')
def analytics_data(request):
    try:
        s3   = boto3.client("s3")
        obj  = s3.get_object(Bucket=BUCKET, Key=KEY)
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return JsonResponse(data)
    except Exception as e:
        return HttpResponseServerError(
            json.dumps({"error": str(e)}),
            content_type="application/json"
        )
