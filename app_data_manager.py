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
from flask import Flask, redirect, render_template, jsonify, request

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from data_manager.db import Database
from data_manager.dft_inspector import DftInspector
from data_manager.smmt import SMmtManager
from data_manager.dft_update import DfTUpdateManager
from data_manager.promotion import AtomicPromotion, ManualRollback, CandidateValidator, candidate_status
from data_manager.candidate_registry import CandidateRegistry
from data_manager.ingestion import web as ingestion_web
from data_manager import smmt_web

app = Flask(__name__)
ingestion_web.init_app(app)   # /dft-ingest, /dft-sources, /api/ingest/* (staging only; never writes production)
from data_manager.c1_refresh import web as c1_refresh_web
c1_refresh_web.init_app(app)  # /c1-refresh (isolated C1 candidates; never publishes)
smmt_web.init_app(app, PROJECT_ROOT)  # /smmt-nowcast (isolated SMMT nowcast candidates and preview; never publishes)
db = Database()
inspector = DftInspector(PROJECT_ROOT)
smmt_manager = SMmtManager()
candidate_registry = CandidateRegistry(PROJECT_ROOT)


def smmt_candidate_card(row):
    """Summary of an SMMT candidate for the home and candidates pages, read from its sealed manifest."""
    m = row["m"]
    first, last = (m.get("nowcast_months") or [None])[0], m.get("latest_nowcast_month")
    base = sum((m.get("ukpn_baseline_stock") or {}).values()) if m.get("ukpn_baseline_stock") else None
    end = sum(m["ukpn_stock_by_month"][last].values()) if last and m.get("ukpn_stock_by_month") else None
    return {
        "run_id": m["candidate_id"], "type": "SMMT provisional nowcast", "status": row["state"],
        "status_detail": row["detail"], "created": m.get("created_utc"),
        "dft_vintage": m.get("baseline_period"), "nowcast_start": first, "nowcast_end": last,
        "baseline_stock": base, "latest_stock": end, "addition": (end - base) if base is not None and end is not None else None,
        "qa": f"{m.get('qa_passed')}/{m.get('qa_total')}" if m.get("qa_total") else "FAILED",
        "description": (f"UKPN EV stock from the C1 actual at {m.get('baseline_period')} plus SMMT registrations "
                        f"{first} to {last}" if last else "; ".join(m.get("errors", []))),
        "review_url": f"/smmt-nowcast/{m['candidate_id']}",
    }


@app.route("/")
def index():
    """Main dashboard showing current state and the latest SMMT nowcast candidate."""
    state = inspector.get_current_ev_state()
    latest = smmt_web.latest_validated()
    current_candidate = smmt_candidate_card(latest) if latest else None
    return render_template("dashboard.html", state=state, current_candidate=current_candidate)


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
    """Former nowcast page: generation, review and preview now live on /smmt-nowcast."""
    return redirect("/smmt-nowcast")


@app.route("/api/generate-nowcast", methods=["POST"])
def api_generate_nowcast():
    """Start an SMMT nowcast candidate job (all contiguous SMMT months after the latest C1 actual)."""
    try:
        jid = smmt_web.start_job(app.extensions["lct_smmt"])
    except smmt_web.CandidateError as e:
        return jsonify({"success": False, "message": str(e)}), 409
    return jsonify({"success": True, "message": "Nowcast candidate generation started", "job_id": jid,
                    "status_url": f"/api/smmt-nowcast/job/{jid}"}), 202


@app.route("/api/build-complete-candidate", methods=["POST"])
def api_build_complete_candidate():
    """Retired: candidates are no longer merged into lsoa_ev_bodytype.csv (that mixed the nowcast with DfT actuals)."""
    return jsonify({"success": False,
                    "message": "Retired. SMMT nowcast candidates are generated complete on /smmt-nowcast."}), 410


@app.route("/candidates")
def candidates():
    """SMMT nowcast candidates (from their sealed manifests) and legacy admin-promotion runs."""
    candidates_list = [smmt_candidate_card(r) for r in smmt_web.candidate_rows()]
    for run_id, info in candidate_registry.list_candidates().items():
        st = candidate_status(PROJECT_ROOT, run_id)
        candidates_list.append({
            "run_id": run_id, "type": f"Legacy {info.get('type', 'run')}", "status": st["status"],
            "status_detail": "; ".join(st["errors"]), "created": info.get("created_at"), "legacy": True,
            "description": "Legacy admin-promotion run (replaces production files). Not eligible while quarantined.",
            "review_url": f"/promotion-review/{run_id}",
        })
    return render_template("candidates.html", candidates=candidates_list)


@app.route("/promotion-review/<run_id>")
def promotion_review(run_id):
    """Review and confirm candidate promotion."""
    if run_id.startswith("SMMT-"):
        return redirect(f"/smmt-nowcast/{run_id}")
    # Get candidate from registry
    candidate_info = candidate_registry.get_candidate(run_id)

    if not candidate_info:
        return render_template(
            "promotion_review.html",
            run_id=run_id,
            can_promote=False,
            errors=[f"Candidate {run_id} not found in registry"],
            metadata={},
            candidate_type=None,
            current_vintage=inspector.get_current_ev_state()['latest_ev_period']
        ), 404

    candidate_type = candidate_info.get("type", "unknown")
    metadata = candidate_info.get("metadata", {})

    # Eligibility comes from the same checks the promotion API applies (quarantine, QA, EV actuals, ...)
    eligibility = candidate_status(PROJECT_ROOT, run_id)
    can_promote = eligibility["can_promote"]
    errors = eligibility["errors"]

    return render_template(
        "promotion_review.html",
        run_id=run_id,
        can_promote=can_promote,
        errors=errors,
        metadata=metadata,
        candidate_type=candidate_type,
        current_vintage=inspector.get_current_ev_state()['latest_ev_period']
    )


@app.route("/api/promote", methods=["POST"])
def api_promote():
    """Execute promotion of candidate to production."""
    data = request.get_json()
    run_id = data.get("run_id")

    if not run_id:
        return jsonify({"success": False, "message": "Run ID required"}), 400
    if run_id.startswith("SMMT-"):
        return jsonify({"success": False, "run_id": run_id,
                        "message": "SMMT nowcast candidates cannot be promoted here: the dashboard forecast files and "
                                   "dashboard.html are pinned by the C1 release protections. Publication needs a "
                                   "separately approved, governed change."}), 403

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
