import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests
import matplotlib.pyplot as plt
import seaborn as sns

from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier, VotingClassifier
from sklearn.metrics import roc_auc_score, accuracy_score, brier_score_loss, confusion_matrix, classification_report
from sklearn.model_selection import TimeSeriesSplit

OUT = Path('/outputs/honolulu_rain_model')
OUT.mkdir(parents=True, exist_ok=True)

LAT, LON = 21.3069, -157.8583
TZ = 'Pacific/Honolulu'
# Current date supplied by system datetime tool in this session.
TODAY = pd.Timestamp('2026-07-12').date()
TOMORROW = TODAY + timedelta(days=1)

# Historical training window. Open-Meteo archive has long records; use daily derived from hourly observations/reanalysis.
HIST_START = '2019-01-01'
HIST_END = str(TODAY - timedelta(days=2))

HOURLY_VARS = [
    'temperature_2m', 'relative_humidity_2m', 'dew_point_2m', 'precipitation', 'rain',
    'cloud_cover', 'surface_pressure', 'wind_speed_10m', 'wind_gusts_10m'
]

def get_json(url, params, label):
    r = requests.get(url, params=params, timeout=60)
    print(f'{label} URL: {r.url}')
    print(f'{label} status: {r.status_code}')
    r.raise_for_status()
    data = r.json()
    if 'error' in data:
        raise RuntimeError(f'{label} API error: {data}')
    return data

def hourly_json_to_df(data):
    h = data['hourly']
    df = pd.DataFrame(h)
    df['time'] = pd.to_datetime(df['time'])
    for c in df.columns:
        if c != 'time':
            df[c] = pd.to_numeric(df[c], errors='coerce')
    df['date'] = df['time'].dt.date
    return df

def daily_features_from_hourly(hourly):
    g = hourly.groupby('date')
    daily = pd.DataFrame(index=pd.Index(sorted(hourly['date'].unique()), name='date'))
    daily['precip_sum_mm'] = g['precipitation'].sum(min_count=1)
    daily['rain_sum_mm'] = g['rain'].sum(min_count=1)
    daily['rain_hours'] = g['precipitation'].apply(lambda s: float((s.fillna(0) > 0.01).sum()))
    daily['temp_mean_c'] = g['temperature_2m'].mean()
    daily['temp_max_c'] = g['temperature_2m'].max()
    daily['temp_min_c'] = g['temperature_2m'].min()
    daily['rh_mean_pct'] = g['relative_humidity_2m'].mean()
    daily['rh_max_pct'] = g['relative_humidity_2m'].max()
    daily['dew_mean_c'] = g['dew_point_2m'].mean()
    daily['cloud_mean_pct'] = g['cloud_cover'].mean()
    daily['cloud_max_pct'] = g['cloud_cover'].max()
    daily['pressure_mean_hpa'] = g['surface_pressure'].mean()
    daily['wind_mean_kmh'] = g['wind_speed_10m'].mean()
    daily['gust_max_kmh'] = g['wind_gusts_10m'].max()
    daily = daily.reset_index()
    daily['date'] = pd.to_datetime(daily['date'])
    return daily

# Fetch historical raw hourly data.
archive_url = 'https://archive-api.open-meteo.com/v1/archive'
archive_params = {
    'latitude': LAT, 'longitude': LON,
    'start_date': HIST_START, 'end_date': HIST_END,
    'hourly': ','.join(HOURLY_VARS),
    'timezone': TZ,
}
archive_data = get_json(archive_url, archive_params, 'archive')
hist_hourly = hourly_json_to_df(archive_data)
hist_hourly.to_csv(OUT / 'raw_open_meteo_hourly_history_honolulu.csv', index=False)
hist_daily = daily_features_from_hourly(hist_hourly)
hist_daily.to_csv(OUT / 'daily_features_history_honolulu.csv', index=False)

# Supervised target: whether the NEXT day has measurable rain >= 0.1 mm.
df = hist_daily.copy().sort_values('date')
df['rain_tomorrow_mm'] = df['precip_sum_mm'].shift(-1)
df['rain_tomorrow'] = (df['rain_tomorrow_mm'] >= 0.1).astype(float)

# Add lag/rolling predictors available at end of current day.
for col in ['precip_sum_mm', 'rain_hours', 'temp_mean_c', 'rh_mean_pct', 'dew_mean_c', 'cloud_mean_pct', 'pressure_mean_hpa', 'wind_mean_kmh', 'gust_max_kmh']:
    df[f'{col}_lag1'] = df[col].shift(1)
    df[f'{col}_roll3'] = df[col].rolling(3, min_periods=2).mean()
    df[f'{col}_roll7'] = df[col].rolling(7, min_periods=4).mean()

df['month'] = df['date'].dt.month
df['doy_sin'] = np.sin(2*np.pi*df['date'].dt.dayofyear/365.25)
df['doy_cos'] = np.cos(2*np.pi*df['date'].dt.dayofyear/365.25)

