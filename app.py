from flask import Flask
import re
import requests
import json
import sqlite3


app = Flask(__name__)

STATE_CODE_PATTERN = re.compile(r"^[A-Z]{2}$")
WEATHER_API_TIMEOUT_SECONDS = 10


def get_bird(state: str):
    conn = sqlite3.connect("./birds.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    print(f"select * from birds where abbreviation = '{state}';")
    row = cursor.execute(f"select * from birds where abbreviation = '{state}';")
    res = row.fetchall()
    list_accumulator = []
    for item in res:
        print(item)
        list_accumulator.append({k: item[k] for k in item.keys()})
    return json.dumps(list_accumulator)


def get_weather(state: str):
    try:
        r = requests.get(
            f"https://api.weather.gov/alerts/active?area={state}",
            timeout=WEATHER_API_TIMEOUT_SECONDS,
            headers={"User-Agent": "birds-app/1.0 (takehome)"},
        )
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"Weather API error for {state}: {exc}")
        return None


@app.get('/')
def hello():
    return "Add a 2 letter state param to learn about birds and the weather challenges they face.", \
           200, \
           {'Content-Type': 'text/html; charset=utf-8'}


@app.get('/health')
def health():
    return "ok", 200, {'Content-Type': 'text/plain; charset=utf-8'}


@app.get('/<state>')
def bird(state):
    state = state.strip().upper()
    if not STATE_CODE_PATTERN.match(state):
        return (
            json.dumps({"error": "Invalid state code. Use a 2-letter abbreviation."}),
            400,
            {"Content-Type": "application/json"},
        )

    bird_data = get_bird(state)
    print(bird_data)
    weather = get_weather(state)
    print(weather)
    out = str([bird_data, weather])
    return out, 200, {"Content-Type": "application/json"}

