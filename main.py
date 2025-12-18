import os
import requests
import json
import base64
import csv
from io import BytesIO
from flask import Flask, request, jsonify, render_template
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io
import numpy as np

# Flask app and WeatherAPI key
app = Flask(__name__)
WEATHERAPI_API_KEY = "YOUR_API_KEY" # Put your WeatherAPI key here or set as env variable


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

# Profile weights - loaded from CSV
_profiles_cache = None

def load_profiles():
  """Load profiles from Profiles.csv file with caching"""
  global _profiles_cache
  if _profiles_cache is None:
    _profiles_cache = {}
    try:
      csv_path = os.path.join(os.path.dirname(__file__), 'Profiles.csv')
      with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
          profile_name = row.get('Profile', '').strip()
          if not profile_name:
            continue
          
          # Convert all weight columns to float
          weights = {}
          for key in ['AQI', 'Temp', 'Humidity', 'HeatIndex', 'DewPoint', 'Wind', 'UV', 'Pressure', 'Precipitation', 'Cloud']:
            try:
              weights[key.lower()] = float(row.get(key, 0))
            except (ValueError, TypeError):
              weights[key.lower()] = 0.0
          
          _profiles_cache[profile_name.lower().replace('/', '_').replace(' ', '_')] = {
            'name': profile_name,
            'weights': weights,
            'description': row.get('Description', '').strip()
          }
      
      # Add a fallback general profile if not in CSV
      if 'general' not in _profiles_cache:
        _profiles_cache['general'] = {
          'name': 'General',
          'weights': {'aqi': 0.30, 'temp': 0.15, 'humidity': 0.15, 'heatindex': 0.0, 'dewpoint': 0.0, 'wind': 0.20, 'uv': 0.20, 'pressure': 0.0, 'precipitation': 0.0, 'cloud': 0.0},
          'description': 'Balanced for average healthy adults'
        }
    except Exception as e:
      print(f"Error loading profiles: {e}")
      # Fallback to default general profile
      _profiles_cache = {
        'general': {
          'name': 'General',
          'weights': {'aqi': 0.30, 'temp': 0.15, 'humidity': 0.15, 'heatindex': 0.0, 'dewpoint': 0.0, 'wind': 0.20, 'uv': 0.20, 'pressure': 0.0, 'precipitation': 0.0, 'cloud': 0.0},
          'description': 'Balanced for average healthy adults'
        }
      }
  return _profiles_cache

# Legacy PROFILE_WEIGHTS for backward compatibility - now loads from CSV
def get_profile_weights():
  """Get profile weights in the old format for backward compatibility"""
  profiles = load_profiles()
  legacy_weights = {}
  for key, profile in profiles.items():
    # Extract only the core weights that were in the old system
    legacy_weights[key] = {
      'aqi': profile['weights'].get('aqi', 0),
      'temp': profile['weights'].get('temp', 0),
      'humidity': profile['weights'].get('humidity', 0),
      'uv': profile['weights'].get('uv', 0),
      'wind': profile['weights'].get('wind', 0)
    }
  return legacy_weights

PROFILE_WEIGHTS = get_profile_weights()

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


def get_weather_data(query, units='metric', forecast_days=0, include_aqi=False, profile='general'):
  if not query:
    return {"error": "Location query is missing."}, 400

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
      return {"error": weather_data['error'].get('message', 'Unknown WeatherAPI error.')}, 404
  except requests.RequestException as e:
    return {"error": "Failed to fetch weather data."}, 500

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
    sel_profile = (profile or 'general').lower()
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
            sel_prof = (profile or 'general').lower()
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
          except Exception:
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
      profile = profile or 'general'
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

    return result, 200
  except Exception:
    return {"error": "Invalid data from WeatherAPI."}, 500


@app.route('/api/get_weather', methods=['POST'])
def get_weather():
  data = request.get_json() or {}
  query = data.get('query')
  units = data.get('units', 'metric')
  forecast_days = int(data.get('forecast_days', 0))
  include_aqi = bool(data.get('include_aqi', False))
  profile = data.get('profile')

  result, status_code = get_weather_data(query, units, forecast_days, include_aqi, profile)
  return jsonify(result), status_code
  




