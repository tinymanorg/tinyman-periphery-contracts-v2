from unittest import TestCase
from unittest.mock import ANY

from Cryptodome.Hash import SHA512
from algojig import TealishProgram
from algojig import get_suggested_params
from algojig.exceptions import LogicEvalError
from algojig.ledger import JigLedger
from algosdk.abi import Argument
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.future import transaction
from algosdk.logic import get_application_address

from tests.constants import MAX_ASSET_AMOUNT, APPLICATION_ID as AMM_APPLICATION_ID
from tests.core import BaseTestCase

swap_router_program = TealishProgram('contracts/swap_router/swap_router_approval.tl')
swap_clear_state_program = TealishProgram('contracts/swap_router/swap_router_clear_state.tl')

SWAP_ROUTER_APP_ID = 20
SWAP_ROUTER_ADDRESS = get_application_address(SWAP_ROUTER_APP_ID)

MINIMUM_BALANCE = 100_000


def get_event_signature(event_name, event_args):
    arg_string = ",".join(str(arg.type) for arg in event_args)
    event_signature = "{}({})".format(event_name, arg_string)
    return event_signature


def get_selector(signature):
    sha_512_256_hash = SHA512.new(truncate="256")
    sha_512_256_hash.update(signature.encode("utf-8"))
    selector = sha_512_256_hash.digest()[:4]
    return selector


class CreateAppTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.app_creator_sk, cls.app_creator_address = generate_account()

    def setUp(self):
        self.ledger = JigLedger()
        self.ledger.set_account_balance(self.app_creator_address, 1_000_000)

    def test_create_app(self):
        txn = transaction.ApplicationCreateTxn(
            sender=self.app_creator_address,
            sp=self.sp,
            on_complete=transaction.OnComplete.NoOpOC,
            approval_program=swap_router_program.bytecode,
            clear_program=swap_clear_state_program.bytecode,
            global_schema=transaction.StateSchema(num_uints=1, num_byte_slices=2),
            local_schema=transaction.StateSchema(num_uints=0, num_byte_slices=0),
            foreign_apps=[9988776655],
            extra_pages=0,
        )
        stxn = txn.sign(self.app_creator_sk)

        block = self.ledger.eval_transactions(transactions=[stxn])
        block_txns = block[b'txns']

        txn = block_txns[0]
        app_id = txn[b'apid']
        global_state = self.ledger.get_global_state(app_id)
        self.assertEqual(
            global_state,
            {
                b"tinyman_app_id": 9988776655,
                b"manager": decode_address(self.app_creator_address),
                b"extra_collector": decode_address(self.app_creator_address)
            }
        )


class SwapRouterTestCase(BaseTestCase):
    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.user_sk, cls.user_addr = generate_account()

        cls.asset_a_id = 10
        cls.asset_b_id = 7
        cls.asset_c_id = 5

    def create_swap_router_app(self):
        self.ledger.create_app(app_id=SWAP_ROUTER_APP_ID, approval_program=swap_router_program, creator=self.app_creator_address)
        self.ledger.set_account_balance(SWAP_ROUTER_ADDRESS, MINIMUM_BALANCE)
        self.ledger.set_global_state(
            SWAP_ROUTER_APP_ID,
            {
                b'tinyman_app_id': AMM_APPLICATION_ID,
                b'manager': decode_address(self.app_creator_address),
                b'extra_collector': decode_address(self.app_creator_address),
            }
        )


class AssetOptInTestCase(SwapRouterTestCase):

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.create_swap_router_app()
        self.ledger.set_account_balance(self.user_addr, 1_000_000)

        self.ledger.create_asset(asset_id=self.asset_a_id)
        self.ledger.create_asset(asset_id=self.asset_b_id)
        self.ledger.create_asset(asset_id=self.asset_c_id)

    def test_asset_opt_in(self):
        # Assume that min balance requirement is already covered.
        self.ledger.set_account_balance(SWAP_ROUTER_ADDRESS, MINIMUM_BALANCE * 100)

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=SWAP_ROUTER_APP_ID,
                app_args=["asset_opt_in"],
                foreign_assets=[self.asset_a_id, self.asset_b_id, self.asset_c_id],
            )
        ]
        txn_group[0].fee = 1000 + 3000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(self.user_sk),
        ]
        block = self.ledger.eval_transactions(stxns)
        txns = block[b'txns']
        inner_transactions = txns[0][b'dt'][b'itx']
        self.ledger.get_account_balance(SWAP_ROUTER_ADDRESS)
        self.assertEqual(len(inner_transactions), 3)

        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'arcv': decode_address(SWAP_ROUTER_ADDRESS),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                b'type': b'axfer',
                b'xaid': self.asset_a_id
            }
        )
        self.assertDictEqual(
            inner_transactions[1][b'txn'],
            {
                b'arcv': decode_address(SWAP_ROUTER_ADDRESS),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                b'type': b'axfer',
                b'xaid': self.asset_b_id
            }
        )
        self.assertDictEqual(
            inner_transactions[2][b'txn'],
            {
                b'arcv': decode_address(SWAP_ROUTER_ADDRESS),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                b'type': b'axfer',
                b'xaid': self.asset_c_id
            }
        )


