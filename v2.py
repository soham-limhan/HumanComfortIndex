import os
import requests
import json
import base64
from io import BytesIO
from flask import Flask, request, jsonify, render_template_string

# Flask app and WeatherAPI key
app = Flask(__name__)
WEATHERAPI_API_KEY = "509a4bd590d64c0fb6a33306250810" # Put your WeatherAPI key here or set as env variable


# ----- HCI helpers -----
def pm25_to_aqi(pm25):
  """Convert PM2.5 concentration (µg/m3) to US EPA AQI (0-500) using standard breakpoints."""
  if pm25 is None:
    return None
  try:
    c = float(pm25)
  except Exception:
    return None
  # Breakpoints for PM2.5 (µg/m3)
  breakpoints = [
    (0.0, 12.0, 0, 50),
    (12.1, 35.4, 51, 100),
    (35.5, 55.4, 101, 150),
    (55.5, 150.4, 151, 200),
    (150.5, 250.4, 201, 300),
    (250.5, 350.4, 301, 400),
    (350.5, 500.4, 401, 500),
  ]
  for (clow, chigh, ilow, ihigh) in breakpoints:
    if clow <= c <= chigh:
      aqi = ((ihigh - ilow) / (chigh - clow)) * (c - clow) + ilow
      return round(aqi, 0)
  # If beyond table, cap
  if c > 500.4:
    return 500
  return None


def normalize(value, min_v, max_v):
  try:
    v = float(value)
  except Exception:
    return None
  if max_v == min_v:
    return 0
  t = (v - min_v) / (max_v - min_v)
  t = max(0.0, min(1.0, t))
  return t * 100.0


def compute_component_scores(temp_c, humidity, uv, wind_kph, aqi_pm25, aqi_us=None):
  """Return component scores (0-100 where higher = worse) for AQI, Temp+Humidity, UV, Wind.

  temp_c: degrees C
  humidity: percent
  uv: UV index (0-11+)
  wind_kph: wind speed in kph
  aqi_pm25: pm2_5 concentration (µg/m3)
  aqi_us: optionally precomputed AQI (0-500)
  """
  # AQI score: compute AQI from pm2.5 if available, else use provided aqi_us
  aqi_val = None
  if aqi_us is not None:
    try:
      aqi_val = float(aqi_us)
    except Exception:
      aqi_val = None
  if aqi_val is None and aqi_pm25 is not None:
    aqi_calc = pm25_to_aqi(aqi_pm25)
    if aqi_calc is not None:
      aqi_val = float(aqi_calc)

  aqi_score = None
  if aqi_val is not None:
    # Map AQI 0-500 to 0-100
    aqi_score = max(0.0, min(100.0, aqi_val / 500.0 * 100.0))

  # Temperature: normalize between -10C and 40C -> 0-100 (higher = worse)
  temp_score = normalize(temp_c, -10.0, 40.0) if temp_c is not None else None
  # Humidity: treat 0-100% linearly -> 0-100 (higher humidity considered worse here)
  humidity_score = None
  try:
    if humidity is not None:
      humidity_score = max(0.0, min(100.0, float(humidity)))
  except Exception:
    humidity_score = None

  # UV: map 0-11+ to 0-100
  uv_score = None
  try:
    if uv is not None:
      uv_score = max(0.0, min(100.0, float(uv) / 11.0 * 100.0))
  except Exception:
    uv_score = None

  # Wind: map 0-120 kph to 0-100
  wind_score = None
  try:
    if wind_kph is not None:
      wind_score = max(0.0, min(100.0, float(wind_kph) / 120.0 * 100.0))
  except Exception:
    wind_score = None

  return {
    'aqi_score': aqi_score,
    'temp_score': temp_score,
    'humidity_score': humidity_score,
    'uv_score': uv_score,
    'wind_score': wind_score,
    'aqi_val': aqi_val
  }

