import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import requests
import matplotlib.pyplot as plt

from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, balanced_accuracy_score, brier_score_loss, classification_report, confusion_matrix, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression

OUTDIR = Path('/outputs/honolulu_rain_prediction')
OUTDIR.mkdir(parents=True, exist_ok=True)

LAT = 21.3069
LON = -157.8583
TZ = 'Pacific/Honolulu'
LOCATION = 'Honolulu, Hawaii'
NOW_UTC = datetime.fromisoformat('2026-07-18T02:41:58+00:00')
TODAY_LOCAL = NOW_UTC.astimezone(ZoneInfo(TZ)).date()
START_DATE = '2015-01-01'
END_DATE = (TODAY_LOCAL - timedelta(days=2)).isoformat()

DAILY_VARS = [
    'temperature_2m_max','temperature_2m_min','temperature_2m_mean',
    'apparent_temperature_max','apparent_temperature_min','apparent_temperature_mean',
    'daylight_duration','sunshine_duration','shortwave_radiation_sum',
    'wind_speed_10m_max','wind_gusts_10m_max','wind_direction_10m_dominant',
    'precipitation_sum'
]
FEATURES = [v for v in DAILY_VARS if v != 'precipitation_sum']

def fetch_json(url, params):
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()

def daily_json_to_df(data):
    df = pd.DataFrame(data['daily'])
    df['time'] = pd.to_datetime(df['time'])
    return df

archive_params = {
    'latitude': LAT, 'longitude': LON, 'start_date': START_DATE, 'end_date': END_DATE,
    'daily': ','.join(DAILY_VARS), 'timezone': TZ,
    'temperature_unit': 'celsius', 'wind_speed_unit': 'kmh', 'precipitation_unit': 'mm'
}
hist = daily_json_to_df(fetch_json('https://archive-api.open-meteo.com/v1/archive', archive_params))
hist['rain'] = (hist['precipitation_sum'] > 0).astype(int)
hist['month'] = hist['time'].dt.month
hist['dayofyear_sin'] = np.sin(2 * np.pi * hist['time'].dt.dayofyear / 365.25)
hist['dayofyear_cos'] = np.cos(2 * np.pi * hist['time'].dt.dayofyear / 365.25)
MODEL_FEATURES = FEATURES + ['month', 'dayofyear_sin', 'dayofyear_cos']
hist_model = hist.dropna(subset=['rain']).sort_values('time').copy()

split_idx = int(len(hist_model) * 0.8)
train, test = hist_model.iloc[:split_idx], hist_model.iloc[split_idx:]
X_train, y_train = train[MODEL_FEATURES], train['rain']
X_test, y_test = test[MODEL_FEATURES], test['rain']

models = {
    'logistic_regression': Pipeline([
        ('imputer', SimpleImputer(strategy='median')),
        ('scaler', StandardScaler()),
        ('clf', LogisticRegression(max_iter=2000, class_weight='balanced')),
    ]),
    'random_forest': Pipeline([
        ('imputer', SimpleImputer(strategy='median')),
        ('clf', RandomForestClassifier(n_estimators=500, min_samples_leaf=8, random_state=42, class_weight='balanced_subsample', n_jobs=-1)),
    ]),
}

metrics = []
test_predictions = {}
for name, model in models.items():
    model.fit(X_train, y_train)
    proba = model.predict_proba(X_test)[:, 1]
    pred = (proba >= 0.5).astype(int)
    test_predictions[name] = proba
    metrics.append({
        'model': name,
        'accuracy': accuracy_score(y_test, pred),
        'balanced_accuracy': balanced_accuracy_score(y_test, pred),
        'roc_auc': roc_auc_score(y_test, proba),
        'brier_score': brier_score_loss(y_test, proba),
        'test_days': int(len(y_test)),
        'test_rain_rate': float(y_test.mean()),
    })
