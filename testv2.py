import os
import requests
import json
import base64
from io import BytesIO
from flask import Flask, request, jsonify, render_template_string

# Flask app and WeatherAPI key
app = Flask(__name__)
WEATHERAPI_API_KEY = "509a4bd590d64c0fb6a33306250810" # Put your WeatherAPI key here or set as env variable


@app.route('/api/get_weather', methods=['POST'])
def get_weather():
  data = request.get_json() or {}
  query = data.get('query')
  units = data.get('units', 'metric')
  forecast_days = int(data.get('forecast_days', 0))
  include_aqi = bool(data.get('include_aqi', False))

  if not query:
    return jsonify({"error": "Location query is missing."}), 400

  # Decide endpoint: current.json or forecast.json
  if forecast_days and forecast_days > 0:
    api_url = f"http://api.weatherapi.com/v1/forecast.json?key={WEATHERAPI_API_KEY}&q={query}&days={min(3,forecast_days)}&aqi={'yes' if include_aqi else 'no'}"
  else:
    api_url = f"http://api.weatherapi.com/v1/current.json?key={WEATHERAPI_API_KEY}&q={query}&aqi={'yes' if include_aqi else 'no'}"
  try:
    resp = requests.get(api_url, timeout=10)
    weather_data = resp.json()
    if 'error' in weather_data:
      return jsonify({"error": weather_data['error'].get('message', 'Unknown WeatherAPI error.')}), 404
  except requests.RequestException as e:
    return jsonify({"error": "Failed to fetch weather data."}), 500

  try:
    location = weather_data.get('location', {})
    location_name = f"{location.get('name','')}, {location.get('country','')}"

    # Current data
    current = weather_data.get('current', {})
    temp_c = float(current.get('temp_c', 0))
    rh = float(current.get('humidity', 0))
    wind_speed_mps = float(current.get('wind_kph', 0)) / 3.6
    condition_text = current.get('condition', {}).get('text', '')
    condition_icon = current.get('condition', {}).get('icon', '')

    # Compute simple Human Comfort Index (user-provided formula)
    try:
      temp_val = temp_c
      rh_val = rh
      hci = (temp_val + rh_val) / 4.0
    except Exception:
      hci = None

    # Map HCI to comfort indicators
    comfort_level = None
    comfort_emoji = ''
    comfort_description = ''
    comfort_class = ''
    try:
      hci_val = float(hci)
      if hci_val < 10:
        comfort_level = 'Cold'
        comfort_emoji = '🧊'
        comfort_description = 'Uncomfortably cold for most people'
        comfort_class = 'text-blue-300 bg-blue-900/30'
      elif hci_val < 18:
        comfort_level = 'Cool'
        comfort_emoji = '❄️'
        comfort_description = 'Cool but tolerable; may need light clothing'
        comfort_class = 'text-sky-200 bg-sky-900/30'
      elif hci_val < 24:
        comfort_level = 'Comfortable'
        comfort_emoji = '🙂'
        comfort_description = 'Ideal thermal comfort for most individuals'
        comfort_class = 'text-green-200 bg-green-900/20'
      elif hci_val < 28:
        comfort_level = 'Warm'
        comfort_emoji = '😅'
        comfort_description = 'Slightly warm, may feel humid or stuffy'
        comfort_class = 'text-yellow-200 bg-yellow-900/20'
      elif hci_val < 32:
        comfort_level = 'Hot'
        comfort_emoji = '🥵'
        comfort_description = 'Uncomfortable heat, risk of heat stress'
        comfort_class = 'text-orange-100 bg-orange-900/20'
      else:
        comfort_level = 'Very Hot'
        comfort_emoji = '🔥'
        comfort_description = 'High risk of heat exhaustion or heatstroke'
        comfort_class = 'text-red-100 bg-red-900/25'
    except Exception:
      hci_val = None

    result = {
      "location_name": location_name,
      "temperature_c": f"{temp_c:.1f}",
      "humidity": f"{rh:.0f}",
      "wind_speed": f"{wind_speed_mps:.1f}",
      "condition": condition_text,
      "condition_icon": condition_icon,
      "local_time": location.get('localtime', ''),
      "hci": f"{hci:.2f}" if hci is not None else None,
      "comfort_level": comfort_level,
      "comfort_emoji": comfort_emoji,
      "comfort_description": comfort_description,
      "comfort_class": comfort_class
    }

    # Forecast (if present)
    forecast_days_data = []
    if 'forecast' in weather_data and weather_data.get('forecast'):
      for day in weather_data['forecast'].get('forecastday', []):
        day_info = day.get('day', {})
        forecast_days_data.append({
          'date': day.get('date'),
          'maxtemp_c': day_info.get('maxtemp_c'),
          'mintemp_c': day_info.get('mintemp_c'),
          'avgtemp_c': day_info.get('avgtemp_c'),
          'condition': day_info.get('condition', {}).get('text')
        })
      result['forecast'] = forecast_days_data

    # AQI (if requested and present)
    if include_aqi and 'current' in weather_data and weather_data['current'].get('air_quality'):
      result['aqi'] = weather_data['current']['air_quality']

    return jsonify(result)
  except Exception:
    return jsonify({"error": "Invalid data from WeatherAPI."}), 500

