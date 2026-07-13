import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from io import StringIO
from pathlib import Path

# NOTE: The RONI data were loaded through IDEA's climate-index tool.
# For reproducible local analysis in this terminal session, fetch the same public CSV
# from the installed data package cache if available; otherwise use the values saved in roni.csv.

csv_path = Path('roni.csv')
if not csv_path.exists():
    raise FileNotFoundError('roni.csv not found. Please save the RONI CSV before running this script.')

df = pd.read_csv(csv_path, parse_dates=['time'])
df = df.sort_values('time').dropna(subset=['value'])
df['year'] = df['time'].dt.year
df['month'] = df['time'].dt.month
df['decade'] = (df['year'] // 10) * 10

def phase(v):
    if v >= 0.5:
        return 'El Niño (>= 0.5)'
    if v <= -0.5:
        return 'La Niña (<= -0.5)'
    return 'Neutral'

df['phase'] = df['value'].apply(phase)
annual = df.groupby('year', as_index=False)['value'].mean()
monthly = df.groupby('month', as_index=False)['value'].agg(['mean','std']).reset_index()
phase_counts = df['phase'].value_counts().reindex(['El Niño (>= 0.5)', 'Neutral', 'La Niña (<= -0.5)'])
decade_stats = df.groupby('decade')['value'].agg(['count','mean','min','max']).reset_index()

# Linear trend on monthly values in index units/year
x = df['year'] + (df['month'] - 0.5) / 12
y = df['value'].to_numpy()
slope, intercept = np.polyfit(x, y, 1)

summary_lines = []
summary_lines.append('# RONI Dataset Exploration Summary\n')
summary_lines.append(f'- Date range: {df.time.min().date()} to {df.time.max().date()}')
summary_lines.append(f'- Rows: {len(df):,}')
summary_lines.append(f'- Mean RONI: {df.value.mean():.3f}')
summary_lines.append(f'- Standard deviation: {df.value.std():.3f}')
summary_lines.append(f'- Minimum: {df.value.min():.2f} on {df.loc[df.value.idxmin(), "time"].date()}')
summary_lines.append(f'- Maximum: {df.value.max():.2f} on {df.loc[df.value.idxmax(), "time"].date()}')
summary_lines.append(f'- Linear trend: {slope:.4f} RONI units/year ({slope*10:.3f} per decade)')
summary_lines.append('\n## ENSO phase counts by month\n')
summary_lines.append(phase_counts.to_markdown())
summary_lines.append('\n\n## Decadal summary\n')
summary_lines.append(decade_stats.to_markdown(index=False, floatfmt='.3f'))
Path('roni_summary.md').write_text('\n'.join(summary_lines))

# Save machine-readable outputs
annual.to_csv('roni_annual_means.csv', index=False)
monthly.to_csv('roni_monthly_climatology.csv', index=False)
decade_stats.to_csv('roni_decadal_stats.csv', index=False)

plt.style.use('seaborn-v0_8-whitegrid')

# 1. Time series with ENSO thresholds
fig, ax = plt.subplots(figsize=(14, 5))
ax.plot(df['time'], df['value'], color='#1f4e79', lw=1.2, label='RONI')
ax.axhline(0.5, color='#c0392b', ls='--', lw=1, label='El Niño threshold (+0.5)')
ax.axhline(-0.5, color='#2874a6', ls='--', lw=1, label='La Niña threshold (-0.5)')
ax.axhline(0, color='0.3', lw=0.8)
ax.fill_between(df['time'], 0.5, df['value'], where=df['value']>=0.5, color='#e74c3c', alpha=0.25)
ax.fill_between(df['time'], -0.5, df['value'], where=df['value']<=-0.5, color='#3498db', alpha=0.25)
ax.set_title('Relative Oceanic Niño Index (RONI), monthly values')
ax.set_ylabel('RONI value')
ax.set_xlabel('Year')
ax.legend(loc='upper right', ncol=3, fontsize=8)
fig.tight_layout()
fig.savefig('roni_time_series.png', dpi=180)
plt.close(fig)

# 2. Annual mean bars
fig, ax = plt.subplots(figsize=(14, 5))
colors = np.where(annual['value'] >= 0.5, '#e74c3c', np.where(annual['value'] <= -0.5, '#3498db', '#7f8c8d'))
ax.bar(annual['year'], annual['value'], color=colors, width=0.85)
ax.axhline(0, color='0.2', lw=0.8)
ax.axhline(0.5, color='#c0392b', ls='--', lw=0.8)
ax.axhline(-0.5, color='#2874a6', ls='--', lw=0.8)
ax.set_title('Annual mean RONI')
ax.set_ylabel('Annual mean RONI')
ax.set_xlabel('Year')
fig.tight_layout()
fig.savefig('roni_annual_bar.png', dpi=180)
plt.close(fig)

# 3. Monthly climatology
fig, ax = plt.subplots(figsize=(9, 5))
month_names = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
ax.errorbar(monthly['month'], monthly['mean'], yerr=monthly['std'], marker='o', capsize=3, color='#2c3e50')
ax.axhline(0, color='0.3', lw=0.8)
ax.set_xticks(range(1,13), month_names)
ax.set_title('RONI monthly climatology with ±1 std. dev.')
ax.set_ylabel('Mean RONI')
ax.set_xlabel('Month')
fig.tight_layout()
fig.savefig('roni_monthly_climatology.png', dpi=180)
plt.close(fig)

# 4. Distribution histogram
fig, ax = plt.subplots(figsize=(9, 5))
ax.hist(df['value'], bins=35, color='#566573', edgecolor='white', alpha=0.9)
ax.axvline(0.5, color='#c0392b', ls='--', label='El Niño threshold')
ax.axvline(-0.5, color='#2874a6', ls='--', label='La Niña threshold')
ax.axvline(df['value'].mean(), color='black', lw=1.2, label=f'Mean = {df.value.mean():.2f}')
ax.set_title('Distribution of monthly RONI values')
ax.set_xlabel('RONI value')
ax.set_ylabel('Count')
ax.legend()
fig.tight_layout()
fig.savefig('roni_distribution.png', dpi=180)
plt.close(fig)

print(Path('roni_summary.md').read_text())
print('\nCreated files:')
for p in ['roni_annual_means.csv','roni_monthly_climatology.csv','roni_decadal_stats.csv','roni_time_series.png','roni_annual_bar.png','roni_monthly_climatology.png','roni_distribution.png']:
    print('-', p, Path(p).stat().st_size, 'bytes')
