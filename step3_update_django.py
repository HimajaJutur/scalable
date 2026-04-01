"""
STEP 3 — Run from your Django project root (where manage.py lives)
Patches views.py, urls.py and creates dashboard.html
Field names match your real DynamoDB table exactly
Run: python3 step3_update_django.py
"""

import os

VIEWS_PATH    = "buddy/views.py"
URLS_PATH     = "buddy/urls.py"
TEMPLATE_PATH = "buddy/templates/buddy/dashboard.html"
BUCKET_NAME   = "ticketbuddy-tickets-943886678148"

# ─────────────────────────────────────────────────────────────────────────────
# 1. Patch views.py
# ─────────────────────────────────────────────────────────────────────────────
DASHBOARD_VIEW = '''

def dashboard_view(request):
    """Reads latest analytics JSON from S3 and renders the dashboard."""
    import boto3, json, os
    AWS_REGION  = os.getenv("AWS_REGION", "us-east-1")
    BUCKET_NAME = "ticketbuddy-tickets-943886678148"
    s3 = boto3.client("s3", region_name=AWS_REGION)
    try:
        obj       = s3.get_object(Bucket=BUCKET_NAME, Key="analytics/dashboard.json")
        analytics = json.loads(obj["Body"].read())
    except Exception as e:
        print(f"Dashboard S3 read error: {e}")
        analytics = {
            "top_routes":       [],
            "ticket_types":     [],
            "revenue":          [],
            "busy_times":       [],
            "top_destinations": [],
            "summary":          {"total_bookings": 0, "total_revenue": 0, "total_tax": 0},
            "last_updated":     "No data yet — run step2_run_glue_once.py first.",
        }
    return render(request, "buddy/dashboard.html", {
        "analytics_json": json.dumps(analytics, default=str)
    })
'''

print("Patching views.py...")
with open(VIEWS_PATH, "r") as f:
    views_content = f.read()

if "def dashboard_view" in views_content:
    print("  ✅ dashboard_view already exists — skipping")
else:
    with open(VIEWS_PATH, "a") as f:
        f.write(DASHBOARD_VIEW)
    print("  ✅ dashboard_view added to views.py")

# ─────────────────────────────────────────────────────────────────────────────
# 2. Patch urls.py
# ─────────────────────────────────────────────────────────────────────────────
print("Patching urls.py...")
with open(URLS_PATH, "r") as f:
    urls_content = f.read()

if "dashboard_view" in urls_content:
    print("  ✅ dashboard url already exists — skipping")
else:
    dashboard_import = "from buddy.views import dashboard_view\n"
    dashboard_path   = "    path('dashboard/', dashboard_view, name='dashboard'),\n"
    if dashboard_import not in urls_content:
        urls_content = dashboard_import + urls_content
    if "urlpatterns = [" in urls_content:
        urls_content = urls_content.replace(
            "urlpatterns = [",
            "urlpatterns = [\n" + dashboard_path
        )
    with open(URLS_PATH, "w") as f:
        f.write(urls_content)
    print("  ✅ /dashboard/ path added to urls.py")