feature_cols = [c for c in df.columns if c not in ['date','rain_tomorrow_mm','rain_tomorrow']]
model_df = df.dropna(subset=feature_cols + ['rain_tomorrow']).copy()
model_df['rain_tomorrow'] = model_df['rain_tomorrow'].astype(int)

# Time-based holdout: final 20% of historical record.
split_idx = int(len(model_df) * 0.80)
train, test = model_df.iloc[:split_idx], model_df.iloc[split_idx:]
X_train, y_train = train[feature_cols], train['rain_tomorrow']
X_test, y_test = test[feature_cols], test['rain_tomorrow']

# Ensemble model: calibrated-ish average of logistic, random forest, gradient boosting.
logit = Pipeline([('scale', StandardScaler()), ('clf', LogisticRegression(max_iter=1000, class_weight='balanced'))])
rf = RandomForestClassifier(n_estimators=350, min_samples_leaf=10, random_state=42, class_weight='balanced_subsample')
gb = GradientBoostingClassifier(random_state=42, learning_rate=0.04, n_estimators=150, max_depth=2)
model = VotingClassifier(estimators=[('logit', logit), ('rf', rf), ('gb', gb)], voting='soft', weights=[1.1, 1.0, 0.8])
model.fit(X_train, y_train)

p_test = model.predict_proba(X_test)[:, 1]
pred_test = (p_test >= 0.5).astype(int)
metrics = {
    'n_history_hourly_rows': int(len(hist_hourly)),
    'n_daily_rows': int(len(hist_daily)),
    'n_model_rows': int(len(model_df)),
    'train_rows': int(len(train)),
    'test_rows': int(len(test)),
    'test_rain_frequency': float(y_test.mean()),
    'test_auc': float(roc_auc_score(y_test, p_test)),
    'test_accuracy_threshold_0_5': float(accuracy_score(y_test, pred_test)),
    'test_brier_score': float(brier_score_loss(y_test, p_test)),
    'confusion_matrix_threshold_0_5': confusion_matrix(y_test, pred_test).tolist(),
    'rain_threshold_mm': 0.1,
}

# Refit on all available history.
model.fit(model_df[feature_cols], model_df['rain_tomorrow'])

# Fetch forecast data for today/tomorrow as raw inputs and provider comparison.
forecast_url = 'https://api.open-meteo.com/v1/forecast'
forecast_params = {
    'latitude': LAT, 'longitude': LON,
    'hourly': ','.join(HOURLY_VARS + ['precipitation_probability', 'weather_code']),
    'daily': 'precipitation_sum,precipitation_probability_max,weather_code',
    'timezone': TZ,
    'start_date': str(TODAY),
    'end_date': str(TOMORROW),
}
forecast_data = get_json(forecast_url, forecast_params, 'forecast')
fcst_hourly = hourly_json_to_df(forecast_data)
fcst_hourly.to_csv(OUT / 'raw_open_meteo_hourly_forecast_honolulu.csv', index=False)
fcst_daily = daily_features_from_hourly(fcst_hourly)
fcst_daily.to_csv(OUT / 'daily_features_forecast_honolulu.csv', index=False)

# Build model features for TODAY using historical recent days + forecast current day.
combo_daily = pd.concat([hist_daily, fcst_daily[fcst_daily['date'].dt.date == TODAY]], ignore_index=True)
combo_daily = combo_daily.drop_duplicates('date', keep='last').sort_values('date')
combo = combo_daily.copy()
for col in ['precip_sum_mm', 'rain_hours', 'temp_mean_c', 'rh_mean_pct', 'dew_mean_c', 'cloud_mean_pct', 'pressure_mean_hpa', 'wind_mean_kmh', 'gust_max_kmh']:
    combo[f'{col}_lag1'] = combo[col].shift(1)
    combo[f'{col}_roll3'] = combo[col].rolling(3, min_periods=2).mean()
    combo[f'{col}_roll7'] = combo[col].rolling(7, min_periods=4).mean()
combo['month'] = combo['date'].dt.month
combo['doy_sin'] = np.sin(2*np.pi*combo['date'].dt.dayofyear/365.25)
combo['doy_cos'] = np.cos(2*np.pi*combo['date'].dt.dayofyear/365.25)

row_today = combo[combo['date'].dt.date == TODAY].iloc[-1]
X_pred = row_today[feature_cols].to_frame().T
model_prob = float(model.predict_proba(X_pred)[:, 1][0])

# Provider forecast probability and rain amount for tomorrow.
daily_provider = pd.DataFrame(forecast_data['daily'])
daily_provider['time'] = pd.to_datetime(daily_provider['time']).dt.date
provider_tomorrow = daily_provider[daily_provider['time'] == TOMORROW].iloc[0].to_dict()
provider_prob = provider_tomorrow.get('precipitation_probability_max')
provider_precip = provider_tomorrow.get('precipitation_sum')
provider_code = provider_tomorrow.get('weather_code')

