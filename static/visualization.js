async function fetchViz(location, units) {
    const resp = await fetch('/api/get_weather', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: location, units: units, forecast_days: 3, include_aqi: true }) });
    const data = await resp.json();
    return data;
}

async function generateCharts(location) {
    const resp = await fetch('/api/generate_charts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ location: location }) });
    const charts = await resp.json();
    return charts;
}

function renderKPIs(data) {
    document.getElementById('viz-hci').textContent = data.hci || '--';
    document.getElementById('viz-temp').textContent = data.temperature_c ? data.temperature_c + ' °C' : '--';
    const vizHumVal = (data.humidity !== undefined && data.humidity !== null && data.humidity !== '') ? data.humidity : null;
    document.getElementById('viz-hum').textContent = vizHumVal !== null ? String(vizHumVal) + ' %' : '--';
    if (data.aqi) {
        const pm25 = data.aqi.pm2_5 || data.aqi['pm2_5'] || null;
        document.getElementById('viz-aqi').textContent = pm25 ? pm25.toFixed(1) : 'N/A';
    } else { document.getElementById('viz-aqi').textContent = '--'; }
}

async function refreshViz() {
    const loc = document.getElementById('viz-location').value || 'London';
    const units = document.getElementById('viz-units').value;

    try {
        const data = await fetchViz(loc, units);
        renderKPIs(data);

        const charts = await generateCharts(loc);
        if (charts.error) {
            console.error('Chart generation error:', charts.error);
        } else {
            // Render charts as images
            document.getElementById('chart-hci').src = charts.hci;
            document.getElementById('chart-temp').src = charts.temperature;
            document.getElementById('chart-humidity').src = charts.humidity;
            document.getElementById('chart-wind').src = charts.wind;
            document.getElementById('chart-monthly').src = charts.monthly;
        }
    } catch (err) {
        console.error('Error refreshing visualization:', err);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('viz-fetch').addEventListener('click', refreshViz);
    document.getElementById('viz-location').value = 'London';
    refreshViz();
});
