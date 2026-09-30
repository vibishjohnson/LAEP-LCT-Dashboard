# LCT Data Manager — Operator Runbook

**For regular SMMT and DfT updates**

---

## MONTHLY SMMT UPDATE (4 steps)

Normal workflow: enter registration data, validate, promote.

### 1. Open Data Manager
```
http://localhost:5000
```

### 2. Enter SMMT Data
Go to **SMMT Input** tab:
- Select month/year (auto-suggests next month)
- Enter:
  - BEV Cars (thousands)
  - BEV Vans (thousands)
  - PHEV Cars (thousands)
  - PHEV Vans (thousands)
- Save

### 3. Review Nowcast
- Candidate preview shows April→August allocations
- Check EPN/SPN/LPN breakdown
- Check QA report

### 4. Promote
- Click "Promote to Production"
- Confirm: "Current: Q1 2026 → New: Q1 2026"
- Promotion completes
- Check Production History

---

## QUARTERLY DfT UPDATE (5 steps)

One-time workflow: upload raw DfT files, automatic normalization, rebasing.

### 1. Download Raw DfT Files
From DfT portal (do NOT manually delete metadata rows):
- VEH0135 raw export
- VEH0142 raw export
- VEH0105 raw export
- VEH0125 raw export

### 2. Open Data Manager
```
http://localhost:5000 → Update DfT tab
```

### 3. Upload & Auto-Normalize
- Drag/select all 4 raw files
- System detects format automatically
- Shows: detected periods, rows ignored, normalisation applied
- Confirms: latest common quarter (e.g., Q2 2026)

### 4. Review Candidate
- DfT source validation: PASS
- Supersession: April→June marked for new DfT
- Rebasing: July+ use new weights
- QA: reconciliation checks

### 5. Promote
- Click "Promote to Production"
- Confirm: "Current: Q1 2026 → New: Q2 2026"
- Promotion completes
- New SMMT input uses Q2 2026 weights

---

## TROUBLESHOOTING

### Problem: "SMMT data not found for June"
**Solution:** SMMT entry for June deleted or missing.
- Go to SMMT Input
- Re-enter June data
- Save
- Retry nowcast

### Problem: "Unrecognized raw DfT format"
**Solution:** File structure changed or corrupted file.
- Verify file downloaded correctly
- Try downloading again from source
- Check file is not corrupted (open in Excel, verify columns)
- If still fails, consult data team

### Problem: "Mismatched DfT quarters (Q2 in VEH0135, Q3 in VEH0142)"
**Solution:** Files are from different releases.
- Verify all 4 files are from same DfT release
- Download consistent set
- Retry upload

### Problem: "Failed QA - EPN/SPN/LPN totals don't reconcile"
**Solution:** Data integrity issue in DfT.
- Do NOT force promotion
- Report to data team with specific missing LSOA
- Use previous production version (see Rollback)

### Problem: "Stale candidate" error
**Solution:** Candidate was generated against old production.
- This prevents mistakes
- Simply retry: candidate regenerates against current production

### Problem: "Synthetic or test candidate rejected"
**Solution:** Candidate marked as test data.
- This is intentional safety feature
- Do not attempt to work around
- Use only validated production candidates

### Problem: "Promotion failed, automatic rollback performed"
**Solution:** Post-promotion QA detected issue.
- Check Promotion History for details
- Production is restored (SAFE)
- Investigate root cause
- Do not retry until issue understood

---

## ROLLBACK

### Manual Rollback to Previous Version

**Go to:** Data Manager → Rollback Options

**Select** previous version (e.g., "Q1 2026 — 31 March 2026")

**Click** ROLLBACK

**Confirm** in dialog

**Result:** Current production backed up, previous version restored

---

## SAFETY FEATURES (automatic)

✓ Raw uploads never modify production directly  
✓ Backup created before every promotion  
✓ Post-promotion validation mandatory  
✓ Failed validation triggers automatic rollback  
✓ Synthetic/test data cannot be promoted  
✓ Stale candidates automatically rejected  
✓ All actions recorded in audit history  
✓ HP/other tech files never regenerated  

---

## WHAT NOT TO DO

❌ Manually delete metadata rows from raw DfT files  
❌ Edit CSV files directly  
❌ Force promotion of failed candidates  
❌ Bypass validation steps  
❌ Promote without confirmation dialogs  
❌ Edit dashboard.html to change source data paths  

---

## VERIFICATION

After each promotion, verify dashboard shows correct data:

1. Open dashboard: `http://localhost:5000/dashboard.html`
2. Check EV chart data appears
3. Verify latest vintage in status bar
4. Check EPN/SPN/LPN totals make sense

---

## EMERGENCY CONTACT

- **Data Issue:** Contact data team
- **Promotion Failed:** Check Promotion History, use Rollback if needed
- **Unrecognized DfT Format:** Verify file structure with source provider
- **Cannot Rollback:** All backups available — see Promotion History for IDs

---

**Last Updated:** 2026-09-30
