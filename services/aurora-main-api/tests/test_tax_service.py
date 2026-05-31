"""
ASG Solutions — Tax Compliance Test Script
==========================================
This script tests the "regulatory brain" to make sure everything works.

HOW TO RUN:
  cd ~/asg_platform
  source venv/bin/activate
  python test_tax_service.py

WHAT IT TESTS:
  1. VAT calculation (18% for 2026)
  2. Threshold before June 2026 (10,000 NIS)
  3. Threshold after June 2026 (5,000 NIS)
  4. Invoice number generation
  5. Mock ITA allocation number
  6. Current active rules display
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
# "asyncio" — needed to run the async ITA mock function
# "date" — needed to create specific test dates
import asyncio
from datetime import date

# Import our services from the app package
from app.services.tax_compliance import (
    check_tax_compliance,
    calculate_vat,
    generate_invoice_number,
    get_current_rules,
)
from app.services.ita_mock_service import request_allocation_number


# ─────────────────────────────────────────────────────────────
# HELPER: print test results in a clear format
# ─────────────────────────────────────────────────────────────
def print_test(test_name, expected, actual, passed):
    """Print a single test result with a clear PASS/FAIL label."""
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status} | {test_name}")
    if not passed:
        print(f"         Expected: {expected}")
        print(f"         Got:      {actual}")


# ═══════════════════════════════════════════════════════════════
# TEST 1: VAT Calculation (18%)
# ═══════════════════════════════════════════════════════════════
def test_vat():
    print("\n" + "=" * 60)
    print("TEST 1: VAT Calculation (18% for 2026)")
    print("=" * 60)

    # Test: 1,000 NIS net → VAT should be 180 NIS, total 1,180 NIS
    result = calculate_vat(1000.0)

    print(f"\n  Input:  1,000 NIS (before tax)")
    print(f"  VAT rate: {result['vat_rate']} ({int(result['vat_rate'] * 100)}%)")
    print(f"  VAT amount: {result['vat_amount']} NIS")
    print(f"  Total: {result['amount_total']} NIS")
    print()

    print_test(
        "VAT amount = 180.0",
        180.0, result["vat_amount"],
        result["vat_amount"] == 180.0
    )
    print_test(
        "Total = 1,180.0",
        1180.0, result["amount_total"],
        result["amount_total"] == 1180.0
    )
    print_test(
        "VAT rate = 0.18",
        0.18, result["vat_rate"],
        result["vat_rate"] == 0.18
    )

    # Test: 5,500 NIS → VAT = 990, total = 6,490
    result2 = calculate_vat(5500.0)
    print_test(
        "5,500 NIS → VAT = 990.0",
        990.0, result2["vat_amount"],
        result2["vat_amount"] == 990.0
    )
    print_test(
        "5,500 NIS → Total = 6,490.0",
        6490.0, result2["amount_total"],
        result2["amount_total"] == 6490.0
    )


# ═══════════════════════════════════════════════════════════════
# TEST 2: Threshold BEFORE June 2026 (10,000 NIS)
# ═══════════════════════════════════════════════════════════════
def test_threshold_before_june():
    print("\n" + "=" * 60)
    print("TEST 2: Threshold BEFORE June 2026 (10,000 NIS)")
    print("=" * 60)

    # Use a date before June 2026
    test_date = date(2026, 4, 15)   # April 15, 2026
    print(f"\n  Test date: {test_date} (before June 1, 2026)")
    print(f"  Threshold should be: 10,000 NIS\n")

    # 9,999 NIS → should NOT require allocation
    result1 = check_tax_compliance(9_999.0, test_date)
    print_test(
        "9,999 NIS → NO allocation needed",
        False, result1["requires_allocation"],
        result1["requires_allocation"] == False
    )

    # 10,000 NIS → SHOULD require allocation (exactly at threshold)
    result2 = check_tax_compliance(10_000.0, test_date)
    print_test(
        "10,000 NIS → YES allocation needed (at threshold)",
        True, result2["requires_allocation"],
        result2["requires_allocation"] == True
    )

    # 15,000 NIS → SHOULD require allocation
    result3 = check_tax_compliance(15_000.0, test_date)
    print_test(
        "15,000 NIS → YES allocation needed (above)",
        True, result3["requires_allocation"],
        result3["requires_allocation"] == True
    )

    # Verify threshold value
    print_test(
        "Threshold = 10,000",
        10_000.0, result1["threshold"],
        result1["threshold"] == 10_000.0
    )


# ═══════════════════════════════════════════════════════════════
# TEST 3: Threshold FROM June 2026 (5,000 NIS)
# ═══════════════════════════════════════════════════════════════
def test_threshold_from_june():
    print("\n" + "=" * 60)
    print("TEST 3: Threshold FROM June 2026 (5,000 NIS)")
    print("=" * 60)

    # Use a date on or after June 1, 2026
    test_date = date(2026, 7, 1)   # July 1, 2026
    print(f"\n  Test date: {test_date} (after June 1, 2026)")
    print(f"  Threshold should be: 5,000 NIS\n")

    # 4,999 NIS → should NOT require allocation
    result1 = check_tax_compliance(4_999.0, test_date)
    print_test(
        "4,999 NIS → NO allocation needed",
        False, result1["requires_allocation"],
        result1["requires_allocation"] == False
    )

    # 5,000 NIS → SHOULD require allocation (exactly at threshold)
    result2 = check_tax_compliance(5_000.0, test_date)
    print_test(
        "5,000 NIS → YES allocation needed (at threshold)",
        True, result2["requires_allocation"],
        result2["requires_allocation"] == True
    )

    # 7,500 NIS → SHOULD require allocation
    result3 = check_tax_compliance(7_500.0, test_date)
    print_test(
        "7,500 NIS → YES allocation needed (above)",
        True, result3["requires_allocation"],
        result3["requires_allocation"] == True
    )

    # Test the exact change date: June 1, 2026
    exact_date = date(2026, 6, 1)
    result4 = check_tax_compliance(5_000.0, exact_date)
    print_test(
        "June 1, 2026 exactly → uses 5,000 threshold",
        5_000.0, result4["threshold"],
        result4["threshold"] == 5_000.0
    )


# ═══════════════════════════════════════════════════════════════
# TEST 4: Invoice Number Generation
# ═══════════════════════════════════════════════════════════════
def test_invoice_numbers():
    print("\n" + "=" * 60)
    print("TEST 4: Invoice Number Generation")
    print("=" * 60)
    print()

    # Business 1, first invoice (count=0 → next is 1)
    inv1 = generate_invoice_number(1, 0)
    print_test(
        "Business 1, first invoice → INV-1-0001",
        "INV-1-0001", inv1,
        inv1 == "INV-1-0001"
    )

    # Business 1, 42nd invoice (count=41 → next is 42)
    inv2 = generate_invoice_number(1, 41)
    print_test(
        "Business 1, 42nd invoice → INV-1-0042",
        "INV-1-0042", inv2,
        inv2 == "INV-1-0042"
    )

    # Business 5, 100th invoice
    inv3 = generate_invoice_number(5, 99)
    print_test(
        "Business 5, 100th invoice → INV-5-0100",
        "INV-5-0100", inv3,
        inv3 == "INV-5-0100"
    )


# ═══════════════════════════════════════════════════════════════
# TEST 5: Mock ITA Allocation Number
# ═══════════════════════════════════════════════════════════════
async def test_ita_mock():
    print("\n" + "=" * 60)
    print("TEST 5: Mock ITA Allocation Number")
    print("=" * 60)
    print()

    # Request an allocation number
    result = await request_allocation_number(
        seller_tax_id="515123456",
        buyer_tax_id="514987654",
        amount=15000.0,
    )

    print(f"\n  Response:")
    print(f"    success: {result['success']}")
    print(f"    allocation_number: {result['allocation_number']}")
    print(f"    message: {result['message']}")
    print(f"    timestamp: {result['timestamp']}")
    print()

    # Check the response has the right structure
    print_test(
        "Response has 'success' key",
        True, "success" in result,
        "success" in result
    )
    print_test(
        "Response has 'allocation_number' key",
        True, "allocation_number" in result,
        "allocation_number" in result
    )
    print_test(
        "Response has 'timestamp' key",
        True, "timestamp" in result,
        "timestamp" in result
    )

    # If successful, check the allocation number is 9 digits
    if result["success"]:
        alloc = result["allocation_number"]
        is_9_digits = len(alloc) == 9 and alloc.isdigit()
        print_test(
            "Allocation number is 9 digits",
            "9-digit number", alloc,
            is_9_digits
        )
    else:
        print("  ⚠️  Got a simulated failure (5% chance) — this is OK!")
        print("     Run the test again to see a successful response.")


# ═══════════════════════════════════════════════════════════════
# TEST 6: Current Rules Display
# ═══════════════════════════════════════════════════════════════
def test_current_rules():
    print("\n" + "=" * 60)
    print("TEST 6: Current Active Rules")
    print("=" * 60)

    rules = get_current_rules()
    print(f"\n  📅 Date:       {rules['date']}")
    print(f"  💰 VAT Rate:   {rules['vat_percent']}")
    print(f"  📊 Threshold:  {rules['threshold']:,.0f} NIS")
    print(f"  📆 Drops on:   {rules['threshold_drops_on']}")
    print(f"\n  📝 {rules['summary']}")


# ═══════════════════════════════════════════════════════════════
# RUN ALL TESTS
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "🔷" * 30)
    print("  ASG Solutions — Tax Compliance Test Suite")
    print("  Testing the Regulatory Brain 🧠")
    print("🔷" * 30)

    # Run synchronous tests
    test_vat()
    test_threshold_before_june()
    test_threshold_from_june()
    test_invoice_numbers()

    # Run async test (ITA mock needs asyncio)
    asyncio.run(test_ita_mock())

    # Show current rules
    test_current_rules()

    print("\n" + "=" * 60)
    print("  ALL TESTS COMPLETE!")
    print("=" * 60)
    print()
