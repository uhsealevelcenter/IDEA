import json
import math
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier, VotingClassifier
from sklearn.metrics import roc_auc_score, brier_score_loss, accuracy_score, precision_score, recall_score, confusion_matrix

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns

OUTDIR = Path('/outputs/honolulu_rain_prediction_tomorrow')
OUTDIR.mkdir(parents=True, exist_ok=True)

LAT, LON = 21.3069, -157.8583
TZ = 'Pacific/Honolulu'
UTC_NOW = dt.datetime.fromisoformat('2026-07-12T20:07:54+00:00')
HST = dt.timezone(dt.timedelta(hours=-10))
LOCAL_NOW = UTC_NOW.astimezone(HST)
TOMORROW = LOCAL_NOW.date() + dt.timedelta(days=1)

def fetch_json(url, params):
    r = requests.get(url, params=params, timeout=90)
    print(f'GET {r.url}')
    print(f'Status: {r.status_code}')
    r.raise_for_status()
    return r.json()

# Historical observations for supervised training.
hist_end = LOCAL_NOW.date() - dt.timedelta(days=7)
hist_start = hist_end - dt.timedelta(days=365 * 8)
hist_params = {
    'latitude': LAT,
    'longitude': LON,
    'start_date': hist_start.isoformat(),
    'end_date': hist_end.isoformat(),
    'daily': ','.join([
        'weather_code',
        'temperature_2m_max', 'temperature_2m_min', 'temperature_2m_mean',
        'apparent_temperature_max', 'apparent_temperature_min', 'apparent_temperature_mean',
        'precipitation_sum', 'rain_sum', 'precipitation_hours',
        'wind_speed_10m_max', 'wind_gusts_10m_max', 'wind_direction_10m_dominant',
        'shortwave_radiation_sum', 'et0_fao_evapotranspiration'
    ]),
    'timezone': TZ
}
hist_json = fetch_json('https://archive-api.open-meteo.com/v1/archive', hist_params)
hist = pd.DataFrame(hist_json['daily'])
hist['time'] = pd.to_datetime(hist['time'])
hist['rain_event'] = (hist['precipitation_sum'].fillna(0) >= 0.1).astype(int)
hist['dayofyear'] = hist['time'].dt.dayofyear
hist['sin_doy'] = np.sin(2 * np.pi * hist['dayofyear'] / 365.25)
hist['cos_doy'] = np.cos(2 * np.pi * hist['dayofyear'] / 365.25)
hist['sin_winddir'] = np.sin(np.deg2rad(hist['wind_direction_10m_dominant']))
hist['cos_winddir'] = np.cos(np.deg2rad(hist['wind_direction_10m_dominant']))
hist.to_csv(OUTDIR / 'raw_open_meteo_historical_daily_honolulu.csv', index=False)

# Clean ML feature set: exclude direct precipitation/rain/weather-code diagnostics.
feature_cols = [
    'temperature_2m_max', 'temperature_2m_min', 'temperature_2m_mean',
    'apparent_temperature_max', 'apparent_temperature_min', 'apparent_temperature_mean',
    'wind_speed_10m_max', 'wind_gusts_10m_max',
    'shortwave_radiation_sum', 'et0_fao_evapotranspiration',
    'sin_doy', 'cos_doy', 'sin_winddir', 'cos_winddir'
]
model_df = hist.dropna(subset=['rain_event']).copy()
X = model_df[feature_cols]
y = model_df['rain_event']

split_idx = int(len(model_df) * 0.8)
X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

base_models = [
    ('logistic', Pipeline([
        ('imputer', SimpleImputer(strategy='median')),
        ('scaler', StandardScaler()),
        ('clf', LogisticRegression(max_iter=1000, class_weight='balanced', random_state=42))
    ])),
    ('rf', Pipeline([
        ('imputer', SimpleImputer(strategy='median')),
        ('clf', RandomForestClassifier(n_estimators=500, min_samples_leaf=8, class_weight='balanced_subsample', random_state=42, n_jobs=-1))
    ])),
    ('gb', Pipeline([
        ('imputer', SimpleImputer(strategy='median')),
        ('clf', GradientBoostingClassifier(random_state=42, max_depth=2, learning_rate=0.04, n_estimators=180))
    ]))
]

validation_rows = []
for name, pipe in base_models:
    pipe.fit(X_train, y_train)
    prob = pipe.predict_proba(X_test)[:, 1]
    pred = (prob >= 0.5).astype(int)
    validation_rows.append({
        'model': name,
        'roc_auc': float(roc_auc_score(y_test, prob)),
        'brier_score': float(brier_score_loss(y_test, prob)),
        'accuracy_at_0.5': float(accuracy_score(y_test, pred)),
        'precision_at_0.5': float(precision_score(y_test, pred, zero_division=0)),
        'recall_at_0.5': float(recall_score(y_test, pred, zero_division=0))
    })

