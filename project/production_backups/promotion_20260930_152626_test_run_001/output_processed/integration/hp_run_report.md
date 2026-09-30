# HP monthly dashboard integration report

Generated: 2026-09-29T13:23:43.696095
Runtime start: 2026-09-29T13:15:29.965970
HP_MONTHLY_DASHBOARD_INTEGRATION: PASS

## Reuse vs HP-specific
Reused: interpolate.py elapsed-day interpolation; geography.py lookup;
aggregate.py monthly matrix + LA/LAEP/DNO roll-up (tech_type/subtype_col parameters);
qa_interpolation_line; FY-end dating Year Y → 31 Mar Y+1; year_offset=0;
parallel outputs then publish-after-QA; no LSOA-month browser CSV.
HP-specific: NESO BB005/BB006 readers; DFES domestic heating Parameter map;
HP actuals copied from existing Stage 2F comparison files; special-group QA;
108/108 C=D=E accounting; Exclusion A (12 LSOAs); hp_* output filenames;
HP display-horizon constants separate from EV_DEMO_DISPLAY_END_PERIOD.

## 1. Files created
- dashboard_integration/outputs/hp_annual_forecast_anchors.csv
- dashboard_integration/outputs/hp_monthly_forecast_dno.csv
- dashboard_integration/outputs/hp_monthly_forecast_la.csv
- dashboard_integration/outputs/hp_monthly_forecast_laep.csv
- dashboard_integration/outputs/hp_actuals_*.csv
- dashboard_integration/outputs/hp_metadata.json
- dashboard_integration/outputs/hp_run_report.md
- dashboard_integration/integration_qa/hp_*

## 2. Files modified
aggregate.py: optional tech_type and subtype_col (EV defaults unchanged).
Validated HP/EV production, tRESP scripts, and dashboard_comparison_*.csv not modified.
dashboard.html is updated only after QA PASS, in a separate step.

## 3. Annual HP anchor row counts
- All anchors: 1,779,462
- NESO: 1,196,316
- DFES: 583,146
- Unique NESO LSOAs: 11,077

## 4. Monthly HP forecast period range
- Data: 2025-03 to 2051-03
- Display (existing HP window): 2025-04 to 2026-03

## 5. BB005/BB006 mapping
- Lct_BB005 → Domestic heat pump
- Lct_BB006 → Domestic hybrid heat pump
- Total Heat Pumps = BB005 + BB006 at query/display only. No synthetic BB.
- Existing dashboard Heat Pump chart was a single combined measure (actuals from
  MCS/LCT/ECR etc.; DFES Domestic+I&C via 07_dfes_heat_pump_processing.py).
- New NESO traces are domestic BB005+BB006. New DFES traces use the same two
  domestic Parameters so NESO vs DFES is like-for-like. I&C is not a NESO BB
  here and is omitted from the new DFES series. Actual HP values are unchanged.
- DFES workbook: C:\Users\johns2v\Desktop\LAEP LCT Dashboard\dfes\DFES 2026\UKPN-domestic-heating-scenarios-LSOA-w-LAEP.xlsx

## 6. Source/scenario combinations
forecast_source       forecast_scenario         technology_detail building_block_id      n
           DFES      HolisticTransition        Domestic heat pump         Lct_BB005 291573
           DFES      HolisticTransition Domestic hybrid heat pump         Lct_BB006 291573
           NESO      HolisticTransition        Domestic heat pump         Lct_BB005 299079
           NESO      HolisticTransition Domestic hybrid heat pump         Lct_BB006 299079
           NESO HolisticTransitionBasic        Domestic heat pump         Lct_BB005 299079
           NESO HolisticTransitionBasic Domestic hybrid heat pump         Lct_BB006 299079

## 7. Latest defensible HP actual period
- 2026-03
- Source: dashboard_comparison_* (Stage 2F; Stage 2D INCLUDE observations)
- Grain: month
- Window: 2025-04 to 2026-03
- Rule: Closed FY 2025/26 window from Stage 2E/2F, not max(date) of raw registers. All twelve months in the window are retained, including zero-install months.