metrics_df = pd.DataFrame(metrics).sort_values(['brier_score', 'roc_auc'], ascending=[True, False])
best_name = str(metrics_df.iloc[0]['model'])
best_model = models[best_name]
selected_test_proba = test_predictions[best_name]
selected_test_pred = (selected_test_proba >= 0.5).astype(int)
cm = confusion_matrix(y_test, selected_test_pred)
report = classification_report(y_test, selected_test_pred, target_names=['no_rain', 'rain'], output_dict=True, zero_division=0)

best_model.fit(hist_model[MODEL_FEATURES], hist_model['rain'])

forecast_params = {
    'latitude': LAT, 'longitude': LON,
    'daily': ','.join(FEATURES + ['precipitation_probability_max', 'precipitation_sum']),
    'timezone': TZ, 'temperature_unit': 'celsius', 'wind_speed_unit': 'kmh', 'precipitation_unit': 'mm',
    'forecast_days': 7
}
forecast = daily_json_to_df(fetch_json('https://api.open-meteo.com/v1/forecast', forecast_params))
forecast['date'] = forecast['time'].dt.date
today_row = forecast.loc[forecast['date'] == TODAY_LOCAL].copy()
if today_row.empty:
    raise RuntimeError(f'No forecast row found for local date {TODAY_LOCAL}')
today_row['month'] = today_row['time'].dt.month
today_row['dayofyear_sin'] = np.sin(2 * np.pi * today_row['time'].dt.dayofyear / 365.25)
today_row['dayofyear_cos'] = np.cos(2 * np.pi * today_row['time'].dt.dayofyear / 365.25)

rain_probability_model = float(best_model.predict_proba(today_row[MODEL_FEATURES])[:, 1][0])
rain_prediction = bool(rain_probability_model >= 0.5)
historical_rain_rate = float(hist_model['rain'].mean())
month_rain_rate = float(hist_model.loc[hist_model['month'] == TODAY_LOCAL.month, 'rain'].mean())

summary = {
    'location': LOCATION,
    'latitude': LAT,
    'longitude': LON,
    'timezone': TZ,
    'current_utc': NOW_UTC.isoformat(),
    'local_today': TODAY_LOCAL.isoformat(),
    'historical_training_start': START_DATE,
    'historical_training_end': END_DATE,
    'historical_days': int(len(hist_model)),
    'historical_rain_rate': historical_rain_rate,
    'month_rain_rate': month_rain_rate,
    'selected_model': best_name,
    'model_rain_probability': rain_probability_model,
    'model_prediction_rain': rain_prediction,
    'open_meteo_forecast_precipitation_probability_max': None if pd.isna(today_row['precipitation_probability_max'].iloc[0]) else float(today_row['precipitation_probability_max'].iloc[0]),
    'open_meteo_forecast_precipitation_sum_mm': None if pd.isna(today_row['precipitation_sum'].iloc[0]) else float(today_row['precipitation_sum'].iloc[0]),
    'today_forecast_features': {k: (None if pd.isna(today_row[k].iloc[0]) else float(today_row[k].iloc[0])) for k in FEATURES},
    'selected_model_test_confusion_matrix': cm.tolist(),
    'selected_model_test_classification_report': report,
}

hist.to_csv(OUTDIR / 'honolulu_historical_daily_weather.csv', index=False)
forecast.to_csv(OUTDIR / 'honolulu_forecast_daily_weather.csv', index=False)
metrics_df.to_csv(OUTDIR / 'model_metrics.csv', index=False)
joblib.dump(best_model, OUTDIR / 'rain_classifier.joblib')
with open(OUTDIR / 'prediction_summary.json', 'w') as f:
    json.dump(summary, f, indent=2)