# ─────────────────────────────────────────────────────────────────────────────
# 3. Create dashboard.html
#    Charts use the exact fields produced by the Glue job:
#      top_routes       -> route, total_bookings
#      ticket_types     -> ticket_type, count
#      revenue          -> month, total_revenue
#      busy_times       -> departure_time, total_bookings
#      top_destinations -> destination, total_bookings
#      summary          -> total_bookings, total_revenue, total_tax
# ─────────────────────────────────────────────────────────────────────────────
DASHBOARD_HTML = """{% extends "buddy/base.html" %}
{% block title %}Analytics Dashboard - RideReserve{% endblock %}
{% block content %}

<style>
.dash-wrapper { width: 88%; margin: 30px auto; }
.dash-header  { text-align: center; margin-bottom: 28px; }
.dash-header h2 { color: #1a1a2e; font-size: 26px; margin-bottom: 4px; }
.dash-header p  { color: #888; font-size: 14px; }
.last-updated {
    display: inline-block; background: #fff8e1; color: #7a6000;
    border-radius: 20px; padding: 4px 14px;
    font-size: 12px; font-weight: 600; margin-top: 8px;
}
.stat-row {
    display: grid; grid-template-columns: repeat(3, 1fr);
    gap: 16px; margin-bottom: 24px;
}
.stat-card {
    background: white; border-radius: 12px; padding: 20px 24px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.08);
    border-top: 4px solid #f0c040; text-align: center;
}
.stat-num   { font-size: 30px; font-weight: 800; color: #1a1a2e; }
.stat-label { font-size: 13px; color: #888; margin-top: 4px; }
.dash-grid  { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }
.dash-card  {
    background: white; border-radius: 14px; padding: 24px;
    box-shadow: 0 4px 14px rgba(0,0,0,0.08);
    border-top: 4px solid #f0c040;
}
.dash-card h5 { margin: 0 0 18px 0; color: #1a1a2e; font-size: 15px; font-weight: 700; }
.full-width   { grid-column: span 2; }
canvas        { max-height: 270px; }
</style>

<div class="dash-wrapper">

    <div class="dash-header">
        <h2>&#128202; Analytics Dashboard</h2>
        <p>Powered by AWS Glue + PySpark &#8212; auto-updates every hour</p>
        <span class="last-updated" id="last-updated-badge">Loading...</span>
    </div>

    <!-- Summary cards — pulled from summary{} block in JSON -->
    <div class="stat-row">
        <div class="stat-card">
            <div class="stat-num" id="stat-bookings">-</div>
            <div class="stat-label">Total Confirmed Bookings</div>
        </div>
        <div class="stat-card">
            <div class="stat-num" id="stat-revenue">-</div>
            <div class="stat-label">Total Revenue (incl. VAT)</div>
        </div>
        <div class="stat-card">
            <div class="stat-num" id="stat-tax">-</div>
            <div class="stat-label">Total VAT Collected</div>
        </div>
    </div>

    <div class="dash-grid">

        <!-- 1. Top 5 routes: route, total_bookings -->
        <div class="dash-card">
            <h5>&#128739; Top 5 Most Booked Routes</h5>
            <canvas id="routesChart"></canvas>
        </div>

        <!-- 2. One Way vs Return: ticket_type, count -->
        <div class="dash-card">
            <h5>&#127915; One Way vs Return Tickets</h5>
            <canvas id="ticketTypeChart"></canvas>
        </div>

        <!-- 3. Revenue by month: month, total_revenue -->
        <div class="dash-card full-width">
            <h5>&#128176; Revenue by Month (EUR incl. VAT)</h5>
            <canvas id="revenueChart"></canvas>
        </div>

        <!-- 4. Busiest departure times: departure_time, total_bookings -->
        <div class="dash-card">
            <h5>&#128336; Busiest Departure Times</h5>
            <canvas id="timesChart"></canvas>
        </div>

        <!-- 5. Top destinations: destination, total_bookings -->
        <div class="dash-card">
            <h5>&#128205; Most Popular Destinations</h5>
            <canvas id="destChart"></canvas>
        </div>

    </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
const data = {{ analytics_json|safe }};

// Last updated
document.getElementById('last-updated-badge').textContent =
    'Last updated: ' + (data.last_updated || 'N/A');

// Summary cards from data.summary
const s = data.summary || {};
document.getElementById('stat-bookings').textContent =
    s.total_bookings || '-';
document.getElementById('stat-revenue').textContent =
    s.total_revenue ? 'EUR ' + parseFloat(s.total_revenue).toFixed(2) : '-';
document.getElementById('stat-tax').textContent =
    s.total_tax ? 'EUR ' + parseFloat(s.total_tax).toFixed(2) : '-';

const YELLOW = '#f0c040';
const DARK   = '#1a1a2e';
const COLORS = [YELLOW, DARK, '#4CAF50', '#2196F3', '#FF5722', '#9C27B0', '#00BCD4'];

// 1. Top 5 Routes — horizontal bar
// data.top_routes[].route, .total_bookings
new Chart(document.getElementById('routesChart'), {
    type: 'bar',
    data: {
        labels: data.top_routes.map(r => r.route),
        datasets: [{
            data: data.top_routes.map(r => r.total_bookings),
            backgroundColor: YELLOW, borderRadius: 6
        }]
    },
    options: {
        indexAxis: 'y',
        plugins: { legend: { display: false } },
        scales: { x: { beginAtZero: true, ticks: { stepSize: 1 } } }
    }
});

// 2. One Way vs Return — doughnut
// data.ticket_types[].ticket_type, .count
new Chart(document.getElementById('ticketTypeChart'), {
    type: 'doughnut',
    data: {
        labels: data.ticket_types.map(t => t.ticket_type || 'Unknown'),
        datasets: [{
            data: data.ticket_types.map(t => t.count),
            backgroundColor: [YELLOW, DARK], borderWidth: 2, borderColor: '#fff'
        }]
    },
    options: {
        cutout: '60%',
        plugins: { legend: { position: 'bottom', labels: { padding: 16 } } }
    }
});

// 3. Revenue by Month — line
// data.revenue[].month, .total_revenue
new Chart(document.getElementById('revenueChart'), {
    type: 'line',
    data: {
        labels: data.revenue.map(r => r.month),
        datasets: [{
            label: 'Revenue (EUR)',
            data: data.revenue.map(r => parseFloat(r.total_revenue) || 0),
            borderColor: YELLOW, backgroundColor: 'rgba(240,192,64,0.12)',
            borderWidth: 3, fill: true, tension: 0.4,
            pointBackgroundColor: YELLOW, pointRadius: 5
        }]
    },
    options: {
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, ticks: { callback: v => 'EUR ' + v } } }
    }
});

// 4. Busiest Departure Times — bar
// data.busy_times[].departure_time, .total_bookings
new Chart(document.getElementById('timesChart'), {
    type: 'bar',
    data: {
        labels: data.busy_times.map(t => t.departure_time),
        datasets: [{
            data: data.busy_times.map(t => t.total_bookings),
            backgroundColor: DARK, borderRadius: 6
        }]
    },
    options: {
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } }
    }
});

// 5. Top Destinations — horizontal bar
// data.top_destinations[].destination, .total_bookings
new Chart(document.getElementById('destChart'), {
    type: 'bar',
    data: {
        labels: data.top_destinations.map(d => d.destination),
        datasets: [{
            data: data.top_destinations.map(d => d.total_bookings),
            backgroundColor: COLORS, borderRadius: 6
        }]
    },
    options: {
        indexAxis: 'y',
        plugins: { legend: { display: false } },
        scales: { x: { beginAtZero: true, ticks: { stepSize: 1 } } }
    }
});
</script>

{% endblock %}
"""

print("Creating dashboard.html...")
os.makedirs(os.path.dirname(TEMPLATE_PATH), exist_ok=True)
with open(TEMPLATE_PATH, "w") as f:
    f.write(DASHBOARD_HTML)
print(f"  ✅ Created: {TEMPLATE_PATH}")

print("\n" + "="*60)
print("STEP 3 COMPLETE")
print("="*60)
print("  views.py       -> dashboard_view added")
print("  urls.py        -> /dashboard/ path added")
print("  dashboard.html -> created")
print("\nRestart Django:")
print("  python3 manage.py runserver 0.0.0.0:8080")
print("\nOpen in browser:")
print("  http://your-ec2-ip:8080/dashboard/")
print("="*60)