## 8-16. QA
                         check       forecast_scenario building_block_id   n_file  n_anchor  n_matched  max_abs_diff  pass  n_cells  n_pass_file  n_bad_anchor_vs_E  n_bad_C_vs_E  max_abs_anchor_vs_E    n_rows  n_bad  min_year  max_year     expected       actual  n_positive  n_null  n_years  min_laep_share  max_laep_share dfes_ht neso_ht neso_htb bb005 bb006  n_combos  n_mid_months max_period  n_expected  n_got  n_missing  n_extra  n_dup  fraction_non_integer                              group  n_lsoa  n_year_cells  n_src  n_out                 bbs                                      details
             NESO_ANNUAL_EXACT      HolisticTransition         Lct_BB005 296880.0  299079.0   296880.0  0.000000e+00  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
             NESO_ANNUAL_EXACT      HolisticTransition         Lct_BB006 296880.0  299079.0   296880.0  0.000000e+00  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
             NESO_ANNUAL_EXACT HolisticTransitionBasic         Lct_BB005 296880.0  299079.0   296880.0  0.000000e+00  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
             NESO_ANNUAL_EXACT HolisticTransitionBasic         Lct_BB006 296880.0  299079.0   296880.0  0.000000e+00  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
    ACCOUNTING_108_C_EQ_D_EQ_E                     NaN               NaN      NaN       NaN        NaN           NaN  True    108.0        108.0                0.0           0.0         4.656613e-10       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
   ANCHOR_DATE_31_MAR_Y_PLUS_1                     NaN               NaN      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN 1779462.0    0.0       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
              YEAR_OFFSET_ZERO                     NaN               NaN      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN    2024.0    2050.0          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
               NESO_LSOA_COUNT                     NaN               NaN      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN 11077.000000 11077.000000         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
              HT_ONLY_RETAINED                     NaN               NaN      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN    66.000000    66.000000         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
        DASHBOARD_ONLY_NO_NESO                     NaN               NaN      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN     0.000000     0.000000         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
       EXCLUDED_12_NOT_IN_NESO                     NaN               NaN      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN     0.000000     0.000000         0.0     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
               EXCLUDED_12_SET                     NaN               NaN      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN    12.000000    12.000000         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
      NESO_MONTHLY_LSOA_ANCHOR                     NaN               NaN      NaN       NaN        NaN  0.000000e+00  True      NaN          NaN                NaN           NaN                  NaN 1196316.0    0.0       NaN       NaN          NaN          NaN         NaN     0.0      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
     NESO_MONTHLY_ANCHOR_TOTAL      HolisticTransition         Lct_BB005      NaN       NaN        NaN  9.313226e-10  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN     27.0             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
     NESO_MONTHLY_ANCHOR_TOTAL      HolisticTransition         Lct_BB006      NaN       NaN        NaN  1.164153e-10  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN     27.0             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
     NESO_MONTHLY_ANCHOR_TOTAL HolisticTransitionBasic         Lct_BB005      NaN       NaN        NaN  9.313226e-10  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN     27.0             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
     NESO_MONTHLY_ANCHOR_TOTAL HolisticTransitionBasic         Lct_BB006      NaN       NaN        NaN  2.328306e-10  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN     27.0             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
                GEO_LSOA_EQ_LA                     NaN               NaN      NaN       NaN        NaN  1.862645e-09  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
               GEO_LSOA_EQ_DNO                     NaN               NaN      NaN       NaN        NaN  9.313226e-10  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
GEO_LAEP_INCOMPLETE_DOCUMENTED                     NaN               NaN      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN        0.556384        0.736643     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
      SOURCE_SCENARIO_DISTINCT                     NaN               NaN      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN    True    True     True  True  True       6.0           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
         INTERP_ENDPOINT_START                     NaN               NaN      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN   100.000000   100.000000         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
           INTERP_ENDPOINT_END                     NaN               NaN      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN   200.000000   200.000000         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
            INTERP_MID_ON_LINE                     NaN               NaN      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN   150.136986   150.136986         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
              INTERP_LEAP_DAYS                     NaN               NaN      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN   153.000000   153.000000         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
            INTERP_DNO_ON_LINE                     NaN               NaN      NaN       NaN        NaN  7.275958e-12  True      NaN          NaN                NaN           NaN                  NaN       NaN    0.0       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN          11.0        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
       INTERP_NO_EXTRAPOLATION                     NaN               NaN      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN 2051-03-31         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
                MONTH_CALENDAR      HolisticTransition         Lct_BB005      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN       313.0  313.0        0.0      0.0    0.0                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
                MONTH_CALENDAR      HolisticTransition         Lct_BB006      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN       313.0  313.0        0.0      0.0    0.0                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
                MONTH_CALENDAR HolisticTransitionBasic         Lct_BB005      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN       313.0  313.0        0.0      0.0    0.0                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
                MONTH_CALENDAR HolisticTransitionBasic         Lct_BB006      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN       313.0  313.0        0.0      0.0    0.0                   NaN                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
     FRACTIONAL_VALUES_PRESENT                     NaN               NaN      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   1.0                                NaN     NaN           NaN    NaN    NaN                 NaN                                          NaN
                 SPECIAL_GROUP                     NaN               NaN      NaN       NaN        NaN  0.000000e+00  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN     Pelham / Wymondley / Twinstead   324.0         108.0    NaN    NaN                 NaN                                          NaN
                 SPECIAL_GROUP                     NaN               NaN      NaN       NaN        NaN  0.000000e+00  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN          Ninfield / Little Horsted   354.0         108.0    NaN    NaN                 NaN                                          NaN
                 SPECIAL_GROUP                     NaN               NaN      NaN       NaN        NaN  0.000000e+00  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN               Braintree / Rayleigh   551.0         108.0    NaN    NaN                 NaN                                          NaN
                 SPECIAL_GROUP                     NaN               NaN      NaN       NaN        NaN  0.000000e+00  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                            Kemsley   111.0         108.0    NaN    NaN                 NaN                                          NaN
                 SPECIAL_GROUP                     NaN               NaN      NaN       NaN        NaN  0.000000e+00  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN            Sellindge / Richborough   292.0         108.0    NaN    NaN                 NaN                                          NaN
                 SPECIAL_GROUP                     NaN               NaN      NaN       NaN        NaN  0.000000e+00  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                      Willesden EPN    43.0         108.0    NaN    NaN                 NaN                                          NaN
                 SPECIAL_GROUP                     NaN               NaN      NaN       NaN        NaN  0.000000e+00  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                      Willesden LPN   139.0         108.0    NaN    NaN                 NaN                                          NaN
                 SPECIAL_GROUP                     NaN               NaN      NaN       NaN        NaN  0.000000e+00  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                        Barking LPN    46.0         108.0    NaN    NaN                 NaN                                          NaN
                 SPECIAL_GROUP                     NaN               NaN      NaN       NaN        NaN  0.000000e+00  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                Wimbledon South 3/4   423.0         108.0    NaN    NaN                 NaN                                          NaN
                 SPECIAL_GROUP                     NaN               NaN      NaN       NaN        NaN  2.910383e-11  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN New Cross / Wimbledon / Bengeworth   766.0         108.0    NaN    NaN                 NaN                                          NaN
          HP_ACTUALS_UNALTERED                     NaN               NaN      NaN       NaN       36.0  0.000000e+00  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN   36.0   36.0                 NaN                                          NaN
         NO_SYNTHETIC_TOTAL_BB                     NaN               NaN      NaN       NaN        NaN           NaN  True      NaN          NaN                NaN           NaN                  NaN       NaN    NaN       NaN       NaN          NaN          NaN         NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN   NaN   NaN       NaN           NaN        NaN         NaN    NaN        NaN      NaN    NaN                   NaN                                NaN     NaN           NaN    NaN    NaN Lct_BB005,Lct_BB006 Domestic heat pump,Domestic hybrid heat pump