# Blend ML climatology/history model with direct NWP provider precipitation probability.
# This is still our model output, but acknowledges tomorrow's forecast forcing.
if provider_prob is not None and not pd.isna(provider_prob):
    blended_prob = 0.55 * model_prob + 0.45 * (float(provider_prob) / 100.0)
else:
    blended_prob = model_prob

rain_prediction = bool(blended_prob >= 0.5 or (provider_precip is not None and float(provider_precip) >= 0.1 and blended_prob >= 0.35))
label = 'Rain likely' if rain_prediction else 'Rain not likely'

# Save prediction table.
prediction = {
    'location': 'Honolulu, Hawaii',
    'latitude': LAT,
    'longitude': LON,
    'timezone': TZ,
    'today_local_date': str(TODAY),
    'target_date_local': str(TOMORROW),
    'rain_definition': 'daily precipitation >= 0.1 mm',
    'ml_probability_rain_tomorrow': model_prob,
    'provider_probability_max_tomorrow': None if provider_prob is None else float(provider_prob)/100.0,
    'provider_precipitation_sum_tomorrow_mm': None if provider_precip is None else float(provider_precip),
    'provider_weather_code_tomorrow': None if provider_code is None else int(provider_code),
    'final_blended_probability_rain_tomorrow': blended_prob,
    'prediction': label,
    'metrics': metrics,
    'data_source': 'Open-Meteo archive and forecast APIs',
}
with open(OUT / 'prediction_summary.json', 'w') as f:
    json.dump(prediction, f, indent=2)

pd.DataFrame([prediction | {k: v for k, v in metrics.items() if not isinstance(v, list)}]).to_csv(OUT / 'prediction_summary.csv', index=False)

# Diagnostic plots.
sns.set_theme(style='whitegrid')
fig, axes = plt.subplots(2, 1, figsize=(10, 8), constrained_layout=True)

# Historical rainfall distribution and tomorrow marker.
ax = axes[0]
hist_daily['precip_sum_mm'].clip(upper=20).hist(ax=ax, bins=40, color='#4C78A8', alpha=0.8)
ax.axvline(float(provider_precip or 0), color='#E45756', lw=2, label=f"Provider tomorrow: {float(provider_precip or 0):.1f} mm")
ax.set_title('Honolulu Daily Precipitation Distribution, clipped at 20 mm')
ax.set_xlabel('Daily precipitation (mm)')
ax.set_ylabel('Number of days')
ax.legend()

# Test set reliability-ish scatter/rolling calibration.
ax = axes[1]
cal = pd.DataFrame({'prob': p_test, 'obs': y_test.values}).sort_values('prob')
cal['bin'] = pd.qcut(cal['prob'], q=8, duplicates='drop')
cal_bin = cal.groupby('bin', observed=True).agg(mean_prob=('prob','mean'), observed_freq=('obs','mean'), n=('obs','size')).reset_index()
ax.plot([0,1], [0,1], 'k--', alpha=0.5, label='Perfect calibration')
ax.scatter(cal_bin['mean_prob'], cal_bin['observed_freq'], s=cal_bin['n']*2, color='#72B7B2', edgecolor='k', label='Model bins')
ax.axvline(blended_prob, color='#E45756', lw=2, label=f"Tomorrow final: {blended_prob:.0%}")
ax.set_xlim(0,1); ax.set_ylim(0,1)
ax.set_xlabel('Predicted probability')
ax.set_ylabel('Observed rain frequency')
ax.set_title('Holdout Calibration Check')
ax.legend()
fig.savefig(OUT / 'model_diagnostics.png', dpi=180)
fig.savefig(OUT / 'model_diagnostics.svg')
plt.close(fig)

# Hourly forecast chart for tomorrow.
tom_hourly = fcst_hourly[fcst_hourly['date'] == TOMORROW].copy()
fig, ax1 = plt.subplots(figsize=(11, 5), constrained_layout=True)
ax2 = ax1.twinx()
ax1.bar(tom_hourly['time'].dt.hour, tom_hourly['precipitation'], color='#4C78A8', alpha=0.65, label='Forecast precip (mm)')
if 'precipitation_probability' in tom_hourly:
    ax2.plot(tom_hourly['time'].dt.hour, tom_hourly['precipitation_probability'], color='#E45756', marker='o', label='Provider precip probability (%)')
ax1.set_xlabel('Hour of day, HST')
ax1.set_ylabel('Forecast precipitation (mm)')
ax2.set_ylabel('Precipitation probability (%)')
ax1.set_title(f'Honolulu Hourly Rain Forecast for {TOMORROW}')
ax1.set_xticks(range(0,24,2))
lines1, labels1 = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper right')
fig.savefig(OUT / 'hourly_forecast_tomorrow.png', dpi=180)
fig.savefig(OUT / 'hourly_forecast_tomorrow.svg')
plt.close(fig)

print(json.dumps(prediction, indent=2))
print('Saved outputs to', OUT)
