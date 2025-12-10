let hciChartInstance = null;
let tempChartInstance = null;
let humidityChartInstance = null;
let windChartInstance = null;
let monthlyTempChartInstance = null;

function destroyCharts() {
    if (hciChartInstance) hciChartInstance.destroy();
    if (tempChartInstance) tempChartInstance.destroy();
    if (humidityChartInstance) humidityChartInstance.destroy();
    if (windChartInstance) windChartInstance.destroy();
    if (monthlyTempChartInstance) monthlyTempChartInstance.destroy();
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

    async function fetchWeather() {
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
        try {
            const r = await fetch('/api/get_weather', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query: q, units: units, forecast_days: days, include_aqi: true, include_uv: true, profile: profile })
            });
            const data = await r.json();
            console.log('API Response received:', data);
            console.log('Humidity in response:', data.humidity);
            if (!r.ok) {
                status.textContent = 'Error: ' + (data.error || 'Failed to fetch weather data');
                fetchButton.disabled = false;
                return;
            }
            outLoc.textContent = data.location_name;
            outCond.textContent = data.condition;
            outTemp.textContent = data.temperature_c + (units === 'metric' ? ' °C' : ' °F');
            // Current average temperature (if present)
            if (data.avgtemp_c) { outAvgTemp.textContent = data.avgtemp_c + (units === 'metric' ? ' °C' : ' °F'); } else { outAvgTemp.textContent = '--'; }
            // HCI and comfort indicators
            const hciVal = document.getElementById('hci-value');
            const comfortBadge = document.getElementById('comfort-badge');
            const comfortDesc = document.getElementById('comfort-desc');
            if (data.hci) { hciVal.textContent = data.hci; } else { hciVal.textContent = '--'; }
            // Weighted HCI from profile (if present)
            const wEl = document.getElementById('weighted-hci');
            if (data.weighted_hci) { wEl.textContent = data.weighted_hci + ' (profile: ' + (data.profile_used || '-') + ')'; } else { wEl.textContent = '--'; }
            // Component scores - formatted
            if (data.component_scores) {
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
            if (data.band) { bandEl.textContent = data.band; } else { bandEl.textContent = '--'; }
            if (data.environmental_interpretation) { envEl.textContent = data.environmental_interpretation; } else { envEl.textContent = '--'; }
            if (data.recommendations) { recEl.textContent = data.recommendations.join('; '); } else { recEl.textContent = '--'; }
            if (data.comfort_level) {
                comfortBadge.textContent = `${data.comfort_emoji} ${data.comfort_level}`;
                comfortDesc.textContent = data.comfort_description || '';
                // apply simple class if provided
                if (data.comfort_class) { comfortBadge.className = 'mt-2 px-3 py-1 rounded-full text-sm ' + data.comfort_class; }
            } else {
                comfortBadge.textContent = '--';
                comfortDesc.textContent = '';
            }
            // Condition icon (WeatherAPI icons often start with //)
            const iconEl = document.getElementById('cond_icon');
            if (data.condition_icon) {
                iconEl.src = data.condition_icon.startsWith('//') ? 'https:' + data.condition_icon : data.condition_icon;
                iconEl.style.display = 'inline-block';
            } else { iconEl.style.display = 'none'; }
            status.textContent = 'Updated: ' + (data.local_time || '');

            // If forecast provided, render it
            const forecastSectionEl = document.getElementById('forecast-section');
            if (data.forecast && data.forecast.length) {
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
            if (data.aqi) {
                const pm25 = data.aqi.pm2_5 || data.aqi['pm2_5'] || null;
                aqiEl.textContent = 'Air Quality (PM2.5): ' + (pm25 ? pm25.toFixed(2) : 'N/A');
            } else { aqiEl.textContent = ''; }
        } catch (e) {
            console.error(e);
            status.textContent = '';
        } finally { fetchButton.disabled = false; }
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
    locInput.addEventListener('keypress', (e) => { if (e.key === 'Enter') fetchWeather(); });

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
    window.fetchWeather = async function () {
        await originalFetchWeather();
        // Add icon animation after weather is fetched
        const weatherIcon = document.querySelector('[data-weather-icon]');
        if (weatherIcon) {
            weatherIcon.classList.add('animate-bounce');
        }
    };

    fetchButton.addEventListener('click', fetchWeather);
    locInput.addEventListener('keypress', (e) => { if (e.key === 'Enter') fetchWeather(); });
});
