#!/usr/bin/env python3
"""
LCT Data Manager — Flask web application

Phase 1: Foundation + DfT vintage detection

Usage:
    python app_data_manager.py

Access at: http://localhost:5000
"""

import os
import sys
import json
import calendar
from datetime import datetime
from flask import Flask, render_template, jsonify, request

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from data_manager.db import Database
from data_manager.dft_inspector import DftInspector
from data_manager.smmt import SMmtManager
from data_manager.nowcast import NowcastEngine
from data_manager.dft_update import DfTUpdateManager
from data_manager.promotion import AtomicPromotion, ManualRollback, CandidateValidator
from data_manager.ingestion import web as ingestion_web

app = Flask(__name__)
ingestion_web.init_app(app)   # /dft-ingest, /dft-sources, /api/ingest/* (staging only; never writes production)
from data_manager.c1_refresh import web as c1_refresh_web
c1_refresh_web.init_app(app)  # /c1-refresh (isolated C1 candidates; never publishes)
db = Database()
inspector = DftInspector(PROJECT_ROOT)
smmt_manager = SMmtManager()

# Initialize nowcast engine with processed DfT stock
lsoa_ev_path = os.path.join(PROJECT_ROOT, "project", "output_processed", "lsoa_ev_bodytype.csv")
if os.path.exists(lsoa_ev_path):
    nowcast_engine = NowcastEngine(lsoa_ev_path)
else:
    nowcast_engine = None


@app.route("/")
def index():
    """Main dashboard showing current state."""
    state = inspector.get_current_ev_state()
    return render_template("dashboard.html", state=state)


@app.route("/api/dft-status")
def dft_status():
    """API endpoint for DfT status."""
    state = inspector.get_current_ev_state()
    return jsonify({
        "latest_dft_quarter": state["latest_dft_quarter"],
        "dft_tables": state["dft_tables_present"],
        "ev_outputs": state["ev_outputs_exist"],
        "latest_ev_period": state["latest_ev_period"],
    })


@app.route("/api/processing-history")
def processing_history():
    """API endpoint for processing history."""
    records = db.get_processing_history(limit=20)
    return jsonify(records)


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "message": "Data Manager is running"}), 200


@app.route("/smmt")
def smmt_input():
    """SMMT input form and history."""
    latest_dft = smmt_manager.get_latest_authoritative_dft_period()
    latest_smmt = smmt_manager.get_latest_smmt_entry()
    history_entries = smmt_manager.get_smmt_history()
    suggested = smmt_manager.suggest_next_month()

    # Format for display
    latest_dft_quarter = None
    dft_quarter_name = None
    if latest_dft:
        year, month = latest_dft
        month_days = {3: "31", 6: "30", 9: "30", 12: "31"}
        latest_dft_quarter = f"{year}-{month:02d}-{month_days.get(month, '30')}"
        q = (month - 1) // 3 + 1
        dft_quarter_name = f"Q{q} {year}"

    latest_smmt_month = None
    if latest_smmt:
        year, month = latest_smmt
        latest_smmt_month = f"{SMmtManager.month_name(month)} {year}"

    suggested_month = None
    suggested_month_display = None
    if suggested:
        suggested_month = suggested
        year, month = suggested
        suggested_month_display = f"{SMmtManager.month_name(month)} {year}"

    # Format history for display
    formatted_history = []
    version_counts = {}
    for entry in history_entries:
        key = (entry['year'], entry['month'])
        version_counts[key] = version_counts.get(key, 0) + 1

    for entry in history_entries:
        key = (entry['year'], entry['month'])
        formatted_history.append({
            'year': entry['year'],
            'month': entry['month'],
            'month_display': f"{SMmtManager.month_short(entry['month'])}-{str(entry['year'])[2:]}",
            'bev_cars': entry['bev_cars'],
            'bev_cars_formatted': f"{entry['bev_cars']:,}",
            'bev_vans': entry['bev_vans'],
            'bev_vans_formatted': f"{entry['bev_vans']:,}",
            'phev_cars': entry['phev_cars'],
            'phev_cars_formatted': f"{entry['phev_cars']:,}",
            'phev_vans': entry['phev_vans'],
            'phev_vans_formatted': f"{entry['phev_vans']:,}",
            'status': entry['status'],
            'version_count': version_counts.get(key, 1),
        })

    month_names = [SMmtManager.month_name(i) for i in range(1, 13)]

    return render_template(
        "smmt_input.html",
        latest_dft_quarter=latest_dft_quarter,
        dft_quarter_name=dft_quarter_name,
        latest_smmt_month=latest_smmt_month,
        history=formatted_history,
        suggested_month=suggested_month,
        suggested_month_display=suggested_month_display,
        month_names=month_names,
    )


