# Monthly dashboard integration report

Generated: 2026-09-25T15:35:11.050034
Runtime start: 2026-09-25T15:32:50.606584
MONTHLY_DASHBOARD_INTEGRATION: PASS

## 1. Files created
- dashboard_integration/outputs/annual_forecast_anchors.csv
- dashboard_integration/outputs/monthly_forecast_dno.csv
- dashboard_integration/outputs/monthly_forecast_la.csv
- dashboard_integration/outputs/monthly_forecast_laep.csv
- dashboard_integration/outputs/geography_lookup.csv
- dashboard_integration/outputs/ev_actuals_*.csv
- dashboard_integration/integration_qa/

## 2. Files modified
None in this runner. Validated HT outputs are read-only.
dashboard.html is updated only after QA PASS, in a separate step.

## 3. Annual anchor row counts
- All anchors: 1,791,558
- NESO: 1,196,316
- DFES: 595,242
- Unique NESO LSOAs: 11,077

## 4. Monthly period range
- 2025-03 to 2051-03

## 5. DFES forecast horizon
- Source years 2024-2050; anchors 2025-03-31 to 2051-03-31

## 6. NESO forecast horizon
- Source years 2024-2050; anchors 2025-03-31 to 2051-03-31

## 7. Latest EV actual period
- 2026-03

## 8. Source/scenario combinations
forecast_source       forecast_scenario EV_Type      n
           DFES      HolisticTransition     BEV 297621
           DFES      HolisticTransition    PHEV 297621
           NESO      HolisticTransition     BEV 299079
           NESO      HolisticTransition    PHEV 299079
           NESO HolisticTransitionBasic     BEV 299079
           NESO HolisticTransitionBasic    PHEV 299079

## 9-11. QA
                         check       forecast_scenario building_block_id   n_file  n_anchor  n_matched  max_abs_diff  pass  expected_rounded   actual_sum  rounded_actual    n_rows  n_bad     expected       actual  n_null  n_years  min_laep_share  max_laep_share dfes_ht neso_ht neso_htb  n_combos  fraction_non_integer
             NESO_ANNUAL_EXACT      HolisticTransition         Lct_BB001 296880.0  299079.0   296880.0  0.000000e+00  True               NaN          NaN             NaN       NaN    NaN          NaN          NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
             NESO_ANNUAL_EXACT      HolisticTransition         Lct_BB002 296880.0  299079.0   296880.0  0.000000e+00  True               NaN          NaN             NaN       NaN    NaN          NaN          NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
             NESO_ANNUAL_EXACT HolisticTransitionBasic         Lct_BB001 296880.0  299079.0   296880.0  0.000000e+00  True               NaN          NaN             NaN       NaN    NaN          NaN          NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
             NESO_ANNUAL_EXACT HolisticTransitionBasic         Lct_BB002 296880.0  299079.0   296880.0  0.000000e+00  True               NaN          NaN             NaN       NaN    NaN          NaN          NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
          NESO_PUBLISHED_TOTAL      HolisticTransition         Lct_BB001      NaN       NaN        NaN           NaN  True       179617757.0 1.796178e+08     179617757.0       NaN    NaN          NaN          NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
          NESO_PUBLISHED_TOTAL      HolisticTransition         Lct_BB002      NaN       NaN        NaN           NaN  True        12954004.0 1.295400e+07      12954004.0       NaN    NaN          NaN          NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
          NESO_PUBLISHED_TOTAL HolisticTransitionBasic         Lct_BB001      NaN       NaN        NaN           NaN  True       176822397.0 1.768224e+08     176822397.0       NaN    NaN          NaN          NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
          NESO_PUBLISHED_TOTAL HolisticTransitionBasic         Lct_BB002      NaN       NaN        NaN           NaN  True        13820698.0 1.382070e+07      13820698.0       NaN    NaN          NaN          NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
   ANCHOR_DATE_31_MAR_Y_PLUS_1                     NaN               NaN      NaN       NaN        NaN           NaN  True               NaN          NaN             NaN 1791558.0    0.0          NaN          NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
               NESO_LSOA_COUNT                     NaN               NaN      NaN       NaN        NaN           NaN  True               NaN          NaN             NaN       NaN    NaN 11077.000000 11077.000000     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
              HT_ONLY_RETAINED                     NaN               NaN      NaN       NaN        NaN           NaN  True               NaN          NaN             NaN       NaN    NaN    66.000000    66.000000     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
        DASHBOARD_ONLY_NO_NESO                     NaN               NaN      NaN       NaN        NaN           NaN  True               NaN          NaN             NaN       NaN    NaN     0.000000     0.000000     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
                HT_ONLY_VOLUME                     NaN               NaN      NaN       NaN        NaN           NaN  True               NaN          NaN             NaN       NaN    NaN     0.000000     0.000000     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
      NESO_MONTHLY_LSOA_ANCHOR                     NaN               NaN      NaN       NaN        NaN  0.000000e+00  True               NaN          NaN             NaN 1196316.0    0.0          NaN          NaN     0.0      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
     NESO_MONTHLY_ANCHOR_TOTAL      HolisticTransition         Lct_BB001      NaN       NaN        NaN  1.862645e-09  True               NaN          NaN             NaN       NaN    NaN          NaN          NaN     NaN     27.0             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
     NESO_MONTHLY_ANCHOR_TOTAL      HolisticTransition         Lct_BB002      NaN       NaN        NaN  1.164153e-10  True               NaN          NaN             NaN       NaN    NaN          NaN          NaN     NaN     27.0             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
     NESO_MONTHLY_ANCHOR_TOTAL HolisticTransitionBasic         Lct_BB001      NaN       NaN        NaN  1.862645e-09  True               NaN          NaN             NaN       NaN    NaN          NaN          NaN     NaN     27.0             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
     NESO_MONTHLY_ANCHOR_TOTAL HolisticTransitionBasic         Lct_BB002      NaN       NaN        NaN  1.164153e-10  True               NaN          NaN             NaN       NaN    NaN          NaN          NaN     NaN     27.0             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
                GEO_LSOA_EQ_LA                     NaN               NaN      NaN       NaN        NaN  3.725290e-09  True               NaN          NaN             NaN       NaN    NaN          NaN          NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
               GEO_LSOA_EQ_DNO                     NaN               NaN      NaN       NaN        NaN  3.725290e-09  True               NaN          NaN             NaN       NaN    NaN          NaN          NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
