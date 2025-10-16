from flask import Flask, render_template, request, redirect, url_for, jsonify
import requests

app = Flask(__name__)

# WeatherAPI key (keep yours safe; you can set env var WEATHERAPI_API_KEY)
WEATHERAPI_API_KEY = "509a4bd590d64c0fb6a33306250810"


def build_person_profile(name: str, age: int, diseases: list[str]) -> dict:
    """Builds a person profile and suggestions.

    Returns a dict with: name, age, diseases, hci (string), suggestions (list).
    """
    # Normalize disease names
    diseases = [d.strip().lower() for d in diseases if d.strip()]

    # If age > 60, add fragile bones
    if age > 60 and 'fragile bones' not in diseases:
        diseases.append('fragile bones')

    # Build a simple HCI string based on age and diseases
    hci_parts = []
    if age >= 65:
        hci_parts.append('Provide large, high-contrast text and simplified layouts.')
    elif age >= 50:
        hci_parts.append('Prefer medium-sized text and clear spacing.')
    else:
        hci_parts.append('Use standard readable fonts and spacing.')

    if 'asthama' in diseases or 'asthma' in diseases:
        hci_parts.append('Avoid interfaces that require prolonged breath-holding or rapid input.')

    if 'skin disease' in diseases:
        hci_parts.append('Use soft color palettes and avoid harsh flashing elements.')

    if 'fragile bones' in diseases:
        hci_parts.append('Minimize the need for physical interaction; provide larger touch targets.')

    hci = ' '.join(hci_parts)

    # Suggestions
    suggestions = []
    suggestions.append(f'Hello {name}, age {age}.')

    if 'skin disease' in diseases:
        suggestions.append('See a dermatologist and use recommended topical treatments; protect skin from irritants.')

    if 'asthama' in diseases or 'asthma' in diseases:
        suggestions.append('Keep inhaler accessible and avoid triggers; consult your pulmonologist for action plans.')

    if 'fragile bones' in diseases:
        suggestions.append('Schedule bone density testing, ensure adequate calcium and vitamin D, and fall-proof the living space.')

    # Generic healthy suggestions
    suggestions.append('Maintain a balanced diet, regular checkups, and stay physically active within comfort limits.')

    return {
        'name': name,
        'age': age,
        'diseases': diseases,
        'hci': hci,
        'suggestions': suggestions,
    }


@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')


@app.route('/submit', methods=['POST'])
def submit():
    name = request.form.get('name', '').strip()
    age_raw = request.form.get('age', '').strip()
    diseases_raw = request.form.getlist('diseases')

    try:
        age = int(age_raw) if age_raw else 0
    except ValueError:
        age = 0

    # diseases_raw may contain multiple items separated by commas if user typed them
    diseases = []
    for d in diseases_raw:
        # split by comma
        parts = [p.strip() for p in d.split(',') if p.strip()]
        diseases.extend(parts)

    profile = build_person_profile(name or 'User', age, diseases)

    return render_template('result.html', profile=profile)


if __name__ == '__main__':
    app.run(debug=True)


@app.route('/api/get_weather', methods=['POST'])
def get_weather():
    data = request.get_json() or {}
    query = data.get('query')
    units = data.get('units', 'metric')
    forecast_days = int(data.get('forecast_days', 0))
    include_aqi = bool(data.get('include_aqi', False))

    if not query:
        return jsonify({"error": "Location query is missing."}), 400

    if forecast_days and forecast_days > 0:
        api_url = f"http://api.weatherapi.com/v1/forecast.json?key={WEATHERAPI_API_KEY}&q={query}&days={min(3,forecast_days)}&aqi={'yes' if include_aqi else 'no'}"
    else:
        api_url = f"http://api.weatherapi.com/v1/current.json?key={WEATHERAPI_API_KEY}&q={query}&aqi={'yes' if include_aqi else 'no'}"
    try:
        resp = requests.get(api_url, timeout=10)
        weather_data = resp.json()
        if 'error' in weather_data:
            return jsonify({"error": weather_data['error'].get('message', 'Unknown WeatherAPI error.')}), 404
    except requests.RequestException:
        return jsonify({"error": "Failed to fetch weather data."}), 500

    try:
        location = weather_data.get('location', {})
        location_name = f"{location.get('name','')}, {location.get('country','')}"

        current = weather_data.get('current', {})
        temp_c = float(current.get('temp_c', 0))
        rh = float(current.get('humidity', 0))
        wind_speed_mps = float(current.get('wind_kph', 0)) / 3.6
        condition_text = current.get('condition', {}).get('text', '')
        condition_icon = current.get('condition', {}).get('icon', '')

        try:
            hci = (temp_c + rh) / 4.0
        except Exception:
            hci = None

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

        if include_aqi and 'current' in weather_data and weather_data['current'].get('air_quality'):
            result['aqi'] = weather_data['current']['air_quality']

        return jsonify(result)
    except Exception:
        return jsonify({"error": "Invalid data from WeatherAPI."}), 500