ensemble = VotingClassifier(estimators=base_models, voting='soft', weights=[1, 2, 1])
ensemble.fit(X_train, y_train)
prob_test = ensemble.predict_proba(X_test)[:, 1]
pred_test = (prob_test >= 0.5).astype(int)
validation_rows.append({
    'model': 'clean_soft_voting_ensemble',
    'roc_auc': float(roc_auc_score(y_test, prob_test)),
    'brier_score': float(brier_score_loss(y_test, prob_test)),
    'accuracy_at_0.5': float(accuracy_score(y_test, pred_test)),
    'precision_at_0.5': float(precision_score(y_test, pred_test, zero_division=0)),
    'recall_at_0.5': float(recall_score(y_test, pred_test, zero_division=0))
})
validation = pd.DataFrame(validation_rows).sort_values('brier_score')
validation.to_csv(OUTDIR / 'model_validation_metrics.csv', index=False)

# Refit final clean model on all history.
final_model = VotingClassifier(estimators=base_models, voting='soft', weights=[1, 2, 1])
final_model.fit(X, y)

# Forecast data for tomorrow.
forecast_params = {
    'latitude': LAT,
    'longitude': LON,
    'daily': ','.join([
        'weather_code',
        'temperature_2m_max', 'temperature_2m_min', 'apparent_temperature_max', 'apparent_temperature_min',
        'precipitation_sum', 'rain_sum', 'precipitation_hours', 'precipitation_probability_max',
        'wind_speed_10m_max', 'wind_gusts_10m_max', 'wind_direction_10m_dominant',
        'shortwave_radiation_sum', 'et0_fao_evapotranspiration'
    ]),
    'hourly': ','.join([
        'temperature_2m', 'relative_humidity_2m', 'precipitation_probability', 'precipitation', 'rain',
        'cloud_cover', 'pressure_msl', 'wind_speed_10m', 'wind_gusts_10m', 'weather_code'
    ]),
    'timezone': TZ,
    'start_date': TOMORROW.isoformat(),
    'end_date': TOMORROW.isoformat(),
    'models': 'best_match'
}
forecast_json = fetch_json('https://api.open-meteo.com/v1/forecast', forecast_params)
forecast_daily = pd.DataFrame(forecast_json['daily'])
forecast_hourly = pd.DataFrame(forecast_json['hourly'])
forecast_daily['time'] = pd.to_datetime(forecast_daily['time'])
forecast_hourly['time'] = pd.to_datetime(forecast_hourly['time'])
forecast_daily.to_csv(OUTDIR / 'raw_open_meteo_forecast_daily_honolulu_tomorrow.csv', index=False)
forecast_hourly.to_csv(OUTDIR / 'raw_open_meteo_forecast_hourly_honolulu_tomorrow.csv', index=False)

frow = forecast_daily.iloc[0]
tom_features = {
    'temperature_2m_max': frow.get('temperature_2m_max', np.nan),
    'temperature_2m_min': frow.get('temperature_2m_min', np.nan),
    'temperature_2m_mean': np.nanmean([frow.get('temperature_2m_max', np.nan), frow.get('temperature_2m_min', np.nan)]),
    'apparent_temperature_max': frow.get('apparent_temperature_max', np.nan),
    'apparent_temperature_min': frow.get('apparent_temperature_min', np.nan),
    'apparent_temperature_mean': np.nanmean([frow.get('apparent_temperature_max', np.nan), frow.get('apparent_temperature_min', np.nan)]),
    'wind_speed_10m_max': frow.get('wind_speed_10m_max', np.nan),
    'wind_gusts_10m_max': frow.get('wind_gusts_10m_max', np.nan),
    'shortwave_radiation_sum': frow.get('shortwave_radiation_sum', np.nan),
    'et0_fao_evapotranspiration': frow.get('et0_fao_evapotranspiration', np.nan),
}
doy = TOMORROW.timetuple().tm_yday
tom_features['sin_doy'] = np.sin(2 * np.pi * doy / 365.25)
tom_features['cos_doy'] = np.cos(2 * np.pi * doy / 365.25)
wdir = frow.get('wind_direction_10m_dominant', np.nan)
tom_features['sin_winddir'] = np.sin(np.deg2rad(wdir)) if pd.notnull(wdir) else np.nan
tom_features['cos_winddir'] = np.cos(np.deg2rad(wdir)) if pd.notnull(wdir) else np.nan
X_tom = pd.DataFrame([tom_features])[feature_cols]

