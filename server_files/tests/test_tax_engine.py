"""
Aurora LTS — Tax Engine Unit Tests
====================================
Plain-Python runnable test script (no pytest dependency). Run from
the `server_files/` directory:

    cd ~/Desktop/ASG-Middleware/server_files
    ../venv/bin/python tests/test_tax_engine.py

Exits 0 on success, non-zero on first failure.

WHAT THIS COVERS:
  - Progressive bracket math correctness at boundary points
  - Marginal rate selection at bracket transitions
  - Income tax with credit-point subtraction
  - VAT: only Osek Morshe charges; Osek Patur stays 0
  - Pension recommendation respects the ceiling
  - Today-net projection from a given today_gross
  - Edge cases: zero income, expenses > income, very large income
"""

from __future__ import annotations

import sys
import os
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.tax_engine import (
    TaxPosition,
    compute_tax_position,
    progressive_tax,
    get_brackets,
    get_constants,
)


def _approx(actual: float, expected: float, tol: float = 0.5) -> bool:
    """Within tol shekels."""
    return abs(actual - expected) <= tol


_PASS = 0
_FAIL = 0


def check(name: str, condition: bool, detail: str = ""):
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"  ✅ {name}")
    else:
        _FAIL += 1
        print(f"  ❌ {name}  {detail}")


# ─────────────────────────────────────────────────────────────
# Test 1 — progressive_tax at known boundary points (2024 brackets)
# ─────────────────────────────────────────────────────────────
def test_progressive_tax_boundaries():
    print("\n[T1] progressive_tax boundaries (2024 IT brackets)")
    brackets = get_brackets("income_tax", 2024)

    # ₪0 income → ₪0 tax
    check("₪0 gross → ₪0 tax", _approx(progressive_tax(0.0, brackets), 0.0))

    # ₪10,000 → 10% × 10,000 = ₪1,000
    check("₪10K → ₪1,000 (10% bracket)",
          _approx(progressive_tax(10_000.0, brackets), 1_000.0))

    # Exactly ₪84,120 (top of 10% bracket) → ₪8,412
    check("₪84,120 boundary → ₪8,412",
          _approx(progressive_tax(84_120.0, brackets), 8_412.0))

    # ₪100,000:
    #   first 84,120  @ 10%  = 8,412
    #   next  15,880  @ 14%  = 2,223.2
    #   total                = 10,635.2
    check("₪100K → ₪10,635.2",
          _approx(progressive_tax(100_000.0, brackets), 10_635.2))

    # ₪200,000:
    #   84,120 @ 10%  = 8,412
    #   36,600 @ 14%  = 5,124
    #   73,080 @ 20%  = 14,616
    #   6,200  @ 31%  = 1,922
    #   total          = 30,074
    check("₪200K → ₪30,074",
          _approx(progressive_tax(200_000.0, brackets), 30_074.0))

    # Top bracket: ₪1,000,000
    #   84,120 @ 10%  = 8,412
    #   36,600 @ 14%  = 5,124
    #   73,080 @ 20%  = 14,616
    #   75,480 @ 31%  = 23,398.8
    #   291,000 @ 35% = 101,850
    #   161,280 @ 47% = 75,801.6
    #   278,440 @ 50% = 139,220
    #   total          = 368,422.4
    check("₪1M → ₪368,422.4",
          _approx(progressive_tax(1_000_000.0, brackets), 368_422.4))


# ─────────────────────────────────────────────────────────────
# Test 2 — compute_tax_position for a typical Wolt courier
# ─────────────────────────────────────────────────────────────
def test_courier_typical():
    print("\n[T2] Courier — typical Wolt driver, Osek Patur, ₪80K gross")
    # Assume YTD gross ₪80,000, fuel + phone + vehicle expenses ₪15,000
    p = compute_tax_position(
        ytd_gross=80_000.0,
        ytd_expenses=15_000.0,
        tax_status="osek_patur",
        tax_year=2024,
        today_gross=480.0,
    )

    check("Taxable income = 65,000", _approx(p.taxable_income, 65_000.0))

    # Income tax: 65,000 @ 10% = 6,500
    # Credit points 2.25 × ₪2,904 = ₪6,534 — wipes out the income tax.
    check("Income tax fully credited (₪0 after credits)",
          _approx(p.income_tax, 0.0),
          f"got {p.income_tax}")

    # NI: 65,000 @ 2.87% = 1,865.5
    check("National insurance ≈ ₪1,865",
          _approx(p.national_insurance, 1_865.5))

    # Health: 65,000 @ 3.10% = 2,015
    check("Health tax ≈ ₪2,015",
          _approx(p.health_tax, 2_015.0))

    # VAT: Osek Patur → 0
    check("VAT = 0 for Osek Patur", _approx(p.vat_owed, 0.0))

    # Pension: 65,000 × 5% = 3,250 (well below ceiling)
    check("Pension recommended = ₪3,250",
          _approx(p.pension_recommended, 3_250.0))

    # Marginal rate at 65,000: IT 10% + NI 2.87% + Health 3.10% = 15.97%
    check("Marginal rate ≈ 15.97%",
          _approx(p.marginal_rate, 0.1597, tol=0.001))

    # Today: 480 × 15.97% ≈ ₪76.66 set aside, ₪403.34 net
    check("Today shield ≈ ₪76.66",
          _approx(p.today_shield_set_aside or 0, 76.66, tol=0.5))
    check("Today net ≈ ₪403.34",
          _approx(p.today_net or 0, 403.34, tol=0.5))