HTML_TEMPLATE = r"""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Weather.ai</title>
    <script src="https://cdn.tailwindcss.com"></script>
  </head>
  <body class="bg-slate-900 text-slate-100 min-h-screen flex items-center justify-center">
    <div class="max-w-xl w-full p-6">
      <h1 class="text-3xl font-bold mb-4">Weather.ai</h1>

      <div class="mb-4">
        <input id="location-input" type="text" placeholder="Enter city or 'lat,lon'" class="w-full p-3 rounded-md bg-slate-800 border border-slate-700" />
        <div class="mt-3 grid grid-cols-3 gap-2">
          <select id="units" class="p-2 rounded-md bg-slate-800 border border-slate-700">
            <option value="metric">Celsius</option>
            <option value="imperial">Fahrenheit</option>
          </select>
          <select id="forecast_days" class="p-2 rounded-md bg-slate-800 border border-slate-700">
            <option value="0">Now</option>
            <option value="1">1 day</option>
            <option value="2">2 days</option>
            <option value="3">3 days</option>
          </select>
          <label class="flex items-center gap-2"><input id="include_aqi" type="checkbox"/> Include AQI</label>
        </div>
        <button id="fetch-weather-btn" class="mt-3 w-full p-3 bg-blue-600 rounded-md">Get Weather</button>
      </div>

      <div id="status-message" class="mb-3 text-sm opacity-80"></div>

      <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
        <div id="hci-card" class="bg-amber-500/10 p-4 rounded-md flex flex-col items-center justify-center">
          <div class="text-sm text-amber-300">Human Comfort Index</div>
          <div id="hci-value" class="text-3xl font-bold">--</div>
          <div id="comfort-badge" class="mt-2 px-3 py-1 rounded-full text-sm">--</div>
          <div id="comfort-desc" class="mt-2 text-xs text-slate-300 text-center">--</div>
        </div>
        <div class="md:col-span-2 bg-slate-800 p-4 rounded-md" id="weather-output">
        <p id="loc" class="text-xl font-semibold">--</p>
        <div class="flex items-center gap-3">
          <img id="cond_icon" src="" alt="icon" class="w-12 h-12"/>
          <p id="cond" class="text-sm text-slate-300">--</p>
        </div>
        <div class="grid grid-cols-3 gap-4 mt-3">
          <div>
            <div class="text-xs text-slate-400">Temp (°C)</div>
            <div id="temp" class="text-lg font-bold">--</div>
          </div>
          <div>
            <div class="text-xs text-slate-400">Humidity</div>
            <div id="hum" class="text-lg font-bold">--</div>
          </div>
          <div>
            <div class="text-xs text-slate-400">Wind (m/s)</div>
            <div id="wind" class="text-lg font-bold">--</div>
          </div>
        </div>
        <div id="aqi" class="mt-3 text-sm text-slate-300"></div>
        <div id="forecast" class="mt-3 text-sm text-slate-300"></div>
      </div>
    </div>

    <script>
      document.addEventListener('DOMContentLoaded', () => {
        const fetchButton = document.getElementById('fetch-weather-btn');
        const locInput = document.getElementById('location-input');
        const status = document.getElementById('status-message');

        const outLoc = document.getElementById('loc');
        const outCond = document.getElementById('cond');
        const outTemp = document.getElementById('temp');
        const outHum = document.getElementById('hum');
        const outWind = document.getElementById('wind');

        async function fetchWeather(){
          const q = locInput.value || 'London';
          const units = document.getElementById('units').value;
          const days = parseInt(document.getElementById('forecast_days').value, 10);
          const include_aqi = document.getElementById('include_aqi').checked;
          fetchButton.disabled = true;
          status.textContent = 'Fetching...';
          try{
            const r = await fetch('/api/get_weather', {
              method: 'POST', headers: {'Content-Type':'application/json'},
              body: JSON.stringify({query: q, units: units, forecast_days: days, include_aqi: include_aqi})
            });
            const data = await r.json();
            if(!r.ok){ status.textContent = data.error || 'Error'; return; }
            outLoc.textContent = data.location_name;
            outCond.textContent = data.condition;
            outTemp.textContent = data.temperature_c + (units === 'metric' ? ' °C' : ' °F');
            // HCI and comfort indicators
            const hciVal = document.getElementById('hci-value');
            const comfortBadge = document.getElementById('comfort-badge');
            const comfortDesc = document.getElementById('comfort-desc');
            if(data.hci){ hciVal.textContent = data.hci; } else { hciVal.textContent = '--'; }
            if(data.comfort_level){
              comfortBadge.textContent = `${data.comfort_emoji} ${data.comfort_level}`;
              comfortDesc.textContent = data.comfort_description || '';
              // apply simple class if provided
              if(data.comfort_class){ comfortBadge.className = 'mt-2 px-3 py-1 rounded-full text-sm ' + data.comfort_class; }
            } else {
              comfortBadge.textContent = '--';
              comfortDesc.textContent = '';
            }
            // Condition icon (WeatherAPI icons often start with //)
            const iconEl = document.getElementById('cond_icon');
            if(data.condition_icon){
              iconEl.src = data.condition_icon.startsWith('//') ? 'https:' + data.condition_icon : data.condition_icon;
              iconEl.style.display = 'inline-block';
            } else { iconEl.style.display = 'none'; }
            outHum.textContent = data.humidity + ' %';
            outWind.textContent = data.wind_speed + ' m/s';
            status.textContent = 'Updated: ' + (data.local_time || '');

            // If forecast provided, render it
            const forecastEl = document.getElementById('forecast');
            if(data.forecast && data.forecast.length){
              forecastEl.innerHTML = data.forecast.map(d => `<div class="p-2 bg-slate-700 rounded-md mt-2">${d.date}: ${d.condition} — ${d.avgtemp_c} °C (min ${d.mintemp_c}, max ${d.maxtemp_c})</div>`).join('');
            } else { forecastEl.innerHTML = ''; }

            // If AQI included, render a small summary
            const aqiEl = document.getElementById('aqi');
            if(data.aqi){
              const pm25 = data.aqi.pm2_5 || data.aqi['pm2_5'] || null;
              aqiEl.textContent = 'Air Quality (PM2.5): ' + (pm25 ? pm25.toFixed(2) : 'N/A');
            } else { aqiEl.textContent = ''; }
          }catch(e){
            status.textContent = 'Network error';
          }finally{ fetchButton.disabled = false; }
        }

        fetchButton.addEventListener('click', fetchWeather);
        locInput.addEventListener('keypress', (e)=>{ if(e.key==='Enter') fetchWeather(); });
      });
    </script>
  </body>
</html>
"""


@app.route('/')
def index():
  return render_template_string(HTML_TEMPLATE)

if __name__ == '__main__':
    # Using host='0.0.0.0' for environment compatibility
    print("---------------------------------------------------------------------")
    print("Flask Application 'Weather.AI' is starting...")
    print("Access the dashboard at: http://127.0.0.1:5000/")
    print("---------------------------------------------------------------------")
    app.run(debug=True, host='0.0.0.0')