@app.route("/api/smmt-entry", methods=["POST"])
def api_smmt_entry():
    """API endpoint to add/correct SMMT entry."""
    data = request.get_json()

    year = data.get("year")
    month = data.get("month")
    bev_cars = data.get("bev_cars")
    bev_vans = data.get("bev_vans")
    phev_cars = data.get("phev_cars")
    phev_vans = data.get("phev_vans")
    notes = data.get("notes")

    # Check for existing entry
    existing = smmt_manager.check_existing_entry(year, month)

    if existing:
        # Correct mode
        success, message = smmt_manager.correct_smmt_entry(
            year, month, bev_cars, bev_vans, phev_cars, phev_vans, notes
        )
    else:
        # Add mode
        success, message = smmt_manager.add_smmt_entry(
            year, month, bev_cars, bev_vans, phev_cars, phev_vans, notes
        )

    return jsonify({"success": success, "message": message})


@app.route("/api/smmt-entry", methods=["GET"])
def api_get_smmt_entry():
    """API endpoint to get existing SMMT entry for editing."""
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)

    if not year or not month:
        return jsonify({"entry": None})

    entry = smmt_manager.db.get_current_smmt_entry(year, month)
    if entry:
        entry = dict(entry)
        entry['entered_at'] = str(entry.get('entered_at', ''))

    return jsonify({"entry": entry})


@app.route("/api/smmt-entry-history", methods=["GET"])
def api_smmt_entry_history():
    """API endpoint to get full history of SMMT entry (all versions)."""
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)

    if not year or not month:
        return jsonify({"versions": []})

    versions = smmt_manager.get_entry_versions(year, month)
    # Convert to dicts and ensure timestamps are strings
    result = []
    for v in versions:
        v_dict = dict(v)
        v_dict['entered_at'] = str(v_dict.get('entered_at', ''))
        v_dict['corrected_at'] = str(v_dict.get('corrected_at', '')) if v_dict.get('corrected_at') else None
        result.append(v_dict)

    return jsonify({"versions": result})


@app.route("/dft-update")
def dft_update():
    """DfT update workflow and candidate processing."""
    latest_vintage = inspector.get_current_ev_state()['latest_ev_period']

    # Parse vintage to quarter
    if latest_vintage:
        year, month_day = latest_vintage.split('-')[0], latest_vintage.split('-')[1]
        month = int(month_day)
        quarter = (month - 1) // 3 + 1
        quarter_name = f"Q{quarter} {year}"
        reference_date = latest_vintage.replace('-', ' ').replace('03-31', '31 Mar').replace('06-30', '30 Jun').replace('09-30', '30 Sep').replace('12-31', '31 Dec')
    else:
        quarter_name = "Q1 2026"
        reference_date = "31 Mar 2026"

    return render_template(
        "dft_update.html",
        current_quarter_name=quarter_name,
        current_reference_date=reference_date
    )


@app.route("/nowcast")
def nowcast_preview():
    """SMMT Nowcast preview and generation interface."""
    if not nowcast_engine:
        return render_template(
            "nowcast_preview.html",
            error="Nowcast engine not initialized (lsoa_ev_bodytype.csv not found)"
        )

    # Get weight info
    weights_summary = {}
    for cat in ["BEV Cars", "BEV Vans", "PHEV Cars", "PHEV Vans"]:
        if cat in nowcast_engine.weights:
            w = nowcast_engine.weights[cat]
            weights_summary[cat] = {
                'gb_total': w['gb_total'],
                'ukpn_total': w['ukpn_total'],
                'ukpn_weight': f"{w['gb_weight']:.1%}",
                'lsoa_count': len(w['lsoa_weights'])
            }

    # Get SMMT history
    smmt_history = db.get_current_smmt_entries_for_history()
    smmt_by_month = {}
    for entry in smmt_history:
        key = (entry['year'], entry['month'])
        smmt_by_month[key] = entry

    return render_template(
        "nowcast_preview.html",
        baseline_period="2026-03-31",
        weights_summary=weights_summary,
        smmt_history=smmt_history,
        qa_report=nowcast_engine.get_qa_report()
    )