# Profile weights (percent -> fraction)
PROFILE_WEIGHTS = {
  # split the Temp+Humidity weight into equal temp and humidity parts
  'general': {'aqi':0.30,'temp':0.15,'humidity':0.15,'uv':0.20,'wind':0.20},
  'asthma': {'aqi':0.50,'temp':0.10,'humidity':0.10,'uv':0.15,'wind':0.15},
  'elderly_child': {'aqi':0.30,'temp':0.20,'humidity':0.20,'uv':0.20,'wind':0.10},
  'athlete': {'aqi':0.20,'temp':0.15,'humidity':0.15,'uv':0.20,'wind':0.30},
}


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
    avgtemp_c = float(current.get('avgtemp_c', temp_c))  # Fallback to current temp if avg not present
    rh = float(current.get('humidity', 0))
    wind_speed_mps = float(current.get('wind_kph', 0)) / 3.6
    condition_text = current.get('condition', {}).get('text', '')
    condition_icon = current.get('condition', {}).get('icon', '')
    # UV index may appear in current or in forecast day data
    uv_index = None
    if 'uv' in current:
      try:
        uv_index = float(current.get('uv'))
      except Exception:
        uv_index = None

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

    # Ensure avg temperature key is exposed as `avgtemp_c` (consistent with forecast entries)
    result = {
      "location_name": location_name,
      "temperature_c": f"{temp_c:.1f}",
      "avgtemp_c": f"{avgtemp_c:.1f}",
      "humidity": f"{rh:.0f}",
      "wind_speed": f"{wind_speed_mps:.1f}",
      "wind_kph": f"{current.get('wind_kph', 0):.1f}",
      "condition": condition_text,
      "condition_icon": condition_icon,
      "local_time": location.get('localtime', ''),
      "hci": f"{hci:.2f}" if hci is not None else None,
      "comfort_level": comfort_level,
      "comfort_emoji": comfort_emoji,
      "comfort_description": comfort_description,
      "comfort_class": comfort_class,
      "current_date": location.get('localtime', '').split(' ')[0] if location.get('localtime') else None
    }

    # Forecast (if present)
    forecast_days_data = []
    # Extract AQI data early (but do not require it)
    aqi_data = None
    if 'current' in weather_data:
      aqi_data = weather_data['current'].get('air_quality') if isinstance(weather_data['current'], dict) else None
    pm25 = None
    if isinstance(aqi_data, dict):
      for k in ('pm2_5','pm25','pm2_5_us','pm25_us'):
        if k in aqi_data:
          try:
            pm25 = float(aqi_data[k])
            break
          except Exception:
            pm25 = None

    # Compute component scores for current conditions (used to derive profile HCI)
    comp_scores = compute_component_scores(temp_c, rh, uv_index, float(current.get('wind_kph', 0)), pm25)
    result['component_scores'] = comp_scores

    # Compute HCI for every profile so we can clamp forecast HCI values
    profile_hcis = {}
    for prof, weights in PROFILE_WEIGHTS.items():
      contrib = 0.0
      wsum = 0.0
      if comp_scores.get('aqi_score') is not None and weights.get('aqi'):
        contrib += weights['aqi'] * comp_scores['aqi_score']
        wsum += weights['aqi']
      if comp_scores.get('temp_score') is not None and weights.get('temp'):
        contrib += weights['temp'] * comp_scores['temp_score']
        wsum += weights['temp']
      if comp_scores.get('humidity_score') is not None and weights.get('humidity'):
        contrib += weights['humidity'] * comp_scores['humidity_score']
        wsum += weights['humidity']
      if comp_scores.get('uv_score') is not None and weights.get('uv'):
        contrib += weights['uv'] * comp_scores['uv_score']
        wsum += weights['uv']
      if comp_scores.get('wind_score') is not None and weights.get('wind'):
        contrib += weights['wind'] * comp_scores['wind_score']
        wsum += weights['wind']
      if wsum > 0:
        norm_contrib = contrib / wsum
        prof_hci = 100.0 - norm_contrib
        profile_hcis[prof] = round(prof_hci, 2)
      else:
        profile_hcis[prof] = None
    result['profile_hcis'] = profile_hcis

    # Determine which HCI baseline to use for clamping forecast HCI (profile-specific if available)
    sel_profile = (data.get('profile') or 'general').lower()
    current_hci_baseline = profile_hcis.get(sel_profile) if profile_hcis.get(sel_profile) is not None else (float(hci) if hci is not None else None)
    # store chosen baseline as top-level hci in result (string/number preserved)
    result['hci'] = current_hci_baseline

    if 'forecast' in weather_data and weather_data.get('forecast'):
      for day in weather_data['forecast'].get('forecastday', []):
        day_info = day.get('day', {})
        # Extract UV for the day if present
        day_uv = None
        if 'uv' in day_info:
          try:
            day_uv = float(day_info.get('uv'))
          except Exception:
            day_uv = None
        # Raw numeric temps (may be None)
        maxt = day_info.get('maxtemp_c')
        mint = day_info.get('mintemp_c')
        avgt = day_info.get('avgtemp_c')
        # If avg is missing but min/max are present, compute a simple average
        if avgt is None and maxt is not None and mint is not None:
          try:
            avgt = (float(maxt) + float(mint)) / 2.0
          except Exception:
            avgt = None

        # Extract humidity and wind for forecast day (if available)
        day_humidity = day_info.get('avghumidity', None)
        if day_humidity is None:
          day_humidity = rh  # Use current humidity if forecast doesn't have it
        
        day_wind = day.get('hour', [{}])[0].get('wind_kph', 0) if day.get('hour') else 0
        if day_wind == 0:
          day_wind = day_info.get('maxwind_kph', 0)  # Fallback to max wind
        
        # Calculate HCI for this forecast day using profile-weighted approach
        forecast_hci = None
        if avgt is not None:
          try:
            # Compute component scores for forecast day
            day_comp = compute_component_scores(float(avgt), float(day_humidity), day_uv, float(day_wind), pm25)
            
            # Use selected profile's weights to compute forecast HCI
            sel_prof = (data.get('profile') or 'general').lower()
            prof_weights = PROFILE_WEIGHTS.get(sel_prof)
            if prof_weights and day_comp:
              hci_contrib = 0.0
              weight_sum = 0.0
              if day_comp.get('aqi_score') is not None and prof_weights.get('aqi'):
                hci_contrib += prof_weights['aqi'] * day_comp['aqi_score']
                weight_sum += prof_weights['aqi']
              if day_comp.get('temp_score') is not None and prof_weights.get('temp'):
                hci_contrib += prof_weights['temp'] * day_comp['temp_score']
                weight_sum += prof_weights['temp']
              if day_comp.get('humidity_score') is not None and prof_weights.get('humidity'):
                hci_contrib += prof_weights['humidity'] * day_comp['humidity_score']
                weight_sum += prof_weights['humidity']
              if day_comp.get('uv_score') is not None and prof_weights.get('uv'):
                hci_contrib += prof_weights['uv'] * day_comp['uv_score']
                weight_sum += prof_weights['uv']
              if day_comp.get('wind_score') is not None and prof_weights.get('wind'):
                hci_contrib += prof_weights['wind'] * day_comp['wind_score']
                weight_sum += prof_weights['wind']
              
              if weight_sum > 0:
                forecast_hci = 100.0 - (hci_contrib / weight_sum)
                # Clamp to stay within ±15 of current profile HCI
                if current_hci_baseline is not None:
                  baseline = float(current_hci_baseline) if isinstance(current_hci_baseline, str) else current_hci_baseline
                  min_hci = max(0, baseline - 15)
                  max_hci = min(100, baseline + 15)
                  forecast_hci = max(min_hci, min(max_hci, forecast_hci))
          except Exception as e:
            forecast_hci = None
        
        forecast_comp = None

        # Format temperatures to one decimal where available
        forecast_days_data.append({
          'date': day.get('date'),
          'maxtemp_c': f"{maxt:.1f}" if maxt is not None else None,
          'mintemp_c': f"{mint:.1f}" if mint is not None else None,
          'avgtemp_c': f"{avgt:.1f}" if avgt is not None else None,
          'humidity': f"{day_humidity:.0f}" if day_humidity is not None else None,
          'wind_kph': f"{day_wind:.1f}" if day_wind is not None else None,
          'uv': f"{day_uv:.1f}" if day_uv is not None else None,
          'condition': day_info.get('condition', {}).get('text'),
          'possible_hci': f"{forecast_hci:.2f}" if forecast_hci is not None else None,
          'forecast_components': forecast_comp
        })
      result['forecast'] = forecast_days_data

      
      # If profile provided, compute profile-weighted HCI using the new formula
      profile = data.get('profile')
      if profile:
        pw = PROFILE_WEIGHTS.get(profile.lower())
        if pw:
          hci_contrib = 0.0
          weight_sum = 0.0
          if comp_scores.get('aqi_score') is not None and pw.get('aqi'):
            hci_contrib += pw['aqi'] * comp_scores['aqi_score']
            weight_sum += pw['aqi']
          if comp_scores.get('temp_score') is not None and pw.get('temp'):
            hci_contrib += pw['temp'] * comp_scores['temp_score']
            weight_sum += pw['temp']
          if comp_scores.get('humidity_score') is not None and pw.get('humidity'):
            hci_contrib += pw['humidity'] * comp_scores['humidity_score']
            weight_sum += pw['humidity']
          if comp_scores.get('uv_score') is not None and pw.get('uv'):
            hci_contrib += pw['uv'] * comp_scores['uv_score']
            weight_sum += pw['uv']
          if comp_scores.get('wind_score') is not None and pw.get('wind'):
            hci_contrib += pw['wind'] * comp_scores['wind_score']
            weight_sum += pw['wind']

          if weight_sum > 0:
            normalized_contrib = hci_contrib / weight_sum
            hci_weighted = 100.0 - normalized_contrib
            result['weighted_hci'] = round(hci_weighted,2)
            result['comfort_score'] = round(hci_weighted,2)
            result['profile_used'] = profile.lower()
            # Map comfort_score to band and recommendations
            def interpret_comfort(score, aqi_val=None, temp=None, hum=None, uv=None, wind_kph=None):
              s = float(score)
              band = None
              desc = None
              env = []
              rec = []
              if s >= 90:
                band = 'Excellent / Very Comfortable'
                desc = 'Ideal outdoor conditions. Air is clean, UV moderate, pleasant temperature and humidity.'
                rec = ['Best time for outdoor activity, exercise, travel.']
              elif s >= 75:
                band = 'Comfortable'
                desc = 'Slight variation in one or two parameters but overall safe for most people.'
                rec = ['Good for outdoor activity; stay hydrated and use sunscreen if UV is high.']
              elif s >= 60:
                band = 'Moderate Comfort'
                desc = 'Conditions acceptable for healthy individuals, mild discomfort for sensitive people.'
                rec = ['Limit long outdoor exposure, protective gear recommended.']
              elif s >= 45:
                band = 'Uncomfortable'
                desc = 'Increasing discomfort due to high heat, humidity, poor air, or high UV.'
                rec = ['Avoid prolonged outdoor activity, especially for elderly/asthmatics.']
              elif s >= 30:
                band = 'Poor Comfort / Caution'
                desc = 'Multiple factors deteriorate comfort. Air or heat may cause physical strain.'
                rec = ['Sensitive individuals should remain indoors; stay hydrated.']
              elif s >= 15:
                band = 'Very Poor / Health Risk'
                desc = 'Unhealthy air, extreme heat or UV, oppressive humidity.'
                rec = ['Outdoor activity discouraged; use masks and cooling measures.']
              else:
                band = 'Severe Discomfort / Dangerous'
                desc = 'Hazardous environmental conditions, high health risk.'
                rec = ['Stay indoors; emergency conditions for health-sensitive individuals.']

              if aqi_val is not None:
                try:
                  if aqi_val < 50:
                    env.append('AQI < 50')
                  elif aqi_val < 80:
                    env.append('AQI < 80')
                  elif aqi_val < 120:
                    env.append('AQI < 120')
                  elif aqi_val < 150:
                    env.append('AQI 120–150')
                  elif aqi_val < 200:
                    env.append('AQI 150–200')
                  elif aqi_val < 300:
                    env.append('AQI 200–300')
                  else:
                    env.append('AQI > 300')
                except Exception:
                  pass
              if temp is not None:
                try:
                  t = float(temp)
                  if 20 <= t <= 28:
                    env.append('Temp 20–28 °C')
                  elif 28 < t <= 32:
                    env.append('Temp 28–32 °C')
                  elif 32 < t <= 35:
                    env.append('Temp 32–35 °C')
                  elif t > 35:
                    env.append('Temp > 35 °C')
                  elif t < 18:
                    env.append('Temp < 18 °C')
                except Exception:
                  pass
              if hum is not None:
                try:
                  h = float(hum)
                  if h > 70:
                    env.append('Humidity > 70%')
                  elif 40 <= h <= 60:
                    env.append('Humidity 40–60%')
                except Exception:
                  pass
              if uv is not None:
                try:
                  u = float(uv)
                  if u <= 4:
                    env.append('UV ≤ 4')
                  elif u <= 6:
                    env.append('UV ≤ 6')
                  elif u <= 7:
                    env.append('UV ≤ 7')
                  elif u <= 8:
                    env.append('UV > 8')
                  else:
                    env.append('UV very high')
                except Exception:
                  pass
              if wind_kph is not None:
                try:
                  w = float(wind_kph)
                  if 10.8 <= w <= 18.0: # 3-5 m/s in kph
                    env.append('mild wind (3–5 m/s)')
                except Exception:
                  pass

              return {
                'band': band,
                'description': desc,
                'environmental_interpretation': ', '.join(env) if env else None,
                'recommendations': rec
              }

            interp = interpret_comfort(result['comfort_score'], aqi_val=comp_scores.get('aqi_val'), temp=temp_c, hum=rh, uv=uv_index, wind_kph=float(current.get('wind_kph',0)))
            result.update(interp)
        else:
          result['profile_error'] = 'Unknown profile'

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
    <script src="https://cdn.jsdelivr.net/npm/chart.js@3.9.1/dist/chart.min.js"></script>
  </head>
  <body class="bg-slate-900 text-slate-100 min-h-screen p-6">
    <div class="max-w-6xl mx-auto">
      <div class="flex items-center justify-between mb-6">
        <h1 class="text-4xl font-bold">Weather.ai</h1>
        <div class="flex items-center gap-3">
          <a href="/visualization" class="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-md shadow">Open Dashboard</a>
        </div>
      </div>

      <div class="mb-4">
        <input id="location-input" type="text" placeholder="Enter city or 'lat,lon'" class="w-full p-3 rounded-md bg-slate-800 border border-slate-700" />
        <div class="mt-3 grid grid-cols-3 gap-2">
          <select id="units" class="p-2 rounded-md bg-slate-800 border border-slate-700">
            <option value="metric">Celsius</option>
          </select>
          <select id="forecast_days" class="p-2 rounded-md bg-slate-800 border border-slate-700">
            <option value="0">Now</option>
            <option value="1">1 day</option>
            <option value="2">2 days</option>
            <option value="3" selected>3 days</option>
          </select>
          <label class="flex items-center gap-2"><input id="include_aqi" type="checkbox"/> Include AQI</label>
        </div>
        <div class="mt-3 grid grid-cols-3 gap-2">
          <label class="flex items-center gap-2"><select id="profile" class="p-2 rounded-md bg-slate-800 border border-slate-700 w-full">
            <option value="general">General</option>
            <option value="asthma">Asthma</option>
            <option value="elderly_child">Elderly/Child</option>
            <option value="athlete">Athlete</option>
          </select></label>
          <label class="flex items-center gap-2"><input id="include_uv" type="checkbox"/> Include UV</label>
          <div></div>
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
          <div class="mt-2 text-xs text-slate-300">Weighted HCI: <span id="weighted-hci">--</span></div>
          <div class="mt-1 text-xs text-slate-400">
            <div>AQI: <span id="comp-aqi">--</span></div>
            <div>Temp: <span id="comp-temp">--</span></div>
            <div>Humidity: <span id="comp-humidity">--</span></div>
            <div>UV: <span id="comp-uv">--</span></div>
            <div>Wind: <span id="comp-wind">--</span></div>
          </div>
          <div class="mt-2 text-xs text-slate-300">Band: <span id="comfort-band">--</span></div>
          <div class="mt-1 text-xs text-slate-300">Env: <span id="comfort-env">--</span></div>
          <div class="mt-1 text-xs text-slate-300">Rec: <span id="comfort-rec">--</span></div>
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
            <div class="text-xs text-slate-400">Avg Temp (°C)</div>
            <div id="avgtemp_c" class="text-lg font-bold">--</div>
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
      </div>

      <!-- Forecast Section -->
      <div id="forecast-section" class="mt-8"></div>

      <!-- Analytics Section -->
      <div class="mt-8 mb-4">
        <h2 class="text-2xl font-bold mb-4">📊 Weather Analytics</h2>
        
        <!-- Charts Grid -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <!-- HCI Chart -->
          <div class="bg-slate-800 p-4 rounded-lg border border-slate-700">
            <canvas id="hciChart"></canvas>
          </div>
          
          <!-- Temperature Forecast Chart -->
          <div class="bg-slate-800 p-4 rounded-lg border border-slate-700">
            <canvas id="tempChart"></canvas>
          </div>
          
          <!-- Humidity Chart -->
          <div class="bg-slate-800 p-4 rounded-lg border border-slate-700">
            <canvas id="humidityChart"></canvas>
          </div>
          
          <!-- Wind Chart -->
          <div class="bg-slate-800 p-4 rounded-lg border border-slate-700">
            <canvas id="windChart"></canvas>
          </div>
        </div>

        <!-- Monthly Temperature Chart (Full Width) -->
        <div class="mt-6 bg-slate-800 p-4 rounded-lg border border-slate-700">
          <canvas id="monthlyTempChart"></canvas>
        </div>
      </div>
    </div>

    <script>
      let hciChartInstance = null;
      let tempChartInstance = null;
      let humidityChartInstance = null;
      let windChartInstance = null;
      let monthlyTempChartInstance = null;

      function destroyCharts() {
        if(hciChartInstance) hciChartInstance.destroy();
        if(tempChartInstance) tempChartInstance.destroy();
        if(humidityChartInstance) humidityChartInstance.destroy();
        if(windChartInstance) windChartInstance.destroy();
        if(monthlyTempChartInstance) monthlyTempChartInstance.destroy();
      }

      document.addEventListener('DOMContentLoaded', () => {
        const fetchButton = document.getElementById('fetch-weather-btn');
        const locInput = document.getElementById('location-input');
        const status = document.getElementById('status-message');

        const outLoc = document.getElementById('loc');
        const outCond = document.getElementById('cond');
        const outTemp = document.getElementById('temp');
        const outAvgTemp = document.getElementById('avgtemp_c');
        const outHum = document.getElementById('hum');
        const outWind = document.getElementById('wind');

        async function fetchWeather(){
          const q = locInput.value || 'London';
          const units = document.getElementById('units').value;
            const days = parseInt(document.getElementById('forecast_days').value, 10);
          const include_aqi = document.getElementById('include_aqi').checked;
          const include_uv = document.getElementById('include_uv').checked;
          const profile = document.getElementById('profile').value;
          fetchButton.disabled = true;
          status.textContent = 'Fetching...';
          try{
            const r = await fetch('/api/get_weather', {
              method: 'POST', headers: {'Content-Type':'application/json'},
              body: JSON.stringify({query: q, units: units, forecast_days: days, include_aqi: include_aqi, include_uv: include_uv, profile: profile})
            });
            const data = await r.json();
            if(!r.ok){ status.textContent = data.error || 'Error'; return; }
            outLoc.textContent = data.location_name;
            outCond.textContent = data.condition;
            outTemp.textContent = data.temperature_c + (units === 'metric' ? ' °C' : ' °F');
            // Current average temperature (if present)
            if(data.avgtemp_c){ outAvgTemp.textContent = data.avgtemp_c + (units === 'metric' ? ' °C' : ' °F'); } else { outAvgTemp.textContent = '--'; }
            // HCI and comfort indicators
            const hciVal = document.getElementById('hci-value');
            const comfortBadge = document.getElementById('comfort-badge');
            const comfortDesc = document.getElementById('comfort-desc');
            if(data.hci){ hciVal.textContent = data.hci; } else { hciVal.textContent = '--'; }
            // Weighted HCI from profile (if present)
            const wEl = document.getElementById('weighted-hci');
            if(data.weighted_hci){ wEl.textContent = data.weighted_hci + ' (profile: ' + (data.profile_used || '-') + ')'; } else { wEl.textContent = '--'; }
            // Component scores - formatted
            if(data.component_scores){
              document.getElementById('comp-aqi').textContent = data.component_scores.aqi_score !== null ? data.component_scores.aqi_score.toFixed(1) : '--';
              document.getElementById('comp-temp').textContent = data.component_scores.temp_score !== null ? data.component_scores.temp_score.toFixed(1) : '--';
              document.getElementById('comp-humidity').textContent = data.component_scores.humidity_score !== null ? data.component_scores.humidity_score.toFixed(1) : '--';
              document.getElementById('comp-uv').textContent = data.component_scores.uv_score !== null ? data.component_scores.uv_score.toFixed(1) : '--';
              document.getElementById('comp-wind').textContent = data.component_scores.wind_score !== null ? data.component_scores.wind_score.toFixed(1) : '--';
            } else {
              document.getElementById('comp-aqi').textContent = '--';
              document.getElementById('comp-temp').textContent = '--';
              document.getElementById('comp-humidity').textContent = '--';
              document.getElementById('comp-uv').textContent = '--';
              document.getElementById('comp-wind').textContent = '--';
            }
            // Comfort interpretation
            const bandEl = document.getElementById('comfort-band');
            const envEl = document.getElementById('comfort-env');
            const recEl = document.getElementById('comfort-rec');
            if(data.band){ bandEl.textContent = data.band; } else { bandEl.textContent = '--'; }
            if(data.environmental_interpretation){ envEl.textContent = data.environmental_interpretation; } else { envEl.textContent = '--'; }
            if(data.recommendations){ recEl.textContent = data.recommendations.join('; '); } else { recEl.textContent = '--'; }
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
            const forecastSectionEl = document.getElementById('forecast-section');
            if(data.forecast && data.forecast.length){
              let forecastHTML = '<h2 class="text-2xl font-bold mb-4">📅 Forecast (Next ' + data.forecast.length + ' Days)</h2>';
              forecastHTML += '<div class="grid grid-cols-1 md:grid-cols-3 gap-4">';
              
              data.forecast.forEach((d, idx) => {
                const hciColor = d.possible_hci ? (d.possible_hci > 75 ? 'bg-green-900/30 border-green-500' : d.possible_hci > 50 ? 'bg-yellow-900/30 border-yellow-500' : 'bg-red-900/30 border-red-500') : 'bg-slate-700/30';
                forecastHTML += `
                  <div class="bg-slate-800 border ${hciColor} p-4 rounded-lg">
                    <div class="text-sm text-slate-400 font-semibold">${d.date}</div>
                    <div class="text-xl font-bold mt-2">${d.condition || '--'}</div>
                    <div class="grid grid-cols-2 gap-2 mt-3 text-sm">
                      <div><span class="text-slate-400">Avg Temp:</span> <span class="font-bold">${d.avgtemp_c || '--'}°C</span></div>
                      <div><span class="text-slate-400">Min/Max:</span> <span class="font-bold">${d.mintemp_c || '--'}/${d.maxtemp_c || '--'}°C</span></div>
                      <div><span class="text-slate-400">Humidity:</span> <span class="font-bold">${d.humidity || '--'}%</span></div>
                      <div><span class="text-slate-400">Wind:</span> <span class="font-bold">${d.wind_kph || '--'} kph</span></div>
                      <div><span class="text-slate-400">UV Index:</span> <span class="font-bold">${d.uv || '--'}</span></div>
                    </div>
                    <div class="mt-3 p-2 bg-amber-900/30 border border-amber-500 rounded">
                      <div class="text-xs text-amber-300 font-semibold">📊 Possible HCI</div>
                      <div class="text-lg font-bold text-amber-200">${d.possible_hci || '--'}</div>
                      <div class="text-xs text-slate-400 mt-1">* Based on forecast; may vary with real-time conditions</div>
                    </div>
                  </div>
                `;
              });
              
              forecastHTML += '</div>';
              forecastSectionEl.innerHTML = forecastHTML;

              // Prepare data for charts (include current day)
              destroyCharts();
              
              const currentDate = data.current_date || 'Today';
              const dates = [currentDate, ...data.forecast.map(d => d.date)];
              
              // Current day HCI value
              const currentHci = data.hci ? parseFloat(data.hci) : null;
              const hcis = [currentHci, ...data.forecast.map(d => d.possible_hci ? parseFloat(d.possible_hci) : null)];
              
              const currentTemp = data.avgtemp_c ? parseFloat(data.avgtemp_c) : null;
              const temps = [currentTemp, ...data.forecast.map(d => d.avgtemp_c ? parseFloat(d.avgtemp_c) : null)];
              
              const currentHumidity = data.humidity ? parseFloat(data.humidity) : null;
              const humidities = [currentHumidity, ...data.forecast.map(d => d.humidity ? parseFloat(d.humidity) : null)];
              
              const currentWind = data.wind_kph ? parseFloat(data.wind_kph) : null;
              const winds = [currentWind, ...data.forecast.map(d => d.wind_kph ? parseFloat(d.wind_kph) : null)];

              // HCI Chart - with current day highlighted
              const hciCtx = document.getElementById('hciChart').getContext('2d');
              hciChartInstance = new Chart(hciCtx, {
                type: 'line',
                data: {
                  labels: dates,
                  datasets: [{
                    label: 'HCI (Current & Possible)',
                    data: hcis,
                    borderColor: '#fbbf24',
                    backgroundColor: 'rgba(251, 191, 36, 0.1)',
                    tension: 0.4,
                    fill: true,
                    pointBackgroundColor: dates.map((_, i) => i === 0 ? '#10b981' : '#fbbf24'),
                    pointRadius: dates.map((_, i) => i === 0 ? 7 : 5),
                    pointBorderColor: dates.map((_, i) => i === 0 ? '#059669' : '#f59e0b'),
                    pointBorderWidth: 2
                  }]
                },
                options: {
                  responsive: true,
                  maintainAspectRatio: true,
                  plugins: { 
                    legend: { labels: { color: '#cbd5e1' } }, 
                    title: { display: true, text: 'HCI Trend (● Green = Today, ● Yellow = Forecast)', color: '#cbd5e1', font: { size: 12 } } 
                  },
                  scales: { 
                    y: { min: 0, max: 100, ticks: { color: '#cbd5e1' }, grid: { color: '#334155' } }, 
                    x: { ticks: { color: '#cbd5e1' }, grid: { color: '#334155' } } 
                  }
                }
              });

              // Temperature Chart - with current day
              const tempCtx = document.getElementById('tempChart').getContext('2d');
              tempChartInstance = new Chart(tempCtx, {
                type: 'bar',
                data: {
                  labels: dates,
                  datasets: [{
                    label: 'Temperature (°C)',
                    data: temps,
                    backgroundColor: dates.map((_, i) => i === 0 ? '#10b981' : '#60a5fa'),
                    borderColor: dates.map((_, i) => i === 0 ? '#059669' : '#3b82f6'),
                    borderWidth: 2
                  }]
                },
                options: {
                  responsive: true,
                  maintainAspectRatio: true,
                  plugins: { 
                    legend: { labels: { color: '#cbd5e1' } }, 
                    title: { display: true, text: 'Temperature (Green = Today)', color: '#cbd5e1', font: { size: 12 } } 
                  },
                  scales: { 
                    y: { ticks: { color: '#cbd5e1' }, grid: { color: '#334155' } }, 
                    x: { ticks: { color: '#cbd5e1' }, grid: { color: '#334155' } } 
                  }
                }
              });

              // Humidity Chart - with current day
              const humCtx = document.getElementById('humidityChart').getContext('2d');
              humidityChartInstance = new Chart(humCtx, {
                type: 'line',
                data: {
                  labels: dates,
                  datasets: [{
                    label: 'Humidity (%)',
                    data: humidities,
                    borderColor: dates.map((_, i) => i === 0 ? '#10b981' : '#06b6d4'),
                    backgroundColor: dates.map((_, i) => i === 0 ? 'rgba(16, 185, 129, 0.1)' : 'rgba(6, 182, 212, 0.1)'),
                    tension: 0.4,
                    fill: true,
                    pointBackgroundColor: dates.map((_, i) => i === 0 ? '#10b981' : '#06b6d4'),
                    pointRadius: dates.map((_, i) => i === 0 ? 7 : 5),
                    pointBorderWidth: 2,
                    borderWidth: 2
                  }]
                },
                options: {
                  responsive: true,
                  maintainAspectRatio: true,
                  plugins: { 
                    legend: { labels: { color: '#cbd5e1' } }, 
                    title: { display: true, text: 'Humidity (Green = Today)', color: '#cbd5e1', font: { size: 12 } } 
                  },
                  scales: { 
                    y: { min: 0, max: 100, ticks: { color: '#cbd5e1' }, grid: { color: '#334155' } }, 
                    x: { ticks: { color: '#cbd5e1' }, grid: { color: '#334155' } } 
                  }
                }
              });

              // Wind Chart - with current day
              const windCtx = document.getElementById('windChart').getContext('2d');
              windChartInstance = new Chart(windCtx, {
                type: 'bar',
                data: {
                  labels: dates,
                  datasets: [{
                    label: 'Wind Speed (kph)',
                    data: winds,
                    backgroundColor: dates.map((_, i) => i === 0 ? '#10b981' : '#34d399'),
                    borderColor: dates.map((_, i) => i === 0 ? '#059669' : '#10b981'),
                    borderWidth: 2
                  }]
                },
                options: {
                  responsive: true,
                  maintainAspectRatio: true,
                  plugins: { 
                    legend: { labels: { color: '#cbd5e1' } }, 
                    title: { display: true, text: 'Wind Speed (Green = Today)', color: '#cbd5e1', font: { size: 12 } } 
                  },
                  scales: { 
                    y: { ticks: { color: '#cbd5e1' }, grid: { color: '#334155' } }, 
                    x: { ticks: { color: '#cbd5e1' }, grid: { color: '#334155' } } 
                  }
                }
              });

              // Monthly Temperature Chart (simulated data for demo)
              const monthlyDays = Array.from({length: 30}, (_, i) => (i + 1));
              const monthlyTemps = monthlyDays.map(day => {
                // Simulate monthly data - today's temp in focus
                const today = new Date(data.current_date).getDate() || new Date().getDate();
                const tempVariation = (Math.sin(day / 5) * 5) + (Math.random() - 0.5) * 3;
                return parseFloat(data.avgtemp_c) + tempVariation + (day === today ? 2 : 0);
              });

              const monthlyTempCtx = document.getElementById('monthlyTempChart').getContext('2d');
              monthlyTempChartInstance = new Chart(monthlyTempCtx, {
                type: 'line',
                data: {
                  labels: monthlyDays.map(d => 'Day ' + d),
                  datasets: [{
                    label: 'Daily Temperature (°C)',
                    data: monthlyTemps,
                    borderColor: '#f97316',
                    backgroundColor: 'rgba(249, 115, 22, 0.05)',
                    tension: 0.3,
                    fill: true,
                    pointRadius: 3,
                    pointBackgroundColor: monthlyDays.map((d, i) => {
                      const today = new Date(data.current_date).getDate() || new Date().getDate();
                      return d === today ? '#10b981' : '#f97316';
                    }),
                    pointRadius: monthlyDays.map((d, i) => {
                      const today = new Date(data.current_date).getDate() || new Date().getDate();
                      return d === today ? 6 : 3;
                    }),
                    borderWidth: 2
                  }]
                },
                options: {
                  responsive: true,
                  maintainAspectRatio: false,
                  plugins: { 
                    legend: { labels: { color: '#cbd5e1' } }, 
                    title: { display: true, text: 'Monthly Temperature Trend (Green Dot = Today)', color: '#cbd5e1', font: { size: 14 } } 
                  },
                  scales: { 
                    y: { ticks: { color: '#cbd5e1' }, grid: { color: '#334155' } }, 
                    x: { ticks: { color: '#cbd5e1', maxTicksLimit: 10 }, grid: { color: '#334155' } } 
                  }
                }
              });
              
              // Set monthly chart height
              document.getElementById('monthlyTempChart').parentElement.style.height = '350px';
            } else { 
              forecastSectionEl.innerHTML = ''; 
              destroyCharts();
            }

            // If AQI included, render a small summary
            const aqiEl = document.getElementById('aqi');
            if(data.aqi){
              const pm25 = data.aqi.pm2_5 || data.aqi['pm2_5'] || null;
              aqiEl.textContent = 'Air Quality (PM2.5): ' + (pm25 ? pm25.toFixed(2) : 'N/A');
            } else { aqiEl.textContent = ''; }
          }catch(e){
            console.error(e);
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


VIS_TEMPLATE = r"""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Weather.ai — Visualization</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@3.9.1/dist/chart.min.js"></script>
  </head>
  <body class="bg-slate-900 text-slate-100 min-h-screen p-6">
    <div class="max-w-7xl mx-auto">
      <header class="flex items-center justify-between mb-6">
        <div>
          <h1 class="text-3xl font-bold">Visualization Dashboard</h1>
          <div class="text-sm text-slate-400">Analytical view • PowerBI-like tiles</div>
        </div>
        <div class="flex items-center gap-3">
          <a href="/" class="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-md">Back</a>
        </div>
      </header>

      <div class="grid grid-cols-1 lg:grid-cols-4 gap-4">
        <div class="col-span-1 bg-indigo-700/10 p-4 rounded shadow">
          <label class="text-sm text-slate-300">Location</label>
          <input id="viz-location" class="w-full mt-2 p-2 rounded bg-slate-800 border border-slate-700" placeholder="Enter city or 'lat,lon'" />
          <div class="mt-3 grid grid-cols-2 gap-2">
            <select id="viz-units" class="p-2 rounded bg-slate-800 border border-slate-700">
              <option value="metric">Celsius</option>
            </select>
            <button id="viz-fetch" class="p-2 bg-indigo-600 rounded">Refresh</button>
          </div>
          <div class="mt-4 space-y-3">
            <div class="p-3 bg-slate-800 rounded">
              <div class="text-xs text-slate-400">HCI</div>
              <div id="viz-hci" class="text-2xl font-bold">--</div>
            </div>
            <div class="p-3 bg-slate-800 rounded">
              <div class="text-xs text-slate-400">Temperature</div>
              <div id="viz-temp" class="text-2xl font-bold">--</div>
            </div>
            <div class="p-3 bg-slate-800 rounded">
              <div class="text-xs text-slate-400">Humidity</div>
              <div id="viz-hum" class="text-2xl font-bold">--</div>
            </div>
            <div class="p-3 bg-slate-800 rounded">
              <div class="text-xs text-slate-400">AQI (PM2.5)</div>
              <div id="viz-aqi" class="text-2xl font-bold">--</div>
            </div>
          </div>
        </div>

        <div class="lg:col-span-3 grid grid-cols-1 gap-4">
          <div class="bg-slate-800 p-4 rounded">
            <canvas id="viz-hciChart"></canvas>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div class="bg-slate-800 p-4 rounded"><canvas id="viz-tempChart"></canvas></div>
            <div class="bg-slate-800 p-4 rounded"><canvas id="viz-humidityChart"></canvas></div>
            <div class="bg-slate-800 p-4 rounded"><canvas id="viz-windChart"></canvas></div>
          </div>
          <div class="bg-slate-800 p-4 rounded"><canvas id="viz-monthlyChart"></canvas></div>
        </div>
      </div>

    <script>
      async function fetchViz(location, units){
        const resp = await fetch('/api/get_weather', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({query:location, units:units, forecast_days:3, include_aqi:true})});
        const data = await resp.json();
        return data;
      }

      function renderKPIs(data){
        document.getElementById('viz-hci').textContent = data.hci || '--';
        document.getElementById('viz-temp').textContent = data.temperature_c ? data.temperature_c + ' °C' : '--';
        document.getElementById('viz-hum').textContent = data.humidity ? data.humidity + ' %' : '--';
        if(data.aqi){
          const pm25 = data.aqi.pm2_5 || data.aqi['pm2_5'] || null;
          document.getElementById('viz-aqi').textContent = pm25 ? pm25.toFixed(1) : 'N/A';
        } else { document.getElementById('viz-aqi').textContent = '--'; }
      }

      // minimal chart helpers
      let vizCharts = [];
      function clearVizCharts(){ vizCharts.forEach(c=>c.destroy()); vizCharts=[]; }

      async function refreshViz(){
        const loc = document.getElementById('viz-location').value || 'London';
        const units = document.getElementById('viz-units').value;
        const data = await fetchViz(loc, units);
        renderKPIs(data);
        clearVizCharts();

        // Set canvas parent heights
        document.getElementById('viz-hciChart').parentElement.style.minHeight = '300px';
        document.getElementById('viz-tempChart').parentElement.style.minHeight = '250px';
        document.getElementById('viz-humidityChart').parentElement.style.minHeight = '250px';
        document.getElementById('viz-windChart').parentElement.style.minHeight = '250px';
        document.getElementById('viz-monthlyChart').parentElement.style.minHeight = '350px';

        const labels = [data.current_date || 'Today', ...(data.forecast||[]).map(f=>f.date)];
        const hciData = [data.hci ? parseFloat(data.hci) : null, ...(data.forecast||[]).map(f=>f.possible_hci?parseFloat(f.possible_hci):null)];
        
        // HCI Chart with proper styling
        const ctxHci = document.getElementById('viz-hciChart').getContext('2d');
        vizCharts.push(new Chart(ctxHci, {
          type:'line',
          data:{
            labels:labels,
            datasets:[{
              label:'HCI (Current & Possible)',
              data:hciData,
              borderColor:'#fbbf24',
              backgroundColor:'rgba(251, 191, 36, 0.1)',
              fill:true,
              tension:0.4,
              pointRadius:5,
              pointBackgroundColor:labels.map((_,i)=>i===0?'#10b981':'#fbbf24'),
              pointBorderColor:labels.map((_,i)=>i===0?'#059669':'#f59e0b'),
              pointBorderWidth:2
            }]
          },
          options:{
            responsive:true,
            maintainAspectRatio:false,
            plugins:{
              legend:{labels:{color:'#cbd5e1', font:{size:11}}},
              title:{display:true, text:'HCI Trend', color:'#cbd5e1'}
            },
            scales:{
              y:{min:0,max:100,ticks:{color:'#cbd5e1'},grid:{color:'#334155'}},
              x:{ticks:{color:'#cbd5e1'},grid:{color:'#334155'}}
            }
          }
        }));

        // Temperature Chart
        const tempLabels = labels;
        const tempData = [data.avgtemp_c?parseFloat(data.avgtemp_c):null, ...(data.forecast||[]).map(f=>f.avgtemp_c?parseFloat(f.avgtemp_c):null)];
        vizCharts.push(new Chart(document.getElementById('viz-tempChart').getContext('2d'), {
          type:'bar',
          data:{
            labels:tempLabels,
            datasets:[{
              label:'Temp (°C)',
              data:tempData,
              backgroundColor:tempLabels.map((_,i)=>i===0?'#10b981':'#60a5fa'),
              borderColor:tempLabels.map((_,i)=>i===0?'#059669':'#3b82f6'),
              borderWidth:1
            }]
          },
          options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#cbd5e1',font:{size:11}}},title:{display:true,text:'Temperature',color:'#cbd5e1'}},scales:{y:{ticks:{color:'#cbd5e1'},grid:{color:'#334155'}},x:{ticks:{color:'#cbd5e1'},grid:{color:'#334155'}}}}
        }));

        // Humidity Chart
        const humData = [data.humidity?parseFloat(data.humidity):null, ...(data.forecast||[]).map(f=>f.humidity?parseFloat(f.humidity):null)];
        vizCharts.push(new Chart(document.getElementById('viz-humidityChart').getContext('2d'), {
          type:'line',
          data:{
            labels:tempLabels,
            datasets:[{
              label:'Humidity %',
              data:humData,
              borderColor:tempLabels.map((_,i)=>i===0?'#10b981':'#06b6d4'),
              backgroundColor:tempLabels.map((_,i)=>i===0?'rgba(16,185,129,0.1)':'rgba(6,182,212,0.06)'),
              fill:true,
              tension:0.4,
              pointRadius:tempLabels.map((_,i)=>i===0?5:3),
              pointBackgroundColor:tempLabels.map((_,i)=>i===0?'#10b981':'#06b6d4')
            }]
          },
          options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#cbd5e1',font:{size:11}}},title:{display:true,text:'Humidity',color:'#cbd5e1'}},scales:{y:{min:0,max:100,ticks:{color:'#cbd5e1'},grid:{color:'#334155'}},x:{ticks:{color:'#cbd5e1'},grid:{color:'#334155'}}}}
        }));

        // Wind Chart
        const windData = [data.wind_kph?parseFloat(data.wind_kph):null, ...(data.forecast||[]).map(f=>f.wind_kph?parseFloat(f.wind_kph):null)];
        vizCharts.push(new Chart(document.getElementById('viz-windChart').getContext('2d'), {
          type:'bar',
          data:{
            labels:tempLabels,
            datasets:[{
              label:'Wind (kph)',
              data:windData,
              backgroundColor:tempLabels.map((_,i)=>i===0?'#10b981':'#34d399'),
              borderColor:tempLabels.map((_,i)=>i===0?'#059669':'#10b981'),
              borderWidth:1
            }]
          },
          options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#cbd5e1',font:{size:11}}},title:{display:true,text:'Wind Speed',color:'#cbd5e1'}},scales:{y:{ticks:{color:'#cbd5e1'},grid:{color:'#334155'}},x:{ticks:{color:'#cbd5e1'},grid:{color:'#334155'}}}}
        }));

        // Monthly Temperature Chart
        const days = Array.from({length:30},(_,i)=>i+1);
        const base = data.avgtemp_c ? parseFloat(data.avgtemp_c) : (data.temperature_c?parseFloat(data.temperature_c):20);
        const monthly = days.map(d => Math.round((base + Math.sin(d/5)*4 + (Math.random()-0.5)*2)*10)/10);
        vizCharts.push(new Chart(document.getElementById('viz-monthlyChart').getContext('2d'), {
          type:'line',
          data:{
            labels:days.map(d=>'Day '+d),
            datasets:[{
              label:'Daily Temperature (°C)',
              data:monthly,
              borderColor:'#f97316',
              backgroundColor:'rgba(249, 115, 22, 0.05)',
              fill:true,
              tension:0.3,
              pointRadius:3,
              pointBackgroundColor:'#f97316'
            }]
          },
          options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#cbd5e1',font:{size:11}}},title:{display:true,text:'Monthly Temperature Trend',color:'#cbd5e1'}},scales:{y:{ticks:{color:'#cbd5e1'},grid:{color:'#334155'}},x:{ticks:{color:'#cbd5e1',maxTicksLimit:10},grid:{color:'#334155'}}}}
        }));
      }

      document.addEventListener('DOMContentLoaded', ()=>{
        document.getElementById('viz-fetch').addEventListener('click', refreshViz);
        // initial
        document.getElementById('viz-location').value = 'London';
        refreshViz();
      });
    </script>
  </body>
</html>
"""



@app.route('/')
def index():
  return render_template_string(HTML_TEMPLATE)


@app.route('/visualization')
def visualization():
  return render_template_string(VIS_TEMPLATE)

if __name__ == '__main__':
    # Using host='0.0.0.0' for environment compatibility
    print("---------------------------------------------------------------------")
    print("Flask Application 'Weather.AI' is starting...")
    print("Access the dashboard at: http://127.0.0.1:5000/")
    print("---------------------------------------------------------------------")
    app.run(debug=True, host='0.0.0.0')