## 17. 12 excluded LSOAs
- Codes: E01001196, E01001239, E01001244, E01001281, E01001377, E01002579, E01002693, E01003871, E01030920, E01032572, E01032610, E01034452
- geography_flag=NESO_SPATIAL_NOT_ASSIGNED; no NESO HP forecast rows created.
- DFES weights not redistributed. Actuals may still include them.

## 66 HT-only LSOAs
- Count: 66
- NESO volume: 0.0

## LAEP coverage
- NESO volume share in LAEP-mapped LSOAs: 0.6304
- LA/DNO retain full NESO volume. LAEP is a documented incomplete subset.

## DFES old vs new interpolation
 period  old_ukpn      new_ukpn  max_abs_dno    abs_diff
2025-04     82823  88427.164384  3007.479452 5604.164384
2025-05     87688  94218.134247  3272.208219 6530.134247
2025-06     94212  99822.298630  2843.687671 5610.298630
2025-07    101115 105613.268493  2420.416438 4498.268493
2025-08    106160 111404.238356  2579.145205 5244.238356
2025-09    111536 117008.402740  2569.624658 5472.402740
2025-10    122294 122799.372603   638.353425  505.372603
2025-11    127670 128403.536986   628.832877  733.536986
2025-12    132715 134194.506849   787.561644 1479.506849
2026-01    139618 139985.476712   364.290411  367.476712
2026-02    146142 145216.030137   340.578082  925.969863
2026-03    151007 151007.000000     0.000000    0.000000

Old method: month_idx/11, integer rounding, Domestic+I&C, FY 2025/26 only.
New method: elapsed-day interpolation, domestic BB005/BB006 only, 2024-2050.
March anchors and intervening months will differ by design and by I&C scope.

## Browser-facing file sizes
- hp_annual_forecast_anchors.csv: 1,779,462 rows, 207.483 MB
- hp_monthly_forecast_dno.csv: 5,634 rows, 0.848 MB
- hp_monthly_forecast_la.csv: 225,986 rows, 37.636 MB
- hp_monthly_forecast_laep.csv: 26,292 rows, 4.279 MB
- hp_actuals_dno.csv: 36 rows, 0.005 MB
- hp_actuals_la.csv: 1,440 rows, 0.206 MB
- hp_actuals_laep.csv: 168 rows, 0.024 MB

## Protected files
- Validated HP and EV production plus dashboard_comparison hashes unchanged: True
- NESO file totals: {('HolisticTransition', 'Lct_BB005'): 54036017.847206414, ('HolisticTransition', 'Lct_BB006'): 12687040.343290865, ('HolisticTransitionBasic', 'Lct_BB005'): 47555896.535986125, ('HolisticTransitionBasic', 'Lct_BB006'): 14166845.105623584}

## Remaining REVIEW items
- Validated HP production remains REVIEW for 6 distribution-QA observations.
- Those flags are not cleared by dashboard integration.
- LAEP incomplete (NESO share 0.6304).

## Failed checks
None

HP_MONTHLY_DASHBOARD_INTEGRATION: PASS