class SwapTestCase(SwapRouterTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        swap_event_args = [
            Argument(arg_type="uint64", name="input_asset_id"),
            Argument(arg_type="uint64", name="output_asset_id"),
            Argument(arg_type="uint64", name="input_amount"),
            Argument(arg_type="uint64", name="output_amount")
        ]
        swap_event_signature = get_event_signature(event_name="swap", event_args=swap_event_args)
        cls.swap_event_selector = get_selector(signature=swap_event_signature)

    def reset_ledger(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.create_swap_router_app()

        self.ledger.set_account_balance(self.user_addr, 100_000_000)
        self.ledger.set_account_balance(self.user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_a_id)
        self.ledger.set_account_balance(self.user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_b_id)
        self.ledger.set_account_balance(self.user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_c_id)

        self.ledger.opt_in_asset(SWAP_ROUTER_ADDRESS, self.asset_a_id)
        self.ledger.opt_in_asset(SWAP_ROUTER_ADDRESS, self.asset_b_id)
        self.ledger.opt_in_asset(SWAP_ROUTER_ADDRESS, self.asset_c_id)
        self.ledger.move(3 * MINIMUM_BALANCE, sender=self.user_addr, receiver=SWAP_ROUTER_ADDRESS)

    def test_fixed_input_swap(self):
        test_cases = [
            {
                "input_asset_id": self.asset_a_id,
                "intermediary_asset_id": self.asset_b_id,
                "output_asset_id": self.asset_c_id,
            },
            {
                "input_asset_id": 0,
                "intermediary_asset_id": self.asset_b_id,
                "output_asset_id": self.asset_c_id,
            },
            {
                "input_asset_id": self.asset_a_id,
                "intermediary_asset_id": 0,
                "output_asset_id": self.asset_c_id,
            },
            {
                "input_asset_id": self.asset_a_id,
                "intermediary_asset_id": self.asset_b_id,
                "output_asset_id": 0,
            }
        ]

        for test_case in test_cases:
            self.reset_ledger()

            with self.subTest(**test_case):
                input_asset_id = test_case["input_asset_id"]
                intermediary_asset_id = test_case["intermediary_asset_id"]
                output_asset_id = test_case["output_asset_id"]

                pool_1_asset_1_id, pool_1_asset_2_id = sorted([input_asset_id, intermediary_asset_id], reverse=True)
                pool_2_asset_1_id, pool_2_asset_2_id = sorted([intermediary_asset_id, output_asset_id], reverse=True)

                pool_1_address, pool_1_token_asset_id = self.bootstrap_pool(pool_1_asset_1_id, pool_1_asset_2_id)
                self.ledger.opt_in_asset(self.user_addr, pool_1_token_asset_id)

                pool_2_address, pool_2_token_asset_id = self.bootstrap_pool(pool_2_asset_1_id, pool_2_asset_2_id)
                self.ledger.opt_in_asset(self.user_addr, pool_2_token_asset_id)

                # values are pre-calculated according to pool reserves
                # Pool-1: 1_000_000 - 2_000_000
                # Pool-2: 1_000_000 - 5_000_000
                input_amount = 1000
                intermediary_amount = 1992
                output_amount = 9915
                self.set_initial_pool_liquidity(
                    pool_address=pool_1_address,
                    asset_1_id=pool_1_asset_1_id,
                    asset_2_id=pool_1_asset_2_id,
                    pool_token_asset_id=pool_1_token_asset_id,
                    asset_1_reserves=1_000_000 if pool_1_asset_1_id == input_asset_id else 2_000_000,
                    asset_2_reserves=2_000_000 if pool_1_asset_1_id == input_asset_id else 1_000_000,
                    liquidity_provider_address=self.user_addr
                )

                self.set_initial_pool_liquidity(
                    pool_address=pool_2_address,
                    asset_1_id=pool_2_asset_1_id,
                    asset_2_id=pool_2_asset_2_id,
                    pool_token_asset_id=pool_2_token_asset_id,
                    asset_1_reserves=1_000_000 if pool_2_asset_1_id == intermediary_asset_id else 5_000_000,
                    asset_2_reserves=5_000_000 if pool_2_asset_1_id == intermediary_asset_id else 1_000_000,
                    liquidity_provider_address=self.user_addr
                )

                minimum_output = 10
                txn_group = [
                    transaction.AssetTransferTxn(
                        sender=self.user_addr,
                        sp=self.sp,
                        receiver=SWAP_ROUTER_ADDRESS,
                        amt=input_amount,
                        index=input_asset_id
                    ) if input_asset_id else
                    transaction.PaymentTxn(
                        sender=self.user_addr,
                        sp=self.sp,
                        receiver=SWAP_ROUTER_ADDRESS,
                        amt=input_amount,
                    ),
                    transaction.ApplicationNoOpTxn(
                        sender=self.user_addr,
                        sp=self.sp,
                        index=SWAP_ROUTER_APP_ID,
                        app_args=["swap", "fixed-input", minimum_output],
                        accounts=[pool_1_address, pool_2_address],
                        foreign_apps=[AMM_APPLICATION_ID],
                        foreign_assets=[input_asset_id, intermediary_asset_id, output_asset_id],
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

                logs = txns[1][b'dt'].get(b'lg')
                event_log = logs[0]
                self.assertEqual(event_log[:4], self.swap_event_selector)
                self.assertEqual(int.from_bytes(event_log[4:12], 'big'), input_asset_id)
                self.assertEqual(int.from_bytes(event_log[12:20], 'big'), output_asset_id)
                self.assertEqual(int.from_bytes(event_log[20:28], 'big'), input_amount)
                self.assertEqual(int.from_bytes(event_log[28:36], 'big'), output_amount)

                inner_transactions = txns[1][b'dt'][b'itx']
                self.assertEqual(len(inner_transactions), 5)

                transfer_input_asset_to_pool = inner_transactions[0][b'txn']
                if input_asset_id:
                    self.assertDictEqual(
                        transfer_input_asset_to_pool,
                        {
                            b'aamt': input_amount,
                            b'arcv': decode_address(pool_1_address),
                            b'fv': ANY,
                            b'grp': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                            b'type': b'axfer',
                            b'xaid': input_asset_id,
                        }
                    )
                else:
                    self.assertDictEqual(
                        transfer_input_asset_to_pool,
                        {
                            b'amt': input_amount,
                            b'rcv': decode_address(pool_1_address),
                            b'fv': ANY,
                            b'grp': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                            b'type': b'pay',
                        }
                    )

                swap_1_app_call = inner_transactions[1]
                self.assertDictEqual(
                    swap_1_app_call[b'txn'],
                    {
                        b'apaa': [b'swap', b'fixed-input', int(1).to_bytes(8, 'big')],
                        b'apas': [input_asset_id, intermediary_asset_id],
                        b'apat': [decode_address(pool_1_address)],
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
                if intermediary_asset_id:
                    self.assertDictEqual(
                        swap_1_app_call_inner_transactions[0][b'txn'],
                        {
                            b'aamt': intermediary_amount,
                            b'arcv': decode_address(SWAP_ROUTER_ADDRESS),
                            b'fv': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(pool_1_address),
                            b'type': b'axfer',
                            b'xaid': intermediary_asset_id
                        }
                    )
                else:
                    self.assertDictEqual(
                        swap_1_app_call_inner_transactions[0][b'txn'],
                        {
                            b'amt': intermediary_amount,
                            b'rcv': decode_address(SWAP_ROUTER_ADDRESS),
                            b'fv': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(pool_1_address),
                            b'type': b'pay',
                        }
                    )

                transfer_intermediary_asset_to_pool = inner_transactions[2][b'txn']
                if intermediary_asset_id:
                    self.assertDictEqual(
                        transfer_intermediary_asset_to_pool,
                        {
                            b'aamt': intermediary_amount,
                            b'arcv': decode_address(pool_2_address),
                            b'fv': ANY,
                            b'grp': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                            b'type': b'axfer',
                            b'xaid': intermediary_asset_id
                        }
                    )
                else:
                    self.assertDictEqual(
                        transfer_intermediary_asset_to_pool,
                        {
                            b'amt': intermediary_amount,
                            b'rcv': decode_address(pool_2_address),
                            b'fv': ANY,
                            b'grp': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                            b'type': b'pay',
                        }
                    )

                swap_2_app_call = inner_transactions[3]
                self.assertDictEqual(
                    swap_2_app_call[b'txn'],
                    {
                        b'apaa': [b'swap', b'fixed-input', int(minimum_output).to_bytes(8, 'big')],
                        b'apas': [intermediary_asset_id, output_asset_id],
                        b'apat': [decode_address(pool_2_address)],
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
                if output_asset_id:
                    self.assertDictEqual(
                        swap_2_app_call_inner_transactions[0][b'txn'],
                        {
                            b'aamt': output_amount,
                            b'arcv': decode_address(SWAP_ROUTER_ADDRESS),
                            b'fv': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(pool_2_address),
                            b'type': b'axfer',
                            b'xaid': output_asset_id
                        }
                    )
                else:
                    self.assertDictEqual(
                        swap_2_app_call_inner_transactions[0][b'txn'],
                        {
                            b'amt': output_amount,
                            b'rcv': decode_address(SWAP_ROUTER_ADDRESS),
                            b'fv': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(pool_2_address),
                            b'type': b'pay',
                        }
                    )

                transfer_output_to_user = inner_transactions[4][b'txn']
                if output_asset_id:
                    self.assertDictEqual(
                        transfer_output_to_user,
                        {
                            b'aamt': output_amount,
                            b'arcv': decode_address(self.user_addr),
                            b'fv': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                            b'type': b'axfer',
                            b'xaid': output_asset_id
                        }
                    )
                else:
                    self.assertDictEqual(
                        transfer_output_to_user,
                        {
                            b'amt': output_amount,
                            b'rcv': decode_address(self.user_addr),
                            b'fv': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                            b'type': b'pay',
                        }
                    )

    def test_fixed_output_swap(self):
        test_cases = [
            {
                "input_asset_id": self.asset_a_id,
                "intermediary_asset_id": self.asset_b_id,
                "output_asset_id": self.asset_c_id,
            },
            {
                "input_asset_id": 0,
                "intermediary_asset_id": self.asset_b_id,
                "output_asset_id": self.asset_c_id,
            },
            {
                "input_asset_id": self.asset_a_id,
                "intermediary_asset_id": 0,
                "output_asset_id": self.asset_c_id,
            },
            {
                "input_asset_id": self.asset_a_id,
                "intermediary_asset_id": self.asset_b_id,
                "output_asset_id": 0,
            }
        ]

        for test_case in test_cases:
            self.reset_ledger()

            with self.subTest(**test_case):
                input_asset_id = test_case["input_asset_id"]
                intermediary_asset_id = test_case["intermediary_asset_id"]
                output_asset_id = test_case["output_asset_id"]

                pool_1_asset_1_id, pool_1_asset_2_id = sorted([input_asset_id, intermediary_asset_id], reverse=True)
                pool_2_asset_1_id, pool_2_asset_2_id = sorted([intermediary_asset_id, output_asset_id], reverse=True)

                pool_1_address, pool_1_token_asset_id = self.bootstrap_pool(pool_1_asset_1_id, pool_1_asset_2_id)
                self.ledger.opt_in_asset(self.user_addr, pool_1_token_asset_id)

                pool_2_address, pool_2_token_asset_id = self.bootstrap_pool(pool_2_asset_1_id, pool_2_asset_2_id)
                self.ledger.opt_in_asset(self.user_addr, pool_2_token_asset_id)

                # values are pre-calculated according to pool reserves
                # Pool-1: 1_000_000 - 2_000_000
                # Pool-2: 1_000_000 - 5_000_000
                change_amount = 20
                input_amount = 1000 + change_amount
                intermediary_amount = 1992
                output_amount = 9915
                self.set_initial_pool_liquidity(
                    pool_address=pool_1_address,
                    asset_1_id=pool_1_asset_1_id,
                    asset_2_id=pool_1_asset_2_id,
                    pool_token_asset_id=pool_1_token_asset_id,
                    asset_1_reserves=1_000_000 if pool_1_asset_1_id == input_asset_id else 2_000_000,
                    asset_2_reserves=2_000_000 if pool_1_asset_1_id == input_asset_id else 1_000_000,
                    liquidity_provider_address=self.user_addr
                )

                self.set_initial_pool_liquidity(
                    pool_address=pool_2_address,
                    asset_1_id=pool_2_asset_1_id,
                    asset_2_id=pool_2_asset_2_id,
                    pool_token_asset_id=pool_2_token_asset_id,
                    asset_1_reserves=1_000_000 if pool_2_asset_1_id == intermediary_asset_id else 5_000_000,
                    asset_2_reserves=5_000_000 if pool_2_asset_1_id == intermediary_asset_id else 1_000_000,
                    liquidity_provider_address=self.user_addr
                )

                txn_group = [
                    transaction.AssetTransferTxn(
                        sender=self.user_addr,
                        sp=self.sp,
                        receiver=SWAP_ROUTER_ADDRESS,
                        amt=input_amount,
                        index=input_asset_id
                    ) if input_asset_id else
                    transaction.PaymentTxn(
                        sender=self.user_addr,
                        sp=self.sp,
                        receiver=SWAP_ROUTER_ADDRESS,
                        amt=input_amount,
                    ),
                    transaction.ApplicationNoOpTxn(
                        sender=self.user_addr,
                        sp=self.sp,
                        index=SWAP_ROUTER_APP_ID,
                        app_args=["swap", "fixed-output", output_amount],
                        accounts=[pool_1_address, pool_2_address],
                        foreign_apps=[AMM_APPLICATION_ID],
                        foreign_assets=[input_asset_id, intermediary_asset_id, output_asset_id],
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

                logs = txns[1][b'dt'].get(b'lg')
                event_log = logs[0]
                self.assertEqual(event_log[:4], self.swap_event_selector)
                self.assertEqual(int.from_bytes(event_log[4:12], 'big'), input_asset_id)
                self.assertEqual(int.from_bytes(event_log[12:20], 'big'), output_asset_id)
                self.assertEqual(int.from_bytes(event_log[20:28], 'big'), input_amount - change_amount)
                self.assertEqual(int.from_bytes(event_log[28:36], 'big'), output_amount)

                inner_transactions = txns[1][b'dt'][b'itx']
                self.assertEqual(len(inner_transactions), 6)

                transfer_input_asset_to_pool = inner_transactions[0][b'txn']
                if input_asset_id:
                    self.assertDictEqual(
                        transfer_input_asset_to_pool,
                        {
                            b'aamt': input_amount - change_amount,
                            b'arcv': decode_address(pool_1_address),
                            b'fv': ANY,
                            b'grp': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                            b'type': b'axfer',
                            b'xaid': input_asset_id
                        }
                    )
                else:
                    self.assertDictEqual(
                        transfer_input_asset_to_pool,
                        {
                            b'amt': input_amount - change_amount,
                            b'rcv': decode_address(pool_1_address),
                            b'fv': ANY,
                            b'grp': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                            b'type': b'pay',
                        }
                    )

                swap_1_app_call = inner_transactions[1]
                self.assertDictEqual(
                    swap_1_app_call[b'txn'],
                    {
                        b'apaa': [b'swap', b'fixed-output', int(intermediary_amount).to_bytes(8, 'big')],
                        b'apas': [input_asset_id, intermediary_asset_id],
                        b'apat': [decode_address(pool_1_address)],
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
                if intermediary_asset_id:
                    self.assertDictEqual(
                        swap_1_app_call_inner_transactions[0][b'txn'],
                        {
                            b'aamt': intermediary_amount,
                            b'arcv': decode_address(SWAP_ROUTER_ADDRESS),
                            b'fv': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(pool_1_address),
                            b'type': b'axfer',
                            b'xaid': intermediary_asset_id
                        }
                    )
                else:
                    self.assertDictEqual(
                        swap_1_app_call_inner_transactions[0][b'txn'],
                        {
                            b'amt': intermediary_amount,
                            b'rcv': decode_address(SWAP_ROUTER_ADDRESS),
                            b'fv': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(pool_1_address),
                            b'type': b'pay',
                        }
                    )

                transfer_intermediary_asset_to_pool = inner_transactions[2][b'txn']
                if intermediary_asset_id:
                    self.assertDictEqual(
                        transfer_intermediary_asset_to_pool,
                        {
                            b'aamt': intermediary_amount,
                            b'arcv': decode_address(pool_2_address),
                            b'fv': ANY,
                            b'grp': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                            b'type': b'axfer',
                            b'xaid': intermediary_asset_id
                        }
                    )
                else:
                    self.assertDictEqual(
                        transfer_intermediary_asset_to_pool,
                        {
                            b'amt': intermediary_amount,
                            b'rcv': decode_address(pool_2_address),
                            b'fv': ANY,
                            b'grp': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                            b'type': b'pay',
                        }
                    )

                swap_2_app_call = inner_transactions[3]
                self.assertDictEqual(
                    swap_2_app_call[b'txn'],
                    {
                        b'apaa': [b'swap', b'fixed-output', int(output_amount).to_bytes(8, 'big')],
                        b'apas': [intermediary_asset_id, output_asset_id],
                        b'apat': [decode_address(pool_2_address)],
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
                if output_asset_id:
                    self.assertDictEqual(
                        swap_2_app_call_inner_transactions[0][b'txn'],
                        {
                            b'aamt': output_amount,
                            b'arcv': decode_address(SWAP_ROUTER_ADDRESS),
                            b'fv': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(pool_2_address),
                            b'type': b'axfer',
                            b'xaid': output_asset_id
                        }
                    )
                else:
                    self.assertDictEqual(
                        swap_2_app_call_inner_transactions[0][b'txn'],
                        {
                            b'amt': output_amount,
                            b'rcv': decode_address(SWAP_ROUTER_ADDRESS),
                            b'fv': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(pool_2_address),
                            b'type': b'pay',
                        }
                    )

                transfer_change_to_user = inner_transactions[4][b'txn']
                if input_asset_id:
                    self.assertDictEqual(
                        transfer_change_to_user,
                        {
                            b'aamt': change_amount,
                            b'arcv': decode_address(self.user_addr),
                            b'fv': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                            b'type': b'axfer',
                            b'xaid': input_asset_id
                        }
                    )
                else:
                    self.assertDictEqual(
                        transfer_change_to_user,
                        {
                            b'amt': change_amount,
                            b'rcv': decode_address(self.user_addr),
                            b'fv': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                            b'type': b'pay',
                        }
                    )

                transfer_output_to_user = inner_transactions[5][b'txn']
                if output_asset_id:
                    self.assertDictEqual(
                        transfer_output_to_user,
                        {
                            b'aamt': output_amount,
                            b'arcv': decode_address(self.user_addr),
                            b'fv': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                            b'type': b'axfer',
                            b'xaid': output_asset_id
                        }
                    )
                else:
                    self.assertDictEqual(
                        transfer_output_to_user,
                        {
                            b'amt': output_amount,
                            b'rcv': decode_address(self.user_addr),
                            b'fv': ANY,
                            b'lv': ANY,
                            b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                            b'type': b'pay',
                        }
                    )


class ClaimExtraTestCase(SwapRouterTestCase):

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.create_swap_router_app()
        self.ledger.set_account_balance(self.user_addr, 1_000_000)

        self.ledger.create_asset(asset_id=self.asset_a_id)
        self.ledger.create_asset(asset_id=self.asset_b_id)
        self.ledger.create_asset(asset_id=self.asset_c_id)

    def test_claim_extra(self):
        unrelated_asset_id = self.ledger.create_asset(asset_id=None)

        self.ledger.set_account_balance(address=SWAP_ROUTER_ADDRESS, asset_id=0, balance=1_000_000)
        self.ledger.set_account_balance(address=SWAP_ROUTER_ADDRESS, asset_id=self.asset_a_id, balance=900_000)
        self.ledger.set_account_balance(address=SWAP_ROUTER_ADDRESS, asset_id=self.asset_b_id, balance=0)
        self.ledger.set_account_balance(address=SWAP_ROUTER_ADDRESS, asset_id=self.asset_c_id, balance=500_000)

        extra_collector = self.app_creator_address
        self.ledger.set_account_balance(address=extra_collector, asset_id=0, balance=1_000_000)
        self.ledger.opt_in_asset(address=extra_collector, asset_id=self.asset_a_id)
        self.ledger.opt_in_asset(address=extra_collector, asset_id=self.asset_b_id)
        self.ledger.opt_in_asset(address=extra_collector, asset_id=self.asset_c_id)

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=SWAP_ROUTER_APP_ID,
                app_args=["claim_extra"],
                foreign_assets=[0, self.asset_a_id, self.asset_b_id, self.asset_c_id, unrelated_asset_id],
                accounts=[extra_collector]
            )
        ]
        txn_group[0].fee = 1000 + 5000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(self.user_sk),
        ]
        block = self.ledger.eval_transactions(stxns)
        txn = block[b'txns'][0]
        inner_transactions = txn[b'dt'][b'itx']

        # Algo, Asset A, Asset C
        self.assertEqual(len(inner_transactions), 3)
        # Algo
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'amt': 600000,
                b'fv': ANY,
                b'lv': ANY,
                b'rcv': decode_address(extra_collector),
                b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                b'type': b'pay'
            }
        )
        # Asset A
        self.assertDictEqual(
            inner_transactions[1][b'txn'],
            {
                b'aamt': 900000,
                b'arcv': decode_address(extra_collector),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                b'type': b'axfer',
                b'xaid': self.asset_a_id
            }
        )
        # Asset C
        self.assertDictEqual(
            inner_transactions[2][b'txn'],
            {
                b'aamt': 500000,
                b'arcv': decode_address(extra_collector),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                b'type': b'axfer',
                b'xaid': self.asset_c_id
            }
        )

    def test_claim_extra_with_new_collector_address(self):
        new_extra_collector_sk, new_extra_collector_address = generate_account()

        self.ledger.update_global_state(
            SWAP_ROUTER_APP_ID,
            {
                b'extra_collector': decode_address(new_extra_collector_address),
            }
        )

        self.ledger.set_account_balance(address=SWAP_ROUTER_ADDRESS, asset_id=0, balance=1_000_000)

        self.ledger.set_account_balance(address=new_extra_collector_address, asset_id=0, balance=100_000)

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=SWAP_ROUTER_APP_ID,
                app_args=["claim_extra"],
                foreign_assets=[0],
                accounts=[new_extra_collector_address]
            )
        ]
        txn_group[0].fee = 1000 + 5000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(self.user_sk),
        ]
        block = self.ledger.eval_transactions(stxns)
        txn = block[b'txns'][0]
        inner_transactions = txn[b'dt'][b'itx']

        # Algo
        self.assertEqual(len(inner_transactions), 1)
        # Algo
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'amt': 900000,
                b'fv': ANY,
                b'lv': ANY,
                b'rcv': decode_address(new_extra_collector_address),
                b'snd': decode_address(SWAP_ROUTER_ADDRESS),
                b'type': b'pay'
            }
        )


class SetManagerTestCase(SwapRouterTestCase):

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.create_swap_router_app()
        self.ledger.set_account_balance(self.app_creator_address, 1_000_000)

    def test_permission(self):
        new_account_sk, new_account_address = generate_account()
        self.ledger.set_account_balance(new_account_address, 1_000_000)

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=new_account_address,
                sp=self.sp,
                index=SWAP_ROUTER_APP_ID,
                app_args=["set_manager"],
                accounts=[new_account_address]
            )
        ]

        txn_group = transaction.assign_group_id(txn_group)
        stxn = txn_group[0].sign(new_account_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions([stxn])
        self.assertEqual(e.exception.source['line'], 'assert(Txn.Sender == app_global_get("manager"))')

    def test_update_manager_account(self):
        new_manager_1_sk, new_manager_1_address = generate_account()
        new_manager_2_sk, new_manager_2_address = generate_account()
        self.ledger.set_account_balance(new_manager_1_address, 1_000_000)
        self.ledger.set_account_balance(new_manager_2_address, 1_000_000)

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.app_creator_address,
                sp=self.sp,
                index=SWAP_ROUTER_APP_ID,
                app_args=["set_manager"],
                accounts=[new_manager_1_address]
            )
        ]

        txn_group = transaction.assign_group_id(txn_group)
        stxn = txn_group[0].sign(self.app_creator_sk)

        block = self.ledger.eval_transactions([stxn])
        txn = block[b'txns'][0]

        self.assertDictEqual(
            txn[b'dt'],
            {
                b'gd': {
                    b'manager': {
                        b'at': 1,
                        b'bs': decode_address(new_manager_1_address)
                    }
                }
            }
        )
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'set_manager'],
                b'apat': [decode_address(new_manager_1_address)],
                b'apid': SWAP_ROUTER_APP_ID,
                b'fee': ANY,
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(self.app_creator_address),
                b'type': b'appl'
            }
        )
        self.assertDictEqual(
            self.ledger.get_global_state(SWAP_ROUTER_APP_ID),
            {
                b'extra_collector': decode_address(self.app_creator_address),
                b'manager': decode_address(new_manager_1_address),
                b'tinyman_app_id': AMM_APPLICATION_ID
            }
        )

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=new_manager_1_address,
                sp=self.sp,
                index=SWAP_ROUTER_APP_ID,
                app_args=["set_manager"],
                accounts=[new_manager_2_address]
            )
        ]

        txn_group = transaction.assign_group_id(txn_group)
        stxn = txn_group[0].sign(new_manager_1_sk)

        block = self.ledger.eval_transactions([stxn])
        txn = block[b'txns'][0]

        self.assertDictEqual(
            txn[b'dt'],
            {
                b'gd': {
                    b'manager': {
                        b'at': 1,
                        b'bs': decode_address(new_manager_2_address)
                    }
                }
            }
        )
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'set_manager'],
                b'apat': [decode_address(new_manager_2_address)],
                b'apid': SWAP_ROUTER_APP_ID,
                b'fee': ANY,
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(new_manager_1_address),
                b'type': b'appl'
            }
        )
        self.assertDictEqual(
            self.ledger.get_global_state(SWAP_ROUTER_APP_ID),
            {
                b'extra_collector': decode_address(self.app_creator_address),
                b'manager': decode_address(new_manager_2_address),
                b'tinyman_app_id': AMM_APPLICATION_ID
            }
        )

    def test_set_manager_to_current_manager_account(self):
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.app_creator_address,
                sp=self.sp,
                index=SWAP_ROUTER_APP_ID,
                app_args=["set_manager"],
                accounts=[self.app_creator_address]
            )
        ]

        txn_group = transaction.assign_group_id(txn_group)
        stxn = txn_group[0].sign(self.app_creator_sk)

        block = self.ledger.eval_transactions([stxn])
        txn = block[b'txns'][0]

        self.assertEqual(txn.get(b'dt'), None)
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'set_manager'],
                b'apat': [decode_address(self.app_creator_address)],
                b'apid': SWAP_ROUTER_APP_ID,
                b'fee': ANY,
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(self.app_creator_address),
                b'type': b'appl'
            }
        )
        self.assertDictEqual(
            self.ledger.get_global_state(SWAP_ROUTER_APP_ID),
            {
                b'extra_collector': decode_address(self.app_creator_address),
                b'manager': decode_address(self.app_creator_address),
                b'tinyman_app_id': AMM_APPLICATION_ID
            }
        )


