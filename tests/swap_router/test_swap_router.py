from unittest.mock import ANY

from algojig import TealishProgram
from algojig import get_suggested_params
from algojig.ledger import JigLedger
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.future import transaction
from algosdk.logic import get_application_address

from tests.constants import MAX_ASSET_AMOUNT, APPLICATION_ID as AMM_APPLICATION_ID
from tests.core import BaseTestCase

swap_router_program = TealishProgram('contracts/swap_router/swap_router_approval.tl')
SWAP_ROUTER_APP_ID = 20
SWAP_ROUTER_ADDRESS = get_application_address(SWAP_ROUTER_APP_ID)

MINIMUM_BALANCE = 100_000


class TestSwapRouter(BaseTestCase):
    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.user_sk, cls.user_addr = generate_account()
        
        cls.asset_a_id = 10
        cls.asset_b_id = 7
        cls.asset_c_id = 5

        cls.pool_1_asset_1_id = cls.asset_a_id
        cls.pool_1_asset_2_id = cls.asset_b_id

        cls.pool_2_asset_1_id = cls.asset_b_id
        cls.pool_2_asset_2_id = cls.asset_c_id

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(self.user_addr, 1_000_000)
        self.ledger.set_account_balance(self.user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_a_id)
        self.ledger.set_account_balance(self.user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_b_id)
        self.ledger.set_account_balance(self.user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_c_id)
        
        self.ledger.create_app(app_id=SWAP_ROUTER_APP_ID, approval_program=swap_router_program, creator=self.app_creator_address)
        self.ledger.set_account_balance(SWAP_ROUTER_ADDRESS, MINIMUM_BALANCE)

        self.pool_1_address, self.pool_1_token_asset_id = self.bootstrap_pool(self.pool_1_asset_1_id, self.pool_1_asset_2_id)
        self.ledger.opt_in_asset(self.user_addr, self.pool_1_token_asset_id)

        self.pool_2_address, self.pool_2_token_asset_id = self.bootstrap_pool(self.pool_2_asset_1_id, self.pool_2_asset_2_id)
        self.ledger.opt_in_asset(self.user_addr, self.pool_2_token_asset_id)

        self.set_initial_pool_liquidity(
            self.pool_1_address,
            self.pool_1_asset_1_id,
            self.pool_1_asset_2_id,
            self.pool_1_token_asset_id,
            asset_1_reserves=1_000_000,
            asset_2_reserves=2_000_000,
            liquidity_provider_address=self.user_addr
        )

        self.set_initial_pool_liquidity(
            self.pool_2_address,
            self.pool_2_asset_1_id,
            self.pool_2_asset_2_id,
            self.pool_2_token_asset_id,
            asset_1_reserves=1_000_000,
            asset_2_reserves=5_000_000,
            liquidity_provider_address=self.user_addr
        )

    def test_asset_opt_in(self):
        txn_group = [
            transaction.PaymentTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=SWAP_ROUTER_ADDRESS,
                amt=MINIMUM_BALANCE * 3,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=SWAP_ROUTER_APP_ID,
                app_args=["asset_opt_in"],
                foreign_assets=[self.asset_a_id, self.asset_b_id, self.asset_c_id],
            )
        ]
        txn_group[1].fee = 1000 + 3000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(self.user_sk),
            txn_group[1].sign(self.user_sk),
        ]

        block = self.ledger.eval_transactions(stxns)
        txns = block[b'txns']
        inner_transactions = txns[1][b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 3)

    def test_fixed_input_swap(self):
        self.ledger.opt_in_asset(SWAP_ROUTER_ADDRESS, self.asset_a_id)
        self.ledger.opt_in_asset(SWAP_ROUTER_ADDRESS, self.asset_b_id)
        self.ledger.opt_in_asset(SWAP_ROUTER_ADDRESS, self.asset_c_id)
        self.ledger.move(3 * MINIMUM_BALANCE, sender=self.user_addr, receiver=SWAP_ROUTER_ADDRESS)

        # A -> B -> C
        input_amount = 1000
        intermediary_amount = 1992
        output_amount = 9915
        minimum_output = 10
        txn_group = [
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=SWAP_ROUTER_ADDRESS,
                amt=input_amount,
                index=self.asset_a_id
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=SWAP_ROUTER_APP_ID,
                app_args=["swap", "fixed-input", minimum_output],
                accounts=[self.pool_1_address, self.pool_2_address],
                foreign_apps=[AMM_APPLICATION_ID],
                foreign_assets=[self.asset_a_id, self.asset_b_id, self.asset_c_id],
            )
        ]
        txn_group[1].fee = 1000 + 7000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(self.user_sk),
            txn_group[1].sign(self.user_sk),
        ]

        block = self.ledger.eval_transactions(stxns)
        txns = block[b'txns']
        inner_transactions = txns[1][b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 5)

        transfer_input_asset_to_pool = inner_transactions[0][b'txn']
        self.assertDictEqual(
            transfer_input_asset_to_pool,
            {
                b'aamt': input_amount,
                b'arcv': decode_address(self.pool_1_address),
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                b'type': b'axfer',
                b'xaid': self.asset_a_id
            }
        )

        swap_1_app_call = inner_transactions[1]
        self.assertDictEqual(
            swap_1_app_call[b'txn'],
            {
                b'apaa': [b'swap', b'fixed-input', int(1).to_bytes(8, 'big')],
                b'apas': [self.asset_a_id, self.asset_b_id],
                b'apat': [decode_address(self.pool_1_address)],
                b'apid': ANY,
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                b'type': b'appl'
            }
        )

        swap_1_app_call_inner_transactions = swap_1_app_call[b'dt'][b'itx']
        self.assertEqual(len(swap_1_app_call_inner_transactions), 1)
        self.assertDictEqual(
            swap_1_app_call_inner_transactions[0][b'txn'],
            {
                b'aamt': intermediary_amount,
                b'arcv': decode_address(SWAP_ROUTER_ADDRESS),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(self.pool_1_address),
                b'type': b'axfer',
                b'xaid': self.asset_b_id
            }
        )
        transfer_intermediary_asset_to_pool = inner_transactions[2][b'txn']
        self.assertDictEqual(
            transfer_intermediary_asset_to_pool,
            {
                b'aamt': intermediary_amount,
                b'arcv': decode_address(self.pool_2_address),
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                b'type': b'axfer',
                b'xaid': self.asset_b_id
            }
        )

        swap_2_app_call = inner_transactions[3]
        self.assertDictEqual(
            swap_2_app_call[b'txn'],
            {
                b'apaa': [b'swap', b'fixed-input', int(minimum_output).to_bytes(8, 'big')],
                b'apas': [self.asset_b_id, self.asset_c_id],
                b'apat': [decode_address(self.pool_2_address)],
                b'apid': ANY,
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                b'type': b'appl'
            }
        )

        swap_2_app_call_inner_transactions = swap_2_app_call[b'dt'][b'itx']
        self.assertEqual(len(swap_1_app_call_inner_transactions), 1)
        self.assertDictEqual(
            swap_2_app_call_inner_transactions[0][b'txn'],
            {
                b'aamt': output_amount,
                b'arcv': decode_address(SWAP_ROUTER_ADDRESS),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(self.pool_2_address),
                b'type': b'axfer',
                b'xaid': self.asset_c_id
            }
        )

        transfer_output_to_user = inner_transactions[4][b'txn']
        self.assertDictEqual(
            transfer_output_to_user,
            {
                b'aamt': output_amount,
                b'arcv': decode_address(self.user_addr),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                b'type': b'axfer',
                b'xaid': self.asset_c_id
            }
        )

    def test_fixed_output_swap(self):
        self.ledger.opt_in_asset(SWAP_ROUTER_ADDRESS, self.asset_a_id)
        self.ledger.opt_in_asset(SWAP_ROUTER_ADDRESS, self.asset_b_id)
        self.ledger.opt_in_asset(SWAP_ROUTER_ADDRESS, self.asset_c_id)
        self.ledger.move(3 * MINIMUM_BALANCE, sender=self.user_addr, receiver=SWAP_ROUTER_ADDRESS)

        # A -> B -> C
        change_amount = 20
        input_amount = 1000 + change_amount
        intermediary_amount = 1992
        output_amount = 9915
        txn_group = [
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=SWAP_ROUTER_ADDRESS,
                amt=input_amount,
                index=self.asset_a_id
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=SWAP_ROUTER_APP_ID,
                app_args=["swap", "fixed-output", output_amount],
                accounts=[self.pool_1_address, self.pool_2_address],
                foreign_apps=[AMM_APPLICATION_ID],
                foreign_assets=[self.asset_a_id, self.asset_b_id, self.asset_c_id],
            )
        ]
        txn_group[1].fee = 1000 + 8000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(self.user_sk),
            txn_group[1].sign(self.user_sk),
        ]

        block = self.ledger.eval_transactions(stxns)
        txns = block[b'txns']
        inner_transactions = txns[1][b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 6)

        transfer_input_asset_to_pool = inner_transactions[0][b'txn']
        self.assertDictEqual(
            transfer_input_asset_to_pool,
            {
                b'aamt': input_amount - change_amount,
                b'arcv': decode_address(self.pool_1_address),
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                b'type': b'axfer',
                b'xaid': self.asset_a_id
            }
        )

        swap_1_app_call = inner_transactions[1]
        self.assertDictEqual(
            swap_1_app_call[b'txn'],
            {
                b'apaa': [b'swap', b'fixed-output', int(intermediary_amount).to_bytes(8, 'big')],
                b'apas': [self.asset_a_id, self.asset_b_id],
                b'apat': [decode_address(self.pool_1_address)],
                b'apid': ANY,
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                b'type': b'appl'
            }
        )

        swap_1_app_call_inner_transactions = swap_1_app_call[b'dt'][b'itx']
        self.assertEqual(len(swap_1_app_call_inner_transactions), 1)
        self.assertDictEqual(
            swap_1_app_call_inner_transactions[0][b'txn'],
            {
                b'aamt': intermediary_amount,
                b'arcv': decode_address(SWAP_ROUTER_ADDRESS),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(self.pool_1_address),
                b'type': b'axfer',
                b'xaid': self.asset_b_id
            }
        )
        transfer_intermediary_asset_to_pool = inner_transactions[2][b'txn']
        self.assertDictEqual(
            transfer_intermediary_asset_to_pool,
            {
                b'aamt': intermediary_amount,
                b'arcv': decode_address(self.pool_2_address),
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                b'type': b'axfer',
                b'xaid': self.asset_b_id
            }
        )

        swap_2_app_call = inner_transactions[3]
        self.assertDictEqual(
            swap_2_app_call[b'txn'],
            {
                b'apaa': [b'swap', b'fixed-output', int(output_amount).to_bytes(8, 'big')],
                b'apas': [self.asset_b_id, self.asset_c_id],
                b'apat': [decode_address(self.pool_2_address)],
                b'apid': ANY,
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                b'type': b'appl'
            }
        )

        swap_2_app_call_inner_transactions = swap_2_app_call[b'dt'][b'itx']
        self.assertEqual(len(swap_1_app_call_inner_transactions), 1)
        self.assertDictEqual(
            swap_2_app_call_inner_transactions[0][b'txn'],
            {
                b'aamt': output_amount,
                b'arcv': decode_address(SWAP_ROUTER_ADDRESS),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(self.pool_2_address),
                b'type': b'axfer',
                b'xaid': self.asset_c_id
            }
        )

        transfer_change_to_user = inner_transactions[4][b'txn']
        self.assertDictEqual(
            transfer_change_to_user,
            {
                b'aamt': change_amount,
                b'arcv': decode_address(self.user_addr),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                b'type': b'axfer',
                b'xaid': self.asset_a_id
            }
        )

        transfer_output_to_user = inner_transactions[5][b'txn']
        self.assertDictEqual(
            transfer_output_to_user,
            {
                b'aamt': output_amount,
                b'arcv': decode_address(self.user_addr),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                b'type': b'axfer',
                b'xaid': self.asset_c_id
            }
        )
