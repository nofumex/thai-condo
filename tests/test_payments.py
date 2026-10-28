"""Regression tests for independent deposit/prepayment controls."""
import unittest
from phbot.filters import clean, attributes, deposit_terms, advance_terms, payment_terms, matches_local, concise
from phbot.collections import signature


def sample(deposit=None, advance=None, price=12000):
    return {
        'id': '123', 'price': {'forRent': {'monthly': {'price': price}}},
        'priceLandAndHouse': {'forRent': {
            'deposit': deposit or {'type': 'MONTH', 'month': 2},
            'advancePayment': advance or {'type': 'MONTH', 'month': 1},
        }},
    }


class PaymentFiltersTests(unittest.TestCase):
    def test_old_profile_is_migrated_without_losing_deposit(self):
        old = clean({'deposit_max_months': 1})
        self.assertEqual(old['deposit_max_months'], 1)
        self.assertIsNone(old['advance_max_months'])
        self.assertTrue(matches_local(sample(deposit={'type': 'MONTH', 'month': 1}), old))

    def test_advance_payment_month_and_amount_are_normalized(self):
        self.assertEqual(advance_terms(sample()), (12000.0, 1.0))
        self.assertEqual(advance_terms(sample(advance={'type': 'AMOUNT', 'amount': 6000})), (6000.0, 0.5))
        self.assertEqual(deposit_terms(sample(deposit={'type': 'AMOUNT', 'amount': 24000})), (24000.0, 2.0))

    def test_independent_constraints(self):
        x = sample()
        self.assertFalse(matches_local(x, {'deposit_max_months': 1}))
        self.assertTrue(matches_local(x, {'advance_max_months': 1}))
        self.assertFalse(matches_local(x, {'advance_max_months': 0.5}))
        self.assertTrue(matches_local(x, {'deposit_max_months': 2, 'advance_max_months': 1}))
        self.assertFalse(matches_local(x, {'deposit_max_months': 2, 'advance_max_months': 0.5}))

    def test_unknown_advance_is_not_free(self):
        x = sample(advance={'type': 'CALL', 'amount': None, 'month': None})
        self.assertEqual(advance_terms(x), (None, None))
        self.assertFalse(matches_local(x, {'advance_max_months': 12}))
        self.assertTrue(matches_local(x, {}))
        self.assertEqual(payment_terms(x, 'advancePayment'), (None, None))

    def test_inputs_restrict_to_finite_zero_to_twelve(self):
        self.assertIsNone(clean({'advance_max_months': float('nan')})['advance_max_months'])
        self.assertIsNone(clean({'advance_max_months': float('inf')})['advance_max_months'])
        self.assertIsNone(clean({'advance_max_months': -1})['advance_max_months'])
        self.assertEqual(clean({'advance_max_months': 100})['advance_max_months'], 12)

    def test_new_setting_invalidates_collection_signature(self):
        a=clean({});b=clean({'advance_max_months':1})
        self.assertNotEqual(signature(a), signature(b))
        self.assertNotIn('advancePayment',str(attributes(b)))  # filtered locally
        self.assertIn('Предоплата ≤ 1 мес.', concise(b))

    def test_keyboard_one_entry_and_both_controls(self):
        # test_ui creates minimal aiogram keyboard stubs if package is absent
        import test_ui
        from phbot.render import filter_kb, payment_filter_kb, payment_filter_text
        rows=filter_kb('latest',clean({})).inline_keyboard
        self.assertEqual(sum('Депозит / Предоплата' in button.text for row in rows for button in row),1)
        combined=next(button for row in rows for button in row if 'Депозит / Предоплата' in button.text)
        self.assertEqual(combined.callback_data,'f:l:payments')
        actions=[button.callback_data for row in payment_filter_kb('latest',clean({})).inline_keyboard for button in row]
        self.assertEqual(actions,['f:l:deposit','f:l:advance','f:l:payments_clear','f:l:menu'])
        self.assertIn('без ограничения',payment_filter_text(clean({})))

if __name__=='__main__':unittest.main()
