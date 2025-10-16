# Personalized HCI & Health Suggestions

Simple Flask app that provides a homepage to enter Name, Age and Diseases, then returns personalized HCI recommendations and health suggestions. If age > 60, 'fragile bones' is automatically added.

Requirements

- Python 3.8+
- See `requirements.txt` (Flask is required)

Run locally (PowerShell):

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt; python app.py
```

Then open http://127.0.0.1:5000/ in your browser.
