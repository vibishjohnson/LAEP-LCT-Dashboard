"""Pinned expectations for the C1 EV actuals release. Any mismatch makes preflight fail closed.

Values were recorded from the repository on 2026-10-12 (HEAD 97e855f plus working-tree state) at the start of Phase 5.
Change them only in a reviewed change that re-validates the shadow outputs.
"""
from pathlib import Path

RELEASE_DIR = Path(__file__).resolve().parent
EXP_DIR = RELEASE_DIR.parent
REPO = EXP_DIR.parents[1]

# Live-release gate. While False the tool refuses promote/rollback/recover against the real repository whatever
# arguments are given. Enabling it is a separate, approved change made only at release time.
LIVE_RELEASE_ENABLED = False

METHODOLOGY_ID = 'C1_DYNAMIC_PRIVATE_EV'
LPN_MIN_SHARE = 0.20
EXPECTED_PERIODS = ['2025-03', '2025-06', '2025-09', '2025-12', '2026-03']

INTEGRATION_REL = 'project/output_processed/integration'
TARGET_NAMES = ('ev_actuals_dno.csv', 'ev_actuals_la.csv', 'ev_actuals_laep.csv')   # the complete promotion allowlist
EXPECTED_ROWS = {'ev_actuals_dno.csv': 30, 'ev_actuals_la.csv': 1210, 'ev_actuals_laep.csv': 140}
EXPECTED_LAS, EXPECTED_LAEPS = 121, 14

SHADOW_REL = 'experiments/ev_redistribution_c1/shadow_integration'
C1_OUTPUTS_REL = 'experiments/ev_redistribution_c1/outputs'
ARCHIVE_REL = 'experiments/ev_redistribution_c1/method_a_archive'
STATE_REL = 'experiments/ev_redistribution_c1/release/state'

# New (C1) bytes to publish.
SHADOW_SHA256 = {
    'ev_actuals_dno.csv': 'b558a6b02ac03d0698bce480d3d9b92ef77d2f375c8e68ed3d4f14c389f55f95',
    'ev_actuals_la.csv': 'b8c737eaedd3970b603175936b2b37c7f3d3ed66788194553e4e42304085e8e6',
    'ev_actuals_laep.csv': 'ec539024ee749d6035446caf65db551bfd07b5e835b0f14d3d37d4f04ab7c0cb',
}
SHADOW_AUX_SHA256 = {
    f'{SHADOW_REL}/manifest.json': 'a82abbdfeb72e343ef01463da5675efeac5f799a39cc32332372193630485915',
    f'{SHADOW_REL}/metadata_patch_proposal.json': 'bd6c68ff4addddb66b4f4392108e09917a1ca4bb7348979ec8c00931b45b958c',
}
# Current production (Method A) bytes expected in the targets before promotion.
METHOD_A_TARGET_SHA256 = {
    'ev_actuals_dno.csv': '4878bba63abf131f46217cc4240f7a28ad3f85823cf62cd034e3bcf614c9eb7c',
    'ev_actuals_la.csv': 'f4c3ef85863eb3558c13cbd9f9dc41722fbc21cb2bd55218967b3aeb1cacb65e',
    'ev_actuals_laep.csv': 'b9d0a35188b36cee5920011c89996276cfc5badec6f0eed7efff052e404b057c',
}
# Other Method A variants seen in this repository's history/backups (used only to classify, never as a valid "old" state).
KNOWN_METHOD_A_VARIANTS = {
    'a5390820caf24c77': 'external dashboard_integration runner output (5-quarter DNO, 2025-09-25)',
    '9faec76a517012d9': 'SMMT/DfT candidate integration/ev_actuals_dno.csv (dft_staging, 2026-10-01)',
}
PINNED_SHA256 = {
    'dashboard.html': '51fcb61fa47f4a245d2905cb60a692db10171f553c08b9369843c34d860a4fc8',
    f'{INTEGRATION_REL}/monthly_forecast_dno.csv': '6ce3d9a425f19896a32bf9be115411142020649c82710b4fdbf4db82bb1fd909',
    f'{INTEGRATION_REL}/monthly_forecast_la.csv': '64669a9ca5e454b653e004c48d508769403a3db70b3454372e8cc90e4ea56f0a',
    f'{INTEGRATION_REL}/monthly_forecast_laep.csv': 'c0592173ef236f792a61ec6875a99ad9a62ca707b723aae04383d3eee8ca51c1',
    f'{INTEGRATION_REL}/metadata.json': '79fa5212a15b29e5470d0def6f4214e044db5b56d31416cb010feb59dbba4e35',
    f'{INTEGRATION_REL}/geography_lookup.csv': '4c68cb4f8e6bbf32844b119a6bad56275320b350593385eb38946f79ebbe71f2',
    'lookups/LSOA to DNO.csv': 'b6a6cf72e4ad5d82bd4e12a28c8ba96880a2c255ad3d6c5bda04a3785d70e591',
    f'{C1_OUTPUTS_REL}/c1_lsoa_quarterly.csv': '8c539a9a4076d02c2402d7f0d70caca24ef93858930286ccbc62361c625f669a',
    f'{C1_OUTPUTS_REL}/run_metadata.json': '05540614bdca4d1af3a519e0d952d777e2734e26e2f130fab30bf365c2527537',
    f'{ARCHIVE_REL}/ev_actuals_dno_method_a_original.csv': '4878bba63abf131f46217cc4240f7a28ad3f85823cf62cd034e3bcf614c9eb7c',
    f'{ARCHIVE_REL}/method_a_dno_history_before_2025-03.csv': 'e5b234c278f5034c6b202eca5231ec1b5d1d995f0bac0712b01535f9ec01c586',
    f'{ARCHIVE_REL}/manifest.json': '69e52ab9108654740782b9ca7186e92894885b3ca6f1ca2b0b6ca505289ecace',
    **SHADOW_AUX_SHA256,
}

