# LAEP LCT Dashboard Setup Guide

## Overview
Interactive Streamlit dashboard for visualizing Low Carbon Technology (LCT) deployment data across England by technology type, DNO area, and geographic location (LSOA).

**Data Sources:**
- MCS (primary)
- LCT Register (secondary, MPAN deduplicated)
- Period: April 2025 - March 2026
- Geography: England only

**Technologies covered:**
- Heat Pumps
- Solar PV
- Battery Storage
- EV Chargers

## Quick Start (Local)

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run Dashboard
```bash
streamlit run app.py
```

Dashboard will open at `http://localhost:8501`

## Deploy to Streamlit Cloud (Free Hosting)

### 1. Push to GitHub
```bash
git add app.py requirements.txt scripts/04_all_tech_processing.py
git commit -m "Add Streamlit dashboard"
git push
```

### 2. Connect Streamlit Cloud
1. Go to https://share.streamlit.io
2. Sign in with GitHub
3. Click "New app"
4. Select your repository
5. Set main file path: `app.py`
6. Deploy

Your dashboard will be live at a URL like:
`https://your-username-project-name.streamlit.app`

### 3. Add Password Protection (Optional)
Create a `.streamlit/secrets.toml` file:
```toml
password = "your-secure-password-here"
```

Add to app.py (after imports):
```python
import streamlit as st

def check_password():
    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False

    if st.session_state.password_correct:
        return True

    password = st.text_input("Enter password:", type="password")
    if password == st.secrets.get("password"):
        st.session_state.password_correct = True
        st.rerun()
    elif password:
        st.error("Incorrect password")
    return False

if not check_password():
    st.stop()
```

## Automated Monthly Updates

### Setup GitHub Actions Automation

Create `.github/workflows/monthly_refresh.yml`:

```yaml
name: Monthly Data Refresh

on:
  schedule:
    - cron: '0 9 1 * *'  # 9 AM on 1st of each month
  workflow_dispatch:  # Allow manual trigger

jobs:
  refresh-data:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: 3.9
      
      - name: Install dependencies
        run: pip install -r requirements.txt
      
      - name: Run data processing
        run: python scripts/04_all_tech_processing.py
      
      - name: Commit and push changes
        run: |
          git config --local user.email "action@github.com"
          git config --local user.name "GitHub Action"
          git add project/output_processed/
          git commit -m "Monthly data refresh: $(date +%Y-%m-%d)"
          git push
```

This will:
- Automatically run on the 1st of each month at 9 AM
- Update the CSV data files
- Push changes to GitHub
- Streamlit Cloud auto-reloads with fresh data

## File Structure
```
project/
├── app.py                                    # Main Streamlit app
├── requirements.txt                          # Python dependencies
├── DASHBOARD_SETUP.md                        # This file
├── scripts/
│   ├── 04_all_tech_processing.py            # Data processing script
│   └── (other processing scripts)
├── lct/
│   ├── MCS/                                  # Monthly MCS CSV files
│   └── LCT Register.csv                      # Consolidated LCT register
├── lookups/
│   ├── postcode_lsoa21_lookup_spatial.csv   # Spatial postcode→LSOA21 map
│   ├── LSOA to DNO.csv                      # LSOA→DNO mapping
│   └── (other lookups)
└── project/
    └── output_processed/
        ├── dashboard_data_dno.csv            # Aggregated by DNO (for dashboard)
        └── dashboard_data_lsoa.csv           # Aggregated by LSOA (for drilling)
```

## Dashboard Features

### Filters
- **Period Range**: Select month range to analyze
- **Technologies**: Filter by technology type (Heat Pump, PV, Battery, EV Charger)
- **DNO Areas**: Filter by distribution network operator (EPN, LPN, SPN)

### Views
1. **By Month** - Time series trends by technology
2. **By DNO** - Bar charts comparing DNO areas
3. **By LSOA** - Geographic drill-down with detailed metrics
4. **Data Table** - Raw data export with CSV download

### Metrics
- Total installations (count)
- Total capacity (MW)
- Average capacity per installation (kW)
- Technology distribution

## Data Refresh Workflow

### Manual Update
```bash
# Update data files
python scripts/04_all_tech_processing.py

# Commit changes
git add project/output_processed/
git commit -m "Update LCT data"
git push

# Streamlit Cloud auto-reloads (within 1-2 minutes)
```

### Automatic Monthly Update
GitHub Actions runs on schedule and auto-commits. No manual action needed.

## Troubleshooting

### Dashboard won't load
- Check requirements.txt dependencies are installed
- Run `streamlit run app.py` to see error messages
- Check CSV files exist in `project/output_processed/`

### Data not updating
- If hosted: Check "Rerun" button in Streamlit Cloud
- If local: Re-run processing script manually
- If automated: Check GitHub Actions workflow in repo

### Slow performance
- Large datasets can be slow. Consider filtering by shorter date range or specific DNO
- Caching is enabled (`@st.cache_data`) to speed up reloads

## Security Notes

- Password protection is optional (not built-in by default)
- For sensitive data, use Streamlit Cloud's private sharing
- All data stays within your GitHub/Streamlit infrastructure
- No external hosting required