plot_df = test[['time', 'precipitation_sum', 'rain']].copy()
plot_df['predicted_rain_probability'] = selected_test_proba
fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
axes[0].plot(plot_df['time'], plot_df['precipitation_sum'], color='#1f77b4', lw=1)
axes[0].set_ylabel('Daily precip. (mm)')
axes[0].set_title('Honolulu test period: observed precipitation and model rain probabilities')
axes[0].grid(True, alpha=0.3)
axes[1].plot(plot_df['time'], plot_df['predicted_rain_probability'], color='#d62728', lw=1)
axes[1].axhline(0.5, color='black', linestyle='--', lw=1, label='0.5 threshold')
axes[1].set_ylabel('Predicted rain probability')
axes[1].set_xlabel('Date')
axes[1].set_ylim(0, 1)
axes[1].grid(True, alpha=0.3)
axes[1].legend()
fig.tight_layout()
fig.savefig(OUTDIR / 'test_period_predictions.png', dpi=160)
plt.close(fig)

fig, ax = plt.subplots(figsize=(9, 6))
if best_name == 'random_forest':
    vals = best_model.named_steps['clf'].feature_importances_
    imp = pd.DataFrame({'feature': MODEL_FEATURES, 'importance': vals}).sort_values('importance')
    ax.barh(imp['feature'], imp['importance'], color='#2ca02c')
    ax.set_xlabel('Random forest feature importance')
else:
    vals = best_model.named_steps['clf'].coef_[0]
    imp = pd.DataFrame({'feature': MODEL_FEATURES, 'coefficient': vals, 'abs': np.abs(vals)}).sort_values('abs')
    ax.barh(imp['feature'], imp['coefficient'], color=['#d62728' if v < 0 else '#2ca02c' for v in imp['coefficient']])
    ax.axvline(0, color='black', lw=1)
    ax.set_xlabel('Standardized logistic regression coefficient')
ax.set_title(f'Selected model: {best_name}')
ax.grid(True, axis='x', alpha=0.3)
fig.tight_layout()
fig.savefig(OUTDIR / 'model_feature_importance.png', dpi=160)
plt.close(fig)

metrics_text = metrics_df.to_string(index=False)
report_text = f"""# Honolulu Rain Prediction Report\n\nLocation: {LOCATION} ({LAT}, {LON})  \nTimezone: {TZ}  \nCurrent UTC time used: {NOW_UTC.isoformat()}  \nHonolulu local prediction date: {TODAY_LOCAL.isoformat()}\n\n## Data\n\nHistorical daily weather source: Open-Meteo Archive API  \nTraining period: {START_DATE} to {END_DATE}  \nHistorical days used: {len(hist_model)}\n\n## Model\n\nSelected model: {best_name}  \nTarget: rain if daily precipitation_sum > 0 mm  \nFeatures: {', '.join(MODEL_FEATURES)}\n\n## Test metrics\n\n```\n{metrics_text}\n```\n\nConfusion matrix for selected model on chronological test split, rows=true [no rain, rain], columns=predicted [no rain, rain]:\n\n```\n{cm.tolist()}\n```\n\n## Prediction for {TODAY_LOCAL.isoformat()}\n\nModel probability of measurable rain: {rain_probability_model:.3f} ({rain_probability_model*100:.1f}%)  \nModel classification at 0.5 threshold: {'RAIN' if rain_prediction else 'NO RAIN'}\n\nOpen-Meteo forecast precipitation probability max: {summary['open_meteo_forecast_precipitation_probability_max']}%  \nOpen-Meteo forecast precipitation sum: {summary['open_meteo_forecast_precipitation_sum_mm']} mm\n\nHistorical overall rain frequency: {historical_rain_rate:.3f} ({historical_rain_rate*100:.1f}%)  \nHistorical rain frequency for month {TODAY_LOCAL.month}: {month_rain_rate:.3f} ({month_rain_rate*100:.1f}%)\n\n## Notes\n\nThis is a lightweight statistical model trained from daily historical data and today's forecast summary features. It is not a substitute for operational radar-based nowcasting or official forecasts.\n"""
(OUTDIR / 'report.md').write_text(report_text)

print(json.dumps(summary, indent=2))
print('\nMETRICS')
print(metrics_text)
print(f'\nOutputs written to {OUTDIR}')