# Expected C1 stock totals (shadow files); Method A March 2026 for contrast is 908,964.
EXPECTED_TOTALS = {
    '2025-03': {'UKPN': 719684, 'EPN': 338356, 'LPN': 166018, 'SPN': 215310, 'BEV': 478318, 'PHEV': 241366},
    '2025-06': {'UKPN': 759063, 'EPN': 359833, 'LPN': 172310, 'SPN': 226920, 'BEV': 502907, 'PHEV': 256156},
    '2025-09': {'UKPN': 806190, 'EPN': 385455, 'LPN': 179394, 'SPN': 241341, 'BEV': 533367, 'PHEV': 272823},
    '2025-12': {'UKPN': 846833, 'EPN': 407129, 'LPN': 185240, 'SPN': 254464, 'BEV': 561408, 'PHEV': 285425},
    '2026-03': {'UKPN': 896098, 'EPN': 435157, 'LPN': 191386, 'SPN': 269555, 'BEV': 591291, 'PHEV': 304807},
}
METHOD_A_MARCH_2026_UKPN = 908964

# Files whose bytes must be identical before and after any release operation (relative to the root, recursive).
# The three targets, staged temp files and the release state directory are excluded.
PROTECTED_TREES = ('dashboard.html', 'project', 'ev', 'lookups', 'dfes', 'lct', 'templates',
                   SHADOW_REL, C1_OUTPUTS_REL, ARCHIVE_REL)

# Files a disposable environment needs (copied from the repository, read-only on the source).
DISPOSABLE_ENV_FILES = ('dashboard.html', 'lookups/LSOA to DNO.csv',
                        'project/output_processed/dashboard_comparison_dno_monthly.csv',
                        'project/output_processed/dashboard_comparison_la.csv',
                        'project/output_processed/dashboard_comparison_laep.csv',
                        'project/output_processed/dashboard_comparison_lsoa.csv')
DISPOSABLE_ENV_TREES = (INTEGRATION_REL, SHADOW_REL, C1_OUTPUTS_REL, ARCHIVE_REL)
