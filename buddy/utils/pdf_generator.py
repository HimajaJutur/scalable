import io
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
import boto3
from botocore.exceptions import ClientError
import qrcode
from PIL import Image
from urllib.parse import urlparse

BUCKET_NAME = "ticketbuddy-tickets-943886678148"
s3 = boto3.client("s3")


def generate_ticket_pdf(booking):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)

    # RideReserve brand colours
    DARK  = (26/255,  26/255,  46/255)   # #1a1a2e
    GOLD  = (240/255, 192/255,  64/255)  # #f0c040
    DGREY = (30/255,  30/255,  30/255)

    # ── Header bar ──────────────────────────────────────────
    pdf.setFillColorRGB(*DARK)
    pdf.rect(0, 780, 595, 60, fill=1)
    pdf.setFillColorRGB(*GOLD)
    pdf.setFont("Helvetica-Bold", 24)
    pdf.drawString(30, 808, "🚗  RIDERESERVE – RIDE CONFIRMATION")

    # ── Main content box ────────────────────────────────────
    pdf.setStrokeColorRGB(0.8, 0.8, 0.8)
    pdf.setFillColorRGB(1, 1, 1)
    pdf.rect(30, 480, 535, 280, fill=0, stroke=1)

    # Booking ID
    pdf.setFont("Helvetica-Bold", 18)
    pdf.setFillColorRGB(*DGREY)
    pdf.drawString(45, 735, f"Booking ID: {booking['booking_id']}")

    # Passenger
    pdf.setFont("Helvetica", 14)
    pdf.drawString(45, 710, f"Passenger: {booking['username']}")

    # Route
    pdf.setFont("Helvetica-Bold", 20)
    pdf.setFillColorRGB(*DARK)
    pdf.drawString(45, 675, f"{booking['source']} → {booking['destination']}")

    # Time box
    pdf.setFillColorRGB(0.97, 0.97, 0.97)
    pdf.roundRect(40, 600, 250, 60, 10, fill=1)
    pdf.setFillColorRGB(*DGREY)
    pdf.setFont("Helvetica", 12)
    pdf.drawString(50, 640, "Departure:")
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(50, 620, booking["departure_time"])
    pdf.setFont("Helvetica", 12)
    pdf.drawString(160, 640, "Arrival:")
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(160, 620, booking["arrival_time"])

    # Seats
    pdf.setFont("Helvetica", 12)
    pdf.drawString(45, 580, "Seats:")
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(45, 562, ", ".join(booking.get("seats", [])))

    # ── Fare breakdown ───────────────────────────────────────
    tax_rate    = booking.get("tax_rate", 0)
    tax_amount  = booking.get("tax_amount", None)
    final_price = booking.get("final_price", None)
    base_fare   = booking.get("fare", 0)

    y_fare = 540

    if tax_amount is not None and final_price is not None:
        # Gold separator line above fare section
        pdf.setFillColorRGB(*GOLD)
        pdf.rect(40, y_fare + 2, 510, 2, fill=1)

        pdf.setFillColorRGB(*DGREY)
        pdf.setFont("Helvetica", 12)
        pdf.drawString(45, y_fare - 14, "Base Fare:")
        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(160, y_fare - 14, f"€{base_fare}")

        pdf.setFont("Helvetica", 12)
        pdf.setFillColorRGB(*DGREY)
        pdf.drawString(45, y_fare - 32, f"VAT ({tax_rate}%):")
        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(160, y_fare - 32, f"€{tax_amount}")

        # Total incl. VAT — highlighted
        pdf.setFillColorRGB(*GOLD)
        pdf.roundRect(40, y_fare - 60, 250, 22, 5, fill=1)
        pdf.setFillColorRGB(*DARK)
        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(45, y_fare - 53, f"Total incl. VAT:  €{final_price}")
    else:
        # Fallback: no tax data stored (old bookings)
        pdf.setFillColorRGB(*DGREY)
        pdf.setFont("Helvetica", 12)
        pdf.drawString(45, y_fare - 14, "Total Fare:")
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(45, y_fare - 34, f"€{base_fare}")

    # ── QR Code ─────────────────────────────────────────────
    qr_data = (
        f"RideReserve Ride Confirmation\n"
        f"Booking ID: {booking['booking_id']}\n"
        f"Passenger: {booking['username']}\n"
        f"From: {booking['source']}\n"
        f"To: {booking['destination']}\n"
        f"Departure: {booking['departure_time']}\n"
        f"Seats: {', '.join(booking.get('seats', []))}\n"
        f"Total incl. VAT: €{booking.get('final_price', booking.get('fare', ''))}"
    )
    qr_img = qrcode.make(qr_data)
    qr_bytes = io.BytesIO()
    qr_img.save(qr_bytes, format="PNG")
    qr_bytes.seek(0)
    pdf.drawImage(ImageReader(qr_bytes), 450, 560, 100, 100)
    pdf.setFont("Helvetica", 10)
    pdf.setFillColorRGB(*DGREY)
    pdf.drawString(455, 550, "SCAN TO VERIFY")

    # ── Gold accent bar ──────────────────────────────────────
    pdf.setFillColorRGB(*GOLD)
    pdf.rect(30, 475, 535, 4, fill=1)

    # ── Footer ───────────────────────────────────────────────
    pdf.setFillColorRGB(0.4, 0.4, 0.4)
    pdf.setFont("Helvetica-Oblique", 10)
    pdf.drawString(30, 460, "Thank you for choosing RideReserve – Safe travels!")
    pdf.drawString(30, 446, "All prices include VAT as required by Irish Revenue.")

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer


def upload_ticket_pdf(buffer, filename):
    """Uploads PDF to S3 and returns the S3 key (not a presigned URL)."""
    try:
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=filename,
            Body=buffer.getvalue(),
            ContentType="application/pdf"
        )
        return filename
    except ClientError as e:
        print("S3 Upload Error:", e)
        return None


def get_presigned_url(key, expiry=3600):
    """
    Generates a fresh pre-signed URL every time.
    Handles both old full URLs (https://...) and new S3 keys (tickets/xxx.pdf).
    """
    if not key:
        return None

    if key.startswith("http"):
        parsed = urlparse(key)
        key = parsed.path.lstrip("/")
        bucket_prefix = f"{BUCKET_NAME}/"
        if key.startswith(bucket_prefix):
            key = key[len(bucket_prefix):]

    try:
        url = s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": BUCKET_NAME, "Key": key},
            ExpiresIn=expiry
        )
        return url
    except ClientError as e:
        print("Presigned URL Error:", e)
        return None