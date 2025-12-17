# Human Comfort Index (HCI) Weather Application

A comprehensive Flask-based weather application that calculates the **Human Comfort Index (HCI)** based on real-time weather data and personalized health profiles. The application provides customized comfort assessments and recommendations tailored to individual health conditions.

## Features

### 🌍 Location-Based Weather Data
- **City Search Autocomplete**: Smart search with over 40,000+ cities worldwide from `worldcities.csv`
- **Real-time Weather**: Fetches current weather data using WeatherAPI
- **3-Day Forecast**: Temperature, humidity, wind, UV, and HCI predictions

### 📊 Personalized Health Profiles
The application supports **10+ health profiles** loaded from `Profiles.csv`:
- **General**: Balanced for average healthy adults
- **Respiratory**: Optimized for asthma, COPD, and breathing conditions
- **Cardiovascular**: For heart patients and blood pressure concerns
- **Skin Sensitive**: For UV and heat-sensitive individuals
- **Elderly/Seniors**: Age-related sensitivity adjustments
- **Children**: Pediatric comfort parameters
- **Athletes/Outdoor Workers**: Heat and exertion considerations
- **Pregnant Women**: Pregnancy-specific comfort factors
- **Arthritis/Joint Issues**: Humidity and pressure sensitivity
- **Migraine Sufferers**: Weather trigger detection

Each profile has custom weights for:
- Air Quality Index (AQI)
- Temperature
- Humidity
- UV Index
- Wind Speed
- Heat Index, Dew Point, Pressure, Precipitation, Cloud Cover

### 📈 Interactive Visualizations
- **HCI Trend Graph**: 3-day comfort index forecast
- **Temperature Chart**: Multi-day temperature visualization
- **Humidity Tracking**: Relative humidity levels
- **Wind Speed Analysis**: Wind conditions over time
- **KPI Cards**: Quick view of current conditions

### 🎨 Modern UI/UX
- **Responsive Design**: Works on desktop, tablet, and mobile
- **Dark Theme**: Easy on the eyes with glassmorphism effects
- **Dynamic Icons**: Weather-appropriate emoji and icons
- **Color-Coded Alerts**: Visual comfort level indicators

### 💡 Smart Recommendations
Based on comfort score, the app provides:
- **Comfort Band Classification**: Excellent, Comfortable, Moderate, Uncomfortable, Poor, Very Poor, Severe
- **Health Recommendations**: Activity suggestions, protective measures
- **Environmental Interpretation**: Context-aware insights (e.g., "AQI < 50", "Temp 20-28°C")

## Project Structure

```
hcitest/
├── main.py                  # Flask application and API endpoints
├── Profiles.csv             # Health profile configurations
├── worldcities.csv          # Global city database (40,000+ cities)
├── requirements.txt         # Python dependencies
├── templates/
│   ├── initial.html         # Landing page
│   ├── index.html           # Main dashboard
│   ├── profiles.html        # Profile viewer
│   └── visualization.html   # Standalone charts page
└── static/
    ├── script.js            # Dashboard logic
    ├── visualization.js     # Chart generation
    ├── style.css            # Styling
    └── images/              # Logo and background images
```

## Requirements

- **Python 3.8+**
- **WeatherAPI Key**: Sign up at [weatherapi.com](https://www.weatherapi.com/) for a free API key
- Dependencies listed in `requirements.txt`:
  - Flask
  - requests
  - pandas
  - numpy
  - plotly
  - matplotlib

## Installation & Setup

### 1. Clone or Download the Repository

### 2. Set Up Python Environment (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Configure WeatherAPI Key

Edit `main.py` and add your API key:
```python
WEATHERAPI_API_KEY = "your_api_key_here"
```

Or set as environment variable:
```powershell
$env:WEATHERAPI_API_KEY = "your_api_key_here"
```

### 4. Run the Application

```powershell
python main.py
```

The server will start at `http://127.0.0.1:5000/`

## Usage

1. **Landing Page** (`/`)
   - Click "Get Started" to access the dashboard

2. **Dashboard** (`/dashboard`)
   - Search for your city using autocomplete
   - Select a health profile from the dropdown
   - Click "Get Weather" to fetch data
   - View current HCI, weather conditions, and recommendations
   - Explore 3-day forecast with trend graphs

3. **Profiles Page** (`/profiles`)
   - View all available health profiles
   - See weight configurations for each profile
   - Understand how different factors affect comfort

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/search_cities?q={query}` | GET | Search cities by name |
| `/api/get_weather` | POST | Get weather and HCI data |
| `/api/get_profiles` | GET | List all health profiles |
| `/api/generate_charts` | POST | Generate visualization charts |

## How HCI is Calculated

The Human Comfort Index uses a weighted scoring system:

1. **Component Scores** (0-100, higher = worse):
   - AQI: Air Quality Index (0-500 mapped to 0-100)
   - Temperature: Normalized between -10°C and 40°C
   - Humidity: 0-100% direct mapping
   - UV Index: 0-11+ mapped to 0-100
   - Wind Speed: 0-120 kph mapped to 0-100

2. **Profile-Weighted Calculation**:
   ```
   HCI = 100 - (Σ(component_score × weight) / Σ(weights))
   ```

3. **Result**: 0-100 score where **higher is better**
   - 90-100: Excellent / Very Comfortable
   - 75-89: Comfortable
   - 60-74: Moderate Comfort
   - 45-59: Uncomfortable
   - 30-44: Poor Comfort / Caution
   - 15-29: Very Poor / Health Risk
   - 0-14: Severe Discomfort / Dangerous

## Customization

### Add New Health Profiles
Edit `Profiles.csv` with new rows containing:
- Profile name
- Description
- Weight values for each factor (decimal 0.0 - 1.0)

### Add More Cities
The app uses `worldcities.csv`. To add custom cities:
- Append rows with: `city,city_ascii,country,lat,lng`

### Modify UI Theme
Edit `static/style.css` to change colors, fonts, and layout

## Troubleshooting

**Charts not displaying?**
- Ensure `matplotlib` and `plotly` are installed
- Check browser console for JavaScript errors

**City autocomplete not working?**
- Verify `worldcities.csv` exists and is readable
- Check `/api/search_cities` endpoint in browser

**Weather data errors?**
- Confirm WeatherAPI key is valid and has available quota
- Check internet connectivity

**Profile data missing?**
- Ensure `Profiles.csv` is in the root directory
- Verify CSV formatting (no extra commas or malformed rows)

## Future Enhancements
- User authentication and personalized profile storage
- Historical HCI data tracking
- Push notifications for comfort level changes
- Mobile app version
- Multi-language support

## Credits
- **WeatherAPI**: Real-time weather data provider
- **SimpleMaps**: World cities database
- **Flask**: Python web framework
- **Chart.js / Plotly**: Data visualization

## License
This project is open-source. Feel free to modify and extend.

---

**Version**: 2.0  
**Last Updated**: December 2025
