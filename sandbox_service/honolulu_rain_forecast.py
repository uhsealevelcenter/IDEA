import os
import math
import json
from urllib.parse import urlencode
from urllib.request import urlopen, Request

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

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

hourly = pd.DataFrame(data["hourly"])
hourly["time"] = pd.to_datetime(hourly["time"])
now_local = pd.to_datetime(data["current"]["time"])
today = now_local.date()
today_df = hourly[hourly["time"].dt.date == today].copy()

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

pressure_med = today_df["surface_pressure"].median()
risks = []
for _, row in today_df.iterrows():
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
    blended = max(0, min(1, 0.70 * model_p + 0.30 * (pop / 100.0)))
    risks.append(blended)

today_df["model_rain_probability"] = risks
remaining = today_df[today_df["time"] >= now_local.floor("h")].copy()
if remaining.empty:
    remaining = today_df.copy()

remaining["block"] = ((remaining["time"].dt.hour) // 3).astype(int)
block_probs = remaining.groupby("block")["model_rain_probability"].max().clip(0, 0.95)
daily_prob = 1 - float((1 - block_probs).prod())
max_hourly = float(remaining["model_rain_probability"].max())
mean_hourly = float(remaining["model_rain_probability"].mean())
daily_prob_adj = max(max_hourly, 0.65 * daily_prob + 0.35 * mean_hourly)
daily_prob_adj = max(0, min(1, daily_prob_adj))
expected_precip = float(remaining["precipitation"].sum())
max_pop = float(remaining["precipitation_probability"].max())

if daily_prob_adj >= 0.70:
    category = "Rain likely"
elif daily_prob_adj >= 0.45:
    category = "Chance of rain"
elif daily_prob_adj >= 0.25:
    category = "Slight chance of rain"
else:
    category = "Rain unlikely"

today_df.to_csv(os.path.join(OUTDIR, "honolulu_hourly_rain_risk_today.csv"), index=False)

plt.style.use("seaborn-v0_8-whitegrid")
fig, ax1 = plt.subplots(figsize=(10, 5.2))
ax1.plot(today_df["time"], today_df["model_rain_probability"] * 100, marker="o", color="#1764ab", linewidth=2.2, label="Model rain risk")
ax1.plot(today_df["time"], today_df["precipitation_probability"], marker=".", color="#7b3294", linewidth=1.4, alpha=0.75, label="Provider PoP")
ax1.axvline(now_local, color="black", linestyle="--", linewidth=1.2, alpha=0.7, label="Current time")
ax1.set_ylim(0, 100)
ax1.set_ylabel("Hourly probability (%)")
ax1.set_title(f"Rain-risk forecast for {LOCATION} — {today}")
ax1.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
ax1.set_xlabel("Local time (HST)")
ax2 = ax1.twinx()
ax2.bar(today_df["time"], today_df["precipitation"], width=0.028, color="#4daf4a", alpha=0.25, label="Forecast precip.")
ax2.set_ylabel("Forecast precipitation (mm/hr)")
lines1, labels1 = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", frameon=True)
fig.autofmt_xdate()
fig.tight_layout()
plot_path = os.path.join(OUTDIR, "honolulu_rain_risk_today.png")
plt.savefig(plot_path, dpi=180, bbox_inches="tight")
plt.close(fig)

summary = {
    "location": LOCATION,
    "lat": LAT,
    "lon": LON,
    "current_local_time": str(now_local),
    "forecast_date_hst": str(today),
    "category": category,
    "estimated_probability_rain_remaining_today_pct": round(daily_prob_adj * 100, 1),
    "max_hourly_model_probability_pct": round(max_hourly * 100, 1),
    "mean_hourly_model_probability_pct": round(mean_hourly * 100, 1),
    "expected_remaining_precipitation_mm": round(expected_precip, 2),
    "max_provider_hourly_pop_pct": round(max_pop, 1),
    "data_source": "Open-Meteo forecast API, accessed at runtime",
}
with open(os.path.join(OUTDIR, "summary.json"), "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2)

cols = ["time", "model_rain_probability", "precipitation_probability", "precipitation", "relative_humidity_2m", "cloud_cover", "weather_code"]
print(json.dumps(summary, indent=2))
print("\nRemaining hourly forecast preview:")
preview = remaining[cols].copy()
preview["model_rain_probability"] = (preview["model_rain_probability"] * 100).round(1)
print(preview.head(12).to_string(index=False))
print(f"\nPLOT={plot_path}")
print(f"CSV={os.path.join(OUTDIR, 'honolulu_hourly_rain_risk_today.csv')}")
