#!/usr/bin/env python3
"""
LAEP LCT Dashboard - Interactive Streamlit App
Technology types: Heat Pumps, Solar PV, Battery Storage, EV Chargers
Dimensions: By DNO, By Month, By LSOA
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime
import os

# Page config
st.set_page_config(
    page_title="LAEP LCT Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Load data
@st.cache_data
def load_data():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    dno_path = os.path.join(project_dir, "project/output_processed/dashboard_data_dno.csv")
    lsoa_path = os.path.join(project_dir, "project/output_processed/dashboard_data_lsoa.csv")

    df_dno = pd.read_csv(dno_path)
    df_lsoa = pd.read_csv(lsoa_path)

    return df_dno, df_lsoa

df_dno, df_lsoa = load_data()

# Styling
st.markdown("""
    <style>
    .metric-card {
        background-color: #f0f2f6;
        padding: 20px;
        border-radius: 10px;
        text-align: center;
    }
    .metric-value {
        font-size: 32px;
        font-weight: bold;
        color: #1f77b4;
    }
    .metric-label {
        font-size: 14px;
        color: #666;
    }
    </style>
""", unsafe_allow_html=True)

# ============================================================================
# SIDEBAR FILTERS
# ============================================================================
st.sidebar.header("⚙️ Filters")

# Date range
all_periods = sorted(df_dno['period'].unique())
min_period = all_periods[0]
max_period = all_periods[-1]

period_range = st.sidebar.slider(
    "Select Period Range",
    value=(min_period, max_period),
    format="YYYY-MM"
)

# Technology filter
technologies = sorted(df_dno['tech_type'].unique())
selected_tech = st.sidebar.multiselect(
    "Technologies",
    technologies,
    default=technologies
)

# DNO filter
dnos = sorted([x for x in df_dno['DNO'].unique() if pd.notna(x)])
selected_dno = st.sidebar.multiselect(
    "DNO Areas",
    dnos,
    default=dnos
)

# Apply filters
filtered_dno = df_dno[
    (df_dno['period'] >= period_range[0]) &
    (df_dno['period'] <= period_range[1]) &
    (df_dno['tech_type'].isin(selected_tech)) &
    (df_dno['DNO'].isin(selected_dno))
]

filtered_lsoa = df_lsoa[
    (df_lsoa['period'] >= period_range[0]) &
    (df_lsoa['period'] <= period_range[1]) &
    (df_lsoa['tech_type'].isin(selected_tech)) &
    (df_lsoa['DNO'].isin(selected_dno))
]

# ============================================================================
# MAIN DASHBOARD
# ============================================================================
st.title("⚡ LAEP LCT Dashboard")
st.subheader(f"Low Carbon Technology Deployment ({period_range[0]} to {period_range[1]})")

# Summary metrics
col1, col2, col3, col4 = st.columns(4)

total_installs = int(filtered_dno['install_count'].sum())
total_capacity = filtered_dno['total_kw'].sum()
avg_capacity = total_capacity / total_installs if total_installs > 0 else 0

with col1:
    st.metric("Total Installations", f"{total_installs:,}")

with col2:
    st.metric("Total Capacity", f"{total_capacity/1000:.1f} MW")

with col3:
    st.metric("Avg per Install", f"{avg_capacity:.1f} kW")

with col4:
    st.metric("Tech Types", len(selected_tech))

# ============================================================================
# TABS FOR DIFFERENT VIEWS
# ============================================================================
tab1, tab2, tab3, tab4 = st.tabs(["📊 By Month", "🗺️ By DNO", "🏘️ By LSOA", "📋 Data Table"])

# Tab 1: Time Series
with tab1:
    st.subheader("Installation Trends by Month")

    if not filtered_dno.empty:
        # Prepare data
        time_data = filtered_dno.groupby(['period', 'tech_type']).agg({
            'install_count': 'sum',
            'total_kw': 'sum'
        }).reset_index()

        # Plot
        col1, col2 = st.columns(2)

        with col1:
            fig = px.line(
                time_data,
                x='period',
                y='install_count',
                color='tech_type',
                title="Monthly Installation Count by Technology",
                markers=True,
                labels={'period': 'Month', 'install_count': 'Number of Installations', 'tech_type': 'Technology'}
            )
            fig.update_xaxes(tickangle=45)
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            fig = px.line(
                time_data,
                x='period',
                y='total_kw',
                color='tech_type',
                title="Monthly Capacity (kW) by Technology",
                markers=True,
                labels={'period': 'Month', 'total_kw': 'Total Capacity (kW)', 'tech_type': 'Technology'}
            )
            fig.update_xaxes(tickangle=45)
            st.plotly_chart(fig, use_container_width=True)

# Tab 2: DNO Breakdown
with tab2:
    st.subheader("Deployment by DNO Area")

    if not filtered_dno.empty:
        col1, col2 = st.columns(2)

        # Aggregate by DNO and tech
        dno_data = filtered_dno.groupby(['DNO', 'tech_type']).agg({
            'install_count': 'sum',
            'total_kw': 'sum'
        }).reset_index()

        with col1:
            fig = px.bar(
                dno_data,
                x='DNO',
                y='install_count',
                color='tech_type',
                title="Installation Count by DNO",
                barmode='group',
                labels={'DNO': 'DNO Area', 'install_count': 'Installations', 'tech_type': 'Technology'}
            )
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            fig = px.bar(
                dno_data,
                x='DNO',
                y='total_kw',
                color='tech_type',
                title="Total Capacity (kW) by DNO",
                barmode='group',
                labels={'DNO': 'DNO Area', 'total_kw': 'Capacity (kW)', 'tech_type': 'Technology'}
            )
            st.plotly_chart(fig, use_container_width=True)

# Tab 3: LSOA Drill-down
with tab3:
    st.subheader("Geographic Breakdown by LSOA")

    if not filtered_lsoa.empty:
        # LSOA search
        lsoa_options = sorted(filtered_lsoa['LSOA21CD'].unique())
        selected_lsoa = st.selectbox(
            "Select LSOA21CD",
            lsoa_options,
            help="Filter to see data for a specific LSOA area"
        )

        if selected_lsoa:
            lsoa_detail = filtered_lsoa[filtered_lsoa['LSOA21CD'] == selected_lsoa]

            if not lsoa_detail.empty:
                # Summary for this LSOA
                col1, col2, col3 = st.columns(3)

                with col1:
                    st.metric("Total Installs", int(lsoa_detail['install_count'].sum()))

                with col2:
                    st.metric("Total Capacity (MW)", f"{lsoa_detail['total_kw'].sum()/1000:.2f}")

                with col3:
                    st.metric("DNO", lsoa_detail['DNO'].iloc[0] if not lsoa_detail.empty else "N/A")

                # Tech breakdown in this LSOA
                st.subheader("Technology Breakdown")
                tech_breakdown = lsoa_detail.groupby('tech_type').agg({
                    'install_count': 'sum',
                    'total_kw': 'sum'
                }).reset_index()

                fig = px.pie(
                    tech_breakdown,
                    values='install_count',
                    names='tech_type',
                    title=f"Technology Distribution in {selected_lsoa}",
                    labels={'install_count': 'Installations', 'tech_type': 'Technology'}
                )
                st.plotly_chart(fig, use_container_width=True)

                # Time series for this LSOA
                st.subheader("Monthly Trend in this LSOA")
                lsoa_time = lsoa_detail.groupby(['period', 'tech_type']).agg({
                    'install_count': 'sum',
                    'total_kw': 'sum'
                }).reset_index()

                fig = px.line(
                    lsoa_time,
                    x='period',
                    y='install_count',
                    color='tech_type',
                    title="Monthly Installations",
                    markers=True,
                    labels={'period': 'Month', 'install_count': 'Installations', 'tech_type': 'Technology'}
                )
                fig.update_xaxes(tickangle=45)
                st.plotly_chart(fig, use_container_width=True)

# Tab 4: Data Table
with tab4:
    st.subheader("Detailed Data Table (DNO Level)")

    if not filtered_dno.empty:
        # Display table
        display_df = filtered_dno.copy()
        display_df = display_df.sort_values(['period', 'DNO', 'tech_type'])

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True
        )

        # Download button
        csv = display_df.to_csv(index=False)
        st.download_button(
            label="Download Data as CSV",
            data=csv,
            file_name=f"lct_dashboard_{period_range[0]}_{period_range[1]}.csv",
            mime="text/csv"
        )

# ============================================================================
# FOOTER
# ============================================================================
st.divider()
st.caption(f"Dashboard last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')} | Data: MCS + LCT Register (MPAN deduplicated, England only, Apr 2025 - Mar 2026)")
