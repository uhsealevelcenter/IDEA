import os
import math
import json
import csv
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import urlopen, Request

LAT = 21.3069
LON = -157.8583
LOCATION = "Honolulu, Hawaii"
OUTDIR = "/outputs/honolulu_rain_forecast"
os.makedirs(OUTDIR, exist_ok=True)

base_url = "https://api.open-meteo.com/v1/forecast"
params = {
    "latitude": LAT,
    "longitude": LON,
    "timezone": "Pacific/Honolulu",
    "forecast_days": 2,
    "current": "temperature_2m,relative_humidity_2m,precipitation,rain,showers,weather_code,cloud_cover,surface_pressure,wind_speed_10m",
    "hourly": "temperature_2m,relative_humidity_2m,precipitation_probability,precipitation,rain,showers,weather_code,cloud_cover,surface_pressure,wind_speed_10m,wind_gusts_10m",
}
url = base_url + "?" + urlencode(params)
req = Request(url, headers={"User-Agent": "IDEA rain forecast analysis"})
with urlopen(req, timeout=30) as resp:
    data = json.loads(resp.read().decode("utf-8"))

current = data["current"]
now_local = datetime.fromisoformat(current["time"])
today = now_local.date()
hourly = data["hourly"]

rows = []
for i, t in enumerate(hourly["time"]):
    dt = datetime.fromisoformat(t)
    if dt.date() == today:
        row = {"time": dt}
        for k, arr in hourly.items():
            if k != "time":
                row[k] = arr[i]
        rows.append(row)

# Weather-code effects for rain-prone WMO conditions.
def wmo_rain_effect(code):
    try:
        c = int(code)
    except Exception:
        return 0.0
    if 51 <= c <= 57:
        return 0.50
    if 61 <= c <= 67:
        return 0.75
    if 80 <= c <= 82:
        return 0.85
    if c >= 95:
        return 0.90
    if c in (45, 48):
        return 0.10
    if c in (1, 2, 3):
        return 0.03 * c
    return 0.0

def sigmoid(x):
    return 1 / (1 + math.exp(-x))

