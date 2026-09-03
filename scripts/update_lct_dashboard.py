#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LCT Dashboard Automation Script - Stage 2B: Canonical Row-Level Normalization
Processes all LCT data sources and creates canonical row-level observations with full provenance.
Stage 1 (geographic assignment) is embedded for completeness.
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
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

class LCTCanonicalProcessor:
    def __init__(self, data_dir="lct", output_dir="output"):
        self.data_dir = data_dir
        self.output_dir = output_dir
        self.postcode_lookup = None
        self.dno_lookup = None
        self.postcode_lookup_dict = {}
        self.dno_lookup_dict = {}
        self.canonical_observations = []

        Path(self.output_dir).mkdir(parents=True, exist_ok=True)

        # Stage 1 audit (accumulated for MCS across all 21 files)
        self.audit = {
            'MCS': {'raw': 0, 'ukpn_eligible': 0, 'files_processed': 0},
            'LCT_Register': {'raw': 0, 'ukpn_eligible': 0},
            'ECR_Large': {'raw': 0, 'ukpn_eligible': 0},
            'ECR_Small': {'raw': 0, 'ukpn_eligible': 0},
            'ZapMap': {'raw': 0, 'ukpn_eligible': 0},
            'Device_Report': {'raw': 0, 'ukpn_eligible': 0},
        }

        # Stage 2B canonical audit
        self.canonical_audit = {
            'MCS': {'canonical_rows': 0, 'tech_mapped': 0, 'capacity_resolved': 0},
            'LCT_Register': {'canonical_rows': 0, 'tech_mapped': 0, 'generation_rating_used': 0, 'import_rating_used': 0},
            'ECR_Large': {'canonical_rows': 0, 'multi_tech_rows': 0, 'tech_mapped': 0},
            'ECR_Small': {'canonical_rows': 0, 'multi_tech_rows': 0, 'tech_mapped': 0},
            'ZapMap': {'canonical_rows': 0, 'actual_power': 0, 'category_assumption': 0},
            'Device_Report': {'canonical_rows': 0},
        }

        # ZapMap power band mapping (CONFIRMED BUSINESS DECISION)
        self.zapmap_power_band_mapping = {
            'slow': 3.7,
            'fast': 7.64,
            'rapid': 43.0,
            'ultra-rapid': 150.0,
        }

    def load_postcode_lookups(self):
        """Load postcode->LSOA and LSOA->DNO lookups"""
        try:
            pc_path = os.path.join('lookups', 'postcode_lsoa21_lookup_spatial.csv')
            df_pc = pd.read_csv(pc_path)
            df_pc['postcode_std'] = (df_pc['postcode'].str.upper().str.replace(" ", "", regex=False).str.strip())
            self.postcode_lookup_dict = dict(zip(df_pc['postcode_std'], df_pc['LSOA21CD']))
            print("✓ Postcode→LSOA21CD lookup loaded")

            dno_path = os.path.join('lookups', 'LSOA to DNO.csv')
            df_dno = pd.read_csv(dno_path, encoding='utf-8-sig')
            self.dno_lookup_dict = dict(zip(df_dno['LSOA21CD'], df_dno['Majority Licence area']))
            print("✓ LSOA21CD→Licence Area lookup loaded")

        except Exception as e:
            print(f"✗ ERROR loading lookups: {e}")
            raise

    def extract_postcode_from_address(self, address):
        """Extract UK postcode from full address string. Returns standardized postcode or None."""
        if pd.isna(address) or address == '':
            return None
        addr_str = str(address).upper().strip()
        match = re.search(r'\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\b', addr_str)
        if match:
            return match.group(1).replace(" ", "")
        return None

    def standardize_postcode(self, postcode):
        """Normalize postcode format"""
        if pd.isna(postcode) or postcode == '':
            return None
        postcode_str = str(postcode).upper().replace(" ", "").strip()
        return postcode_str if postcode_str else None

    def assign_geography(self, postcode, native_licence=None, source=None):
        """Assign geography and return (licence_area, lsoa21cd, geography_status, ukpn_eligible)"""
        postcode_std = self.standardize_postcode(postcode)

        if postcode_std is None:
            if source in ['LCT_Register', 'ECR_Large', 'ECR_Small'] and native_licence:
                native_std = self.normalize_native_licence(native_licence)
                if native_std in ['EPN', 'SPN', 'LPN']:
                    return native_std, None, 'POSTCODE_BLANK_NATIVE_FALLBACK', True
            if source == 'Device_Report':
                return None, None, 'POSTCODE_BLANK', True
            return None, None, 'POSTCODE_BLANK', False

        lsoa = self.postcode_lookup_dict.get(postcode_std)
        if lsoa is None:
            if source in ['LCT_Register', 'ECR_Large', 'ECR_Small'] and native_licence:
                native_std = self.normalize_native_licence(native_licence)
                if native_std in ['EPN', 'SPN', 'LPN']:
                    return native_std, None, 'POSTCODE_NOT_IN_LOOKUP_NATIVE_FALLBACK', True
            if source == 'Device_Report':
                return None, None, 'POSTCODE_NOT_IN_LOOKUP', True
            return None, None, 'POSTCODE_NOT_IN_LOOKUP', False

        spatial_dno = self.dno_lookup_dict.get(lsoa)

        if source in ['LCT_Register', 'ECR_Large', 'ECR_Small']:
            native_std = self.normalize_native_licence(native_licence) if native_licence else None
            if native_std in ['EPN', 'SPN', 'LPN']:
                if spatial_dno == native_std:
                    return native_std, lsoa, 'RESOLVED_SPATIAL_MATCH', True
                elif spatial_dno in ['EPN', 'SPN', 'LPN']:
                    return native_std, lsoa, 'RESOLVED_SPATIAL_MISMATCH', True
                else:
                    return native_std, lsoa, 'RESOLVED_NATIVE_ONLY', True
            return None, lsoa, 'LICENCE_AREA_UNRESOLVED', False
        elif source == 'Device_Report':
            if spatial_dno in ['EPN', 'SPN', 'LPN']:
                return spatial_dno, lsoa, 'RESOLVED_SPATIAL_MATCH', True
            else:
                return None, lsoa, 'RESOLVED_SPATIAL_OUTSIDE_UKPN', True
        else:
            if spatial_dno in ['EPN', 'SPN', 'LPN']:
                return spatial_dno, lsoa, 'RESOLVED_SPATIAL_MATCH', True
            return None, lsoa, 'OUTSIDE_UKPN', False

    def normalize_native_licence(self, val):
        """Normalize native licence field to EPN/SPN/LPN"""
        if pd.isna(val):
            return None
        s = str(val).strip()
        s_norm = ' '.join(s.upper().split())

        spn_patterns = ['SOUTH EASTERN POWER NETWORKS', 'SOUTH EASTERN POWER NETWORKS (SPN)', 'SPN']
        lpn_patterns = ['LONDON POWER NETWORKS', 'LONDON POWER NETWORKS (LPN)', 'LPN']
        epn_patterns = ['EASTERN POWER NETWORKS', 'EASTERN POWER NETWORKS (EPN)', 'EPN']

        if s_norm in spn_patterns:
            return 'SPN'
        elif s_norm in lpn_patterns:
            return 'LPN'
        elif s_norm in epn_patterns:
            return 'EPN'
        return None

    def normalize_technology(self, tech_raw, source):
        """Normalize technology name (source-specific). Returns (canonical, is_mapped)"""
        if pd.isna(tech_raw):
            return None, False

        tech = str(tech_raw).lower().strip()

        if 'solar' in tech:
            if 'pv' in tech or 'photovoltaic' in tech or 'keymark' in tech:
                return 'Solar PV', True
            elif 'heating' in tech:
                return None, False
            return 'Solar PV', True

        if 'heat pump' in tech or 'heatpump' in tech:
            return 'Heat Pump', True

        if 'battery' in tech or 'storage' in tech or 'energy storage' in tech:
            return 'Battery Storage', True

        if 'ev' in tech or 'charging' in tech or 'electric vehicle' in tech or 'charge point' in tech:
            return 'EV Charging', True

        if 'v2g' in tech or 'vehicle to grid' in tech:
            return 'V2G', True

        return None, False

    def parse_capacity(self, capacity_value, capacity_unit=None, context=None):
        """Parse capacity and return (capacity_kw, capacity_raw, status)"""
        if pd.isna(capacity_value) or capacity_value == '':
            return None, None, 'MISSING'

        capacity_str = str(capacity_value).strip()
        numeric_match = re.search(r'[\d,]+\.?\d*', capacity_str.replace(',', ''))
        if not numeric_match:
            return None, capacity_str, 'INVALID'

        try:
            numeric_value = float(numeric_match.group().replace(',', ''))
        except:
            return None, capacity_str, 'INVALID'

        capacity_lower = capacity_str.lower()

        if 'mw' in capacity_lower:
            return numeric_value * 1000, capacity_str, 'RESOLVED'
        elif 'w' in capacity_lower and 'kw' not in capacity_lower:
            return numeric_value / 1000, capacity_str, 'RESOLVED'
        else:
            return numeric_value, capacity_str, 'RESOLVED'

    def normalize_date(self, date_value):
        """Parse date and return (datetime_obj, is_valid)"""
        if pd.isna(date_value) or date_value == '':
            return None, False

        date_str = str(date_value).strip()
        for fmt in ['%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d']:
            try:
                return pd.to_datetime(date_str, format=fmt), True
            except:
                pass

        try:
            return pd.to_datetime(date_str), True
        except:
            return None, False

    def create_canonical_row(self, source, source_file, source_row_id, **kwargs):
        """Create canonical observation row"""
        row = {
            'source': source,
            'source_file': source_file,
            'source_row_id': source_row_id,
            'source_reference_id': kwargs.get('source_reference_id'),
            'mpan': kwargs.get('mpan'),
            'postcode_std': kwargs.get('postcode_std'),
            'lsoa21cd': kwargs.get('lsoa21cd'),
            'licence_area': kwargs.get('licence_area'),
            'spatial_licence_area': kwargs.get('spatial_licence_area'),
            'geography_status': kwargs.get('geography_status'),
            'ukpn_eligible': kwargs.get('ukpn_eligible'),
            'technology_raw': kwargs.get('technology_raw'),
            'technology_canonical': kwargs.get('technology_canonical'),
            'technology_detail': kwargs.get('technology_detail'),
            'technology_status': kwargs.get('technology_status'),
            'event_date_raw': kwargs.get('event_date_raw'),
            'event_date': kwargs.get('event_date'),
            'reporting_period': kwargs.get('reporting_period'),
            'date_status': kwargs.get('date_status'),
            'capacity_raw': kwargs.get('capacity_raw'),
            'capacity_value': kwargs.get('capacity_value'),
            'capacity_unit': kwargs.get('capacity_unit'),
            'capacity_kw': kwargs.get('capacity_kw'),
            'capacity_type': kwargs.get('capacity_type'),
            'capacity_status': kwargs.get('capacity_status'),
            'source_status': kwargs.get('source_status', 'NORMAL'),
        }
        return row

    def process_mcs_files(self):
        """Process all 21 unique MCS monthly CSV files"""
        print("\n--- Processing MCS (21 unique monthly files) ---")

        mcs_dir = os.path.join(self.data_dir, 'MCS')
        if not os.path.exists(mcs_dir):
            print("✗ MCS directory not found")
            return

        mcs_files = sorted([f for f in glob.glob(os.path.join(mcs_dir, '*.csv'))])
        print(f"Found {len(mcs_files)} MCS CSV files")

        for file_idx, file_path in enumerate(mcs_files, 1):
            try:
                filename = os.path.basename(file_path)
                print(f"  [{file_idx}/{len(mcs_files)}] {filename}...", end=" ", flush=True)

                df = pd.read_csv(file_path)
                self.audit['MCS']['files_processed'] += 1
                self.audit['MCS']['raw'] += len(df)

                for idx, row in df.iterrows():
                    postcode = row.get('Postcode')
                    mpan = row.get('MPAN')
                    tech_raw = row.get('Technology Type')
                    capacity_raw = row.get('Total Installed Capacity')
                    comm_date_raw = row.get('Commissioning Date')

                    licence_area, lsoa21cd, geo_status, ukpn_eligible = self.assign_geography(postcode, source='MCS')

                    if not ukpn_eligible:
                        continue

                    self.audit['MCS']['ukpn_eligible'] += 1

                    tech_canonical, is_mapped = self.normalize_technology(tech_raw, 'MCS')
                    if tech_canonical:
                        self.canonical_audit['MCS']['tech_mapped'] += 1

                    capacity_kw, capacity_str, capacity_status = self.parse_capacity(capacity_raw)
                    if capacity_status == 'RESOLVED':
                        self.canonical_audit['MCS']['capacity_resolved'] += 1

                    event_date, date_valid = self.normalize_date(comm_date_raw)
                    reporting_period = f"{pd.to_datetime(comm_date_raw).strftime('%Y-%m')}" if date_valid else None

                    canonical_row = self.create_canonical_row(
                        source='MCS',
                        source_file=filename,
                        source_row_id=idx,
                        source_reference_id=str(mpan) if mpan else None,
                        mpan=str(mpan) if mpan else None,
                        postcode_std=self.standardize_postcode(postcode),
                        lsoa21cd=lsoa21cd,
                        licence_area=licence_area,
                        geography_status=geo_status,
                        ukpn_eligible=True,
                        technology_raw=tech_raw,
                        technology_canonical=tech_canonical,
                        technology_status='MAPPED' if is_mapped else 'UNMAPPED',
                        event_date_raw=comm_date_raw,
                        event_date=event_date,
                        reporting_period=reporting_period,
                        date_status='VALID' if date_valid else 'INVALID',
                        capacity_raw=capacity_raw,
                        capacity_value=capacity_kw,
                        capacity_unit='kW',
                        capacity_kw=capacity_kw,
                        capacity_type='INSTALLED_CAPACITY',
                        capacity_status=capacity_status,
                    )

                    self.canonical_observations.append(canonical_row)
                    self.canonical_audit['MCS']['canonical_rows'] += 1

                print(f"({len(df)} rows, {self.audit['MCS']['ukpn_eligible']} UKPN cumulative)")

            except Exception as e:
                print(f"\n✗ ERROR processing {filename}: {e}")

    def process_lct_register(self):
        """Process LCT Register - PRESERVE both Generation_Rating and Import_Rating"""
        print("\n--- Processing LCT Register ---")

        file_path = os.path.join(self.data_dir, 'LCT Register.csv')
        if not os.path.exists(file_path):
            print("✗ LCT Register not found")
            return

        try:
            df = pd.read_csv(file_path)
            filename = os.path.basename(file_path)
            self.audit['LCT_Register']['raw'] = len(df)

            for idx, row in df.iterrows():
                postcode = row.get('MPAN_Postcode')
                mpan = row.get('MPAN')
                native_licence = row.get('DNO')
                tech_raw = row.get('Type')
                gen_rating = row.get('Generation_Rating')
                imp_rating = row.get('Import_Rating')
                install_date_raw = row.get('Installation_Date')

                licence_area, lsoa21cd, geo_status, ukpn_eligible = self.assign_geography(
                    postcode, native_licence=native_licence, source='LCT_Register'
                )

                if not ukpn_eligible:
                    continue

                self.audit['LCT_Register']['ukpn_eligible'] += 1

                tech_canonical, is_mapped = self.normalize_technology(tech_raw, 'LCT_Register')
                if tech_canonical:
                    self.canonical_audit['LCT_Register']['tech_mapped'] += 1

                capacity_kw = None
                capacity_status = 'MULTIPLE_RAW_FIELDS'
                capacity_type = 'GENERATION_AND_IMPORT_RATINGS'

                if pd.notna(gen_rating) and pd.notna(imp_rating):
                    self.canonical_audit['LCT_Register']['generation_rating_used'] += 1
                    self.canonical_audit['LCT_Register']['import_rating_used'] += 1
                    capacity_raw = f"Gen:{gen_rating} Imp:{imp_rating}"
                elif pd.notna(gen_rating):
                    self.canonical_audit['LCT_Register']['generation_rating_used'] += 1
                    capacity_raw = f"Gen:{gen_rating}"
                    capacity_kw = gen_rating if isinstance(gen_rating, (int, float)) else None
                    capacity_status = 'RESOLVED' if capacity_kw else 'INVALID'
                elif pd.notna(imp_rating):
                    self.canonical_audit['LCT_Register']['import_rating_used'] += 1
                    capacity_raw = f"Imp:{imp_rating}"
                    capacity_kw = imp_rating if isinstance(imp_rating, (int, float)) else None
                    capacity_status = 'RESOLVED' if capacity_kw else 'INVALID'
                else:
                    capacity_raw = None
                    capacity_status = 'MISSING'

                event_date, date_valid = self.normalize_date(install_date_raw)

                canonical_row = self.create_canonical_row(
                    source='LCT_REGISTER',
                    source_file=filename,
                    source_row_id=idx,
                    source_reference_id=str(mpan) if mpan else None,
                    mpan=str(mpan) if mpan else None,
                    postcode_std=self.standardize_postcode(postcode),
                    lsoa21cd=lsoa21cd,
                    licence_area=licence_area,
                    geography_status=geo_status,
                    ukpn_eligible=True,
                    technology_raw=tech_raw,
                    technology_canonical=tech_canonical,
                    technology_status='MAPPED' if is_mapped else 'UNMAPPED',
                    event_date_raw=install_date_raw,
                    event_date=event_date,
                    date_status='VALID' if date_valid else 'INVALID',
                    capacity_raw=capacity_raw,
                    capacity_value=None,
                    capacity_unit='kW',
                    capacity_kw=capacity_kw,
                    capacity_type=capacity_type,
                    capacity_status=capacity_status,
                    source_status='NORMAL',
                )

                self.canonical_observations.append(canonical_row)
                self.canonical_audit['LCT_Register']['canonical_rows'] += 1

            print(f"  {self.audit['LCT_Register']['raw']} raw → {self.audit['LCT_Register']['ukpn_eligible']} UKPN-eligible → {self.canonical_audit['LCT_Register']['canonical_rows']} canonical")

        except Exception as e:
            print(f"✗ ERROR processing LCT Register: {e}")

    def process_ecr_large(self):
        """Process ECR Large - ONE CANONICAL ROW PER ENERGY SOURCE"""
        print("\n--- Processing ECR Large (>1 MW) ---")

        file_path = os.path.join(self.data_dir, 'ecr_large.csv')
        if not os.path.exists(file_path):
            print("✗ ECR Large not found")
            return

        try:
            df = pd.read_csv(file_path)
            filename = os.path.basename(file_path)
            self.audit['ECR_Large']['raw'] = len(df)

            for idx, row in df.iterrows():
                postcode = row.get('Postcode')
                native_licence = row.get('Licence Area')

                licence_area, lsoa21cd, geo_status, ukpn_eligible = self.assign_geography(
                    postcode, native_licence=native_licence, source='ECR_Large'
                )

                if not ukpn_eligible:
                    continue

                self.audit['ECR_Large']['ukpn_eligible'] += 1

                multi_tech_count = 0

                for energy_src_num in [1, 2, 3]:
                    tech_raw = row.get(f'Energy Source {energy_src_num}')
                    if pd.isna(tech_raw) or tech_raw == '':
                        continue

                    multi_tech_count += 1

                    capacity_raw = row.get(f'Energy Source & Energy Conversion Technology {energy_src_num} - Registered Capacity (MW)')

                    capacity_kw = None
                    capacity_status = 'MISSING'
                    if pd.notna(capacity_raw):
                        try:
                            capacity_val = float(capacity_raw) * 1000
                            capacity_kw = capacity_val
                            capacity_status = 'RESOLVED'
                        except:
                            capacity_status = 'INVALID'

                    tech_canonical, is_mapped = self.normalize_technology(tech_raw, 'ECR_Large')
                    if tech_canonical:
                        self.canonical_audit['ECR_Large']['tech_mapped'] += 1

                    canonical_row = self.create_canonical_row(
                        source='ECR_LARGE',
                        source_file=filename,
                        source_row_id=idx,
                        mpan=None,
                        postcode_std=self.standardize_postcode(postcode),
                        lsoa21cd=lsoa21cd,
                        licence_area=licence_area,
                        geography_status=geo_status,
                        ukpn_eligible=True,
                        technology_raw=tech_raw,
                        technology_canonical=tech_canonical,
                        technology_detail=f'Energy Source {energy_src_num}',
                        technology_status='MAPPED' if is_mapped else 'UNMAPPED',
                        capacity_raw=capacity_raw,
                        capacity_value=capacity_kw,
                        capacity_unit='kW',
                        capacity_kw=capacity_kw,
                        capacity_type='REGISTERED_CAPACITY',
                        capacity_status=capacity_status,
                    )

                    self.canonical_observations.append(canonical_row)
                    self.canonical_audit['ECR_Large']['canonical_rows'] += 1

                if multi_tech_count > 1:
                    self.canonical_audit['ECR_Large']['multi_tech_rows'] += 1

            print(f"  {self.audit['ECR_Large']['raw']} raw → {self.audit['ECR_Large']['ukpn_eligible']} UKPN-eligible → {self.canonical_audit['ECR_Large']['canonical_rows']} canonical ({self.canonical_audit['ECR_Large']['multi_tech_rows']} multi-tech)")

        except Exception as e:
            print(f"✗ ERROR processing ECR Large: {e}")

    def process_ecr_small(self):
        """Process ECR Small - ONE CANONICAL ROW PER ENERGY SOURCE (50 kW–1 MW band)"""
        print("\n--- Processing ECR Small (50 kW–1 MW) ---")

        file_path = os.path.join(self.data_dir, 'ecr_small.csv')
        if not os.path.exists(file_path):
            print("✗ ECR Small not found")
            return

        try:
            df = pd.read_csv(file_path)
            filename = os.path.basename(file_path)
            self.audit['ECR_Small']['raw'] = len(df)

            for idx, row in df.iterrows():
                postcode = row.get('Postcode')
                native_licence = row.get('Licence Area ')

                licence_area, lsoa21cd, geo_status, ukpn_eligible = self.assign_geography(
                    postcode, native_licence=native_licence, source='ECR_Small'
                )

                if not ukpn_eligible:
                    continue

                self.audit['ECR_Small']['ukpn_eligible'] += 1

                multi_tech_count = 0

                for energy_src_num in [1, 2, 3]:
                    tech_raw = row.get(f'Energy Source {energy_src_num}')
                    if pd.isna(tech_raw) or tech_raw == '':
                        continue

                    multi_tech_count += 1

                    capacity_raw = row.get(f'Energy Source & Energy Conversion Technology {energy_src_num} - Registered Capacity (MW)')

                    capacity_value = None
                    capacity_unit = 'kW'
                    capacity_kw = None
                    capacity_status = 'MISSING'

                    if pd.notna(capacity_raw):
                        capacity_raw_str = str(capacity_raw).strip()
                        try:
                            capacity_mw = float(capacity_raw_str)
                            capacity_kw = capacity_mw * 1000
                            capacity_value = capacity_kw
                            capacity_status = 'RESOLVED'
                        except (ValueError, TypeError):
                            capacity_status = 'INVALID'
                        capacity_raw = capacity_raw_str
                    else:
                        capacity_raw = None

                    tech_canonical, is_mapped = self.normalize_technology(tech_raw, 'ECR_Small')

                    canonical_row = self.create_canonical_row(
                        source='ECR_SMALL',
                        source_file=filename,
                        source_row_id=idx,
                        mpan=None,
                        postcode_std=self.standardize_postcode(postcode),
                        lsoa21cd=lsoa21cd,
                        licence_area=licence_area,
                        geography_status=geo_status,
                        ukpn_eligible=True,
                        technology_raw=tech_raw,
                        technology_canonical=tech_canonical,
                        technology_detail=f'Energy Source {energy_src_num}, 50-1000 kW band',
                        technology_status='MAPPED' if is_mapped else 'UNMAPPED',
                        capacity_raw=capacity_raw,
                        capacity_value=capacity_value,
                        capacity_unit=capacity_unit,
                        capacity_kw=capacity_kw,
                        capacity_type='REGISTERED_CAPACITY',
                        capacity_status=capacity_status,
                    )

                    self.canonical_observations.append(canonical_row)
                    self.canonical_audit['ECR_Small']['canonical_rows'] += 1
                    if is_mapped:
                        self.canonical_audit['ECR_Small']['tech_mapped'] += 1

                if multi_tech_count > 1:
                    self.canonical_audit['ECR_Small']['multi_tech_rows'] += 1

            print(f"  {self.audit['ECR_Small']['raw']} raw → {self.audit['ECR_Small']['ukpn_eligible']} UKPN-eligible → {self.canonical_audit['ECR_Small']['canonical_rows']} canonical ({self.canonical_audit['ECR_Small']['multi_tech_rows']} multi-tech)")

        except Exception as e:
            print(f"✗ ERROR processing ECR Small: {e}")

    def process_zapmap(self):
        """Process ZapMap - CONNECTOR LEVEL GRAIN with actual power priority"""
        print("\n--- Processing ZapMap (Public EV Charging) ---")

        file_path = os.path.join(self.data_dir, 'zapmap.csv')
        if not os.path.exists(file_path):
            print("✗ ZapMap not found")
            return

        try:
            df = pd.read_csv(file_path)
            filename = os.path.basename(file_path)
            self.audit['ZapMap']['raw'] = len(df)

            for idx, row in df.iterrows():
                postcode = row.get('postal_code')

                licence_area, lsoa21cd, geo_status, ukpn_eligible = self.assign_geography(postcode, source='ZapMap')

                if not ukpn_eligible:
                    continue

                self.audit['ZapMap']['ukpn_eligible'] += 1

                connector_power_raw = row.get('connector_power_kw')
                power_band_raw = row.get('power_band_name')

                capacity_kw = None
                capacity_status = 'MISSING'
                capacity_type = 'UNKNOWN'

                if pd.notna(connector_power_raw) and connector_power_raw > 0:
                    try:
                        capacity_kw = float(connector_power_raw)
                        capacity_status = 'RESOLVED'
                        capacity_type = 'ACTUAL_CONNECTOR_POWER'
                        self.canonical_audit['ZapMap']['actual_power'] += 1
                    except:
                        pass

                if capacity_kw is None and pd.notna(power_band_raw):
                    power_band_lower = str(power_band_raw).lower().strip()
                    if power_band_lower in self.zapmap_power_band_mapping:
                        capacity_kw = self.zapmap_power_band_mapping[power_band_lower]
                        capacity_status = 'RESOLVED'
                        capacity_type = 'CATEGORY_ASSUMPTION'
                        self.canonical_audit['ZapMap']['category_assumption'] += 1

                capacity_raw = str(connector_power_raw) if pd.notna(connector_power_raw) else power_band_raw

                canonical_row = self.create_canonical_row(
                    source='ZAPMAP',
                    source_file=filename,
                    source_row_id=idx,
                    source_reference_id=str(row.get('zapmap_connector_uid')) if pd.notna(row.get('zapmap_connector_uid')) else None,
                    mpan=None,
                    postcode_std=self.standardize_postcode(postcode),
                    lsoa21cd=lsoa21cd,
                    licence_area=licence_area,
                    geography_status=geo_status,
                    ukpn_eligible=True,
                    technology_raw='Public EV Charging',
                    technology_canonical='EV Charging',
                    technology_detail='Public charging point',
                    technology_status='MAPPED',
                    capacity_raw=capacity_raw,
                    capacity_value=capacity_kw,
                    capacity_unit='kW',
                    capacity_kw=capacity_kw,
                    capacity_type=capacity_type,
                    capacity_status=capacity_status,
                )

                self.canonical_observations.append(canonical_row)
                self.canonical_audit['ZapMap']['canonical_rows'] += 1

            print(f"  {self.audit['ZapMap']['raw']} raw → {self.audit['ZapMap']['ukpn_eligible']} UKPN-eligible → {self.canonical_audit['ZapMap']['canonical_rows']} canonical")

        except Exception as e:
            print(f"✗ ERROR processing ZapMap: {e}")

    def process_device_report(self):
        """Process Device Report - METHODOLOGY ROLE TO CONFIRM"""
        print("\n--- Processing Device Report (Connect Direct) ---")

        file_path = os.path.join(self.data_dir, 'Device-Report (2).csv')
        if not os.path.exists(file_path):
            print("⚠  Device Report not found")
            return

        try:
            df = pd.read_csv(file_path)
            filename = os.path.basename(file_path)
            self.audit['Device_Report']['raw'] = len(df)

            for idx, row in df.iterrows():
                premise_address = row.get('Premise Address')
                postcode = self.extract_postcode_from_address(premise_address)
                mpan = row.get('MPAN')
                tech_raw = row.get('Technology Type')
                capacity_raw = row.get('Export Capacity Total kW')
                app_date_raw = row.get('Application Date Created')

                licence_area, lsoa21cd, geo_status, ukpn_eligible = self.assign_geography(postcode, source='Device_Report')

                if not ukpn_eligible:
                    continue

                self.audit['Device_Report']['ukpn_eligible'] += 1

                tech_canonical, is_mapped = self.normalize_technology(tech_raw, 'Device_Report')

                capacity_kw = None
                capacity_status = 'INVALID'
                try:
                    capacity_kw = float(capacity_raw) if pd.notna(capacity_raw) else None
                    if capacity_kw is not None:
                        capacity_status = 'RESOLVED'
                except:
                    pass

                event_date, date_valid = self.normalize_date(app_date_raw)

                canonical_row = self.create_canonical_row(
                    source='DEVICE_REPORT',
                    source_file=filename,
                    source_row_id=idx,
                    source_reference_id=str(row.get('Application Number')) if pd.notna(row.get('Application Number')) else None,
                    mpan=str(mpan) if pd.notna(mpan) else None,
                    postcode_std=self.standardize_postcode(postcode),
                    lsoa21cd=lsoa21cd,
                    licence_area=licence_area,
                    geography_status=geo_status,
                    ukpn_eligible=True,
                    technology_raw=tech_raw,
                    technology_canonical=tech_canonical,
                    technology_status='MAPPED' if is_mapped else 'UNMAPPED',
                    event_date_raw=app_date_raw,
                    event_date=event_date,
                    date_status='VALID' if date_valid else 'INVALID',
                    capacity_raw=capacity_raw,
                    capacity_value=capacity_kw,
                    capacity_unit='kW',
                    capacity_kw=capacity_kw,
                    capacity_type='EXPORT_CAPACITY',
                    capacity_status=capacity_status,
                    source_status='DATA_AVAILABLE — METHODOLOGY_ROLE_TO_CONFIRM',
                )

                self.canonical_observations.append(canonical_row)
                self.canonical_audit['Device_Report']['canonical_rows'] += 1

            print(f"  {self.audit['Device_Report']['raw']} raw → {self.audit['Device_Report']['ukpn_eligible']} UKPN-eligible → {self.canonical_audit['Device_Report']['canonical_rows']} canonical")
            print("  [source_status = DATA_AVAILABLE — METHODOLOGY_ROLE_TO_CONFIRM]")

        except Exception as e:
            print(f"✗ ERROR processing Device Report: {e}")

    def write_canonical_output(self):
        """Write canonical observations to CSV"""
        print("\n--- Writing Canonical Output ---")

        if not self.canonical_observations:
            print("✗ No canonical observations to write")
            return

        df_canonical = pd.DataFrame(self.canonical_observations)
        output_path = os.path.join(self.output_dir, 'canonical_lct_observations.csv')

        try:
            df_canonical.to_csv(output_path, index=False)
            print(f"✓ Written {len(df_canonical)} canonical observations to {output_path}")
        except Exception as e:
            print(f"✗ ERROR writing output: {e}")

    def print_canonical_audit(self):
        """Print comprehensive canonical audit"""
        print("\n" + "="*100)
        print("STAGE 2B CANONICAL NORMALIZATION AUDIT")
        print("="*100)

        print("\nStage 1 (Geographic Assignment) - Cumulative:")
        for source in ['MCS', 'LCT_Register', 'ECR_Large', 'ECR_Small', 'ZapMap', 'Device_Report']:
            audit = self.audit[source]
            print(f"\n{source}:")
            print(f"  Raw records:           {audit['raw']:>10,}")
            print(f"  UKPN-eligible:         {audit['ukpn_eligible']:>10,}")
            if source == 'MCS':
                print(f"  Files processed:       {audit['files_processed']:>10}")

        print("\n" + "-"*100)
        print("Stage 2B (Canonical Normalization):")

        for source in ['MCS', 'LCT_Register', 'ECR_Large', 'ECR_Small', 'ZapMap', 'Device_Report']:
            can_audit = self.canonical_audit[source]
            print(f"\n{source}:")
            print(f"  Canonical rows:        {can_audit['canonical_rows']:>10,}")
            if source in ['MCS', 'LCT_Register', 'ECR_Large', 'ECR_Small']:
                if 'tech_mapped' in can_audit:
                    print(f"  Technology mapped:     {can_audit['tech_mapped']:>10,}")
            if 'capacity_resolved' in can_audit:
                print(f"  Capacity resolved:     {can_audit['capacity_resolved']:>10,}")
            if source == 'ECR_Large' or source == 'ECR_Small':
                print(f"  Multi-tech rows:       {can_audit['multi_tech_rows']:>10}")
            if source == 'LCT_Register':
                print(f"  Generation Rating used:{can_audit['generation_rating_used']:>10}")
                print(f"  Import Rating used:    {can_audit['import_rating_used']:>10}")
            if source == 'ZapMap':
                print(f"  Actual power used:     {can_audit['actual_power']:>10,}")
                print(f"  Category assumption:   {can_audit['category_assumption']:>10,}")

        print("\n" + "="*100)
        total_canonical = sum(self.canonical_audit[s]['canonical_rows'] for s in self.canonical_audit)
        print(f"TOTAL CANONICAL ROWS: {total_canonical:,}")
        print("(Before deduplication or source precedence)")
        print("="*100)

    def run(self):
        """Run Stage 1 + Stage 2B processing"""
        print("\n" + "="*100)
        print("LCT DASHBOARD PIPELINE: Stage 1 (Geographic) + Stage 2B (Canonical Normalization)")
        print("="*100)

        try:
            self.load_postcode_lookups()

            self.process_mcs_files()
            self.process_lct_register()
            self.process_ecr_large()
            self.process_ecr_small()
            self.process_zapmap()
            self.process_device_report()

            self.write_canonical_output()
            self.print_canonical_audit()

            print("\n✓ Pipeline complete")

        except Exception as e:
            print(f"\n✗ FATAL ERROR: {e}")
            raise

if __name__ == '__main__':
    processor = LCTCanonicalProcessor(data_dir='lct', output_dir='output')
    processor.run()