# ─────────────────────────────────────────────────────────────
# Test 3 — Osek Morshe with VAT
# ─────────────────────────────────────────────────────────────
def test_osek_morshe_vat():
    print("\n[T3] Osek Morshe — VAT only collected, not Osek Patur path")
    p = compute_tax_position(
        ytd_gross=200_000.0,
        ytd_expenses=50_000.0,
        tax_status="osek_morshe",
        tax_year=2024,
        ytd_vat_collected=34_000.0,  # 17% of 200K
        ytd_vat_paid=8_500.0,        # 17% of 50K expenses
    )
    # VAT owed = 34,000 - 8,500 = 25,500
    check("VAT owed = ₪25,500 (collected − paid)",
          _approx(p.vat_owed, 25_500.0))


def test_osek_patur_no_vat_even_if_provided():
    print("\n[T4] Osek Patur — VAT stays 0 even if mistakenly provided")
    p = compute_tax_position(
        ytd_gross=80_000.0,
        ytd_expenses=15_000.0,
        tax_status="osek_patur",
        tax_year=2024,
        ytd_vat_collected=10_000.0,  # should be ignored
        ytd_vat_paid=2_000.0,
    )
    check("Osek Patur ignores VAT inputs", _approx(p.vat_owed, 0.0))


# ─────────────────────────────────────────────────────────────
# Test 5 — Pension ceiling
# ─────────────────────────────────────────────────────────────
def test_pension_ceiling():
    print("\n[T5] Pension recommendation respects the ceiling")
    constants = get_constants(2024)
    # Income far above ceiling
    p = compute_tax_position(
        ytd_gross=500_000.0,
        ytd_expenses=0.0,
        tax_status="osek_patur",
        tax_year=2024,
    )
    expected = constants.pension_ceiling * constants.pension_min_rate
    check(f"Pension capped at ceiling × min_rate (= ₪{expected:.2f})",
          _approx(p.pension_recommended, expected))


# ─────────────────────────────────────────────────────────────
# Test 6 — Edge cases
# ─────────────────────────────────────────────────────────────
def test_edge_cases():
    print("\n[T6] Edge cases")
    # Zero income
    p = compute_tax_position(0.0, 0.0, "osek_patur", tax_year=2024)
    check("Zero gross → all taxes 0",
          p.taxable_income == 0 and p.income_tax == 0 and p.total_owed == 0)

    # Expenses exceed income → taxable_income clamped to 0
    p2 = compute_tax_position(50_000.0, 80_000.0, "osek_patur", tax_year=2024)
    check("Expenses > income → taxable_income = 0",
          _approx(p2.taxable_income, 0.0))

    # Negative inputs — graceful (clamped to 0)
    p3 = compute_tax_position(-100.0, -50.0, "osek_patur", tax_year=2024)
    check("Negative gross → clamped to 0", _approx(p3.ytd_gross, 0.0))

    # today_gross = None → today_net stays None
    p4 = compute_tax_position(10_000.0, 0.0, "osek_patur", tax_year=2024)
    check("today_gross=None → today_net=None", p4.today_net is None)


# ─────────────────────────────────────────────────────────────
# Test 7 — Marginal rate transitions at known boundary
# ─────────────────────────────────────────────────────────────
def test_marginal_rate_transition():
    print("\n[T7] Marginal rate at the 10%→14% IT bracket transition")
    # Just below ₪84,120 should be 10% IT + 2.87% NI + 3.10% Health = 15.97%
    p_low = compute_tax_position(80_000.0, 0.0, "osek_patur", tax_year=2024)
    check("@ ₪80K — marginal ≈ 15.97%",
          _approx(p_low.marginal_rate, 0.1597, tol=0.001),
          f"got {p_low.marginal_rate}")

    # Just above ₪85,464 NI ceiling AND ₪84,120 IT boundary:
    # IT 14% + NI 12.83% + Health 5.00% = 31.83%
    p_high = compute_tax_position(90_000.0, 0.0, "osek_patur", tax_year=2024)
    check("@ ₪90K — marginal ≈ 31.83%",
          _approx(p_high.marginal_rate, 0.3183, tol=0.001),
          f"got {p_high.marginal_rate}")


# ─────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("Aurora Tax Engine — Unit Tests")
    print("=" * 60)
    test_progressive_tax_boundaries()
    test_courier_typical()
    test_osek_morshe_vat()
    test_osek_patur_no_vat_even_if_provided()
    test_pension_ceiling()
    test_edge_cases()
    test_marginal_rate_transition()
    print("-" * 60)
    print(f"Result: {_PASS} passed, {_FAIL} failed")
    print("=" * 60)
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    main()
