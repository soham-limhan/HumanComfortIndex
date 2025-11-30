import os
import requests
import json
import base64
import csv
from io import BytesIO
from flask import Flask, request, jsonify, render_template_string
import plotly.graph_objects as go
import plotly.io as pio
import numpy as np

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

# Location data for dropdowns
# Cache for cities data
_cities_cache = None

def load_cities():
  """Load cities from CSV file with caching"""
  global _cities_cache
  if _cities_cache is None:
    _cities_cache = []
    try:
      csv_path = os.path.join(os.path.dirname(__file__), 'worldcities.csv')
      with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
          _cities_cache.append({
            'city': row.get('city', ''),
            'city_ascii': row.get('city_ascii', ''),
            'country': row.get('country', ''),
            'lat': row.get('lat', ''),
            'lng': row.get('lng', '')
          })
    except Exception as e:
      print(f"Error loading cities: {e}")
      _cities_cache = []
  return _cities_cache

@app.route('/api/search_cities', methods=['GET'])
def search_cities():
  """Search cities by query string"""
  query = request.args.get('q', '').strip().lower()
  if not query or len(query) < 2:
    return jsonify([])
  
  cities = load_cities()
  results = []
  seen = set()  # Track seen cities to avoid duplicates
  
  # Search in city_ascii and city fields
  for city in cities:
    city_ascii = city.get('city_ascii', '').lower()
    city_name = city.get('city', '').lower()
    country = city.get('country', '').lower()
    
    # Create unique key for deduplication
    city_key = f"{city.get('city', '')},{city.get('country', '')}"
    
    # Check if query matches the beginning of city name (for autocomplete)
    if (city_ascii.startswith(query) or city_name.startswith(query)) and city_key not in seen:
      results.append({
        'name': f"{city.get('city', '')}, {city.get('country', '')}",
        'city': city.get('city', ''),
        'country': city.get('country', ''),
        'query': f"{city.get('city', '')}, {city.get('country', '')}"
      })
      seen.add(city_key)
    # Also check if query appears anywhere in city name (for partial matches)
    elif (query in city_ascii or query in city_name) and city_key not in seen:
      results.append({
        'name': f"{city.get('city', '')}, {city.get('country', '')}",
        'city': city.get('city', ''),
        'country': city.get('country', ''),
        'query': f"{city.get('city', '')}, {city.get('country', '')}"
      })
      seen.add(city_key)
  
  # Sort: exact matches first, then by city name
  results.sort(key=lambda x: (
    0 if x['city'].lower().startswith(query) else 1,
    x['city'].lower()
  ))
  
  # Return top 10 results
  return jsonify(results[:10])


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
  # Always use forecast if available (provides more complete data including humidity)
  if forecast_days and forecast_days > 0:
    api_url = f"https://api.weatherapi.com/v1/forecast.json?key={WEATHERAPI_API_KEY}&q={query}&days={min(3,forecast_days)}&aqi={'yes' if include_aqi else 'no'}&alerts=no"
  else:
    api_url = f"https://api.weatherapi.com/v1/forecast.json?key={WEATHERAPI_API_KEY}&q={query}&days=1&aqi={'yes' if include_aqi else 'no'}&alerts=no"
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
    
    # Humidity extraction - try multiple keys since API structure varies
    rh = None
    try:
      rh = float(current.get('humidity', 0))
    except (ValueError, TypeError):
      rh = 0
    
    # Wind extraction - ensure we get wind speed properly
    wind_kph = None
    try:
      wind_kph = float(current.get('wind_kph', current.get('max_wind_kph', 0)))
    except (ValueError, TypeError):
      wind_kph = 0
    
    wind_speed_mps = wind_kph / 3.6
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
      "wind_kph": f"{wind_kph:.1f}",
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
    
    # Add AQI data to result if available
    if aqi_data:
      result['aqi'] = aqi_data

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
      
      # Map comfort_score to band and recommendations - define function BEFORE using it
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

      # Compute profile-weighted HCI using the new formula (default to 'general' if not provided)
      profile = data.get('profile') or 'general'
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
        else:
          # Still compute comfort_score if weight_sum is 0
          result['comfort_score'] = None
      # Call interpret_comfort if comfort_score exists
      if result.get('comfort_score') is not None:
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
    <title>Human Comfort Index - Real-time Weather Analytics</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@3.9.1/dist/chart.min.js"></script>
    <style>
      @keyframes float-left-right {
        0%, 100% { transform: translateX(0px); }
        50% { transform: translateX(30px); }
      }
      @keyframes float-up-down {
        0%, 100% { transform: translateY(0px); }
        50% { transform: translateY(-15px); }
      }
      @keyframes spin-sun {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
      }
      @keyframes wind-blow {
        0%, 100% { transform: scaleX(1); }
        50% { transform: scaleX(1.2); }
      }
      @keyframes cloud-drift {
        0%, 100% { transform: translateX(-20px); }
        50% { transform: translateX(20px); }
      }
      @keyframes slide-in {
        from { opacity: 0; transform: translateY(20px); }
        to { opacity: 1; transform: translateY(0); }
      }
      @keyframes glow-pulse {
        0%, 100% { box-shadow: 0 0 20px rgba(59, 130, 246, 0.5); }
        50% { box-shadow: 0 0 30px rgba(59, 130, 246, 0.8); }
      }
      .animate-float-lr {
        animation: float-left-right 4s ease-in-out infinite;
      }
      .animate-float-ud {
        animation: float-up-down 3s ease-in-out infinite;
      }
      .animate-spin-sun {
        animation: spin-sun 20s linear infinite;
      }
      .animate-wind-blow {
        animation: wind-blow 2s ease-in-out infinite;
      }
      .animate-cloud-drift {
        animation: cloud-drift 5s ease-in-out infinite;
      }
      .animate-slide-in {
        animation: slide-in 0.6s ease-out;
      }
      .animate-glow {
        animation: glow-pulse 2s ease-in-out infinite;
      }
      .card-hover {
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
      }
      .card-hover:hover {
        transform: translateY(-4px);
        box-shadow: 0 20px 40px rgba(0, 0, 0, 0.3);
      }
      .theme-transition {
        transition: background-color 0.3s, color 0.3s, border-color 0.3s;
      }
      body.light-mode {
        background: linear-gradient(to bottom, #f0f9ff, #e0e7ff, #eff6ff) !important;
        color: #1e293b !important;
      }
      body.light-mode * {
        color-scheme: light;
      }
      /* Light mode text colors */
      body.light-mode .text-slate-100,
      body.light-mode .text-slate-300,
      body.light-mode .text-slate-400,
      body.light-mode .text-muted {
        color: #475569 !important;
      }
      body.light-mode .text-slate-500 {
        color: #64748b !important;
      }
      body.light-mode .text-slate-600 {
        color: #475569 !important;
      }
      /* Light mode card backgrounds */
      body.light-mode .bg-slate-950,
      body.light-mode .from-slate-950,
      body.light-mode .bg-gradient-to-b.from-slate-950,
      body.light-mode .bg-gradient-to-br.from-slate-950 {
        background-color: #f1f5f9 !important;
        background: #f1f5f9 !important;
      }
      body.light-mode .bg-slate-900,
      body.light-mode .bg-slate-800,
      body.light-mode .to-slate-900,
      body.light-mode .to-slate-800,
      body.light-mode .bg-gradient-to-br.to-slate-800,
      body.light-mode .bg-gradient-to-br.to-slate-900 {
        background-color: #f8fafc !important;
        background: #f8fafc !important;
      }
      body.light-mode .bg-slate-800\/60 {
        background-color: rgba(241, 245, 249, 0.6) !important;
      }
      body.light-mode .bg-slate-800\/50 {
        background-color: rgba(241, 245, 249, 0.5) !important;
      }
      body.light-mode .bg-slate-800\/30 {
        background-color: rgba(241, 245, 249, 0.3) !important;
      }
      /* Light mode borders */
      body.light-mode .border-slate-700,
      body.light-mode .border-slate-600,
      body.light-mode .border-slate-700\/50 {
        border-color: #cbd5e1 !important;
      }
      body.light-mode .border-amber-500\/20,
      body.light-mode .border-amber-500\/30,
      body.light-mode .border-amber-400\/30 {
        border-color: #fbbf24 !important;
      }
      /* Light mode hover states */
      body.light-mode .hover\:bg-slate-700\/50:hover,
      body.light-mode .hover\:bg-slate-700\/60:hover {
        background-color: rgba(241, 245, 249, 0.7) !important;
      }
      body.light-mode .hover\:bg-slate-800\/30:hover {
        background-color: rgba(241, 245, 249, 0.4) !important;
      }
      /* Light mode gradient overlays */
      body.light-mode .backdrop-blur-sm {
        background-color: rgba(241, 245, 249, 0.9) !important;
      }
      /* Light mode fixed background */
      body.light-mode .fixed.inset-0 {
        background: linear-gradient(to bottom, #f0f9ff, #e0e7ff, #eff6ff) !important;
      }
      /* Light mode specific card styles */
      body.light-mode .bg-gradient-to-br {
        background: linear-gradient(to bottom right, #f1f5f9, #f8fafc) !important;
      }
      body.light-mode .from-indigo-600,
      body.light-mode .from-purple-600 {
        color: #4f46e5 !important;
      }
      body.light-mode h1,
      body.light-mode h2,
      body.light-mode h3,
      body.light-mode h4,
      body.light-mode h5,
      body.light-mode h6 {
        color: #0f172a !important;
      }
      /* Keep sun and clouds visible in light mode */
      body.light-mode .bg-gradient-to-br.from-yellow-300 {
        background: linear-gradient(to bottom right, #fcd34d, #f59e0b, #ea580c) !important;
      }
      body.light-mode .w-16.h-8.bg-white,
      body.light-mode .w-14.h-7.bg-white {
        background-color: #ffffff !important;
        opacity: 0.95 !important;
      }
      body.light-mode .animate-spin-sun {
        filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.2));
      }
      body.light-mode .rounded-full.shadow-2xl.flex.items-center.justify-center {
        background: linear-gradient(to bottom, #bfdbfe, #dbeafe, #f0f9ff) !important;
      }
      .dark-mode-toggle {
        padding: 0.5rem;
        border-radius: 0.5rem;
        cursor: pointer;
        transition: all 0.3s ease;
      }
      .dark-mode-toggle:hover {
        transform: scale(1.1);
      }
      /* Autocomplete dropdown styles */
      #city-suggestions {
        z-index: 9999999 !important;
        position: absolute !important;
        top: 100% !important;
        left: 0 !important;
        right: 0 !important;
        width: 100% !important;
        margin-top: 0.25rem !important;
      }
      #fetch-weather-btn {
        position: relative;
        z-index: 1 !important;
      }
      .mb-6 {
        overflow: visible !important;
      }
      /* Ensure parent containers don't clip the dropdown */
      div.relative {
        overflow: visible !important;
        z-index: auto !important;
      }
      /* Ensure the input container and all parent containers allow the dropdown to appear above */
      .rounded-xl.shadow-lg {
        overflow: visible !important;
      }
      /* Ensure weather cards don't interfere with dropdown */
      .card-hover {
        position: relative;
        z-index: auto !important;
      }
      .rounded-2xl.overflow-hidden {
        position: relative;
        z-index: auto !important;
      }
      .city-suggestion-item {
        padding: 0.75rem 1rem;
        cursor: pointer;
        transition: all 0.2s ease;
        border-bottom: 1px solid rgba(251, 146, 60, 0.1);
        color: #e2e8f0;
      }
      .city-suggestion-item:hover {
        background-color: rgba(251, 146, 60, 0.2);
      }
      .city-suggestion-item:last-child {
        border-bottom: none;
      }
      .city-suggestion-item.active {
        background-color: rgba(251, 146, 60, 0.3);
      }
      }
    </style>
  </head>
  <body class="bg-gradient-to-b from-slate-950 via-indigo-950 to-slate-900 text-slate-100 min-h-screen p-6 theme-transition">
    <!-- Animated Background -->
    <div class="fixed inset-0 z-0 opacity-30 theme-transition" id="bgGradient">
      <div class="absolute top-20 left-10 w-72 h-72 bg-cyan-500 rounded-full mix-blend-multiply filter blur-3xl animate-pulse"></div>
      <div class="absolute top-40 right-10 w-72 h-72 bg-purple-500 rounded-full mix-blend-multiply filter blur-3xl animate-pulse animation-delay-2000"></div>
      <div class="absolute -bottom-8 left-20 w-72 h-72 bg-blue-500 rounded-full mix-blend-multiply filter blur-3xl animate-pulse animation-delay-4000"></div>
    </div>
    
    <div class="max-w-6xl mx-auto relative z-10">
      <!-- Header with Dynamic Animated Logo -->
      <div class="mb-8 rounded-2xl overflow-hidden shadow-2xl animate-slide-in">
        <div class="bg-gradient-to-r from-indigo-600 via-purple-600 to-pink-600 p-1">
          <div class="bg-gradient-to-br from-slate-950/95 to-slate-900/95 backdrop-blur-sm p-8 rounded-xl border border-gradient-to-r from-cyan-400/20 to-purple-400/20 theme-transition">
            <!-- Top Right - Theme Toggle -->
            <div class="flex justify-end mb-4">
              <button id="themeToggle" class="dark-mode-toggle bg-slate-800 hover:bg-slate-700 text-yellow-400 border border-slate-700">
                <span id="themeIcon" class="text-2xl">🌙</span>
              </button>
            </div>
            
            <div class="flex items-center justify-between gap-6">
              <div class="flex-1">
                <h1 class="text-6xl font-bold bg-gradient-to-r from-cyan-300 via-blue-300 to-purple-300 bg-clip-text text-transparent mb-2">Human Comfort Index</h1>
                <p class="text-lg text-slate-300">Real-time Comfort Analysis & Advanced Weather Analytics</p>
                <div class="flex gap-4 mt-4 flex-wrap">
                  <div class="flex items-center gap-2 text-sm text-cyan-300 card-hover cursor-default"><span class="text-xl">🎯</span> <span>Precision Data</span></div>
                  <div class="flex items-center gap-2 text-sm text-purple-300 card-hover cursor-default"><span class="text-xl">📊</span> <span>AI Analytics</span></div>
                  <div class="flex items-center gap-2 text-sm text-pink-300 card-hover cursor-default"><span class="text-xl">⚡</span> <span>Real-time</span></div>
                </div>
              </div>
              
              <!-- Dynamic Animated Logo -->
              <div class="flex flex-col items-center gap-3">
                <div class="relative w-48 h-48 bg-gradient-to-b from-blue-300 to-blue-100 rounded-full shadow-2xl flex items-center justify-center animate-glow">
                  <!-- Cloud 1 - Top Left -->
                  <div class="absolute top-6 left-4 w-16 h-8 bg-white rounded-full opacity-90 shadow-lg animate-cloud-drift"></div>
                  
                  <!-- Cloud 2 - Top Right -->
                  <div class="absolute top-12 right-6 w-14 h-7 bg-white rounded-full opacity-85 shadow-lg animate-cloud-drift" style="animation-delay: 2s;"></div>
                  
                  <!-- Sun - Center -->
                  <div class="animate-spin-sun">
                    <div class="w-14 h-14 bg-gradient-to-br from-yellow-300 via-yellow-400 to-orange-400 rounded-full shadow-xl"></div>
                  </div>
                  
                  <!-- Wind Lines - Bottom -->
                  <div class="absolute bottom-8 left-6 right-6 space-y-2">
                    <div class="h-1 bg-gradient-to-r from-transparent via-cyan-400 to-transparent rounded-full animate-wind-blow"></div>
                    <div class="h-1 bg-gradient-to-r from-transparent via-cyan-400 to-transparent rounded-full animate-wind-blow" style="animation-delay: 0.4s;"></div>
                  </div>
                </div>
                
                <a href="/visualization" class="inline-flex items-center gap-2 px-6 py-3 bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 text-white rounded-lg shadow-lg font-bold transition-all transform hover:scale-105 card-hover">📊 Dashboard</a>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Main Content Grid -->
      <div class="mb-8">

      <div class="mb-6">
        <!-- Manual Entry -->
        <div class="rounded-xl shadow-lg mb-6">
          <div class="bg-gradient-to-r from-orange-500 to-red-500 p-1 rounded-xl">
            <div class="bg-slate-800/60 backdrop-blur-sm p-6 rounded-lg">
              <label class="text-sm font-bold text-orange-300 flex items-center gap-2 mb-3">
                <span class="text-lg">⌨️</span>
                <span><h2>Enter City:</h2></span>
              </label>
              <div class="flex gap-3 items-start">
                <div class="relative flex-1">
                  <input id="location-input" type="text" placeholder="Enter city name or coordinates (lat,lon)" class="w-full p-4 rounded-lg bg-slate-700/60 border-2 border-orange-400/50 text-slate-100 placeholder:text-slate-500 focus:border-orange-300 focus:outline-none transition-all hover:border-orange-400" autocomplete="off" />
                  <div id="city-suggestions" class="bg-slate-800/95 backdrop-blur-sm border border-orange-400/50 rounded-lg shadow-2xl max-h-60 overflow-y-auto hidden"></div>
                </div>
                <button id="fetch-weather-btn" class="relative z-10 px-6 py-4 bg-gradient-to-r from-cyan-500 via-blue-500 to-indigo-500 hover:from-cyan-400 hover:via-blue-400 hover:to-indigo-400 text-white rounded-lg font-bold text-lg shadow-xl transition-all transform hover:scale-105 hover:shadow-2xl whitespace-nowrap">
                  <span class="flex items-center justify-center gap-2">
                    <span class="text-2xl">🔍</span>
                    <span>Get Weather & HCI Analysis</span>
                  </span>
                </button>
              </div>
            </div>
          </div>
        </div>
        
        <!-- Options Grid -->
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-3 mb-6">
          <div class="rounded-lg overflow-hidden shadow-lg">
            <div class="bg-gradient-to-r from-blue-500 to-indigo-500 p-1">
              <select id="units" class="w-full p-3 rounded-lg bg-slate-800/60 border border-blue-400/50 text-slate-100 focus:border-blue-300 focus:outline-none cursor-pointer transition-all font-semibold">
                <option value="metric">🌡️ Celsius</option>
              </select>
            </div>
          </div>
          
          <div class="rounded-lg overflow-hidden shadow-lg">
            <div class="bg-gradient-to-r from-violet-500 to-purple-500 p-1">
              <select id="forecast_days" class="w-full p-3 rounded-lg bg-slate-800/60 border border-violet-400/50 text-slate-100 focus:border-violet-300 focus:outline-none cursor-pointer transition-all font-semibold">
                <option value="0">📅 Now</option>
                <option value="1">📆 1 Day</option>
                <option value="2">📆 2 Days</option>
                <option value="3" selected>📆 3 Days</option>
              </select>
            </div>
          </div>
          
          <div class="flex items-center gap-2 p-3 bg-gradient-to-r from-teal-500 to-cyan-500 rounded-lg shadow-lg">
            <span class="text-lg">✅</span>
            <span class="text-sm font-bold text-white">🌫️ AQI</span>
          </div>
          
          <div class="flex items-center gap-2 p-3 bg-gradient-to-r from-yellow-500 to-orange-500 rounded-lg shadow-lg">
            <span class="text-lg">✅</span>
            <span class="text-sm font-bold text-white">☀️ UV</span>
          </div>
          
          <div class="rounded-lg overflow-hidden shadow-lg">
            <div class="bg-gradient-to-r from-pink-500 to-rose-500 p-1">
              <select id="profile" class="w-full p-3 rounded-lg bg-slate-800/60 border border-pink-400/50 text-slate-100 focus:border-pink-300 focus:outline-none cursor-pointer transition-all font-semibold text-sm">
                <option value="general">👤 General</option>
                <option value="asthma">🫁 Asthma</option>
                <option value="elderly_child">👴👧 Elderly</option>
                <option value="athlete">🏃 Athlete</option>
              </select>
            </div>
          </div>
        </div>
      </div>

      <div id="status-message" class="mb-4 text-sm font-semibold text-transparent bg-gradient-to-r from-cyan-300 to-blue-300 bg-clip-text"></div>

      <!-- Weather Cards Grid -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-8">
        <!-- HCI Card -->
        <div class="rounded-2xl overflow-hidden shadow-2xl card-hover animate-slide-in">
          <div class="bg-gradient-to-r from-amber-500 via-orange-500 to-red-500 p-1">
            <div class="bg-gradient-to-br from-slate-900/95 to-slate-800/95 backdrop-blur-sm p-8 rounded-xl theme-transition">
              <div class="flex items-center gap-3 mb-6">
                <span class="text-4xl animate-pulse">🎯</span>
                <h3 class="text-2xl font-bold text-transparent bg-gradient-to-r from-amber-300 to-orange-300 bg-clip-text">Human Comfort Index</h3>
              </div>
              <div id="hci-value" class="text-7xl font-black text-center my-6 text-transparent bg-gradient-to-r from-amber-300 via-orange-300 to-red-300 bg-clip-text animate-pulse">--</div>
              <div id="comfort-badge" class="text-center px-6 py-3 rounded-full text-sm font-bold mb-4 bg-gradient-to-r transition-all duration-300">--</div>
              <div id="comfort-desc" class="text-center text-sm text-slate-300 mb-6 italic">--</div>
              
              <!-- Band, Env, Rec section -->
              <div class="space-y-3 text-center text-sm mb-6 pb-6 border-b border-amber-500/30">
                <div class="text-slate-300">Band: <span id="comfort-band" class="font-semibold text-amber-300">--</span></div>
                <div class="text-slate-300">Env: <span id="comfort-env" class="font-semibold text-amber-300">--</span></div>
                <div class="text-slate-300">Rec: <span id="comfort-rec" class="font-semibold text-amber-300">--</span></div>
              </div>
              
              <div class="space-y-4 pt-6 border-t border-amber-500/30">
                <div class="flex justify-between items-center p-2 rounded hover:bg-slate-800/30 transition-all">
                  <span class="text-slate-400 text-sm font-semibold">📊 Weighted HCI</span>
                  <span id="weighted-hci" class="font-bold text-lg text-amber-300">--</span>
                </div>
                <div class="grid grid-cols-2 gap-3 text-xs">
                  <div class="bg-slate-800/50 p-3 rounded-lg border border-amber-400/30 hover:border-amber-400/60 hover:bg-slate-700/50 transition-all cursor-default">
                    <div class="text-slate-500 font-semibold">AQI</div>
                    <div id="comp-aqi" class="text-amber-200 font-bold text-lg mt-1">--</div>
                  </div>
                  <div class="bg-slate-800/50 p-3 rounded-lg border border-amber-400/30 hover:border-amber-400/60 hover:bg-slate-700/50 transition-all cursor-default">
                    <div class="text-slate-500 font-semibold">Temp</div>
                    <div id="comp-temp" class="text-amber-200 font-bold text-lg mt-1">--</div>
                  </div>
                  <div class="bg-slate-800/50 p-3 rounded-lg border border-amber-400/30 hover:border-amber-400/60 hover:bg-slate-700/50 transition-all cursor-default">
                    <div class="text-slate-500 font-semibold">Humidity</div>
                    <div id="comp-humidity" class="text-amber-200 font-bold text-lg mt-1">--</div>
                  </div>
                  <div class="bg-slate-800/50 p-3 rounded-lg border border-amber-400/30 hover:border-amber-400/60 hover:bg-slate-700/50 transition-all cursor-default">
                    <div class="text-slate-500 font-semibold">UV</div>
                    <div id="comp-uv" class="text-amber-200 font-bold text-lg mt-1">--</div>
                  </div>
                  <div class="bg-slate-800/50 p-3 rounded-lg border border-amber-400/30 hover:border-amber-400/60 hover:bg-slate-700/50 transition-all cursor-default">
                    <div class="text-slate-500 font-semibold">Wind</div>
                    <div id="comp-wind" class="text-amber-200 font-bold text-lg mt-1">--</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        
        <!-- Weather Info Card -->
        <div class="lg:col-span-2 rounded-2xl overflow-hidden shadow-2xl card-hover animate-slide-in">
          <div class="bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500 p-1">
            <div class="bg-gradient-to-br from-slate-900/95 to-slate-800/95 backdrop-blur-sm p-6 rounded-xl theme-transition">
              <div class="flex items-center justify-between mb-4">
                <div class="flex-1">
                  <p id="loc" class="text-3xl font-bold text-transparent bg-gradient-to-r from-cyan-300 to-blue-300 bg-clip-text">--</p>
                  <div class="flex items-center gap-3 mt-2">
                    <div id="weatherIcon" class="text-4xl animate-bounce" data-weather-icon>🌡️</div>
                    <p id="cond" class="text-base text-slate-300">--</p>
                  </div>
                </div>
              </div>
              
              <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div class="rounded-lg overflow-hidden card-hover">
                  <div class="bg-gradient-to-r from-red-500 to-orange-500 p-1">
                    <div class="bg-slate-800/60 p-4 rounded text-center hover:bg-slate-700/60 transition-all">
                      <div class="text-xs text-slate-400 mb-2">🌡️ Temperature</div>
                      <div id="temp" class="text-2xl font-bold text-red-300">--</div>
                    </div>
                  </div>
                </div>
                
                <div class="rounded-lg overflow-hidden card-hover">
                  <div class="bg-gradient-to-r from-orange-500 to-yellow-500 p-1">
                    <div class="bg-slate-800/60 p-4 rounded text-center hover:bg-slate-700/60 transition-all">
                      <div class="text-xs text-slate-400 mb-2">📊 Avg Temp</div>
                      <div id="avgtemp_c" class="text-2xl font-bold text-yellow-300">--</div>
                    </div>
                  </div>
                </div>
                

              </div>
              
              <div id="aqi" class="mt-4 p-4 bg-slate-800/50 rounded-lg border border-slate-700/50 text-sm text-slate-300 hover:bg-slate-700/50 transition-all"></div>
            </div>
          </div>
        </div>

      <!-- Forecast Section -->
      <div id="forecast-section" class="mt-10"></div>

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
        const suggestionsDiv = document.getElementById('city-suggestions');
        let searchTimeout = null;
        let selectedIndex = -1;

        const outLoc = document.getElementById('loc');
        const outCond = document.getElementById('cond');
        const outTemp = document.getElementById('temp');
        const outAvgTemp = document.getElementById('avgtemp_c');

        async function fetchWeather(){
          let q = locInput.value || 'London';
          
          if (!q) {
            status.textContent = 'Please enter a location';
            return;
          }
          
          const units = document.getElementById('units').value;
            const days = parseInt(document.getElementById('forecast_days').value, 10);
          const profile = document.getElementById('profile').value;
          fetchButton.disabled = true;
          status.textContent = 'Fetching...';
          try{
            const r = await fetch('/api/get_weather', {
              method: 'POST', headers: {'Content-Type':'application/json'},
              body: JSON.stringify({query: q, units: units, forecast_days: days, include_aqi: true, include_uv: true, profile: profile})
            });
            const data = await r.json();
            console.log('API Response received:', data);
            console.log('Humidity in response:', data.humidity);
            if(!r.ok){ 
              status.textContent = 'Error: ' + (data.error || 'Failed to fetch weather data'); 
              fetchButton.disabled = false;
              return; 
            }
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
            status.textContent = 'Updated: ' + (data.local_time || '');

            // If forecast provided, render it
            const forecastSectionEl = document.getElementById('forecast-section');
            if(data.forecast && data.forecast.length){
              let forecastHTML = '<h2 class="text-3xl font-bold mb-6 text-cyan-300">📅 3-Day Forecast</h2>';
              forecastHTML += '<div class="grid grid-cols-1 md:grid-cols-3 gap-6">';
              
              data.forecast.forEach((d, idx) => {
                const hciVal = d.possible_hci ? parseFloat(d.possible_hci) : null;
                let hciColor = 'from-slate-800/50 to-slate-800/30 border-slate-700/30';
                let hciBg = 'bg-slate-800/30';
                if (hciVal) {
                  if (hciVal > 75) {
                    hciColor = 'from-green-900/30 to-green-900/20 border-green-500/30';
                    hciBg = 'bg-green-900/20';
                  } else if (hciVal > 60) {
                    hciColor = 'from-yellow-900/30 to-yellow-900/20 border-yellow-500/30';
                    hciBg = 'bg-yellow-900/20';
                  } else if (hciVal > 45) {
                    hciColor = 'from-orange-900/30 to-orange-900/20 border-orange-500/30';
                    hciBg = 'bg-orange-900/20';
                  } else {
                    hciColor = 'from-red-900/30 to-red-900/20 border-red-500/30';
                    hciBg = 'bg-red-900/20';
                  }
                }
                forecastHTML += `
                  <div class="bg-gradient-to-br ${hciColor} border ${hciBg} p-6 rounded-xl shadow-lg hover:shadow-xl transition-shadow">
                    <div class="text-sm text-cyan-300 font-bold mb-2">${d.date}</div>
                    <div class="text-2xl font-bold text-slate-100 mb-3">${d.condition || '--'}</div>
                    <div class="space-y-2 text-sm mb-4">
                      <div class="flex justify-between"><span class="text-slate-400">Avg Temp:</span> <span class="font-bold text-cyan-200">${d.avgtemp_c || '--'}°C</span></div>
                      <div class="flex justify-between"><span class="text-slate-400">Min/Max:</span> <span class="font-bold text-cyan-200">${d.mintemp_c || '--'}/${d.maxtemp_c || '--'}°C</span></div>
                      <div class="flex justify-between"><span class="text-slate-400">Humidity:</span> <span class="font-bold text-cyan-200">${d.humidity || '--'}%</span></div>
                      <div class="flex justify-between"><span class="text-slate-400">Wind:</span> <span class="font-bold text-cyan-200">${d.wind_kph || '--'} kph</span></div>
                      <div class="flex justify-between"><span class="text-slate-400">UV Index:</span> <span class="font-bold text-cyan-200">${d.uv || '--'}</span></div>
                    </div>
                    <div class="p-3 bg-gradient-to-r from-amber-900/40 to-orange-900/30 border border-amber-500/30 rounded-lg">
                      <div class="text-xs text-amber-300 font-bold">🎯 Possible HCI</div>
                      <div class="text-2xl font-bold text-amber-200 mt-1">${d.possible_hci || '--'}</div>
                      <div class="text-xs text-slate-400 mt-1">📌 Based on forecast conditions</div>
                    </div>
                  </div>
                `;
              });
              
              forecastHTML += '</div>';
              forecastSectionEl.innerHTML = forecastHTML;

              // Charts removed - canvas elements no longer in HTML
              destroyCharts();
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
            status.textContent = '';
          }finally{ fetchButton.disabled = false; }
        }

        // City autocomplete functionality
        async function searchCities(query) {
          if (!query || query.length < 2) {
            suggestionsDiv.classList.add('hidden');
            return;
          }
          
          try {
            const response = await fetch(`/api/search_cities?q=${encodeURIComponent(query)}`);
            const cities = await response.json();
            
            if (cities.length === 0) {
              suggestionsDiv.classList.add('hidden');
              return;
            }
            
            suggestionsDiv.innerHTML = '';
            cities.forEach((city, index) => {
              const item = document.createElement('div');
              item.className = 'city-suggestion-item';
              item.textContent = city.name;
              item.addEventListener('click', () => {
                locInput.value = city.query;
                suggestionsDiv.classList.add('hidden');
                selectedIndex = -1;
              });
              suggestionsDiv.appendChild(item);
            });
            
            suggestionsDiv.classList.remove('hidden');
            selectedIndex = -1;
          } catch (error) {
            console.error('Error searching cities:', error);
            suggestionsDiv.classList.add('hidden');
          }
        }
        
        locInput.addEventListener('input', (e) => {
          const query = e.target.value.trim();
          
          // Clear previous timeout
          if (searchTimeout) {
            clearTimeout(searchTimeout);
          }
          
          // Debounce search - wait 300ms after user stops typing
          searchTimeout = setTimeout(() => {
            searchCities(query);
          }, 300);
        });
        
        // Handle keyboard navigation in suggestions
        locInput.addEventListener('keydown', (e) => {
          const items = suggestionsDiv.querySelectorAll('.city-suggestion-item');
          
          if (items.length === 0) return;
          
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            selectedIndex = Math.min(selectedIndex + 1, items.length - 1);
            updateSelection(items);
          } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            selectedIndex = Math.max(selectedIndex - 1, -1);
            updateSelection(items);
          } else if (e.key === 'Enter' && selectedIndex >= 0) {
            e.preventDefault();
            items[selectedIndex].click();
          } else if (e.key === 'Escape') {
            suggestionsDiv.classList.add('hidden');
            selectedIndex = -1;
          }
        });
        
        function updateSelection(items) {
          items.forEach((item, index) => {
            if (index === selectedIndex) {
              item.classList.add('active');
              item.scrollIntoView({ block: 'nearest' });
            } else {
              item.classList.remove('active');
            }
          });
        }
        
        // Hide suggestions when clicking outside
        document.addEventListener('click', (e) => {
          if (!locInput.contains(e.target) && !suggestionsDiv.contains(e.target)) {
            suggestionsDiv.classList.add('hidden');
          }
        });

        fetchButton.addEventListener('click', fetchWeather);
        locInput.addEventListener('keypress', (e)=>{ if(e.key==='Enter') fetchWeather(); });
        
        // Dark/Light Mode Toggle
        const themeToggle = document.getElementById('themeToggle');
        const themeIcon = document.getElementById('themeIcon');
        
        if (themeToggle && themeIcon) {
          const body = document.body;
          
          // Check saved theme preference
          const savedTheme = localStorage.getItem('theme') || 'dark';
          if (savedTheme === 'light') {
            body.classList.add('light-mode');
            themeIcon.textContent = '☀️';
          }
          
          themeToggle.addEventListener('click', () => {
            body.classList.toggle('light-mode');
            const isLight = body.classList.contains('light-mode');
            themeIcon.textContent = isLight ? '☀️' : '🌙';
            localStorage.setItem('theme', isLight ? 'light' : 'dark');
            console.log('Theme toggled to:', isLight ? 'light' : 'dark');
          });
        }
        
        // Weather Icon Mapping
        function getWeatherIcon(condition) {
          const conditions = {
            'Sunny': '☀️', 'Clear': '🌙', 'Partly cloudy': '⛅',
            'Cloudy': '☁️', 'Overcast': '🌥️', 'Mist': '🌫️',
            'Patchy rain': '🌦️', 'Light rain': '🌧️', 'Moderate rain': '🌧️',
            'Heavy rain': '⛈️', 'Thunderstorm': '⛈️', 'Light snow': '❄️',
            'Heavy snow': '❄️', 'Wind': '💨', 'Fog': '🌫️'
          };
          for (let key in conditions) {
            if (condition.includes(key)) return conditions[key];
          }
          return '🌡️';
        }
        
        // Add weather icon display in weather info card
        const originalFetchWeather = window.fetchWeather;
        window.fetchWeather = async function() {
          await originalFetchWeather();
          // Add icon animation after weather is fetched
          const weatherIcon = document.querySelector('[data-weather-icon]');
          if (weatherIcon) {
            weatherIcon.classList.add('animate-bounce');
          }
        };
        
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
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
      /* Reuse main UI animations and theme helpers for consistent look */
      @keyframes float-left-right { 0%, 100% { transform: translateX(0px); } 50% { transform: translateX(30px); } }
      @keyframes float-up-down { 0%, 100% { transform: translateY(0px); } 50% { transform: translateY(-15px); } }
      @keyframes spin-sun { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
      @keyframes wind-blow { 0%, 100% { transform: scaleX(1); } 50% { transform: scaleX(1.2); } }
      @keyframes cloud-drift { 0%, 100% { transform: translateX(-20px); } 50% { transform: translateX(20px); } }
      @keyframes slide-in { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
      @keyframes glow-pulse { 0%, 100% { box-shadow: 0 0 20px rgba(59, 130, 246, 0.5); } 50% { box-shadow: 0 0 30px rgba(59, 130, 246, 0.8); } }
      .animate-float-lr { animation: float-left-right 4s ease-in-out infinite; }
      .animate-float-ud { animation: float-up-down 3s ease-in-out infinite; }
      .animate-spin-sun { animation: spin-sun 20s linear infinite; }
      .animate-wind-blow { animation: wind-blow 2s ease-in-out infinite; }
      .animate-cloud-drift { animation: cloud-drift 5s ease-in-out infinite; }
      .animate-slide-in { animation: slide-in 0.6s ease-out; }
      .animate-glow { animation: glow-pulse 2s ease-in-out infinite; }
      .card-hover { transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); }
      .card-hover:hover { transform: translateY(-4px); box-shadow: 0 20px 40px rgba(0,0,0,0.3); }
      .theme-transition { transition: background-color 0.3s, color 0.3s, border-color 0.3s; }
      body.light-mode { background: linear-gradient(to bottom, #f0f9ff, #e0e7ff, #eff6ff) !important; color: #1e293b !important; }
      /* Plotly sizing helpers */
      .plotly-container { width: 100% !important; height: 100% !important; }
      .plotly { width: 100% !important; height: 100% !important; }
      #chart-hci, #chart-temp, #chart-humidity, #chart-wind, #chart-monthly { width:100%; height:100%; }
    </style>
  </head>
  <body class="bg-gradient-to-b from-slate-950 via-indigo-950 to-slate-900 text-slate-100 min-h-screen p-6 theme-transition">
    <!-- Animated Background (same look as main) -->
    <div class="fixed inset-0 z-0 opacity-30 theme-transition" id="bgGradient">
      <div class="absolute top-20 left-10 w-72 h-72 bg-cyan-500 rounded-full mix-blend-multiply filter blur-3xl animate-pulse"></div>
      <div class="absolute top-40 right-10 w-72 h-72 bg-purple-500 rounded-full mix-blend-multiply filter blur-3xl animate-pulse animation-delay-2000"></div>
      <div class="absolute -bottom-8 left-20 w-72 h-72 bg-blue-500 rounded-full mix-blend-multiply filter blur-3xl animate-pulse animation-delay-4000"></div>
    </div>

    <div class="max-w-7xl mx-auto relative z-10">
      <!-- Header Card matching main page style -->
      <div class="mb-6 rounded-2xl overflow-hidden shadow-2xl animate-slide-in">
        <div class="bg-gradient-to-r from-indigo-600 via-purple-600 to-pink-600 p-1 rounded-2xl">
          <div class="bg-gradient-to-br from-slate-950/95 to-slate-900/95 backdrop-blur-sm p-6 rounded-xl border border-gradient-to-r from-cyan-400/20 to-purple-400/20 theme-transition flex items-center justify-between">
            <div>
              <h1 class="text-4xl font-bold text-transparent bg-gradient-to-r from-cyan-300 via-blue-300 to-purple-300 bg-clip-text">Weather Analytics — Visualization</h1>
              <p class="text-sm text-slate-300 mt-1">Interactive charts and KPIs — styled to match main dashboard</p>
            </div>
            <div class="flex items-center gap-3">
              <a href="/" class="inline-flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-purple-600 to-pink-600 text-white rounded-lg shadow hover:scale-105">🏠 Back</a>
            </div>
          </div>
        </div>
      </div>

      <!-- Main visualization grid (keeps original layout but restyled) -->
      <div class="grid grid-cols-1 lg:grid-cols-4 gap-4">
        <div class="col-span-1 p-4 rounded-2xl border border-slate-700/30 bg-gradient-to-br from-slate-900/60 to-slate-800/40 shadow-lg">
          <label class="text-sm text-slate-300">Location</label>
          <input id="viz-location" class="w-full mt-3 p-3 rounded-lg bg-slate-800/60 border border-slate-700 text-slate-100" placeholder="Enter city or 'lat,lon'" />
          <div class="mt-3 grid grid-cols-2 gap-2">
            <select id="viz-units" class="p-2 rounded bg-slate-800 border border-slate-700 text-slate-100">
              <option value="metric">Celsius</option>
            </select>
            <button id="viz-fetch" class="p-2 bg-gradient-to-r from-cyan-500 to-blue-600 rounded-lg text-white font-bold">Refresh</button>
          </div>

          <div class="mt-4 space-y-3">
            <div class="p-3 bg-slate-800/50 rounded-lg border border-slate-700/30">
              <div class="text-xs text-slate-400">HCI</div>
              <div id="viz-hci" class="text-2xl font-bold text-amber-300">--</div>
            </div>
            <div class="p-3 bg-slate-800/50 rounded-lg border border-slate-700/30">
              <div class="text-xs text-slate-400">Temperature</div>
              <div id="viz-temp" class="text-2xl font-bold text-yellow-300">--</div>
            </div>
            <div class="p-3 bg-slate-800/50 rounded-lg border border-slate-700/30">
              <div class="text-xs text-slate-400">Humidity</div>
              <div id="viz-hum" class="text-2xl font-bold text-cyan-300">--</div>
            </div>
            <div class="p-3 bg-slate-800/50 rounded-lg border border-slate-700/30">
              <div class="text-xs text-slate-400">AQI (PM2.5)</div>
              <div id="viz-aqi" class="text-2xl font-bold text-amber-200">--</div>
            </div>
          </div>
        </div>

        <div class="lg:col-span-3 grid grid-cols-1 gap-4">
          <div class="bg-gradient-to-br from-slate-900/60 to-slate-800/50 p-4 rounded-2xl border border-slate-700/30 shadow-lg h-96">
            <div id="chart-hci" class="w-full h-full plotly-container"></div>
          </div>

          <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div class="bg-gradient-to-br from-slate-900/60 to-slate-800/50 p-4 rounded-lg border border-slate-700/30 shadow-lg h-80"><div id="chart-temp" class="w-full h-full plotly-container"></div></div>
            <div class="bg-gradient-to-br from-slate-900/60 to-slate-800/50 p-4 rounded-lg border border-slate-700/30 shadow-lg h-80"><div id="chart-humidity" class="w-full h-full plotly-container"></div></div>
            <div class="bg-gradient-to-br from-slate-900/60 to-slate-800/50 p-4 rounded-lg border border-slate-700/30 shadow-lg h-80"><div id="chart-wind" class="w-full h-full plotly-container"></div></div>
          </div>

          <div class="bg-gradient-to-br from-slate-900/60 to-slate-800/50 p-4 rounded-2xl border border-slate-700/30 shadow-lg h-96">
            <div id="chart-monthly" class="w-full h-full plotly-container"></div>
          </div>
        </div>
      </div>

    <script>
      async function fetchViz(location, units){
        const resp = await fetch('/api/get_weather', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({query:location, units:units, forecast_days:3, include_aqi:true})});
        const data = await resp.json();
        return data;
      }

      async function generateCharts(location) {
        const resp = await fetch('/api/generate_charts', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({location:location})});
        const charts = await resp.json();
        return charts;
      }

      function renderKPIs(data){
        document.getElementById('viz-hci').textContent = data.hci || '--';
        document.getElementById('viz-temp').textContent = data.temperature_c ? data.temperature_c + ' °C' : '--';
        const vizHumVal = (data.humidity !== undefined && data.humidity !== null && data.humidity !== '') ? data.humidity : null;
        document.getElementById('viz-hum').textContent = vizHumVal !== null ? String(vizHumVal) + ' %' : '--';
        if(data.aqi){
          const pm25 = data.aqi.pm2_5 || data.aqi['pm2_5'] || null;
          document.getElementById('viz-aqi').textContent = pm25 ? pm25.toFixed(1) : 'N/A';
        } else { document.getElementById('viz-aqi').textContent = '--'; }
      }

      async function refreshViz(){
        const loc = document.getElementById('viz-location').value || 'London';
        const units = document.getElementById('viz-units').value;
        
        try {
          const data = await fetchViz(loc, units);
          renderKPIs(data);
          
          const charts = await generateCharts(loc);
          if(charts.error) {
            console.error('Chart generation error:', charts.error);
          } else {
            // Render each chart using Plotly.newPlot
            Plotly.newPlot('chart-hci', charts.hci.data, charts.hci.layout, {responsive: true});
            Plotly.newPlot('chart-temp', charts.temperature.data, charts.temperature.layout, {responsive: true});
            Plotly.newPlot('chart-humidity', charts.humidity.data, charts.humidity.layout, {responsive: true});
            Plotly.newPlot('chart-wind', charts.wind.data, charts.wind.layout, {responsive: true});
            Plotly.newPlot('chart-monthly', charts.monthly.data, charts.monthly.layout, {responsive: true});
          }
        } catch (err) {
          console.error('Error refreshing visualization:', err);
        }
      }

      document.addEventListener('DOMContentLoaded', ()=>{
        document.getElementById('viz-fetch').addEventListener('click', refreshViz);
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


@app.route('/api/generate_charts', methods=['POST'])
def generate_charts():
  """Generate interactive Plotly charts as JSON"""
  try:
    data = request.get_json()
    location = data.get('location', 'London')
    profile = data.get('profile', 'general')
    
    # Call the get_weather endpoint to get processed data with possible_hci
    weather_resp = requests.post('http://127.0.0.1:5000/api/get_weather', 
                                 json={'query': location, 'profile': profile, 'forecast_days': 3, 'include_aqi': True})
    weather_data = weather_resp.json()
    
    if 'error' in weather_data:
      return jsonify({'error': weather_data['error']}), 400
    
    forecast_custom = weather_data.get('forecast', [])
    
    # Extract data from the custom forecast structure (which has possible_hci)
    labels = ['Today'] + [f.get('date', '') for f in forecast_custom]
    # Get HCI from weather_data result which has the computed HCI values
    current_hci = float(weather_data.get('hci', 50)) if weather_data.get('hci') is not None else 50.0
    hci_values = [current_hci] + [float(f.get('possible_hci', 50)) if f.get('possible_hci') is not None else 50.0 for f in forecast_custom]
    temp_values = [float(weather_data.get('avgtemp_c', 20)) if weather_data.get('avgtemp_c') is not None else 20.0] + [float(f.get('avgtemp_c', 20)) if f.get('avgtemp_c') is not None else 20.0 for f in forecast_custom]
    humidity_values = [float(weather_data.get('humidity', 50)) if weather_data.get('humidity') is not None else 50.0] + [float(f.get('avghumidity', 50)) if f.get('avghumidity') is not None else 50.0 for f in forecast_custom]
    wind_values = [float(weather_data.get('wind_kph', 10)) if weather_data.get('wind_kph') is not None else 10.0] + [float(f.get('wind_kph', 10)) if f.get('wind_kph') is not None else 10.0 for f in forecast_custom]
    
    charts = {}
    
    # 1. HCI Trend
    fig_hci = go.Figure()
    fig_hci.add_trace(go.Scatter(
      x=labels, y=hci_values, mode='lines+markers', name='HCI Trend',
      line=dict(color='#fbbf24', width=3),
      marker=dict(size=10, color='#fbbf24', line=dict(color='#f59e0b', width=2)),
      fill='tozeroy', fillcolor='rgba(251, 191, 36, 0.2)',
      hovertemplate='<b>%{x}</b><br>HCI: %{y:.1f}<extra></extra>'
    ))
    fig_hci.update_layout(
      title='HCI Trend', xaxis_title='Day', yaxis_title='HCI Score',
      hovermode='x unified', plot_bgcolor='#0f172a', paper_bgcolor='#1e293b',
      font=dict(color='#cbd5e1'), height=350, margin=dict(l=50, r=50, t=60, b=50),
      yaxis=dict(range=[0, 100]),
      xaxis=dict(showgrid=True, gridwidth=1, gridcolor='#334155'),
      yaxis_showgrid=True, yaxis_gridwidth=1, yaxis_gridcolor='#334155'
    )
    charts['hci'] = json.loads(pio.to_json(fig_hci))
    
    # 2. Temperature
    fig_temp = go.Figure()
    colors = ['#10b981' if i == 0 else '#60a5fa' for i in range(len(labels))]
    fig_temp.add_trace(go.Bar(x=labels, y=temp_values, name='Temperature',
      marker=dict(color=colors, line=dict(color='#334155', width=1)),
      hovertemplate='<b>%{x}</b><br>Temperature: %{y:.1f}°C<extra></extra>'
    ))
    fig_temp.update_layout(
      title='Temperature', xaxis_title='Day', yaxis_title='°C',
      hovermode='x unified', plot_bgcolor='#0f172a', paper_bgcolor='#1e293b',
      font=dict(color='#cbd5e1'), height=300, margin=dict(l=50, r=30, t=60, b=50),
      showlegend=False,
      xaxis=dict(showgrid=True, gridwidth=1, gridcolor='#334155'),
      yaxis_showgrid=True, yaxis_gridwidth=1, yaxis_gridcolor='#334155'
    )
    charts['temperature'] = json.loads(pio.to_json(fig_temp))
    
    # 3. Humidity
    fig_hum = go.Figure()
    fig_hum.add_trace(go.Scatter(x=labels, y=humidity_values, mode='lines+markers', name='Humidity',
      line=dict(color='#06b6d4', width=3),
      marker=dict(size=9, color='#06b6d4', line=dict(color='#0891b2', width=2)),
      fill='tozeroy', fillcolor='rgba(6, 182, 212, 0.2)',
      hovertemplate='<b>%{x}</b><br>Humidity: %{y:.0f}%<extra></extra>'
    ))
    fig_hum.update_layout(
      title='Humidity', xaxis_title='Day', yaxis_title='%',
      hovermode='x unified', plot_bgcolor='#0f172a', paper_bgcolor='#1e293b',
      font=dict(color='#cbd5e1'), height=300, margin=dict(l=50, r=30, t=60, b=50),
      yaxis=dict(range=[0, 100]), showlegend=False,
      xaxis=dict(showgrid=True, gridwidth=1, gridcolor='#334155'),
      yaxis_showgrid=True, yaxis_gridwidth=1, yaxis_gridcolor='#334155'
    )
    charts['humidity'] = json.loads(pio.to_json(fig_hum))
    
    # 4. Wind Speed
    fig_wind = go.Figure()
    colors_wind = ['#10b981' if i == 0 else '#34d399' for i in range(len(labels))]
    fig_wind.add_trace(go.Bar(x=labels, y=wind_values, name='Wind Speed',
      marker=dict(color=colors_wind, line=dict(color='#334155', width=1)),
      hovertemplate='<b>%{x}</b><br>Wind Speed: %{y:.1f} kph<extra></extra>'
    ))
    fig_wind.update_layout(
      title='Wind Speed', xaxis_title='Day', yaxis_title='kph',
      hovermode='x unified', plot_bgcolor='#0f172a', paper_bgcolor='#1e293b',
      font=dict(color='#cbd5e1'), height=300, margin=dict(l=50, r=30, t=60, b=50),
      showlegend=False,
      xaxis=dict(showgrid=True, gridwidth=1, gridcolor='#334155'),
      yaxis_showgrid=True, yaxis_gridwidth=1, yaxis_gridcolor='#334155'
    )
    charts['wind'] = json.loads(pio.to_json(fig_wind))
    
    # 5. Monthly Temperature
    days_in_month = list(range(1, 31))
    base_temp = float(temp_values[0])
    np.random.seed(42)  # For consistency
    monthly_temps = [base_temp + 3*np.sin(d/5) + np.random.randn()*0.5 for d in days_in_month]
    
    fig_monthly = go.Figure()
    fig_monthly.add_trace(go.Scatter(x=days_in_month, y=monthly_temps, mode='lines', name='Daily Temperature',
      line=dict(color='#f97316', width=3), fill='tozeroy', fillcolor='rgba(249, 115, 22, 0.2)',
      marker=dict(size=6, color='#f97316'),
      hovertemplate='<b>Day %{x}</b><br>Temperature: %{y:.1f}°C<extra></extra>'
    ))
    fig_monthly.update_layout(
      title='Monthly Temperature Trend', xaxis_title='Day', yaxis_title='°C',
      hovermode='x unified', plot_bgcolor='#0f172a', paper_bgcolor='#1e293b',
      font=dict(color='#cbd5e1'), height=350, margin=dict(l=50, r=50, t=60, b=50),
      showlegend=False,
      xaxis=dict(showgrid=True, gridwidth=1, gridcolor='#334155'),
      yaxis_showgrid=True, yaxis_gridwidth=1, yaxis_gridcolor='#334155'
    )
    charts['monthly'] = json.loads(pio.to_json(fig_monthly))
    
    return jsonify(charts)
  except Exception as e:
    return jsonify({'error': str(e)}), 500


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