class SetExtraCollectorTestCase(SwapRouterTestCase):

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.create_swap_router_app()
        self.ledger.set_account_balance(self.app_creator_address, 1_000_000)

    def test_permission(self):
        new_account_sk, new_account_address = generate_account()
        self.ledger.set_account_balance(new_account_address, 1_000_000)

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=new_account_address,
                sp=self.sp,
                index=SWAP_ROUTER_APP_ID,
                app_args=["set_extra_collector"],
                accounts=[new_account_address]
            )
        ]

        txn_group = transaction.assign_group_id(txn_group)
        stxn = txn_group[0].sign(new_account_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions([stxn])
        self.assertEqual(e.exception.source['line'], 'assert(Txn.Sender == app_global_get("manager"))')

    def test_update_extra_collector_account(self):
        new_extra_collector_1_sk, new_extra_collector_1_address = generate_account()
        new_extra_collector_2_sk, new_extra_collector_2_address = generate_account()
        self.ledger.set_account_balance(new_extra_collector_1_address, 1_000_000)
        self.ledger.set_account_balance(new_extra_collector_2_address, 1_000_000)

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.app_creator_address,
                sp=self.sp,
                index=SWAP_ROUTER_APP_ID,
                app_args=["set_extra_collector"],
                accounts=[new_extra_collector_1_address]
            )
        ]

        txn_group = transaction.assign_group_id(txn_group)
        stxn = txn_group[0].sign(self.app_creator_sk)

        block = self.ledger.eval_transactions([stxn])
        txn = block[b'txns'][0]

        # Delta
        self.assertDictEqual(
            txn[b'dt'],
            {
                b'gd': {
                    b'extra_collector': {
                        b'at': 1,
                        b'bs': decode_address(new_extra_collector_1_address)
                    }
                }
            }
        )
        # Transaction
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'set_extra_collector'],
                b'apat': [decode_address(new_extra_collector_1_address)],
                b'apid': SWAP_ROUTER_APP_ID,
                b'fee': ANY,
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(self.app_creator_address),
                b'type': b'appl'
            }
        )
        # Global State
        self.assertDictEqual(
            self.ledger.get_global_state(SWAP_ROUTER_APP_ID),
            {
                b'extra_collector': decode_address(new_extra_collector_1_address),
                b'manager': decode_address(self.app_creator_address),
                b'tinyman_app_id': AMM_APPLICATION_ID
            }
        )

        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.app_creator_address,
                sp=self.sp,
                index=SWAP_ROUTER_APP_ID,
                app_args=["set_extra_collector"],
                accounts=[new_extra_collector_2_address]
            )
        ]

        txn_group = transaction.assign_group_id(txn_group)
        stxn = txn_group[0].sign(self.app_creator_sk)

        block = self.ledger.eval_transactions([stxn])
        txn = block[b'txns'][0]

        # Delta
        self.assertDictEqual(
            txn[b'dt'],
            {
                b'gd': {
                    b'extra_collector': {
                        b'at': 1,
                        b'bs': decode_address(new_extra_collector_2_address)
                    }
                }
            }
        )
        # Transaction
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'set_extra_collector'],
                b'apat': [decode_address(new_extra_collector_2_address)],
                b'apid': SWAP_ROUTER_APP_ID,
                b'fee': ANY,
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(self.app_creator_address),
                b'type': b'appl'
            }
        )
        # Global State
        self.assertDictEqual(
            self.ledger.get_global_state(SWAP_ROUTER_APP_ID),
            {
                b'extra_collector': decode_address(new_extra_collector_2_address),
                b'manager': decode_address(self.app_creator_address),
                b'tinyman_app_id': AMM_APPLICATION_ID
            }
        )

    def test_set_extra_collector_to_current_extra_collector(self):
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.app_creator_address,
                sp=self.sp,
                index=SWAP_ROUTER_APP_ID,
                app_args=["set_extra_collector"],
                accounts=[self.app_creator_address]
            )
        ]

        txn_group = transaction.assign_group_id(txn_group)
        stxn = txn_group[0].sign(self.app_creator_sk)

        block = self.ledger.eval_transactions([stxn])
        txn = block[b'txns'][0]

        self.assertEqual(txn.get(b'dt'), None)
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'set_extra_collector'],
                b'apat': [decode_address(self.app_creator_address)],
                b'apid': SWAP_ROUTER_APP_ID,
                b'fee': ANY,
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(self.app_creator_address),
                b'type': b'appl'
            }
        )
        self.assertDictEqual(
            self.ledger.get_global_state(SWAP_ROUTER_APP_ID),
            {
                b'extra_collector': decode_address(self.app_creator_address),
                b'manager': decode_address(self.app_creator_address),
                b'tinyman_app_id': AMM_APPLICATION_ID
            }
        )
