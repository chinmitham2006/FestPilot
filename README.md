# FestPilot — AI-Powered College Fest Planner

FestPilot is a Streamlit-based planning prototype that brings together venue discovery, AI-assisted event planning, weather context, draft scheduling, and budget/resource estimation in one workspace.

## Project team

- **Chinmitha M.** 
- **Kavana Bhageerath Nayak** 
- **Komal P. Shah**

## Features

- **Venue Discovery:** Searches Geoapify Places for venue/activity listings around Bengaluru, within a configured 35 km radius.
- **Agentic AI Planner:** Uses a Groq-hosted language model with configured tools to assist with planning tasks.
- **Weather Intelligence:** Provides weather context through the configured weather service.
- **Smart Schedule:** Supports preparation and review of a draft event timeline.
- **Budget & Resources:** Provides editable planning assumptions and a venue-quotation field.
- **Venue-to-budget hand-off:** Carries the selected venue into the budget workspace.

> **Important data limitation:** Geoapify place listings do not guarantee venue suitability, capacity, facilities, booking availability, or rental price. Contact venue operators to verify these details. Budget values other than an actual entered quotation are planning assumptions. AI output and draft schedules must be reviewed by the organising team.

## Technology stack

- Python
- Streamlit
- Geoapify Places API
- Groq API
- Pandas / OpenPyXL
- Plotly

## Project structure

```text
FestPilot/
├── app.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── .streamlit/
│   └── config.toml
├── .vscode/
│   └── extensions.json
└── data/
    └── FestPilot_Bengaluru_Dataset.xlsx
```

## Run locally in VS Code

### 1. Install Python

Install Python 3.10 or later and VS Code. In VS Code, open this project folder.

### 2. Create and activate a virtual environment

**Windows PowerShell:**

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, use Command Prompt:

```bat
.venv\Scripts\activate.bat
```

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure API keys

Copy `.env.example` to `.env` and replace the placeholder values with your own Geoapify and Groq API keys.

The application automatically loads the `GEOAPIFY_API_KEY` and `GROQ_API_KEY` values from `.env` using `python-dotenv`.

**PowerShell example:**

```powershell
$env:GEOAPIFY_API_KEY="your_actual_geoapify_key"
$env:GROQ_API_KEY="your_actual_groq_key"
```

Do not upload `.env`, API keys, or Streamlit secrets to GitHub.

### 5. Start FestPilot

```bash
streamlit run app.py
```

Streamlit normally opens the app at:

`http://localhost:8501`

## Data workbook

The workbook in `data/` contains editable starter sheets for Events, Resources, Budget and Guidelines. The Venues sheet is a template; venue search results are retrieved live from Geoapify.

## GitHub security checklist

- Keep `.env` out of Git commits.
- Do not paste API keys into `app.py`, notebooks, screenshots, or README files.
- If a key was ever committed publicly, revoke/rotate it in the provider dashboard.
- Use `.env.example` with placeholders only.

## Current prototype limitations

- Designed for a Bengaluru-centred 35 km venue search area.
- External API availability, coverage, quotas and credentials affect live features.
- Venue capacity, availability, facilities and prices require direct verification.
- Scheduling is a draft-planning aid, not a full constraint-optimisation engine.
- Local app session data is not a persistent multi-user database.

## Academic project

Department of Artificial Intelligence and Machine Learning (AIML)  
Don Bosco Institute of Technology — DBIT-SIC  
Academic project, 2026–2027
