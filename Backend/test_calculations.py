"""Unit tests for the FairPay Engine core calculation functions."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from models import ContractVersion, TransactionRecord, Deduction
from calculations import calculate_contractual_net, calculate_payments
from decimal import Decimal
from datetime import date, datetime


def create_swiggy_contract():
    return ContractVersion(
        platform='Swiggy',
        version_id='SWG-2.4',
        effective_from=date(2026, 5, 1),
        effective_to=None,
        source='seed',
        confidence=Decimal('1.0'),
        document_title='Swiggy Contract SWG-2.4',
        status='active',
        clauses=[],
        base_pay_per_order=Decimal('28.00'),
        distance_rate_per_km=Decimal('6.00'),
        wait_free_minutes=4,
        wait_rate_per_min=Decimal('1.25'),
        wait_max_billable_minutes=12,
        peak_amount=Decimal('15.00'),
        peak_windows=['12:00-15:00', '19:00-22:30'],
        weekend_amount=Decimal('0.00'),
        weekend_days=[],
        daily_threshold=8,
        daily_bonus=Decimal('90.00'),
        weekly_threshold=35,
        weekly_bonus=Decimal('350.00'),
        weekly_effective_from=date(2026, 8, 24),
        platform_fee_rate=Decimal('0.0150'),
        platform_fee_name='Platform Service Fee',
        insurance_amount=Decimal('0.75'),
        insurance_name='Accident Insurance Premium',
        penalty_trigger_minutes=38,
        penalty_amount=Decimal('15.00'),
        penalty_name='Late Delivery Penalty',
        campaign_amount=Decimal('8.00'),
        campaign_name='Monsoon Rain Boost',
        campaign_effective_from=date(2026, 9, 1),
        campaign_effective_to=date(2026, 9, 14),
        campaign_weekdays_only=False,
        campaign_windows=['17:00-23:00'],
        campaign_min_distance_km=None,
    )


def create_zomato_contract_zom31():
    return ContractVersion(
        platform='Zomato',
        version_id='ZOM-3.1',
        effective_from=date(2026, 4, 1),
        effective_to=date(2026, 9, 7),
        source='seed',
        confidence=Decimal('1.0'),
        document_title='Zomato Contract ZOM-3.1',
        status='active',
        clauses=[],
        base_pay_per_order=Decimal('30.00'),
        distance_rate_per_km=Decimal('6.00'),
        wait_free_minutes=5,
        wait_rate_per_min=Decimal('1.00'),
        wait_max_billable_minutes=15,
        peak_amount=Decimal('12.00'),
        peak_windows=['12:00-15:00', '19:00-22:00'],
        weekend_amount=Decimal('5.00'),
        weekend_days=['SAT', 'SUN'],
        daily_threshold=7,
        daily_bonus=Decimal('80.00'),
        weekly_threshold=25,
        weekly_bonus=Decimal('250.00'),
        weekly_effective_from=date(2026, 8, 24),
        platform_fee_rate=Decimal('0.0200'),
        platform_fee_name='Platform Service Fee',
        insurance_amount=Decimal('1.00'),
        insurance_name='Accident Insurance Premium',
        penalty_trigger_minutes=35,
        penalty_amount=Decimal('10.00'),
        penalty_name='Late Delivery Penalty',
    )


def create_zomato_contract_zom32():
    return ContractVersion(
        platform='Zomato',
        version_id='ZOM-3.2',
        effective_from=date(2026, 9, 8),
        effective_to=None,
        source='seed',
        confidence=Decimal('1.0'),
        document_title='Zomato Contract ZOM-3.2',
        status='active',
        clauses=[],
        base_pay_per_order=Decimal('30.00'),
        distance_rate_per_km=Decimal('6.50'),
        wait_free_minutes=5,
        wait_rate_per_min=Decimal('1.00'),
        wait_max_billable_minutes=15,
        peak_amount=Decimal('14.00'),
        peak_windows=['12:00-15:00', '19:00-22:00'],
        weekend_amount=Decimal('5.00'),
        weekend_days=['SAT', 'SUN'],
        daily_threshold=7,
        daily_bonus=Decimal('80.00'),
        weekly_threshold=25,
        weekly_bonus=Decimal('250.00'),
        weekly_effective_from=date(2026, 9, 8),
        platform_fee_rate=Decimal('0.0200'),
        platform_fee_name='Platform Service Fee',
        insurance_amount=Decimal('1.00'),
        insurance_name='Accident Insurance Premium',
        penalty_trigger_minutes=35,
        penalty_amount=Decimal('10.00'),
        penalty_name='Late Delivery Penalty',
        campaign_amount=Decimal('10.00'),
        campaign_name='Festive Week Boost',
        campaign_effective_from=date(2026, 9, 10),
        campaign_effective_to=date(2026, 9, 16),
        campaign_weekdays_only=False,
        campaign_windows=[],
        campaign_min_distance_km=Decimal('3.00'),
    )


def create_ubereats_contract():
    return ContractVersion(
        platform='UberEats',
        version_id='UBE-1.8',
        effective_from=date(2026, 3, 15),
        effective_to=None,
        source='seed',
        confidence=Decimal('1.0'),
        document_title='UberEats Contract UBE-1.8',
        status='active',
        clauses=[],
        base_pay_per_order=Decimal('32.00'),
        distance_rate_per_km=Decimal('5.50'),
        wait_free_minutes=3,
        wait_rate_per_min=Decimal('1.50'),
        wait_max_billable_minutes=10,
        peak_amount=Decimal('10.00'),
        peak_windows=['19:00-22:00'],
        weekend_amount=Decimal('8.00'),
        weekend_days=['SAT', 'SUN'],
        daily_threshold=4,
        daily_bonus=Decimal('40.00'),
        weekly_threshold=12,
        weekly_bonus=Decimal('100.00'),
        weekly_effective_from=date(2026, 8, 24),
        platform_fee_rate=Decimal('0.0250'),
        platform_fee_name='Platform Service Fee',
        insurance_amount=Decimal('0.00'),
        insurance_name='Accident Insurance Premium',
        penalty_trigger_minutes=0,
        penalty_amount=Decimal('0.00'),
        penalty_name='Late Delivery Penalty',
        campaign_amount=Decimal('6.00'),
        campaign_name='Weekday Lunch Push',
        campaign_effective_from=date(2026, 8, 24),
        campaign_effective_to=date(2026, 9, 4),
        campaign_weekdays_only=True,
        campaign_windows=['11:00-15:00'],
        campaign_min_distance_km=None,
    )


def test_swiggy_basic():
    """Test Swiggy basic transaction - no incentives"""
    tx = TransactionRecord(
        transaction_id='TXN-SWG-20260821-001',
        platform='Swiggy',
        transaction_date=date(2026, 8, 21),
        gross_amount=Decimal('40.24'),
        deductions=[
            Deduction(name='Platform Service Fee', amount=Decimal('0.85'), policy_reference='', category='fee'),
            Deduction(name='Accident Insurance Premium', amount=Decimal('0.50'), policy_reference='', category='insurance')
        ],
        incentives=[],
        net_amount=Decimal('38.89'),
        distance_km=Decimal('2.04'),
        duration_minutes=17,
        wait_time_minutes=1,
        contract_version='SWG-2.4',
        order_timestamp=datetime.fromisoformat('2026-08-21T11:51:22+05:30')
    )
    contract = create_swiggy_contract()
    net = calculate_contractual_net(tx, contract)
    assert net == Decimal('38.89'), f"Expected 38.89, got {net}"
    print("[PASS] test_swiggy_basic passed")


def test_swiggy_peak():
    """Test Swiggy with peak incentive"""
    tx = TransactionRecord(
        transaction_id='TXN-SWG-20260821-002',
        platform='Swiggy',
        transaction_date=date(2026, 8, 21),
        gross_amount=Decimal('65.08'),
        deductions=[
            Deduction(name='Platform Service Fee', amount=Decimal('0.75'), policy_reference='', category='fee'),
            Deduction(name='Accident Insurance Premium', amount=Decimal('0.75'), policy_reference='', category='insurance')
        ],
        incentives=[],
        net_amount=Decimal('63.58'),
        distance_km=Decimal('3.68'),
        duration_minutes=14,
        wait_time_minutes=3,
        contract_version='SWG-2.4',
        order_timestamp=datetime.fromisoformat('2026-08-21T12:25:29+05:30')
    )
    contract = create_swiggy_contract()
    net = calculate_contractual_net(tx, contract)
    assert net == Decimal('63.58'), f"Expected 63.58, got {net}"
    print("[PASS] test_swiggy_peak passed")


def test_zomato_peak():
    """Test Zomato with peak incentive"""
    tx = TransactionRecord(
        transaction_id='TXN-ZOM-20260821-001',
        platform='Zomato',
        transaction_date=date(2026, 8, 21),
        gross_amount=Decimal('75.84'),
        deductions=[
            Deduction(name='Platform Service Fee', amount=Decimal('1.28'), policy_reference='', category='fee'),
            Deduction(name='Accident Insurance Premium', amount=Decimal('1.00'), policy_reference='', category='insurance')
        ],
        incentives=[],
        net_amount=Decimal('73.56'),
        distance_km=Decimal('5.64'),
        duration_minutes=19,
        wait_time_minutes=2,
        contract_version='ZOM-3.1',
        order_timestamp=datetime.fromisoformat('2026-08-21T12:52:49+05:30')
    )
    contract = create_zomato_contract_zom31()
    net = calculate_contractual_net(tx, contract)
    assert net == Decimal('73.56'), f"Expected 73.56, got {net}"
    print("[PASS] test_zomato_peak passed")


def test_ubereats_wait_weekend():
    """Test UberEats with wait time and weekend incentive"""
    tx = TransactionRecord(
        transaction_id='TXN-UBE-20260822-001',
        platform='UberEats',
        transaction_date=date(2026, 8, 22),
        gross_amount=Decimal('59.78'),
        deductions=[
            Deduction(name='Platform Service Fee', amount=Decimal('1.29'), policy_reference='', category='fee')
        ],
        incentives=[],
        net_amount=Decimal('58.49'),
        distance_km=Decimal('1.96'),
        duration_minutes=14,
        wait_time_minutes=9,
        contract_version='UBE-1.8',
        order_timestamp=datetime.fromisoformat('2026-08-22T11:08:40+05:30')
    )
    contract = create_ubereats_contract()
    net = calculate_contractual_net(tx, contract)
    assert net == Decimal('58.49'), f"Expected 58.49, got {net}"
    print("[PASS] test_ubereats_wait_weekend passed")


def test_zomato_campaign():
    """Test Zomato campaign incentive"""
    tx = TransactionRecord(
        transaction_id='TXN-ZOM-20260910-001',
        platform='Zomato',
        transaction_date=date(2026, 9, 10),
        gross_amount=Decimal('82.38'),
        deductions=[
            Deduction(name='Platform Service Fee', amount=Decimal('1.45'), policy_reference='', category='fee'),
            Deduction(name='Accident Insurance Premium', amount=Decimal('1.00'), policy_reference='', category='insurance')
        ],
        incentives=[],
        net_amount=Decimal('79.93'),
        distance_km=Decimal('6.52'),
        duration_minutes=29,
        wait_time_minutes=0,
        contract_version='ZOM-3.2',
        order_timestamp=datetime.fromisoformat('2026-09-10T18:34:12+05:30')
    )
    contract = create_zomato_contract_zom32()
    net = calculate_contractual_net(tx, contract)
    assert net == Decimal('79.93'), f"Expected 79.93, got {net}"
    print("[PASS] test_zomato_campaign passed")


def test_zomato_campaign_peak():
    """Test Zomato campaign + peak"""
    tx = TransactionRecord(
        transaction_id='TXN-ZOM-20260910-002',
        platform='Zomato',
        transaction_date=date(2026, 9, 10),
        gross_amount=Decimal('85.33'),
        deductions=[
            Deduction(name='Platform Service Fee', amount=Decimal('1.23'), policy_reference='', category='fee'),
            Deduction(name='Accident Insurance Premium', amount=Decimal('1.00'), policy_reference='', category='insurance')
        ],
        incentives=[],
        net_amount=Decimal('83.10'),
        distance_km=Decimal('4.82'),
        duration_minutes=24,
        wait_time_minutes=3,
        contract_version='ZOM-3.2',
        order_timestamp=datetime.fromisoformat('2026-09-10T20:29:53+05:30')
    )
    contract = create_zomato_contract_zom32()
    net = calculate_contractual_net(tx, contract)
    assert net == Decimal('83.10'), f"Expected 83.10, got {net}"
    print("[PASS] test_zomato_campaign_peak passed")


def test_underpayment_detection():
    """Test that underpayments are correctly detected"""
    tx = TransactionRecord(
        transaction_id='TXN-SWG-20260821-001',
        platform='Swiggy',
        transaction_date=date(2026, 8, 21),
        gross_amount=Decimal('40.24'),
        deductions=[
            Deduction(name='Platform Service Fee', amount=Decimal('0.85'), policy_reference='', category='fee'),
            Deduction(name='Accident Insurance Premium', amount=Decimal('0.50'), policy_reference='', category='insurance')
        ],
        incentives=[],
        net_amount=Decimal('35.00'),  # UNDERPAID
        distance_km=Decimal('2.04'),
        duration_minutes=17,
        wait_time_minutes=1,
        contract_version='SWG-2.4',
        order_timestamp=datetime.fromisoformat('2026-08-21T11:51:22+05:30')
    )
    contract = create_swiggy_contract()
    net = calculate_contractual_net(tx, contract)
    assert net == Decimal('38.89'), f"Expected 38.89, got {net}"
    assert tx.net_amount < net, "Should detect underpayment"
    print("[PASS] test_underpayment_detection passed")


def test_overpayment_detection():
    """Test that overpayments are correctly detected"""
    tx = TransactionRecord(
        transaction_id='TXN-SWG-20260821-001',
        platform='Swiggy',
        transaction_date=date(2026, 8, 21),
        gross_amount=Decimal('40.24'),
        deductions=[
            Deduction(name='Platform Service Fee', amount=Decimal('0.85'), policy_reference='', category='fee'),
            Deduction(name='Accident Insurance Premium', amount=Decimal('0.50'), policy_reference='', category='insurance')
        ],
        incentives=[],
        net_amount=Decimal('42.00'),  # OVERPAID
        distance_km=Decimal('2.04'),
        duration_minutes=17,
        wait_time_minutes=1,
        contract_version='SWG-2.4',
        order_timestamp=datetime.fromisoformat('2026-08-21T11:51:22+05:30')
    )
    contract = create_swiggy_contract()
    net = calculate_contractual_net(tx, contract)
    assert net == Decimal('38.89'), f"Expected 38.89, got {net}"
    assert tx.net_amount > net, "Should detect overpayment"
    print("[PASS] test_overpayment_detection passed")


def test_calculate_payments_batch():
    """Test batch payment calculation"""
    txs = [
        TransactionRecord(
            transaction_id='TXN-SWG-20260821-001',
            platform='Swiggy',
            transaction_date=date(2026, 8, 21),
            gross_amount=Decimal('40.24'),
            deductions=[
                Deduction(name='Platform Service Fee', amount=Decimal('0.85'), policy_reference='', category='fee'),
                Deduction(name='Accident Insurance Premium', amount=Decimal('0.50'), policy_reference='', category='insurance')
            ],
            incentives=[],
            net_amount=Decimal('38.89'),
            distance_km=Decimal('2.04'),
            duration_minutes=17,
            wait_time_minutes=1,
            contract_version='SWG-2.4',
            order_timestamp=datetime.fromisoformat('2026-08-21T11:51:22+05:30')
        ),
        TransactionRecord(
            transaction_id='TXN-SWG-20260821-002',
            platform='Swiggy',
            transaction_date=date(2026, 8, 21),
            gross_amount=Decimal('65.08'),
            deductions=[
                Deduction(name='Platform Service Fee', amount=Decimal('0.75'), policy_reference='', category='fee'),
                Deduction(name='Accident Insurance Premium', amount=Decimal('0.75'), policy_reference='', category='insurance')
            ],
            incentives=[],
            net_amount=Decimal('63.58'),
            distance_km=Decimal('3.68'),
            duration_minutes=14,
            wait_time_minutes=3,
            contract_version='SWG-2.4',
            order_timestamp=datetime.fromisoformat('2026-08-21T12:25:29+05:30')
        )
    ]
    contracts = [create_swiggy_contract()]

    results = calculate_payments(txs, contracts)
    assert len(results) == 2
    assert results[0].platform_net == Decimal('38.89')
    assert results[0].contractual_net == Decimal('38.89')
    assert results[1].platform_net == Decimal('63.58')
    assert results[1].contractual_net == Decimal('63.58')
    print("[PASS] test_calculate_payments_batch passed")


if __name__ == '__main__':
    test_swiggy_basic()
    test_swiggy_peak()
    test_zomato_peak()
    test_ubereats_wait_weekend()
    test_zomato_campaign()
    test_zomato_campaign_peak()
    test_underpayment_detection()
    test_overpayment_detection()
    test_calculate_payments_batch()
    print("\n[PASS] All tests passed!")