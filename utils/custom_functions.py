custom_tool = """
import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
import requests
from io import StringIO
from datetime import datetime, timedelta, timezone

def get_datetime():
    now_utc = datetime.now(timezone.utc)
    if now_utc.microsecond >= 500_000:
        now_utc += timedelta(seconds=1)
    now_utc = now_utc.replace(microsecond=0)
    return {
        "iso": now_utc.isoformat(),
        "human": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    }

def fractional_year_to_datetime(year):
    # Convert fractional year (e.g., 1992.7978142) to datetime.
    year_int = int(year)
    fraction = year - year_int
    start_of_year = datetime(year_int, 1, 1)
    days_in_year = (datetime(year_int + 1, 1, 1) - start_of_year).days
    return start_of_year + timedelta(days=fraction * days_in_year)

"""