pressures = sorted(float(r.get("surface_pressure") or 0) for r in rows)
pressure_med = pressures[len(pressures)//2] if pressures else 1013.0

for row in rows:
    pop = float(row.get("precipitation_probability", 0) or 0)
    precip = float(row.get("precipitation", 0) or 0)
    rain = float(row.get("rain", 0) or 0)
    showers = float(row.get("showers", 0) or 0)
    rh = float(row.get("relative_humidity_2m", 0) or 0)
    cloud = float(row.get("cloud_cover", 0) or 0)
    pressure = float(row.get("surface_pressure", pressure_med) or pressure_med)
    gust = float(row.get("wind_gusts_10m", 0) or 0)
    code_effect = wmo_rain_effect(row.get("weather_code"))
    precip_signal = math.log1p(max(precip, rain, showers, 0))
    low_pressure_signal = max(0.0, pressure_med - pressure)
    wind_signal = min(max((gust - 20) / 25, 0), 1)
    score = (
        -4.3
        + 0.032 * pop
        + 1.45 * precip_signal
        + 0.025 * max(rh - 65, 0)
        + 0.015 * max(cloud - 45, 0)
        + 0.18 * low_pressure_signal
        + 0.32 * wind_signal
        + 1.55 * code_effect
    )
    model_p = sigmoid(score)
    row["model_rain_probability"] = max(0, min(1, 0.70 * model_p + 0.30 * (pop / 100.0)))

current_hour = now_local.replace(minute=0, second=0, microsecond=0)
remaining = [r for r in rows if r["time"] >= current_hour] or rows

# Aggregate correlated hourly probabilities into 3-hour blocks.
block_max = {}
for r in remaining:
    block = r["time"].hour // 3
    block_max[block] = max(block_max.get(block, 0), min(r["model_rain_probability"], 0.95))
daily_prob = 1.0
for p in block_max.values():
    daily_prob *= (1 - p)
daily_prob = 1 - daily_prob
max_hourly = max(r["model_rain_probability"] for r in remaining)
mean_hourly = sum(r["model_rain_probability"] for r in remaining) / len(remaining)
daily_prob_adj = max(max_hourly, 0.65 * daily_prob + 0.35 * mean_hourly)
daily_prob_adj = max(0, min(1, daily_prob_adj))
expected_precip = sum(float(r.get("precipitation") or 0) for r in remaining)
max_pop = max(float(r.get("precipitation_probability") or 0) for r in remaining)

if daily_prob_adj >= 0.70:
    category = "Rain likely"
elif daily_prob_adj >= 0.45:
    category = "Chance of rain"
elif daily_prob_adj >= 0.25:
    category = "Slight chance of rain"
else:
    category = "Rain unlikely"

csv_path = os.path.join(OUTDIR, "honolulu_hourly_rain_risk_today.csv")
fieldnames = ["time", "model_rain_probability", "precipitation_probability", "precipitation", "rain", "showers", "temperature_2m", "relative_humidity_2m", "cloud_cover", "surface_pressure", "wind_speed_10m", "wind_gusts_10m", "weather_code"]
with open(csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        out = {k: r.get(k, "") for k in fieldnames}
        out["time"] = r["time"].isoformat()
        writer.writerow(out)

# Create simple SVG plot.
svg_path = os.path.join(OUTDIR, "honolulu_rain_risk_today.svg")
W, H = 1000, 520
left, right, top, bottom = 75, 45, 65, 75
plot_w, plot_h = W - left - right, H - top - bottom
max_precip = max([float(r.get("precipitation") or 0) for r in rows] + [0.1])

def x_for(dt):
    return left + (dt.hour + dt.minute/60) / 23 * plot_w

def y_prob(p):
    return top + (1 - p) * plot_h

def y_precip(mm):
    return top + (1 - (mm / max_precip if max_precip else 0)) * plot_h

poly_model = " ".join(f"{x_for(r['time']):.1f},{y_prob(r['model_rain_probability']):.1f}" for r in rows)
poly_pop = " ".join(f"{x_for(r['time']):.1f},{y_prob((float(r.get('precipitation_probability') or 0))/100):.1f}" for r in rows)
now_x = x_for(now_local)
svg = []
svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
svg.append('<rect width="100%" height="100%" fill="white"/>')
svg.append(f'<text x="{W/2}" y="32" text-anchor="middle" font-family="Arial" font-size="22" font-weight="bold">Rain-risk forecast for Honolulu, Hawaii — {today}</text>')
# grid and y labels
for pct in range(0, 101, 20):
    y = y_prob(pct/100)
    svg.append(f'<line x1="{left}" y1="{y:.1f}" x2="{W-right}" y2="{y:.1f}" stroke="#ddd"/>')
    svg.append(f'<text x="{left-12}" y="{y+5:.1f}" text-anchor="end" font-family="Arial" font-size="13">{pct}%</text>')
# x ticks
for hr in range(0, 24, 3):
    x = left + hr/23*plot_w
    svg.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{H-bottom}" stroke="#eee"/>')
    svg.append(f'<text x="{x:.1f}" y="{H-bottom+24}" text-anchor="middle" font-family="Arial" font-size="13">{hr:02d}:00</text>')
# precip bars
bar_w = plot_w / 24 * 0.55
for r in rows:
    mm = float(r.get("precipitation") or 0)
    if mm > 0:
        x = x_for(r["time"]) - bar_w/2
        y = y_precip(mm)
        svg.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{H-bottom-y:.1f}" fill="#4daf4a" opacity="0.28"/>')
# axes
svg.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{H-bottom}" stroke="#333"/>')
svg.append(f'<line x1="{left}" y1="{H-bottom}" x2="{W-right}" y2="{H-bottom}" stroke="#333"/>')
# lines
svg.append(f'<polyline points="{poly_model}" fill="none" stroke="#1764ab" stroke-width="3"/>')
svg.append(f'<polyline points="{poly_pop}" fill="none" stroke="#7b3294" stroke-width="2" opacity="0.8"/>')
for r in rows:
    svg.append(f'<circle cx="{x_for(r["time"]):.1f}" cy="{y_prob(r["model_rain_probability"]):.1f}" r="4" fill="#1764ab"/>')
svg.append(f'<line x1="{now_x:.1f}" y1="{top}" x2="{now_x:.1f}" y2="{H-bottom}" stroke="#111" stroke-dasharray="6,5" stroke-width="2"/>')
svg.append(f'<text x="{now_x+6:.1f}" y="{top+18}" font-family="Arial" font-size="13">current</text>')
# labels and legend
svg.append(f'<text x="{left + plot_w/2}" y="{H-18}" text-anchor="middle" font-family="Arial" font-size="15">Local time (HST)</text>')
svg.append(f'<text x="22" y="{top + plot_h/2}" transform="rotate(-90 22,{top + plot_h/2})" text-anchor="middle" font-family="Arial" font-size="15">Hourly probability</text>')
svg.append(f'<rect x="{W-315}" y="{top+8}" width="250" height="74" fill="white" stroke="#bbb" opacity="0.95"/>')
svg.append(f'<line x1="{W-295}" y1="{top+30}" x2="{W-255}" y2="{top+30}" stroke="#1764ab" stroke-width="3"/><text x="{W-245}" y="{top+35}" font-family="Arial" font-size="13">Model rain risk</text>')
svg.append(f'<line x1="{W-295}" y1="{top+52}" x2="{W-255}" y2="{top+52}" stroke="#7b3294" stroke-width="2"/><text x="{W-245}" y="{top+57}" font-family="Arial" font-size="13">Provider PoP</text>')
svg.append(f'<rect x="{W-295}" y="{top+65}" width="38" height="10" fill="#4daf4a" opacity="0.28"/><text x="{W-245}" y="{top+76}" font-family="Arial" font-size="13">Forecast precip.</text>')
svg.append('</svg>')
with open(svg_path, "w", encoding="utf-8") as f:
    f.write("\n".join(svg))

summary = {
    "location": LOCATION,
    "lat": LAT,
    "lon": LON,
    "current_local_time": now_local.isoformat(),
    "forecast_date_hst": str(today),
    "category": category,
    "estimated_probability_rain_remaining_today_pct": round(daily_prob_adj * 100, 1),
    "max_hourly_model_probability_pct": round(max_hourly * 100, 1),
    "mean_hourly_model_probability_pct": round(mean_hourly * 100, 1),
    "expected_remaining_precipitation_mm": round(expected_precip, 2),
    "max_provider_hourly_pop_pct": round(max_pop, 1),
    "data_source": "Open-Meteo forecast API, accessed at runtime",
    "svg_path": svg_path,
    "csv_path": csv_path,
}
summary_path = os.path.join(OUTDIR, "summary.json")
with open(summary_path, "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2)

print(json.dumps(summary, indent=2))
print("\nRemaining hourly forecast preview:")
print("time                 model_%  provider_pop_%  precip_mm  rh_%  cloud_%  wmo")
for r in remaining[:12]:
    print(f"{r['time'].isoformat():19s} {100*r['model_rain_probability']:7.1f} {float(r.get('precipitation_probability') or 0):15.1f} {float(r.get('precipitation') or 0):10.2f} {float(r.get('relative_humidity_2m') or 0):5.0f} {float(r.get('cloud_cover') or 0):8.0f} {int(r.get('weather_code') or 0):4d}")
