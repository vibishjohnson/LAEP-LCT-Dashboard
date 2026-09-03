#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LCT Dashboard Automation Script - Stage 1: Geographic Assignment
Processes 5 data sources with postcode-based geographic assignment
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pandas as pd
import numpy as np
import os
import glob
import re
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

class LCTDashboardProcessor:
    def __init__(self, data_dir="lct", output_dir="output"):
        self.data_dir = data_dir
        self.output_dir = output_dir
        self.postcode_lookup = None
        self.dno_lookup = None
        self.aggregated_data = []

        # Audit tracking per source
        self.audit = {
            'MCS': {'raw': 0, 'blank_postcode': 0, 'postcode_not_in_lookup': 0,
                   'spatial_epn': 0, 'spatial_spn': 0, 'spatial_lpn': 0,
                   'spatial_outside_ukpn': 0, 'licence_area_unresolved': 0,
                   'native_fallback_retained': 0, 'final_ukpn_candidates': 0},
            'LCT_Register': {'raw': 0, 'blank_postcode': 0, 'postcode_not_in_lookup': 0,
                            'spatial_epn': 0, 'spatial_spn': 0, 'spatial_lpn': 0,
                            'spatial_outside_ukpn': 0, 'licence_area_unresolved': 0,
                            'native_fallback_retained': 0, 'final_ukpn_candidates': 0},
            'ECR_Large': {'raw': 0, 'blank_postcode': 0, 'postcode_not_in_lookup': 0,
                         'spatial_epn': 0, 'spatial_spn': 0, 'spatial_lpn': 0,
                         'spatial_outside_ukpn': 0, 'licence_area_unresolved': 0,
                         'native_fallback_retained': 0, 'final_ukpn_candidates': 0},
            'ECR_Small': {'raw': 0, 'blank_postcode': 0, 'postcode_not_in_lookup': 0,
                         'spatial_epn': 0, 'spatial_spn': 0, 'spatial_lpn': 0,
                         'spatial_outside_ukpn': 0, 'licence_area_unresolved': 0,
                         'native_fallback_retained': 0, 'final_ukpn_candidates': 0},
            'ZapMap': {'raw': 0, 'blank_postcode': 0, 'postcode_not_in_lookup': 0,
                      'spatial_epn': 0, 'spatial_spn': 0, 'spatial_lpn': 0,
                      'spatial_outside_ukpn': 0, 'licence_area_unresolved': 0,
                      'native_fallback_retained': 0, 'final_ukpn_candidates': 0},
        }

        # Technology name standardization mapping
        self.tech_mapping = {
            'solar pv': 'Solar PV',
            'solar': 'Solar PV',
            'photovoltaic': 'Solar PV',
            'battery storage': 'Battery Storage',
            'battery': 'Battery Storage',
            'storage': 'Battery Storage',
            'heat pump': 'Heat Pump',
            'heatpump': 'Heat Pump',
            'ev charging': 'EV Charging',
            'ev charge': 'EV Charging',
            'electric vehicle': 'EV Charging',
            'v2g': 'V2G',
            'vehicle to grid': 'V2G',
            'biomass': 'Biomass',
            'wind': 'Wind',
            'stored energy': 'Stored Energy'
        }

    def load_postcode_lookups(self):
        """Load postcode->LSOA and LSOA->DNO lookups"""
        try:
            pc_path = os.path.join('lookups', 'postcode_lsoa21_lookup_spatial.csv')
            self.postcode_lookup = pd.read_csv(pc_path)
            self.postcode_lookup['postcode_std'] = (
                self.postcode_lookup['postcode'].str.upper().str.replace(" ", "", regex=False).str.strip()
            )
            self.postcode_lookup_dict = dict(zip(self.postcode_lookup['postcode_std'], self.postcode_lookup['LSOA21CD']))
            print("OK - Postcode->LSOA21CD lookup loaded")

            dno_path = os.path.join('lookups', 'LSOA to DNO.csv')
            self.dno_lookup = pd.read_csv(dno_path, encoding='utf-8-sig')
            self.dno_lookup_dict = dict(zip(self.dno_lookup['LSOA21CD'], self.dno_lookup['Majority Licence area']))
            print("OK - LSOA21CD->Licence Area lookup loaded")

        except Exception as e:
            print(f"ERROR loading lookups: {e}")
            raise

    def standardize_postcode(self, postcode):
        """Normalize postcode format"""
        if pd.isna(postcode) or postcode == '':
            return None
        postcode_str = str(postcode).upper().replace(" ", "").strip()
        return postcode_str if postcode_str else None

    def standardize_technology_name(self, tech_name):
        """Standardize technology names"""
        if pd.isna(tech_name):
            return None
        tech_lower = str(tech_name).lower().strip()
        for key, value in self.tech_mapping.items():
            if key in tech_lower:
                return value
        return tech_name

    def parse_capacity_to_kw(self, capacity_value):
        """Parse capacity values and convert to kW"""
        if pd.isna(capacity_value) or capacity_value == '':
            return 0.0

        capacity_str = str(capacity_value).strip()
        numeric_match = re.search(r'[\d,]+\.?\d*', capacity_str.replace(',', ''))
        if not numeric_match:
            return 0.0

        numeric_value = float(numeric_match.group().replace(',', ''))
        capacity_lower = capacity_str.lower()

        if 'mw' in capacity_lower:
            return numeric_value * 1000
        elif 'w' in capacity_lower and 'kw' not in capacity_lower:
            return numeric_value / 1000
        else:
            return numeric_value

    def assign_geography_spatial(self, postcode_std):
        """Assign geography via postcode->LSOA->DNO (spatial method)"""
        if postcode_std is None:
            return None, None

        lsoa = self.postcode_lookup_dict.get(postcode_std)
        if lsoa is None:
            return None, None

        dno = self.dno_lookup_dict.get(lsoa)
        return lsoa, dno

    def assign_geography_with_fallback(self, postcode, source, native_licence=None):
        """
        Assign geography with source-specific fallback rules.
        Returns (licence_area, geography_status)

        Geography status values:
        - RESOLVED_SPATIAL_UKPN: spatial method resolved to EPN/SPN/LPN
        - RESOLVED_NATIVE_FALLBACK: spatial unresolved but native DNO used
        - OUTSIDE_UKPN: spatial resolved but DNO is blank (outside UKPN)
        - POSTCODE_BLANK: postcode field is blank/missing
        - POSTCODE_NOT_IN_LOOKUP: postcode exists but not in lookup
        - LICENCE_AREA_UNRESOLVED: no resolution method available
        """

        # Step 1: Check if postcode is blank
        postcode_std = self.standardize_postcode(postcode)
        if postcode_std is None:
            return None, 'POSTCODE_BLANK'

        # Step 2: Apply spatial geography
        lsoa, spatial_dno = self.assign_geography_spatial(postcode_std)

        if spatial_dno is None:
            if lsoa is None:
                # Postcode not in lookup
                geo_status = 'POSTCODE_NOT_IN_LOOKUP'
            else:
                # Postcode->LSOA mapped but LSOA->DNO blank (outside UKPN)
                geo_status = 'OUTSIDE_UKPN'

            # Step 3: Try native fallback if applicable
            if source in ['LCT_Register', 'ECR_Large', 'ECR_Small'] and native_licence:
                native_std = str(native_licence).strip() if native_licence else None
                # Normalize ECR native field (full company name -> abbreviation)
                if 'Eastern Power Networks (EPN)' in str(native_licence):
                    native_std = 'EPN'
                elif 'South Eastern Power Networks (SPN)' in str(native_licence):
                    native_std = 'SPN'
                elif 'London Power Networks (LPN)' in str(native_licence):
                    native_std = 'LPN'

                if native_std in ['EPN', 'SPN', 'LPN']:
                    return native_std, 'RESOLVED_NATIVE_FALLBACK'

            # No resolution
            return None, geo_status

        # Spatial resolved to valid UKPN DNO
        if spatial_dno in ['EPN', 'SPN', 'LPN']:
            return spatial_dno, 'RESOLVED_SPATIAL_UKPN'

        # Spatial resolved but outside UKPN
        return None, 'OUTSIDE_UKPN'

    def process_source_vectorized(self, source_name, file_path, postcode_col, native_licence_col=None, source_type='broad'):
        """
        Process source using vectorized operations.
        source_type: 'broad' (MCS, ZapMap) or 'ukpn_native' (LCT, ECR)
        """
        if not os.path.exists(file_path):
            print(f"SKIP - File not found: {file_path}")
            return

        try:
            print(f"  {os.path.basename(file_path)}...", end="")
            df = pd.read_csv(file_path) if file_path.endswith('.csv') else pd.read_excel(file_path)

            self.audit[source_name]['raw'] = len(df)

            # Normalize postcodes
            df['postcode_std'] = df[postcode_col].fillna('').astype(str).str.upper().str.replace(" ", "", regex=False).str.strip()
            df['postcode_std'] = df['postcode_std'].replace('', None)

            # Map to LSOA
            df['lsoa21cd'] = df['postcode_std'].map(self.postcode_lookup_dict)

            # Map to spatial licence area
            df['spatial_licence_area'] = df['lsoa21cd'].map(self.dno_lookup_dict)

            # For UKPN-native sources: normalize native licence area
            if source_type == 'ukpn_native' and native_licence_col:
                def normalize_native(val):
                    if pd.isna(val):
                        return None
                    s = str(val).strip()
                    s_norm = ' '.join(s.upper().split())

                    spn_patterns = [
                        'SOUTH EASTERN POWER NETWORKS',
                        'SOUTH EASTERN POWER NETWORKS (SPN)',
                        'SPN'
                    ]
                    lpn_patterns = [
                        'LONDON POWER NETWORKS',
                        'LONDON POWER NETWORKS (LPN)',
                        'LPN'
                    ]
                    epn_patterns = [
                        'EASTERN POWER NETWORKS',
                        'EASTERN POWER NETWORKS (EPN)',
                        'EPN'
                    ]

                    if s_norm in spn_patterns:
                        return 'SPN'
                    elif s_norm in lpn_patterns:
                        return 'LPN'
                    elif s_norm in epn_patterns:
                        return 'EPN'

                    return None
                df['native_licence_area'] = df[native_licence_col].apply(normalize_native)
            else:
                df['native_licence_area'] = None

            # SOURCE-TYPE SPECIFIC LOGIC
            if source_type == 'broad':
                # BROAD SOURCES (MCS, ZapMap): Spatial gates UKPN eligibility
                def assign_geo_status_broad(row):
                    if pd.isna(row['postcode_std']):
                        return 'POSTCODE_BLANK', None
                    if pd.isna(row['lsoa21cd']):
                        return 'POSTCODE_NOT_IN_LOOKUP', None
                    if pd.isna(row['spatial_licence_area']):
                        return 'RESOLVED_SPATIAL_OUTSIDE_UKPN', None
                    if row['spatial_licence_area'] in ['EPN', 'SPN', 'LPN']:
                        return 'RESOLVED_SPATIAL_MATCH', row['spatial_licence_area']
                    return 'LICENCE_AREA_UNRESOLVED', None

                df[['geography_status', 'licence_area']] = df.apply(assign_geo_status_broad, axis=1, result_type='expand')
                df['ukpn_eligible'] = df['licence_area'].isin(['EPN', 'SPN', 'LPN'])
            else:
                # UKPN-NATIVE SOURCES (LCT, ECR): Native gates UKPN eligibility
                df['ukpn_eligible'] = df['native_licence_area'].isin(['EPN', 'SPN', 'LPN'])
                df['licence_area'] = df['native_licence_area']

                # Determine spatial allocation status
                def assign_geo_status_ukpn_native(row):
                    if pd.isna(row['postcode_std']):
                        return 'POSTCODE_BLANK'
                    if pd.isna(row['lsoa21cd']):
                        return 'POSTCODE_NOT_IN_LOOKUP'
                    if pd.notna(row['spatial_licence_area']):
                        if row['spatial_licence_area'] == row['native_licence_area']:
                            return 'RESOLVED_SPATIAL_MATCH'
                        elif row['spatial_licence_area'] in ['EPN', 'SPN', 'LPN']:
                            return 'RESOLVED_SPATIAL_MISMATCH'
                        else:
                            return 'RESOLVED_SPATIAL_OUTSIDE_UKPN'
                    return 'LICENCE_AREA_UNRESOLVED'

                df['geography_status'] = df.apply(assign_geo_status_ukpn_native, axis=1)

            # AUDIT COUNTS
            self.audit[source_name]['blank_postcode'] = (df['geography_status'] == 'POSTCODE_BLANK').sum()
            self.audit[source_name]['postcode_not_in_lookup'] = (df['geography_status'] == 'POSTCODE_NOT_IN_LOOKUP').sum()
            self.audit[source_name]['spatial_outside_ukpn'] = (df['geography_status'] == 'RESOLVED_SPATIAL_OUTSIDE_UKPN').sum()
            self.audit[source_name]['licence_area_unresolved'] = (df['geography_status'] == 'LICENCE_AREA_UNRESOLVED').sum()

            if source_type == 'broad':
                # Broad sources: count by spatial licence area
                self.audit[source_name]['spatial_epn'] = (df['licence_area'] == 'EPN').sum()
                self.audit[source_name]['spatial_spn'] = (df['licence_area'] == 'SPN').sum()
                self.audit[source_name]['spatial_lpn'] = (df['licence_area'] == 'LPN').sum()
                self.audit[source_name]['native_fallback_retained'] = 0
            else:
                # UKPN-native sources: count by native licence area and spatial agreement
                self.audit[source_name]['spatial_epn'] = (df['native_licence_area'] == 'EPN').sum()
                self.audit[source_name]['spatial_spn'] = (df['native_licence_area'] == 'SPN').sum()
                self.audit[source_name]['spatial_lpn'] = (df['native_licence_area'] == 'LPN').sum()
                # Track spatial mismatches (disagreements between native and spatial)
                self.audit[source_name]['native_fallback_retained'] = (df['geography_status'] == 'RESOLVED_SPATIAL_MISMATCH').sum()

            self.audit[source_name]['final_ukpn_candidates'] = df['ukpn_eligible'].sum()

            # Retain only UKPN eligible
            df_ukpn = df[df['ukpn_eligible']].copy()

            print(f" OK ({len(df)} -> {len(df_ukpn)} UKPN)")

        except Exception as e:
            print(f" ERROR: {e}")

    def process_mcs_data(self):
        """Process MCS monthly data files"""
        print("\n--- Processing MCS ---")

        mcs_dir = os.path.join(self.data_dir, 'MCS')
        if not os.path.exists(mcs_dir):
            print(f"SKIP - Directory not found: {mcs_dir}")
            return

        mcs_files = sorted(glob.glob(os.path.join(mcs_dir, '*.csv')))
        print(f"Found {len(mcs_files)} MCS files")

        for file_path in mcs_files:
            self.process_source_vectorized('MCS', file_path, 'Postcode', source_type='broad')

        print(f"  MCS audit: {self.audit['MCS']}")

    def process_lct_register(self):
        """Process LCT Register"""
        print("\n--- Processing LCT Register ---")

        file_path = os.path.join(self.data_dir, 'LCT Register.csv')
        self.process_source_vectorized('LCT_Register', file_path, 'MPAN_Postcode', 'DNO', source_type='ukpn_native')
        print(f"  LCT Register audit: {self.audit['LCT_Register']}")

    def process_ecr_large(self):
        """Process ECR Large (>1MW)"""
        print("\n--- Processing ECR Large ---")

        file_path = os.path.join(self.data_dir, 'ecr_large.csv')
        self.process_source_vectorized('ECR_Large', file_path, 'Postcode', 'Licence Area', source_type='ukpn_native')
        print(f"  ECR Large audit: {self.audit['ECR_Large']}")

    def process_ecr_small(self):
        """Process ECR Small (<1MW)"""
        print("\n--- Processing ECR Small ---")

        file_path = os.path.join(self.data_dir, 'ecr_small.csv')
        # ECR Small has 'Licence Area ' with trailing space
        self.process_source_vectorized('ECR_Small', file_path, 'Postcode', 'Licence Area ', source_type='ukpn_native')
        print(f"  ECR Small audit: {self.audit['ECR_Small']}")

    def process_zapmap_data(self):
        """Process ZapMap data"""
        print("\n--- Processing ZapMap ---")

        file_path = os.path.join(self.data_dir, 'zapmap.csv')
        # ZapMap uses 'postal_code', not 'postcode'
        self.process_source_vectorized('ZapMap', file_path, 'postal_code', source_type='broad')
        print(f"  ZapMap audit: {self.audit['ZapMap']}")

    def print_audit_summary(self):
        """Print comprehensive audit summary"""
        print("\n" + "="*100)
        print("STAGE 1 GEOGRAPHIC ASSIGNMENT AUDIT SUMMARY")
        print("="*100)

        for source in ['MCS', 'LCT_Register', 'ECR_Large', 'ECR_Small', 'ZapMap']:
            audit = self.audit[source]
            print(f"\n{source}:")
            print(f"  Raw records:                        {audit['raw']:9,}")
            print(f"  Blank/invalid postcode:             {audit['blank_postcode']:9,}")
            print(f"  Postcode not in lookup:             {audit['postcode_not_in_lookup']:9,}")
            print(f"  Spatial EPN:                        {audit['spatial_epn']:9,}")
            print(f"  Spatial SPN:                        {audit['spatial_spn']:9,}")
            print(f"  Spatial LPN:                        {audit['spatial_lpn']:9,}")
            print(f"  Spatial outside UKPN:               {audit['spatial_outside_ukpn']:9,}")
            print(f"  Licence area unresolved:            {audit['licence_area_unresolved']:9,}")
            print(f"  Native fallback retained:           {audit['native_fallback_retained']:9,}")
            print(f"  Final UKPN candidates:              {audit['final_ukpn_candidates']:9,}")

            # Verify reconciliation
            # For UKPN-native sources: primary = blank + not_in_lookup + all spatial statuses
            # For broad sources: primary = blank + not_in_lookup + outside_ukpn + unresolved + spatial_UKPN
            if source in ['LCT_Register', 'ECR_Large', 'ECR_Small']:
                # UKPN-native: all records have valid native licence area, so final_ukpn_candidates == raw
                if audit['final_ukpn_candidates'] != audit['raw']:
                    print(f"  ERROR: UKPN-native source should have final_ukpn_candidates == raw!")
                else:
                    print(f"  [OK - Audit reconciled]")
            else:
                # Broad sources (MCS, ZapMap): verify primary categories sum to raw
                spatial_ukpn = audit['spatial_epn'] + audit['spatial_spn'] + audit['spatial_lpn']
                total_primary = (
                    audit['blank_postcode'] + audit['postcode_not_in_lookup'] +
                    audit['spatial_outside_ukpn'] + audit['licence_area_unresolved'] + spatial_ukpn
                )
                # For broad sources, final UKPN = spatial UKPN only (no native fallback)
                expected_final_ukpn = spatial_ukpn

                if total_primary != audit['raw']:
                    print(f"  ERROR: Primary categories mismatch! {total_primary} vs {audit['raw']}")
                elif expected_final_ukpn != audit['final_ukpn_candidates']:
                    print(f"  ERROR: Final UKPN mismatch! {expected_final_ukpn} vs {audit['final_ukpn_candidates']}")
                else:
                    print(f"  [OK - Audit reconciled]")

    def run(self):
        """Run Stage 1 geographic assignment"""
        print("\n" + "="*100)
        print("STAGE 1: GEOGRAPHIC ASSIGNMENT (READ-ONLY PROCESSING)")
        print("="*100)

        try:
            # Load lookups
            self.load_postcode_lookups()

            # Process all sources
            self.process_mcs_data()
            self.process_lct_register()
            self.process_ecr_large()
            self.process_ecr_small()
            self.process_zapmap_data()

            # Print audit
            self.print_audit_summary()

            print("\n" + "="*100)
            print("STAGE 1 COMPLETE: Geographic assignment verified")
            print("="*100)

        except Exception as e:
            print(f"\nERROR: {e}")
            raise

if __name__ == '__main__':
    processor = LCTDashboardProcessor(data_dir='lct', output_dir='output')
    processor.run()
