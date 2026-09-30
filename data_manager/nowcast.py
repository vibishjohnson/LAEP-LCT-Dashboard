"""
SMMT Nowcast Engine — Phase 3

Allocates GB-wide national SMMT monthly registrations to UKPN LSOA level
using authoritative DfT processed stock distribution as weighting base.

Process:
1. Load authoritative DfT stock from lsoa_ev_bodytype.csv (latest period)
2. Calculate four-way category weights: GB → UKPN, UKPN → LSOA
3. For each month of SMMT input:
   - Apply GB → UKPN weight (market-share allocation)
   - Apply UKPN → LSOA weights (geographic distribution)
   - Accumulate onto March 2026 baseline stock
4. QA validation at each stage
5. Store as candidate data (not production)
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple, List, Optional
from datetime import datetime
import os

# Constants
BASELINE_PERIOD = "2026-03-31"  # Latest authoritative DfT period
CATEGORIES = ["BEV Cars", "BEV Vans", "PHEV Cars", "PHEV Vans"]
DNOS = ["EPN", "SPN", "LPN"]


class NowcastEngine:
    """SMMT flow allocation to UKPN LSOA-level provisional stock."""

    def __init__(self, lsoa_ev_path: str):
        """
        Initialize with path to lsoa_ev_bodytype.csv (processed DfT output).

        Args:
            lsoa_ev_path: Path to lsoa_ev_bodytype.csv
        """
        self.lsoa_ev = pd.read_csv(lsoa_ev_path)
        self.lsoa_dno = None
        self.weights = {}
        self.baseline_stock = {}
        self.qa_log = []

        self._load_baseline()
        self._load_dno_mapping()
        self._calculate_weights()

    def _load_baseline(self):
        """Extract March 2026 authoritative stock by category and LSOA."""
        baseline = self.lsoa_ev[self.lsoa_ev['period'] == BASELINE_PERIOD].copy()

        # Map BodyType to category names
        for fuel, bodytype in [('BEV', 'Cars'), ('BEV', 'Vans'),
                               ('PHEV', 'Cars'), ('PHEV', 'Vans')]:
            cat = f"{fuel} {bodytype}"
            subset = baseline[(baseline['Fuel'] == fuel) &
                            (baseline['BodyType'] == bodytype)].copy()
            self.baseline_stock[cat] = subset[['LSOA21CD', 'ev_count']].rename(
                columns={'ev_count': 'stock'}
            )

        self.qa_log.append(f"Loaded baseline stock from {BASELINE_PERIOD}")

    def _load_dno_mapping(self):
        """Load LSOA21CD → DNO mapping."""
        try:
            import openpyxl
            mapping_path = "ev/LSOA mapping.xlsx"
            df = pd.read_excel(mapping_path)
            self.lsoa_dno = df[['LSOA21CD', 'Majority Licence area']].drop_duplicates()
            self.lsoa_dno.columns = ['LSOA21CD', 'DNO']
            self.qa_log.append(f"Loaded LSOA-to-DNO mapping ({len(self.lsoa_dno)} entries)")
        except Exception as e:
            self.qa_log.append(f"WARNING: Could not load DNO mapping: {e}")

    def _calculate_weights(self):
        """
        Calculate dynamic allocation weights from baseline stock.

        GB_weight[category] = UKPN stock / GB stock
        LSOA_weight[lsoa, category] = LSOA stock / UKPN stock
        """
        baseline_period_data = self.lsoa_ev[
            self.lsoa_ev['period'] == BASELINE_PERIOD
        ].copy()

        if self.lsoa_dno is not None:
            baseline_period_data = baseline_period_data.merge(
                self.lsoa_dno, on='LSOA21CD', how='left'
            )

        self.weights = {}

        for fuel, bodytype in [('BEV', 'Cars'), ('BEV', 'Vans'),
                               ('PHEV', 'Cars'), ('PHEV', 'Vans')]:
            cat = f"{fuel} {bodytype}"
            subset = baseline_period_data[
                (baseline_period_data['Fuel'] == fuel) &
                (baseline_period_data['BodyType'] == bodytype)
            ].copy()

            gb_total = subset['ev_count'].sum()
            ukpn_total = subset[subset['DNO'].notna()]['ev_count'].sum()

            if gb_total == 0:
                self.qa_log.append(f"WARNING: {cat} has zero GB stock")
                gb_weight = 0.0
                lsoa_weights = {}
            else:
                gb_weight = ukpn_total / gb_total

                # LSOA weights within UKPN
                lsoa_weights = {}
                if ukpn_total > 0:
                    for _, row in subset[subset['DNO'].notna()].iterrows():
                        lsoa_id = row['LSOA21CD']
                        lsoa_weight = row['ev_count'] / ukpn_total
                        lsoa_weights[lsoa_id] = lsoa_weight

            self.weights[cat] = {
                'gb_weight': gb_weight,
                'gb_total': gb_total,
                'ukpn_total': ukpn_total,
                'lsoa_weights': lsoa_weights
            }

            self.qa_log.append(
                f"{cat}: GB {gb_total:,} | UKPN {ukpn_total:,} | "
                f"UKPN weight {gb_weight:.1%} | LSOAs {len(lsoa_weights)}"
            )

    def allocate_monthly_smmt(
        self,
        smmt_input: Dict[str, int],
        month: str
    ) -> Dict[str, pd.DataFrame]:
        """
        Allocate GB SMMT registrations for one month to UKPN LSOAs.

        Args:
            smmt_input: {category: gb_total_registrations, ...}
            month: "YYYY-MM-DD" format

        Returns:
            {category: DataFrame with LSOA allocations}
        """
        results = {}

        for cat in CATEGORIES:
            if cat not in smmt_input:
                self.qa_log.append(f"WARNING: {cat} missing from SMMT input for {month}")
                continue

            gb_reg = smmt_input[cat]
            gb_weight = self.weights[cat]['gb_weight']

            # First stage: GB → UKPN
            ukpn_reg = gb_reg * gb_weight

            # Deterministic rounding: use largest remainder method
            ukpn_reg_int = self._round_largest_remainder([ukpn_reg], gb_reg)[0]

            # Second stage: UKPN → LSOA
            lsoa_weights = self.weights[cat]['lsoa_weights']

            if not lsoa_weights or ukpn_reg_int == 0:
                results[cat] = pd.DataFrame(columns=['LSOA21CD', 'month', 'addition'])
                continue

            # Allocate to each LSOA
            lsoa_allocations = []
            for lsoa_id, weight in lsoa_weights.items():
                lsoa_reg = ukpn_reg_int * weight
                lsoa_allocations.append(lsoa_reg)

            # Round with largest remainder
            lsoa_allocations_int = self._round_largest_remainder(
                lsoa_allocations, ukpn_reg_int
            )

            # Build result
            result_rows = []
            for lsoa_id, alloc in zip(lsoa_weights.keys(), lsoa_allocations_int):
                if alloc > 0:
                    result_rows.append({
                        'LSOA21CD': lsoa_id,
                        'month': month,
                        'category': cat,
                        'addition': alloc
                    })

            results[cat] = pd.DataFrame(result_rows)

            # QA check
            total_alloc = sum(lsoa_allocations_int)
            self.qa_log.append(
                f"{cat} {month}: GB {gb_reg:,} → UKPN {ukpn_reg_int:,} "
                f"→ LSOAs {len(results[cat])} rows, sum {total_alloc:,}"
            )

        return results

    def build_cumulative_stock(
        self,
        monthly_allocations: Dict[str, List[pd.DataFrame]]
    ) -> Dict[str, pd.DataFrame]:
        """
        Build cumulative stock from baseline + monthly additions.

        Args:
            monthly_allocations: {category: [DataFrame_for_month1, DataFrame_for_month2, ...]}

        Returns:
            {category: cumulative stock by LSOA and month}
        """
        cumulative = {}

        for cat in CATEGORIES:
            if cat not in monthly_allocations or not monthly_allocations[cat]:
                continue

            # Start with baseline
            baseline = self.baseline_stock[cat].copy()
            baseline['month'] = BASELINE_PERIOD
            baseline['cumulative'] = baseline['stock']

            # Concatenate all monthly additions
            all_additions = pd.concat(
                monthly_allocations[cat], ignore_index=True
            )

            # Pivot additions to wide format (LSOA × month)
            if all_additions.empty:
                cumulative[cat] = baseline
                continue

            additions_pivot = all_additions.pivot_table(
                index='LSOA21CD',
                columns='month',
                values='addition',
                fill_value=0
            )

            # Merge baseline with additions
            result = baseline.merge(
                additions_pivot, on='LSOA21CD', how='left'
            )

            # Calculate cumulative for each month
            months = sorted(additions_pivot.columns)
            result['cumulative'] = result['stock']
            for month in months:
                if month in result.columns:
                    result[f'cumulative_{month}'] = result['cumulative'] + result[month]

            cumulative[cat] = result
            self.qa_log.append(f"{cat}: Cumulative stock calculated for {len(months)} months")

        return cumulative

    def _round_largest_remainder(
        self,
        values: List[float],
        target: float
    ) -> List[int]:
        """
        Round list of floats to integers preserving target sum.
        Uses largest remainder method.
        """
        values_arr = np.array(values, dtype=float)
        floors = np.floor(values_arr).astype(int)
        fracs = values_arr - floors

        floor_sum = floors.sum()
        remaining = int(round(target)) - floor_sum

        if remaining > 0:
            order = np.argsort(-fracs)
            for idx in order[:remaining]:
                floors[idx] += 1
        elif remaining < 0:
            order = np.argsort(fracs)
            for idx in order[:-remaining]:
                if floors[idx] > 0:
                    floors[idx] -= 1

        return floors.tolist()

    def validate_qa(self) -> Tuple[bool, List[str]]:
        """Run QA validation checks. Return (is_valid, messages)."""
        errors = []

        # Check weights sum close to 1.0 per category (allow rounding tolerance)
        for cat in CATEGORIES:
            if cat not in self.weights:
                errors.append(f"{cat} missing from weights")
                continue

            lsoa_weights = self.weights[cat]['lsoa_weights']
            if lsoa_weights:
                weight_sum = sum(lsoa_weights.values())
                # Allow up to 0.5% tolerance due to rounding and missing/unmapped data
                if abs(weight_sum - 1.0) > 0.005:
                    errors.append(
                        f"{cat}: LSOA weights sum to {weight_sum:.4f} (tolerance: 0.5%)"
                    )

        # Check baseline stock exists
        for cat in CATEGORIES:
            if cat not in self.baseline_stock:
                errors.append(f"{cat} baseline missing")

        is_valid = len(errors) == 0
        return is_valid, errors

    def get_qa_report(self) -> str:
        """Return formatted QA log."""
        return "\n".join([f"  {line}" for line in self.qa_log])