GEO_LAEP_INCOMPLETE_DOCUMENTED                     NaN               NaN      NaN       NaN        NaN           NaN  True               NaN          NaN             NaN       NaN    NaN          NaN          NaN     NaN      NaN        0.555636        0.686828     NaN     NaN      NaN       NaN                   NaN
      SOURCE_SCENARIO_DISTINCT                     NaN               NaN      NaN       NaN        NaN           NaN  True               NaN          NaN             NaN       NaN    NaN          NaN          NaN     NaN      NaN             NaN             NaN    True    True     True       6.0                   NaN
         INTERP_ENDPOINT_START                     NaN               NaN      NaN       NaN        NaN           NaN  True               NaN          NaN             NaN       NaN    NaN   100.000000   100.000000     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
           INTERP_ENDPOINT_END                     NaN               NaN      NaN       NaN        NaN           NaN  True               NaN          NaN             NaN       NaN    NaN   200.000000   200.000000     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
            INTERP_MID_ON_LINE                     NaN               NaN      NaN       NaN        NaN           NaN  True               NaN          NaN             NaN       NaN    NaN   150.136986   150.136986     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
              INTERP_LEAP_DAYS                     NaN               NaN      NaN       NaN        NaN           NaN  True               NaN          NaN             NaN       NaN    NaN   153.000000   153.000000     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   NaN
     FRACTIONAL_VALUES_PRESENT                     NaN               NaN      NaN       NaN        NaN           NaN  True               NaN          NaN             NaN       NaN    NaN          NaN          NaN     NaN      NaN             NaN             NaN     NaN     NaN      NaN       NaN                   1.0

## 12. 66 HT-only LSOAs
- Count: 66
- NESO volume: 0.0
- Retained in NESO universe with geography_flag=HT_ONLY_BOUNDARY

## 13. 12 dashboard-only LSOAs
- Count: 12
- Codes: E01001196, E01001239, E01001244, E01001281, E01001377, E01002579, E01002693, E01003871, E01030920, E01032572, E01032610, E01034452
- geography_flag=NESO_SPATIAL_NOT_ASSIGNED; no NESO rows created

## 14. DFES old vs new interpolation
 period  old_ukpn      new_ukpn  max_abs_dno     abs_diff
2025-04    661867 687058.452055  7865.835616 25191.452055
2025-05    687209 713089.619178  8543.865753 25880.619178
2025-06    714924 738281.071233  7400.701370 23357.071233
2025-07    744544 764312.238356  6403.731507 19768.238356
2025-08    770066 790343.405479  6911.761644 20277.405479
2025-09    795905 815534.857534  6986.597260 19629.857534
2025-10    834325 841566.024658  1984.104110  7241.024658
2025-11    860164 866757.476712  1869.679452  6593.476712
2025-12    885686 892788.643836  2312.273973  7102.643836
2026-01    915306 918819.810959   989.523288  3513.810959
2026-02    943021 942331.832877   678.030137   689.167123
2026-03    968363 968363.000000     0.000000     0.000000

Old method: month_idx/11 and integer rounding at vehicle-type grain.
New method: elapsed-day interpolation at BEV/PHEV grain, float precision.
March anchors should be close; intervening months will differ by design.

## 15. Dashboard changes
Applied only after QA PASS, using project/output_processed/integration/ copies.
Existing dashboard_comparison_*.csv files are not overwritten.

## 16. Regression
- HT SHA256 unchanged: True
- File totals: {('HolisticTransition', 'Lct_BB001'): 179617756.57646158, ('HolisticTransition', 'Lct_BB002'): 12954004.163440445, ('HolisticTransitionBasic', 'Lct_BB001'): 176822397.4045268, ('HolisticTransitionBasic', 'Lct_BB002'): 13820697.54186766}

## 17. REVIEW items
- LAEP coverage (NESO volume share): 0.5808
- HP/PV/charger completeness rule not changed

## 18. tRESP / HT outputs
- Validated HT files untouched: True

## Failed checks
None

MONTHLY_DASHBOARD_INTEGRATION: PASS