ml_prob = float(final_model.predict_proba(X_tom)[0, 1])
provider_pop = frow.get('precipitation_probability_max', np.nan)
provider_pop01 = float(provider_pop) / 100 if pd.notnull(provider_pop) else np.nan
forecast_precip = float(frow.get('precipitation_sum', 0) or 0)
forecast_rain = float(frow.get('rain_sum', 0) or 0)
precip_hours = float(frow.get('precipitation_hours', 0) or 0)
amount_prob = 1 - math.exp(-forecast_precip / 1.5) if forecast_precip >= 0 else 0
# Forecast-informed blend: independent ML gets weight, but tomorrow-specific precip forecasts are included.
if np.isfinite(provider_pop01):
    final_prob = 0.45 * ml_prob + 0.40 * provider_pop01 + 0.15 * amount_prob
else:
    final_prob = 0.75 * ml_prob + 0.25 * amount_prob
final_prob = float(np.clip(final_prob, 0, 1))
classification = 'Rain likely' if final_prob >= 0.60 else ('Chance of rain' if final_prob >= 0.35 else 'Rain unlikely')

# Hourly transparent risk curve.
h = forecast_hourly.copy()
for col in ['precipitation_probability', 'precipitation', 'rain', 'relative_humidity_2m', 'cloud_cover', 'wind_gusts_10m']:
    h[col] = pd.to_numeric(h[col], errors='coerce')
h['hourly_rain_risk'] = (
    0.55 * h['precipitation_probability'].fillna(0) / 100 +
    0.20 * (1 - np.exp(-h['precipitation'].fillna(0) / 1.0)) +
    0.10 * (h['relative_humidity_2m'].fillna(60).clip(50, 100) - 50) / 50 +
    0.10 * h['cloud_cover'].fillna(50).clip(0, 100) / 100 +
    0.05 * (h['wind_gusts_10m'].fillna(15).clip(0, 60) / 60)
).clip(0, 1)
h.to_csv(OUTDIR / 'tomorrow_hourly_rain_risk_honolulu.csv', index=False)

prediction_table = pd.DataFrame([{
    'forecast_date_hst': TOMORROW.isoformat(),
    'clean_model_probability_rain': ml_prob,
    'provider_precip_probability_max': provider_pop01,
    'forecast_precipitation_sum_mm': forecast_precip,
    'forecast_rain_sum_mm': forecast_rain,
    'forecast_precipitation_hours': precip_hours,
    'forecast_weather_code': int(frow.get('weather_code')) if pd.notnull(frow.get('weather_code')) else None,
    'blended_probability_rain': final_prob,
    'classification': classification
}])
prediction_table.to_csv(OUTDIR / 'tomorrow_rain_prediction_honolulu.csv', index=False)

sns.set_theme(style='whitegrid')
fig, ax = plt.subplots(figsize=(10, 5))
ax.plot(h['time'], h['hourly_rain_risk'] * 100, marker='o', linewidth=2, label='Hourly rain-risk algorithm')
ax.plot(h['time'], h['precipitation_probability'], marker='s', linewidth=1.5, alpha=0.75, label='Raw forecast precip probability')
ax.bar(h['time'], h['precipitation'].fillna(0) * 10, width=0.03, alpha=0.25, label='Precip amount ×10 (mm)')
ax.set_title(f'Honolulu hourly rain risk for {TOMORROW.isoformat()} HST')
ax.set_ylabel('Probability / scaled precipitation')
ax.set_xlabel('Hour HST')
ax.set_ylim(0, max(55, np.nanmax(h['precipitation_probability'].fillna(0)) + 10))
ax.legend(loc='upper right')
fig.autofmt_xdate()
fig.tight_layout()
fig.savefig(OUTDIR / 'honolulu_hourly_rain_risk_tomorrow.png', dpi=180)
fig.savefig(OUTDIR / 'honolulu_hourly_rain_risk_tomorrow.svg')
plt.close(fig)

fig, ax = plt.subplots(figsize=(6.8, 4.5))
validation_plot = validation.sort_values('roc_auc', ascending=False)
sns.barplot(data=validation_plot, x='roc_auc', y='model', ax=ax, color='#4c78a8')
ax.set_xlim(0, 1)
ax.set_title('Clean rain/no-rain model validation ROC-AUC')
ax.set_xlabel('ROC-AUC on recent holdout period')
ax.set_ylabel('')
fig.tight_layout()
fig.savefig(OUTDIR / 'model_validation_roc_auc.png', dpi=180)
fig.savefig(OUTDIR / 'model_validation_roc_auc.svg')
plt.close(fig)

