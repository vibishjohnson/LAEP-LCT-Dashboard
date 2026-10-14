"""Method C1 (dynamic private-EV-weighted redistribution) - EXPERIMENTAL CANDIDATE.

Not used by the dashboard or any production pipeline. See README.md.

Pure functions operate on numpy arrays so they can be tested with small fixtures.
`run_quarter` applies one method to one quarter of national LSOA data.
Method A (current production) is reimplemented here only as a regression control.
"""
from dataclasses import dataclass

import numpy as np

METHODOLOGY_ID = 'C1_DYNAMIC_PRIVATE_EV'
CONTROL_METHODOLOGY_ID = 'A_PRODUCTION_REFERENCE'
THRESHOLD = 100
C1_LPN_MIN_SHARE = 0.20          # C1 candidate: LPN floor of 20% of post-redistribution UKPN stock
PRODUCTION_LPN_TARGET_SHARE = 0.25  # Method A reference only, so it still reproduces production
LPN_FLOOR = {'C1': C1_LPN_MIN_SHARE, 'A': PRODUCTION_LPN_TARGET_SHARE}
MISC_CODE = 'Miscellaneous'


def largest_remainder(ideal, total, codes):
    """Integerise `ideal` so it sums to `total`.

    Floor every value, then give +1 to the largest fractional remainders.
    Ties are broken by ascending LSOA code (matches production).
    """
    ideal = np.asarray(ideal, dtype=float)
    base = np.floor(ideal).astype(np.int64)
    remainder = ideal - base
    extra = int(total - base.sum())
    if extra < 0 or extra > len(ideal):
        raise ValueError(f'largest_remainder cannot reach total {total} (floor sum {base.sum()})')
    order = np.lexsort((np.asarray(codes, dtype=str), -remainder))
    add = np.zeros(len(ideal), np.int64)
    add[order[:extra]] = 1
    return base + add


def split_by_fuel(keep, bev, phev):
    """Split integer `keep` between BEV and PHEV in proportion bev:phev.

    Floor both shares; if one vehicle is left over it goes to the fuel with the
    larger fractional remainder, ties to BEV (production rule for splitting 100).
    Requires keep <= bev + phev.
    """
    keep = np.asarray(keep, dtype=np.int64)
    bev = np.asarray(bev, dtype=np.int64)
    phev = np.asarray(phev, dtype=np.int64)
    tot = bev + phev
    if np.any(keep > tot):
        raise ValueError('split_by_fuel: keep exceeds available stock')
    eb = np.divide(keep * bev, tot, out=np.zeros(len(tot)), where=tot > 0)
    ep = np.divide(keep * phev, tot, out=np.zeros(len(tot)), where=tot > 0)
    fb, fp = np.floor(eb), np.floor(ep)
    give_bev = ((keep - fb - fp) > 0) & ((eb - fb) >= (ep - fp))
    kb = (fb + give_bev).astype(np.int64)
    return kb, keep - kb


def classify_keepers(total_bev, total_phev, private_bev, private_phev, company_bev, company_phev):
    """Reported private and company counts (suppressed cells already 0), capped so components never exceed the total.

    unclassified = total - private - company: vehicles whose keeper cell was suppressed or incomplete.
    """
    pb = np.minimum(private_bev, total_bev)
    pp = np.minimum(private_phev, total_phev)
    cb = np.minimum(company_bev, total_bev - pb)
    cp = np.minimum(company_phev, total_phev - pp)
    return pb, pp, cb, cp, total_bev - pb - cb, total_phev - pp - cp


@dataclass
class QuarterInput:
    codes: np.ndarray        # LSOA codes (str), sorted, unique; includes MISC_CODE if present
    total_bev: np.ndarray
    total_phev: np.ndarray
    private_bev: np.ndarray
    private_phev: np.ndarray
    company_bev: np.ndarray
    company_phev: np.ndarray