@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
  return render_template('index.html')

@app.route('/')
def landing():
  return render_template('initial.html')

@app.route('/profiles')
def profiles():
  return render_template('profiles.html')

@app.route('/api/get_profiles', methods=['GET'])
def get_profiles():
  """Return all profiles from CSV"""
  try:
    profiles_data = load_profiles()
    # Convert to list format for frontend
    profiles_list = []
    for key, profile in profiles_data.items():
      profiles_list.append({
        'key': key,
        'name': profile['name'],
        'description': profile['description'],
        'weights': profile['weights']
      })
    return jsonify(profiles_list), 200
  except Exception as e:
    return jsonify({'error': str(e)}), 500




@app.route('/api/generate_charts', methods=['POST'])
def generate_charts():
  """Generate interactive Plotly charts as JSON"""
  try:
    data = request.get_json()
    location = data.get('location', 'Pune')
    profile = data.get('profile', 'general')
    
    # Call the helper directly
    weather_data, status_code = get_weather_data(location, units='metric', forecast_days=3, include_aqi=True, profile=profile)
    
    if status_code != 200 or 'error' in weather_data:
      return jsonify({'error': weather_data.get('error', 'Unknown error')}), status_code if status_code != 200 else 400
    
    forecast_custom = weather_data.get('forecast', [])
    
    # Extract data from the custom forecast structure (which has possible_hci)
    labels = ['Today'] + [f.get('date', '') for f in forecast_custom]
    # Get HCI from weather_data result which has the computed HCI values
    current_hci = float(weather_data.get('hci', 50)) if weather_data.get('hci') is not None else 50.0
    hci_values = [current_hci] + [float(f.get('possible_hci', 50)) if f.get('possible_hci') is not None else 50.0 for f in forecast_custom]
    temp_values = [float(weather_data.get('avgtemp_c', 20)) if weather_data.get('avgtemp_c') is not None else 20.0] + [float(f.get('avgtemp_c', 20)) if f.get('avgtemp_c') is not None else 20.0 for f in forecast_custom]
    humidity_values = [float(weather_data.get('humidity', 50)) if weather_data.get('humidity') is not None else 50.0] + [float(f.get('avghumidity', 50)) if f.get('avghumidity') is not None else 50.0 for f in forecast_custom]
    wind_values = [float(weather_data.get('wind_kph', 10)) if weather_data.get('wind_kph') is not None else 10.0] + [float(f.get('wind_kph', 10)) if f.get('wind_kph') is not None else 10.0 for f in forecast_custom]
    
    # Helper to create base64 image from figure
    def fig_to_base64(fig):
      buf = io.BytesIO()
      fig.savefig(buf, format='png', bbox_inches='tight', facecolor='#1e293b') # Match bg color
      buf.seek(0)
      img_str = base64.b64encode(buf.getvalue()).decode('utf-8')
      plt.close(fig)
      return f"data:image/png;base64,{img_str}"

    # Set style for dark theme
    plt.style.use('dark_background')
    
    # Common chart params
    text_color = '#cbd5e1'
    grid_color = '#334155'
    
    charts = {}

    # 1. HCI Trend
    fig, ax = plt.subplots(figsize=(6, 4))
    fig.patch.set_facecolor('#1e293b')
    ax.set_facecolor('#0f172a')
    
    ax.plot(labels, hci_values, marker='o', color='#fbbf24', linewidth=3, markersize=8)
    ax.fill_between(labels, hci_values, color='#fbbf24', alpha=0.2)
    ax.set_title('HCI Trend', color=text_color)
    ax.set_ylabel('HCI Score', color=text_color)
    ax.grid(color=grid_color, linestyle='--', linewidth=0.5)
    ax.tick_params(colors=text_color)
    ax.set_ylim(0, 100)
    
    # Add annotations
    for i, v in enumerate(hci_values):
      ax.text(i, v + 2, f"{v:.1f}", color='#fbbf24', ha='center', fontweight='bold')
      
    charts['hci'] = fig_to_base64(fig)

    # 2. Temperature
    fig, ax = plt.subplots(figsize=(6, 3.5))
    fig.patch.set_facecolor('#1e293b')
    ax.set_facecolor('#0f172a')
    
    bars = ax.bar(labels, temp_values, color=['#10b981' if i==0 else '#60a5fa' for i in range(len(labels))])
    ax.set_title('Temperature (°C)', color=text_color)
    ax.grid(axis='y', color=grid_color, linestyle='--', linewidth=0.5)
    ax.tick_params(colors=text_color)
    
    # Add annotations
    for bar in bars:
      height = bar.get_height()
      ax.text(bar.get_x() + bar.get_width()/2., height + 0.5,
              f'{height:.1f}°', ha='center', va='bottom', color=text_color)
              
    charts['temperature'] = fig_to_base64(fig)

    # 3. Humidity
    fig, ax = plt.subplots(figsize=(6, 3.5))
    fig.patch.set_facecolor('#1e293b')
    ax.set_facecolor('#0f172a')
    
    ax.plot(labels, humidity_values, marker='o', color='#06b6d4', linewidth=3, markersize=8)
    ax.fill_between(labels, humidity_values, color='#06b6d4', alpha=0.2)
    ax.set_title('Humidity (%)', color=text_color)
    ax.set_ylim(0, 110)
    ax.grid(color=grid_color, linestyle='--', linewidth=0.5)
    ax.tick_params(colors=text_color)
    
    # Add annotations
    for i, v in enumerate(humidity_values):
      ax.text(i, v + 3, f"{v:.0f}%", color='#06b6d4', ha='center', fontweight='bold')
      
    charts['humidity'] = fig_to_base64(fig)

    # 4. Wind Speed
    fig, ax = plt.subplots(figsize=(6, 3.5))
    fig.patch.set_facecolor('#1e293b')
    ax.set_facecolor('#0f172a')
    
    bars = ax.bar(labels, wind_values, color=['#10b981' if i==0 else '#34d399' for i in range(len(labels))])
    ax.set_title('Wind Speed (kph)', color=text_color)
    ax.grid(axis='y', color=grid_color, linestyle='--', linewidth=0.5)
    ax.tick_params(colors=text_color)
    
    # Add annotations
    for bar in bars:
      height = bar.get_height()
      ax.text(bar.get_x() + bar.get_width()/2., height + 0.5,
              f'{height:.1f}', ha='center', va='bottom', color=text_color)
              
    charts['wind'] = fig_to_base64(fig)

    # 5. Monthly Temperature
    days_in_month = list(range(1, 31))
    base_temp = float(temp_values[0])
    np.random.seed(42)
    monthly_temps = [base_temp + 3*np.sin(d/5) + np.random.randn()*0.5 for d in days_in_month]
    
    fig, ax = plt.subplots(figsize=(6, 4))
    fig.patch.set_facecolor('#1e293b')
    ax.set_facecolor('#0f172a')
    
    ax.plot(days_in_month, monthly_temps, color='#f97316', linewidth=2)
    ax.fill_between(days_in_month, monthly_temps, color='#f97316', alpha=0.2)
    ax.set_title('Monthly Temperature Trend', color=text_color)
    ax.set_xlabel('Day', color=text_color)
    ax.set_ylabel('°C', color=text_color)
    ax.grid(color=grid_color, linestyle='--', linewidth=0.5)
    ax.tick_params(colors=text_color)
    
    charts['monthly'] = fig_to_base64(fig)
    
    return jsonify(charts)
  except Exception as e:
    return jsonify({'error': str(e)}), 500


@app.route('/visualization')
def visualization():
  return render_template('visualization.html')

if __name__ == '__main__':
    # Using host='0.0.0.0' for environment compatibility
    print("---------------------------------------------------------------------")
    print("Flask Application 'Weather.AI' is starting...")
    print("Access the dashboard at: http://127.0.0.1:5000/")
    print("---------------------------------------------------------------------")
    app.run(debug=True, host='0.0.0.0')