cm = confusion_matrix(y_test, pred_test)
fig, ax = plt.subplots(figsize=(4.5, 4))
sns.heatmap(cm, annot=True, fmt='d', cmap='Blues', cbar=False, ax=ax,
            xticklabels=['No rain pred', 'Rain pred'], yticklabels=['No rain obs', 'Rain obs'])
ax.set_title('Clean ensemble holdout confusion matrix')
fig.tight_layout()
fig.savefig(OUTDIR / 'ensemble_confusion_matrix.png', dpi=180)
fig.savefig(OUTDIR / 'ensemble_confusion_matrix.svg')
plt.close(fig)

summary = {
    'generated_utc': UTC_NOW.isoformat(),
    'local_time_hst_used': LOCAL_NOW.isoformat(),
    'location': {'name': 'Honolulu, Hawaii', 'latitude': LAT, 'longitude': LON},
    'target_date_hst': TOMORROW.isoformat(),
    'training_period': {'start': hist_start.isoformat(), 'end': hist_end.isoformat(), 'rows': int(len(model_df))},
    'rain_definition': 'daily precipitation_sum >= 0.1 mm',
    'historical_rain_frequency': float(y.mean()),
    'clean_model_probability_rain': ml_prob,
    'provider_precip_probability_max': provider_pop01,
    'forecast_precipitation_sum_mm': forecast_precip,
    'forecast_rain_sum_mm': forecast_rain,
    'forecast_precipitation_hours': precip_hours,
    'blended_probability_rain': final_prob,
    'classification': classification,
    'validation_note': 'Clean ML model excludes direct precipitation/rain/weather-code predictors; forecast precip probability and amount are blended afterward.',
    'best_validation_model_by_brier': validation.iloc[0].to_dict(),
    'raw_data_files': [
        'raw_open_meteo_historical_daily_honolulu.csv',
        'raw_open_meteo_forecast_daily_honolulu_tomorrow.csv',
        'raw_open_meteo_forecast_hourly_honolulu_tomorrow.csv'
    ],
    'prediction_files': [
        'tomorrow_rain_prediction_honolulu.csv',
        'tomorrow_hourly_rain_risk_honolulu.csv'
    ],
    'figure_files': [
        'honolulu_hourly_rain_risk_tomorrow.png',
        'model_validation_roc_auc.png',
        'ensemble_confusion_matrix.png'
    ],
    'important_note': 'This is an experimental data-driven estimate, not an official NWS forecast.'
}
with open(OUTDIR / 'summary.json', 'w') as f:
    json.dump(summary, f, indent=2)

pop_text = 'unavailable' if not np.isfinite(provider_pop01) else f'{provider_pop01:.1%}'
report = f'''# Honolulu Rain Prediction for Tomorrow

Generated UTC: {UTC_NOW.isoformat()}  
Local time used: {LOCAL_NOW.isoformat()}  
Target date: {TOMORROW.isoformat()} HST

## Result

- Classification: **{classification}**
- Blended probability of measurable rain: **{final_prob:.1%}**
- Clean supervised model probability: **{ml_prob:.1%}**
- Raw forecast max precipitation probability: **{pop_text}**
- Forecast precipitation sum: **{forecast_precip:.2f} mm**
- Forecast rain sum: **{forecast_rain:.2f} mm**
- Forecast precipitation hours: **{precip_hours:.1f} hours**

## Model setup

The clean supervised model was trained on Open-Meteo historical daily data for Honolulu from {hist_start.isoformat()} to {hist_end.isoformat()} using non-precipitation predictors: temperature, apparent temperature, wind, solar radiation, evapotranspiration, wind direction, and seasonal cycle. Direct precipitation, rain, precipitation-hours, and weather-code fields were excluded from the ML feature set to avoid an overly circular model.

The final estimate blends the clean ML probability with tomorrow-specific raw forecast precipitation probability and precipitation amount.

## Validation

See `model_validation_metrics.csv`, `model_validation_roc_auc.png`, and `ensemble_confusion_matrix.png`.

## Note

This is an experimental data-driven estimate and should not replace an official National Weather Service forecast.
'''
with open(OUTDIR / 'model_report.md', 'w') as f:
    f.write(report)

print('\nSUMMARY')
print(json.dumps(summary, indent=2))
print('\nVALIDATION')
print(validation.to_string(index=False))
print('\nPREDICTION')
print(prediction_table.to_string(index=False))
print(f'Outputs saved to: {OUTDIR}')