def retention(q: QuarterInput, method: str):
    """Return per-LSOA retained BEV/PHEV plus keeper-component diagnostics."""
    TB, TP = q.total_bev, q.total_phev
    T = TB + TP
    misc = q.codes == MISC_CODE
    pb, pp, cb, cp, ub, up = classify_keepers(TB, TP, q.private_bev, q.private_phev, q.company_bev, q.company_phev)
    # Combined-stock rule (production and Miscellaneous): keep min(T, 100), split by fuel.
    ab, ap = split_by_fuel(np.minimum(T, THRESHOLD), TB, TP)
    ab = np.where(T > THRESHOLD, ab, TB)
    ap = np.where(T > THRESHOLD, ap, TP)
    if method == 'A':
        kb, kp = ab, ap
    elif method == 'C1':
        P = pb + pp
        nonprivate_b, nonprivate_p = TB - pb, TP - pp          # company + unclassified, treated as one block
        allowance = np.maximum(0, THRESHOLD - P)
        keep_np = np.minimum(nonprivate_b + nonprivate_p, allowance)
        knb, knp = split_by_fuel(keep_np, nonprivate_b, nonprivate_p)
        kb, kp = pb + knb, pp + knp
        kb = np.where(misc, ab, kb)                            # Miscellaneous keeps production treatment
        kp = np.where(misc, ap, kp)
    else:
        raise ValueError(method)
    # Diagnostic attribution of retained non-private stock to company vs unclassified
    # (company share floored, remainder to unclassified). Does not affect any result.
    np_kb, np_kp = kb - np.minimum(kb, pb), kp - np.minimum(kp, pp)
    ckb = np.floor(np.divide(np_kb * cb, cb + ub, out=np.zeros(len(T)), where=(cb + ub) > 0)).astype(np.int64)
    ckp = np.floor(np.divide(np_kp * cp, cp + up, out=np.zeros(len(T)), where=(cp + up) > 0)).astype(np.int64)
    diag = dict(private_bev=pb, private_phev=pp, company_bev=cb, company_phev=cp, unclassified_bev=ub, unclassified_phev=up,
                retained_private_bev=np.minimum(kb, pb), retained_private_phev=np.minimum(kp, pp),
                retained_company_bev=ckb, retained_company_phev=ckp,
                retained_unclassified_bev=np_kb - ckb, retained_unclassified_phev=np_kp - ckp)
    return kb, kp, diag


def recipient_weights(q: QuarterInput, method: str, private_total):
    """Unnormalised recipient weights. Miscellaneous never receives."""
    T = q.total_bev + q.total_phev
    geo = q.codes != MISC_CODE
    if method == 'A':
        w = np.where((T < THRESHOLD) & geo, T, 0)
    else:
        w = np.where(geo, private_total, 0)
    return w.astype(float)


def run_quarter(q: QuarterInput, method: str):
    kb, kp, diag = retention(q, method)
    pool_b = int((q.total_bev - kb).sum())
    pool_p = int((q.total_phev - kp).sum())
    w = recipient_weights(q, method, diag['private_bev'] + diag['private_phev'])
    if w.sum() <= 0:
        raise ValueError('no recipient weight')
    w = w / w.sum()
    rb = largest_remainder(w * pool_b, pool_b, q.codes)
    rp = largest_remainder(w * pool_p, pool_p, q.codes)
    return dict(retained_bev=kb, retained_phev=kp, received_bev=rb, received_phev=rp,
                post_bev=kb + rb, post_phev=kp + rp, pool_bev=pool_b, pool_phev=pool_p, weight=w, **diag)


def lpn_uplift(bev, phev, is_lpn, codes, target):
    """LPN minimum-share adjustment on post-redistribution UKPN stock (production formula and rounding).

    x = max(0, round((target*UKPN - LPN)/(1-target))). Never negative, so LPN is never reduced when its share
    already meets the target. BEV share = int(x * LPN BEV fraction) (truncation); each fuel spread over LPN LSOAs
    in proportion to their stock with largest-remainder rounding. C1 uses target 0.20; the Method A reference 0.25.
    """
    bev = np.asarray(bev, np.int64)
    phev = np.asarray(phev, np.int64)
    tot = bev + phev
    lpn, ukpn = int(tot[is_lpn].sum()), int(tot.sum())
    uplift = max(0, int(round((target * ukpn - lpn) / (1 - target))))
    lb, lp = int(bev[is_lpn].sum()), int(phev[is_lpn].sum())
    bev_fraction = lb / (lb + lp) if (lb + lp) > 0 else 0.5
    up_b = int(uplift * bev_fraction)
    up_p = uplift - up_b
    add_b = np.zeros(len(bev), np.int64)
    add_p = np.zeros(len(bev), np.int64)
    idx = np.where(is_lpn)[0]
    if lb > 0:
        add_b[idx] = largest_remainder(bev[idx] / lb * up_b, up_b, np.asarray(codes)[idx])
    if lp > 0:
        add_p[idx] = largest_remainder(phev[idx] / lp * up_p, up_p, np.asarray(codes)[idx])
    return add_b, add_p, uplift