@app.route("/api/generate-nowcast", methods=["POST"])
def api_generate_nowcast():
    """Generate SMMT nowcast allocation."""
    if not nowcast_engine:
        return jsonify({
            "success": False,
            "message": "Nowcast engine not initialized"
        }), 500

    data = request.get_json()
    months = data.get("months", [])  # List of (year, month) tuples

    if not months:
        return jsonify({
            "success": False,
            "message": "No months specified"
        })

    # Collect SMMT data for requested months
    monthly_smmt = {}
    for year, month in months:
        entry = db.get_current_smmt_entry(year, month)
        if not entry:
            return jsonify({
                "success": False,
                "message": f"SMMT data not found for {year}-{month:02d}"
            })

        # Generate month-end date using calendar.monthrange for robustness
        last_day = calendar.monthrange(year, month)[1]
        month_str = f"{year}-{month:02d}-{last_day:02d}"
        monthly_smmt[month_str] = {
            "BEV Cars": entry['bev_cars'],
            "BEV Vans": entry['bev_vans'],
            "PHEV Cars": entry['phev_cars'],
            "PHEV Vans": entry['phev_vans']
        }

    # Validate QA
    is_valid, errors = nowcast_engine.validate_qa()
    if not is_valid:
        return jsonify({
            "success": False,
            "message": f"QA validation failed: {errors}"
        })

    # Run allocation
    try:
        monthly_allocs = {}
        for month_str, smmt_input in monthly_smmt.items():
            allocs = nowcast_engine.allocate_monthly_smmt(smmt_input, month_str)
            monthly_allocs[month_str] = list(allocs.values())

        # Build cumulative
        cumulative = nowcast_engine.build_cumulative_stock(monthly_allocs)

        # Create a candidate run with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"nowcast_{timestamp}"

        # Create staging directory for this run
        staging_root = os.path.join(PROJECT_ROOT, "project", "dft_staging")
        run_dir = os.path.join(staging_root, f"run_{run_id}")
        outputs_dir = os.path.join(run_dir, "outputs")
        os.makedirs(outputs_dir, exist_ok=True)

        # Store metadata
        metadata = {
            "run_id": run_id,
            "created_at": datetime.now().isoformat(),
            "type": "nowcast",
            "months_included": sorted(list(monthly_smmt.keys())),
            "qa_status": "PASS",
            "old_vintage": "2026-03-31",
            "new_vintage": "2026-03-31"
        }

        metadata_path = os.path.join(run_dir, "metadata.json")
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2)

        return jsonify({
            "success": True,
            "message": f"Nowcast candidate created: {run_id}",
            "run_id": run_id,
            "qa_report": nowcast_engine.get_qa_report()
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Allocation error: {str(e)}"
        }), 500


@app.route("/promotion-review/<run_id>")
def promotion_review(run_id):
    """Review and confirm candidate promotion."""
    promoter = AtomicPromotion(PROJECT_ROOT)
    validator = CandidateValidator(PROJECT_ROOT)

    can_promote, errors = validator.can_promote(run_id)
    metadata = validator.get_candidate_metadata(run_id)

    return render_template(
        "promotion_review.html",
        run_id=run_id,
        can_promote=can_promote,
        errors=errors,
        metadata=metadata or {},
        current_vintage=inspector.get_current_ev_state()['latest_ev_period']
    )


@app.route("/api/promote", methods=["POST"])
def api_promote():
    """Execute promotion of candidate to production."""
    data = request.get_json()
    run_id = data.get("run_id")

    if not run_id:
        return jsonify({"success": False, "message": "Run ID required"}), 400

    promoter = AtomicPromotion(PROJECT_ROOT)
    success, message = promoter.promote(run_id)

    return jsonify({
        "success": success,
        "message": message,
        "run_id": run_id
    })


@app.route("/promotion-history")
def promotion_history():
    """View promotion and rollback history."""
    history_path = os.path.join(PROJECT_ROOT, "project", "promotion_history.jsonl")

    history = []
    if os.path.exists(history_path):
        with open(history_path, 'r') as f:
            for line in f:
                try:
                    history.append(json.loads(line))
                except:
                    pass

    history.reverse()  # Newest first

    return render_template("promotion_history.html", history=history)


@app.route("/rollback-options")
def rollback_options():
    """Show available backups for manual rollback."""
    rollback = ManualRollback(PROJECT_ROOT)
    backups = rollback.list_available_backups()

    return render_template("rollback_options.html", backups=backups)


@app.route("/api/rollback", methods=["POST"])
def api_rollback():
    """Execute manual rollback to previous state."""
    data = request.get_json()
    backup_id = data.get("backup_id")

    if not backup_id:
        return jsonify({"success": False, "message": "Backup ID required"}), 400

    rollback = ManualRollback(PROJECT_ROOT)
    success, message = rollback.rollback(backup_id)

    return jsonify({
        "success": success,
        "message": message,
        "backup_id": backup_id
    })


if __name__ == "__main__":
    print("=" * 70)
    print("LCT DATA MANAGER — Phase 1-5")
    print("=" * 70)
    print("\nStarting Data Manager...")
    print(f"Database: {db.db_path}")
    print(f"Project root: {PROJECT_ROOT}")
    print("\n" + inspector.get_inspection_report())
    print("\nWeb application starting...")
    print("Access at: http://localhost:5000\n")

    app.run(host="127.0.0.1", port=5000, debug=True)
