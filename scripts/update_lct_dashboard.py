#!/usr/bin/env python3
"""
LCT Dashboard Automation Script
Automates PowerBI LCT dashboard from 6 different data sources
"""

import pandas as pd
import numpy as np
import os
import glob
import re
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

class LCTDashboardProcessor:
    def __init__(self, data_dir="data", output_dir="output"):
        self.data_dir = data_dir
        self.output_dir = output_dir
        self.postcode_lookup = None
        self.aggregated_data = []
        
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
        
        # License area mapping from MPAN prefixes
        self.mpan_license_mapping = {
            '10': 'EPN',
            '12': 'LPN', 
            '19': 'SPN'
        }
        
        print("🚀 LCT Dashboard Processor initialized")
        
    def load_postcode_lookup(self):
        """Load postcode to license area mapping"""
        try:
            self.postcode_lookup = pd.read_csv('postcode_lookup.csv')
            print("✅ Postcode lookup loaded successfully")
        except Exception as e:
            print(f"❌ Error loading postcode lookup: {e}")
            self.postcode_lookup = None
    
    def standardize_technology_name(self, tech_name):
        """Standardize technology names"""
        if pd.isna(tech_name):
            return None
        
        tech_lower = str(tech_name).lower().strip()
        for key, value in self.tech_mapping.items():
            if key in tech_lower:
                return value
        return tech_name
    
    def parse_capacity_to_kw(self, capacity_value, source=None):
        """Parse capacity values and convert to kW"""
        if pd.isna(capacity_value):
            return 0.0
        
        capacity_str = str(capacity_value).strip()
        
        # Extract numeric value
        numeric_match = re.search(r'[\d,]+\.?\d*', capacity_str.replace(',', ''))
        if not numeric_match:
            return 0.0
        
        numeric_value = float(numeric_match.group().replace(',', ''))
        
        # Determine unit and convert to kW
        capacity_lower = capacity_str.lower()
        if 'mw' in capacity_lower:
            return numeric_value * 1000  # MW to kW
        elif 'w' in capacity_lower and 'kw' not in capacity_lower:
            return numeric_value / 1000  # W to kW
        elif source in ['ECR_GT_1MW', 'ECR_LT_1MW']:
            # ECR data is in MW, convert to kW
            return numeric_value * 1000
        else:
            # Default case - assume kW
            return numeric_value
    
    def get_license_area_from_mpan(self, mpan):
        """Get license area from MPAN prefix"""
        if pd.isna(mpan):
            return None
        
        mpan_str = str(mpan).strip()
        if len(mpan_str) >= 2:
            prefix = mpan_str[:2]
            return self.mpan_license_mapping.get(prefix)
        return None
    
    def get_license_area_from_postcode(self, postcode):
        """Get license area from postcode using lookup table"""
        if pd.isna(postcode) or self.postcode_lookup is None:
            return None
        
        postcode_str = str(postcode).strip().upper()
        
        # Extract postcode prefix (first 2-3 characters)
        prefix_match = re.match(r'^([A-Z]{1,3})', postcode_str)
        if not prefix_match:
            return None
        
        prefix = prefix_match.group(1)
        
        # Look up in postcode mapping
        for _, row in self.postcode_lookup.iterrows():
            if pd.notna(row['LPN']) and prefix in str(row['LPN']):
                return 'LPN'
            elif pd.notna(row['SPN']) and prefix in str(row['SPN']):
                return 'SPN'
            elif pd.notna(row['EPN']) and prefix in str(row['EPN']):
                return 'EPN'
        
        return None
    
    def assign_license_area(self, mpan, postcode):
        """Assign license area using MPAN first, then postcode"""
        # Try MPAN first
        license_area = self.get_license_area_from_mpan(mpan)
        if license_area:
            return license_area
        
        # Fall back to postcode
        return self.get_license_area_from_postcode(postcode)
    
    def extract_month_from_date(self, date_value):
        """Extract month from date column"""
        if pd.isna(date_value):
            return None
        
        try:
            if isinstance(date_value, str):
                # Try different date formats
                for fmt in ['%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%Y-%m-%d %H:%M:%S', '%d/%m/%Y %H:%M', 
                           '%d/%m/%Y %H:%M:%S', '%d-%m-%Y', '%Y/%m/%d', '%d.%m.%Y', '%d %m %Y']:
                    try:
                        date_obj = datetime.strptime(date_value, fmt)
                        return date_obj.strftime('%Y-%m')
                    except ValueError:
                        continue
                else:
                    # Assume it's already a datetime
                    return pd.to_datetime(date_value).strftime('%Y-%m')
            else:
                # Assume it's already a datetime
                return pd.to_datetime(date_value).strftime('%Y-%m')
        except:
            pass
        
        return None
    
    def process_mcs_data(self):
        """Process MCS monthly data files"""
        print("\n📊 Processing MCS data...")
        
        mcs_dir = os.path.join(self.data_dir, 'mcs')
        if not os.path.exists(mcs_dir):
            print("❌ MCS directory not found")
            return
        
        mcs_files = glob.glob(os.path.join(mcs_dir, '*.xlsx'))
        print(f"Found {len(mcs_files)} MCS files")
        
        for file_path in mcs_files:
            try:
                # Extract month from filename
                filename = os.path.basename(file_path)
                month_match = re.search(r'(\w+)\s+(\d{4})', filename)
                if month_match:
                    month_name = month_match.group(1)
                    year = month_match.group(2)
                    month_num = datetime.strptime(month_name, '%B').month
                    month = f"{year}-{month_num:02d}"
                else:
                    print(f"⚠️ Could not extract month from {filename}")
                    continue
                
                print(f"Processing {filename} -> {month}")
                
                # Read Excel file
                df = pd.read_excel(file_path)
                
                # Process each row
                for _, row in df.iterrows():
                    # Extract technology type from 'Technology Type' column
                    tech_name = self.standardize_technology_name(row.get('Technology Type'))
                    if not tech_name:
                        continue
                    
                    # Extract capacity from 'Total Installed Capacity' column
                    capacity_kw = self.parse_capacity_to_kw(row.get('Total Installed Capacity'))
                    
                    # For battery storage, use 'Battery Max AC Power Output' if available
                    if tech_name == 'Battery Storage' and pd.notna(row.get('Battery Max AC Power Output')):
                        battery_capacity = self.parse_capacity_to_kw(row.get('Battery Max AC Power Output'))
                        if battery_capacity > 0:
                            capacity_kw = battery_capacity
                    
                    # Extract MPAN and postcode
                    mpan = row.get('MPAN')
                    postcode = row.get('Postcode')
                    
                    # Assign license area
                    license_area = self.assign_license_area(mpan, postcode)
                    
                    # Extract month from Commissioning Date
                    commissioning_date = row.get('Commissioning Date')
                    if pd.notna(commissioning_date):
                        date_month = self.extract_month_from_date(commissioning_date)
                        if date_month:
                            month = date_month
                    
                    # Apply MCS-specific rules
                    if tech_name == 'Heat Pump':
                        g99_status = 'N/A'  # Not applicable for non-generation
                    else:
                        g99_status = 'G99' if capacity_kw > 3.68 else 'G98'
                    connection_level = 'Secondary'
                    
                    # Extract domestic/commercial classification from Installation Type
                    installation_type = str(row.get('Installation Type', '')).lower()
                    if 'domestic' in installation_type:
                        domestic_commercial = 'Domestic'
                    elif 'commercial' in installation_type or 'business' in installation_type:
                        domestic_commercial = 'Commercial'
                    else:
                        domestic_commercial = 'Unknown'
                    
                    # Add to aggregated data
                    self.aggregated_data.append({
                        'month': month,
                        'technology': tech_name,
                        'license_area': license_area,
                        'g99_status': g99_status,
                        'connection_level': connection_level,
                        'capacity_kw': capacity_kw,
                        'source': 'MCS',
                        'mpan': mpan,
                        'domestic_commercial': domestic_commercial
                    })
                    
            except Exception as e:
                print(f"❌ Error processing {file_path}: {e}")
        
        print(f"✅ Processed MCS data: {len([d for d in self.aggregated_data if d['source'] == 'MCS'])} records")
    
    def process_ecr_gt_1mw(self):
        """Process ECR >1MW data"""
        print("\n📊 Processing ECR >1MW data...")
        
        file_path = os.path.join(self.data_dir, 'ecr_gt_1mw.xlsx')
        if not os.path.exists(file_path):
            print("❌ ECR >1MW file not found")
            return
        
        try:
            df = pd.read_excel(file_path)
            
            for _, row in df.iterrows():
                # Filter for connected status only
                connection_status = row.get('Connection Status')
                if pd.notna(connection_status) and str(connection_status).strip().lower() != 'connected':
                    continue
                
                # Extract technology from 'Energy Conversion Technology 1' and 'Primary Resource Type_Group'
                tech_name = None
                if pd.notna(row.get('Energy Conversion Technology 1')):
                    tech_name = self.standardize_technology_name(row.get('Energy Conversion Technology 1'))
                elif pd.notna(row.get('Primary Resource Type_Group')):
                    tech_name = self.standardize_technology_name(row.get('Primary Resource Type_Group'))
                
                if not tech_name:
                    continue
                
                # Extract capacity from 'Already connected Registered Capacity (MW)'
                raw_capacity = row.get('Already connected Registered Capacity (MW)')
                capacity_kw = self.parse_capacity_to_kw(raw_capacity, 'ECR_GT_1MW')
                
                # Debug: Print first few ECR >1MW records to check capacity parsing
                if len(self.aggregated_data) < 5:  # Only print first 5 records
                    print(f"ECR >1MW Debug - Raw capacity: {raw_capacity} -> Parsed: {capacity_kw} kW")
                
                # Extract MPAN and postcode
                mpan = row.get('Export MPAN / MSID')
                postcode = row.get('Postcode')
                
                # Assign license area
                license_area = self.assign_license_area(mpan, postcode)
                
                # Extract month from 'Date Connected'
                month = self.extract_month_from_date(row.get('Date Connected'))
                if not month:
                    continue
                
                # Apply ECR >1MW rules
                voltage = str(row.get('Point of Connection (POC)_Voltage (kV)', '')).upper()
                connection_level = 'Primary'
                if 'EHV' in voltage or 'HV' in voltage:
                    connection_level = 'Primary'
                else:
                    connection_level = 'Secondary'
                
                g99_status = 'G99'  # All ECR >1MW are G99
                
                self.aggregated_data.append({
                    'month': month,
                    'technology': tech_name,
                    'license_area': license_area,
                    'g99_status': g99_status,
                    'connection_level': connection_level,
                    'capacity_kw': capacity_kw,
                    'source': 'ECR_GT_1MW',
                    'mpan': mpan,
                    'domestic_commercial': 'Commercial'  # ECR are all commercial
                })
                    
        except Exception as e:
            print(f"❌ Error processing ECR >1MW: {e}")
        
        print(f"✅ Processed ECR >1MW data: {len([d for d in self.aggregated_data if d['source'] == 'ECR_GT_1MW'])} records")
    
    def process_ecr_lt_1mw(self):
        """Process ECR <1MW data"""
        print("\n📊 Processing ECR <1MW data...")
        
        file_path = os.path.join(self.data_dir, 'ecr_lt_1mw.xlsx')
        if not os.path.exists(file_path):
            print("❌ ECR <1MW file not found")
            return
        
        try:
            df = pd.read_excel(file_path)
            
            for _, row in df.iterrows():
                # Filter for connected status only
                connection_status = row.get('Connection Status')
                if pd.notna(connection_status) and str(connection_status).strip().lower() != 'connected':
                    continue
                
                # Extract technology from 'Energy Conversion Technology 1' and 'Primary Resource Type_Group'
                tech_name = None
                if pd.notna(row.get('Energy Conversion Technology 1')):
                    tech_name = self.standardize_technology_name(row.get('Energy Conversion Technology 1'))
                elif pd.notna(row.get('Primary Resource Type_Group')):
                    tech_name = self.standardize_technology_name(row.get('Primary Resource Type_Group'))
                
                if not tech_name:
                    continue
            
                # Extract capacity from 'Already connected Registered Capacity (MW)'
                capacity_kw = self.parse_capacity_to_kw(row.get('Already connected Registered Capacity (MW)'), 'ECR_LT_1MW')
                
                # Extract MPAN and postcode
                mpan = row.get('Export MPAN / MSID')
                postcode = row.get('Postcode')
                
                # Assign license area
                license_area = self.assign_license_area(mpan, postcode)
                
                # Extract month from 'Date Connected'
                month = self.extract_month_from_date(row.get('Date Connected'))
                if not month:
                    continue
                
                # Apply ECR <1MW rules - all are Primary
                connection_level = 'Primary'
                g99_status = 'G99'  # All ECR are G99
                
                self.aggregated_data.append({
                    'month': month,
                    'technology': tech_name,
                    'license_area': license_area,
                    'g99_status': g99_status,
                    'connection_level': connection_level,
                    'capacity_kw': capacity_kw,
                    'source': 'ECR_LT_1MW',
                    'mpan': mpan,
                    'domestic_commercial': 'Commercial'  # ECR are all commercial
                })
        
        except Exception as e:
            print(f"❌ Error processing ECR <1MW: {e}")
        
        print(f"✅ Processed ECR <1MW data: {len([d for d in self.aggregated_data if d['source'] == 'ECR_LT_1MW'])} records")
    
    def process_lct_register(self):
        """Process LCT Register data"""
        print("\n📊 Processing LCT Register data...")
        
        file_path = os.path.join(self.data_dir, 'lct_register.xlsx')
        if not os.path.exists(file_path):
            print("❌ LCT Register file not found")
            return
        
        try:
            df = pd.read_excel(file_path)
            
            for _, row in df.iterrows():
                # Filter for connected status only
                status = row.get('Status')
                if pd.notna(status) and str(status).strip().lower() != 'connected':
                    continue
                
                # Extract technology from 'Type' column
                tech_name = self.standardize_technology_name(row.get('Type'))
                if not tech_name:
                    continue
                
                # Extract capacity from 'Generation_Rating' column
                capacity_kw = self.parse_capacity_to_kw(row.get('Generation_Rating'))
                
                # Extract MPAN and postcode
                mpan = row.get('MPAN')
                postcode = row.get('MPAN_Postcode')
                
                # Assign license area
                license_area = self.assign_license_area(mpan, postcode)
                
                # Extract month from 'Installation_Date' or 'Commissioning_Date'
                month = self.extract_month_from_date(row.get('Installation_Date'))
                if not month:
                    month = self.extract_month_from_date(row.get('Commissioning_Date'))
                
                if not month:
                    continue
                
                # Apply LCT Register rules
                if tech_name in ['Solar PV', 'Battery Storage']:
                    g99_status = 'G99' if capacity_kw > 3.68 else 'G98'
                elif tech_name == 'EV Charging':
                    if capacity_kw <= 3.68:
                        g99_status = 'Slow'
                    elif capacity_kw <= 7:
                        g99_status = 'Fast'
                    else:
                        g99_status = 'Public'
                elif tech_name == 'Heat Pump':
                    g99_status = 'N/A'  # Not applicable for non-generation
                else:
                    g99_status = 'G99'
                
                # Skip EV chargers >7kW as they're public
                if tech_name == 'EV Charging' and capacity_kw > 7:
                    continue
                
                # Extract domestic/commercial classification from Property_Type
                property_type = str(row.get('Property_Type', '')).lower()
                if 'domestic' in property_type:
                    domestic_commercial = 'Domestic'
                elif 'non domestic' in property_type or 'commercial' in property_type:
                    domestic_commercial = 'Commercial'
                else:
                    domestic_commercial = 'Unknown'
                
                connection_level = 'Secondary'  # LCT Register connections are typically Secondary
                
                self.aggregated_data.append({
                    'month': month,
                    'technology': tech_name,
                    'license_area': license_area,
                    'g99_status': g99_status,
                    'connection_level': connection_level,
                    'capacity_kw': capacity_kw,
                    'source': 'LCT_Register',
                    'mpan': mpan,
                    'domestic_commercial': domestic_commercial
                })
                
        except Exception as e:
            print(f"❌ Error processing LCT Register: {e}")
        
        print(f"✅ Processed LCT Register data: {len([d for d in self.aggregated_data if d['source'] == 'LCT_Register'])} records")
    
    def process_zapmap_data(self):
        """Process ZapMap data"""
        print("\n📊 Processing ZapMap data...")
        
        file_path = os.path.join(self.data_dir, 'zapmap.csv')
        if not os.path.exists(file_path):
            print("❌ ZapMap file not found")
            return
        
        try:
            # Try different encodings
            for encoding in ['utf-8', 'latin-1', 'cp1252']:
                try:
                    df = pd.read_csv(file_path, encoding=encoding)
                    break
                except:
                    continue
            else:
                print("❌ Could not read ZapMap file with any encoding")
                return
            
            print(f"   Total ZapMap records: {len(df)}")
            
            processed_count = 0
            skipped_no_month = 0
            skipped_no_capacity = 0
            
            for _, row in df.iterrows():
                # Extract power group and max power
                power_group = str(row.get('power_group', '')).lower()
                max_power = row.get('max_power', 0)
                
                # Derive kW by charger type from power_group
                if 'slow' in power_group:
                    capacity_kw = 3.7  # Single charger
                elif 'fast' in power_group:
                    capacity_kw = 8.4  # Single charger
                elif 'rapid' in power_group:
                    capacity_kw = 43   # Single charger
                else:
                    # Use max_power if available (in watts, convert to kW)
                    if pd.notna(max_power) and max_power > 0:
                        capacity_kw = max_power / 1000  # Convert watts to kW
                    else:
                        skipped_no_capacity += 1
                        continue
                
                # Extract postcode
                postcode = row.get('postal_code')
                license_area = self.get_license_area_from_postcode(postcode)
                
                # Extract month from date_connector_added
                date_value = row.get('date_connector_added')
                month = self.extract_month_from_date(date_value)
                if not month:
                    skipped_no_month += 1
                    # Debug: show first few failed date formats
                    if skipped_no_month <= 3:
                        print(f"   Debug - Failed to parse date: '{date_value}' (type: {type(date_value)})")
                    continue
                
                # ZapMap rules - all are public chargers
                self.aggregated_data.append({
                    'month': month,
                    'technology': 'EV Charging',
                    'license_area': license_area,
                    'g99_status': 'Public',
                    'connection_level': 'Secondary',
                    'capacity_kw': capacity_kw,
                    'source': 'ZapMap',
                    'mpan': None,
                    'domestic_commercial': 'Commercial'  # ZapMap are all commercial (public chargers)
                })
                processed_count += 1
            
            print(f"   Processed: {processed_count}")
            print(f"   Skipped (no month): {skipped_no_month}")
            print(f"   Skipped (no capacity): {skipped_no_capacity}")
                
        except Exception as e:
            print(f"❌ Error processing ZapMap: {e}")
        
        print(f"✅ Processed ZapMap data: {len([d for d in self.aggregated_data if d['source'] == 'ZapMap'])} records")
    
    def process_smart_connect(self):
        """Process SmartConnect data"""
        print("\n📊 Processing SmartConnect data...")
        
        file_path = os.path.join(self.data_dir, 'smart_connect.csv')
        if not os.path.exists(file_path):
            print("❌ SmartConnect file not found")
            return
        
        try:
            # Try different encodings
            for encoding in ['utf-8', 'latin-1', 'cp1252']:
                try:
                    df = pd.read_csv(file_path, encoding=encoding)
                    break
                except:
                    continue
            else:
                print("❌ Could not read SmartConnect file with any encoding")
                return
            
            # Get existing MPANs from MCS for deduplication (non-EV technologies)
            existing_mcs_mpans = set(d['mpan'] for d in self.aggregated_data if d['source'] == 'MCS' and d['mpan'])
            
            print(f"   Total SmartConnect records: {len(df)}")
            
            processed_count = 0
            skipped_status = 0
            skipped_tech = 0
            skipped_mcs_duplicate = 0
            skipped_no_month = 0
            
            for _, row in df.iterrows():
                # Filter for completed status only (be more flexible)
                actual_status = row.get('Actual Status')
                if pd.notna(actual_status):
                    status_lower = str(actual_status).strip().lower()
                    if status_lower not in ['completed', 'connected']:
                        skipped_status += 1
                        continue
                
                # Extract technology from 'Installation Type' column
                installation_type = str(row.get('Installation Type', '')).lower()
                
                # Map installation types to standardized technology names
                if 'battery' in installation_type:
                    tech_name = 'Battery Storage'
                elif 'pv' in installation_type or 'solar' in installation_type:
                    tech_name = 'Solar PV'
                elif 'heat pump' in installation_type:
                    tech_name = 'Heat Pump'
                elif 'electric vehicle' in installation_type or 'ev' in installation_type:
                    tech_name = 'EV Charging'
                else:
                    skipped_tech += 1
                    continue
                
                # Extract capacity from 'Application Export kW' or 'Application Import kW'
                capacity_kw = 0.0
                export_kw = row.get('Application Export kW')
                import_kw = row.get('Application Import kW')
                
                if pd.notna(export_kw):
                    capacity_kw = self.parse_capacity_to_kw(export_kw)
                elif pd.notna(import_kw):
                    capacity_kw = self.parse_capacity_to_kw(import_kw)
                
                # Extract MPAN and postcode
                mpan = row.get('MPAN')
                postcode = row.get('Postcode')
                
                # Apply SmartConnect deduplication rules (non-EV only)
                if mpan and mpan in existing_mcs_mpans and tech_name in ['Solar PV', 'Heat Pump']:
                    skipped_mcs_duplicate += 1
                    continue  # Skip if MPAN exists in MCS for Solar/Heat Pump
                
                # Note: EV Charging deduplication is handled separately in deduplicate_data()
                
                # Assign license area
                license_area = self.assign_license_area(mpan, postcode)
                
                # Extract month from 'Date created' or 'Approval Date'
                month = self.extract_month_from_date(row.get('Date created'))
                if not month:
                    month = self.extract_month_from_date(row.get('Approval Date'))
                
                if not month:
                    skipped_no_month += 1
                    continue
                
                # Apply SmartConnect rules
                if tech_name == 'EV Charging':
                    if capacity_kw <= 3.68:
                        g99_status = 'Slow'
                    elif capacity_kw <= 7:
                        g99_status = 'Fast'
                    else:
                        continue  # Skip >7kW
                elif tech_name == 'Heat Pump':
                    g99_status = 'N/A'  # Not applicable for non-generation
                else:
                    g99_status = 'G99' if capacity_kw > 3.68 else 'G98'
                
                connection_level = 'Secondary'  # All SmartConnect are Secondary
                
                self.aggregated_data.append({
                    'month': month,
                    'technology': tech_name,
                    'license_area': license_area,
                    'g99_status': g99_status,
                    'connection_level': connection_level,
                    'capacity_kw': capacity_kw,
                    'source': 'SmartConnect',
                    'mpan': mpan,
                    'domestic_commercial': 'Domestic'  # SmartConnect assumed to be domestic
                })
                processed_count += 1
            
            print(f"   Processed: {processed_count}")
            print(f"   Skipped (status): {skipped_status}")
            print(f"   Skipped (tech): {skipped_tech}")
            print(f"   Skipped (MCS duplicate): {skipped_mcs_duplicate}")
            print(f"   Skipped (no month): {skipped_no_month}")
                
        except Exception as e:
            print(f"❌ Error processing SmartConnect: {e}")
        
        print(f"✅ Processed SmartConnect data: {len([d for d in self.aggregated_data if d['source'] == 'SmartConnect'])} records")
    
    def deduplicate_data(self):
        """Deduplicate data using MPAN as primary key with priority order"""
        print("\n🔄 Deduplicating data...")
        
        # Priority order: MCS > LCT Register > SmartConnect > ECR > ZapMap
        priority_order = {
            'MCS': 1,
            'LCT_Register': 2,
            'SmartConnect': 3,
            'ECR_GT_1MW': 4,
            'ECR_LT_1MW': 5,
            'ZapMap': 6
        }
        
        # Store original data for deduplication tracking
        self.original_data = self.aggregated_data.copy()
        
        # Special handling for EV Charging
        ev_charging_records = []
        non_ev_records = []
        
        # Separate EV charging records from others
        for record in self.aggregated_data:
            if record['technology'] == 'EV Charging':
                ev_charging_records.append(record)
            else:
                non_ev_records.append(record)
        
        # Process EV Charging records with special logic
        ev_deduplicated = self._deduplicate_ev_charging(ev_charging_records, priority_order)
        
        # Process non-EV records with standard deduplication
        non_ev_deduplicated = self._deduplicate_standard(non_ev_records, priority_order)
        
        # Combine results
        self.aggregated_data = non_ev_deduplicated + ev_deduplicated
        
        original_count = len(self.original_data)
        final_count = len(self.aggregated_data)
        
        print(f"✅ Deduplication complete: {original_count} -> {final_count} records")
    
    def _deduplicate_ev_charging(self, ev_records, priority_order):
        """Special deduplication logic for EV Charging records"""
        print("🔄 Applying special EV Charging deduplication logic...")
        
        # Separate by source
        lct_ev = [r for r in ev_records if r['source'] == 'LCT_Register']
        smartconnect_ev = [r for r in ev_records if r['source'] == 'SmartConnect']
        zapmap_ev = [r for r in ev_records if r['source'] == 'ZapMap']
        
        # Step 1: Deduplicate LCT Register and SmartConnect by MPAN
        private_ev_records = []
        mpan_groups = {}
        
        # Process LCT Register first (higher priority)
        for record in lct_ev:
            mpan = record['mpan']
            if mpan:
                mpan_groups[mpan] = record
            else:
                private_ev_records.append(record)
        
        # Process SmartConnect (lower priority)
        for record in smartconnect_ev:
            mpan = record['mpan']
            if mpan and mpan in mpan_groups:
                # Skip - LCT Register already has this MPAN
                continue
            elif mpan:
                mpan_groups[mpan] = record
            else:
                private_ev_records.append(record)
        
        # Add deduplicated private records
        private_ev_records.extend(mpan_groups.values())
        
        # Step 2: Filter out private chargers >7kW (assumed to be public)
        filtered_private_ev = []
        discarded_count = 0
        
        for record in private_ev_records:
            if record['capacity_kw'] > 7:
                discarded_count += 1
                print(f"   Discarding private EV charger >7kW: {record['capacity_kw']}kW from {record['source']}")
            else:
                filtered_private_ev.append(record)
        
        # Step 3: Keep ALL ZapMap records (public chargers)
        final_ev_records = filtered_private_ev + zapmap_ev
        
        print(f"   EV Charging deduplication: {len(ev_records)} -> {len(final_ev_records)} records")
        print(f"   - Private chargers (≤7kW): {len(filtered_private_ev)}")
        print(f"   - Public chargers (ZapMap): {len(zapmap_ev)}")
        print(f"   - Discarded private >7kW: {discarded_count}")
        
        return final_ev_records
    
    def _deduplicate_standard(self, records, priority_order):
        """Standard deduplication logic for non-EV records"""
        # Group by MPAN and keep highest priority
        mpan_groups = {}
        for record in records:
            mpan = record['mpan']
            source = record['source']
            
            if mpan and mpan in mpan_groups:
                # Check priority
                if priority_order[source] < priority_order[mpan_groups[mpan]['source']]:
                    mpan_groups[mpan] = record
            elif mpan:
                mpan_groups[mpan] = record
        
        # Create deduplicated list
        deduplicated_data = []
        processed_mpans = set()
        
        for record in records:
            mpan = record['mpan']
            
            if mpan and mpan in mpan_groups:
                if mpan not in processed_mpans:
                    deduplicated_data.append(mpan_groups[mpan])
                    processed_mpans.add(mpan)
            else:
                # Records without MPAN are kept
                deduplicated_data.append(record)
        
        return deduplicated_data
    
    def create_deduplication_report(self):
        """Create a detailed report of deduplication decisions"""
        print("\n📊 Creating deduplication report...")
        
        if not hasattr(self, 'original_data'):
            print("❌ No original data available for deduplication report")
            return None
    
        # Create deduplication tracking
        dedup_report = []
        
        # Create sets of final MPANs for quick lookup
        final_mpans = set()
        final_records_by_mpan = {}
        
        for record in self.aggregated_data:
            if record['mpan']:
                final_mpans.add(record['mpan'])
                final_records_by_mpan[record['mpan']] = record
        
        # Process each original record
        for record in self.original_data:
            mpan = record['mpan']
            source = record['source']
            technology = record['technology']
            
            # Determine deduplication status
            if not mpan:
                status = "KEPT (No MPAN)"
                reason = "Records without MPAN are always kept"
            elif mpan in final_mpans:
                final_record = final_records_by_mpan[mpan]
                if final_record['source'] == source:
                    status = "KEPT"
                    reason = f"Highest priority source for MPAN {mpan}"
                else:
                    status = "REMOVED"
                    reason = f"Superseded by {final_record['source']} (higher priority)"
            else:
                status = "REMOVED"
                reason = "Not in final dataset"
            
            # Special handling for EV Charging
            if technology == 'EV Charging':
                if source == 'ZapMap':
                    status = "KEPT"
                    reason = "All ZapMap records kept (public chargers)"
                elif record['capacity_kw'] > 7:
                    status = "REMOVED"
                    reason = "Private charger >7kW discarded (assumed public)"
            
            dedup_report.append({
                'mpan': mpan,
                'source': source,
                'technology': technology,
                'month': record['month'],
                'license_area': record['license_area'],
                'capacity_kw': record['capacity_kw'],
                'g99_status': record['g99_status'],
                'connection_level': record['connection_level'],
                'deduplication_status': status,
                'deduplication_reason': reason
            })
        
        # Convert to DataFrame
        dedup_df = pd.DataFrame(dedup_report)
        
        # Sort by MPAN, then by source priority
        source_priority = {'MCS': 1, 'LCT_Register': 2, 'SmartConnect': 3, 'ECR_GT_1MW': 4, 'ECR_LT_1MW': 5, 'ZapMap': 6}
        dedup_df['source_priority'] = dedup_df['source'].map(source_priority)
        dedup_df = dedup_df.sort_values(['mpan', 'source_priority'])
        dedup_df = dedup_df.drop('source_priority', axis=1)
        
        print(f"✅ Deduplication report complete: {len(dedup_df)} records")
        return dedup_df
    
    def create_domestic_commercial_breakdown(self):
        """Create domestic vs commercial breakdown of the final aggregated data"""
        print("\n📊 Creating domestic/commercial breakdown...")
        
        if not self.aggregated_data:
            print("❌ No data to create domestic/commercial breakdown")
            return None
    
        # Convert to DataFrame
        df = pd.DataFrame(self.aggregated_data)
        
        # Group by domestic/commercial + required columns
        grouped = df.groupby(['domestic_commercial', 'month', 'technology', 'license_area', 'g99_status', 'connection_level']).agg({
            'capacity_kw': ['count', 'sum']
        }).reset_index()
        
        # Flatten column names
        grouped.columns = ['domestic_commercial', 'month', 'technology', 'license_area', 'g99_status', 'connection_level', 'install_count', 'total_kw']
        
        # Convert kW to MW
        grouped['total_mw'] = grouped['total_kw'] / 1000
        
        # Drop the kW column
        grouped = grouped.drop('total_kw', axis=1)
        
        # Sort by domestic/commercial, month, technology, license_area
        grouped = grouped.sort_values(['domestic_commercial', 'month', 'technology', 'license_area', 'g99_status', 'connection_level'])
        
        print(f"✅ Domestic/commercial breakdown complete: {len(grouped)} records")
        return grouped
    
    def create_source_breakdown(self):
        """Create source breakdown before deduplication"""
        print("\n📊 Creating source breakdown...")
        
        if not self.aggregated_data:
            print("❌ No data to create breakdown")
            return None
    
        # Convert to DataFrame
        df = pd.DataFrame(self.aggregated_data)
        
        # Group by source + required columns
        grouped = df.groupby(['source', 'month', 'technology', 'license_area', 'g99_status', 'connection_level']).agg({
            'capacity_kw': ['count', 'sum']
        }).reset_index()
        
        # Flatten column names
        grouped.columns = ['source', 'month', 'technology', 'license_area', 'g99_status', 'connection_level', 'install_count', 'total_kw']
        
        # Convert kW to MW
        grouped['total_mw'] = grouped['total_kw'] / 1000
        
        # Drop the kW column
        grouped = grouped.drop('total_kw', axis=1)
        
        # Sort by source, month, technology, license_area
        grouped = grouped.sort_values(['source', 'month', 'technology', 'license_area', 'g99_status', 'connection_level'])
        
        print(f"✅ Source breakdown complete: {len(grouped)} records")
        return grouped
    
    def aggregate_final_results(self):
        """Aggregate final results by month, technology, license_area, g99_status, connection_level"""
        print("\n📊 Aggregating final results...")
        
        if not self.aggregated_data:
            print("❌ No data to aggregate")
            return None
        
        # Convert to DataFrame
        df = pd.DataFrame(self.aggregated_data)
        
        # Group by required columns
        grouped = df.groupby(['month', 'technology', 'license_area', 'g99_status', 'connection_level']).agg({
            'capacity_kw': ['count', 'sum']
        }).reset_index()
        
        # Flatten column names
        grouped.columns = ['month', 'technology', 'license_area', 'g99_status', 'connection_level', 'install_count', 'total_kw']
        
        # Convert kW to MW
        grouped['total_mw'] = grouped['total_kw'] / 1000
        
        # Drop the kW column
        grouped = grouped.drop('total_kw', axis=1)
        
        # Sort by month, technology, license_area
        grouped = grouped.sort_values(['month', 'technology', 'license_area', 'g99_status', 'connection_level'])
        
        print(f"✅ Aggregation complete: {len(grouped)} final records")
        return grouped
    
    def save_output(self, aggregated_df, source_breakdown_df, deduplication_df=None, domestic_commercial_df=None):
        """Save aggregated results to CSV files"""
        if aggregated_df is None:
            print("❌ No data to save")
            return
        
        # Ensure output directory exists
        os.makedirs(self.output_dir, exist_ok=True)
        
        # Save main aggregated output
        main_output_path = os.path.join(self.output_dir, 'aggregated_dashboard_output.csv')
        try:
            aggregated_df.to_csv(main_output_path, index=False)
            print(f"✅ Main output saved to: {main_output_path}")
        except Exception as e:
            print(f"❌ Error saving main output: {e}")
        
        # Save source breakdown output
        if source_breakdown_df is not None:
            breakdown_output_path = os.path.join(self.output_dir, 'source_breakdown_output.csv')
            try:
                source_breakdown_df.to_csv(breakdown_output_path, index=False)
                print(f"✅ Source breakdown saved to: {breakdown_output_path}")
            except Exception as e:
                print(f"❌ Error saving source breakdown: {e}")
        
        # Save deduplication report
        if deduplication_df is not None:
            dedup_output_path = os.path.join(self.output_dir, 'deduplication_report.csv')
            try:
                deduplication_df.to_csv(dedup_output_path, index=False)
                print(f"✅ Deduplication report saved to: {dedup_output_path}")
            except Exception as e:
                print(f"❌ Error saving deduplication report: {e}")
        
        # Save domestic/commercial breakdown
        if domestic_commercial_df is not None:
            dc_output_path = os.path.join(self.output_dir, 'domestic_commercial_breakdown.csv')
            try:
                domestic_commercial_df.to_csv(dc_output_path, index=False)
                print(f"✅ Domestic/commercial breakdown saved to: {dc_output_path}")
            except Exception as e:
                print(f"❌ Error saving domestic/commercial breakdown: {e}")
        
        # Print summary
        print(f"\n📊 Final summary:")
        print(f"   Total records (final): {len(aggregated_df)}")
        print(f"   Total records (by source): {len(source_breakdown_df) if source_breakdown_df is not None else 0}")
        print(f"   Total records (deduplication report): {len(deduplication_df) if deduplication_df is not None else 0}")
        print(f"   Total records (domestic/commercial): {len(domestic_commercial_df) if domestic_commercial_df is not None else 0}")
        print(f"   Technologies: {aggregated_df['technology'].nunique()}")
        print(f"   License areas: {aggregated_df['license_area'].nunique()}")
        print(f"   Months: {aggregated_df['month'].nunique()}")
        print(f"   Total installations: {aggregated_df['install_count'].sum():,}")
        print(f"   Total capacity: {aggregated_df['total_mw'].sum():.2f} MW")
    
    def run(self):
        """Run the complete LCT dashboard processing pipeline"""
        print("🚀 Starting LCT Dashboard Processing Pipeline")
        print("=" * 60)
        
        try:
            # Load postcode lookup
            self.load_postcode_lookup()
            
            # Process all data sources
            self.process_mcs_data()
            self.process_ecr_gt_1mw()
            self.process_ecr_lt_1mw()
            self.process_lct_register()
            self.process_zapmap_data()
            self.process_smart_connect()
            
            # Create source breakdown before deduplication
            source_breakdown_df = self.create_source_breakdown()
            
            # Deduplicate data
            self.deduplicate_data()
            
            # Create deduplication report
            deduplication_df = self.create_deduplication_report()
            
            # Create domestic/commercial breakdown
            domestic_commercial_df = self.create_domestic_commercial_breakdown()
            
            # Aggregate final results
            aggregated_df = self.aggregate_final_results()
            
            # Save all outputs
            self.save_output(aggregated_df, source_breakdown_df, deduplication_df, domestic_commercial_df)
            
            print("\n🎉 LCT Dashboard processing completed successfully!")
            
        except Exception as e:
            print(f"\n❌ Pipeline failed with error: {e}")
            raise

def main():
    """Main function"""
    processor = LCTDashboardProcessor()
    processor.run() 

if __name__ == "__main__":
    main()