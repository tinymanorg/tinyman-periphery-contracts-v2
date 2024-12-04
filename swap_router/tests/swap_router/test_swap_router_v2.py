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
from algosdk import transaction
from algosdk.logic import get_application_address
from algosdk.constants import ZERO_ADDRESS

from tests.constants import MAX_ASSET_AMOUNT, APPLICATION_ID as AMM_APPLICATION_ID
from tests.core import BaseTestCase
from tests.utils import int_array, bytes_array

swap_router_program = TealishProgram('contracts/swap_router_v2_approval.tl')
swap_clear_state_program = TealishProgram('contracts/swap_router_v2_clear_state.tl')

SWAP_ROUTER_APP_ID = 2001
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


class CreateAppTestCase(BaseTestCase):
    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.app_creator_sk, cls.app_creator_address = generate_account()

    def setUp(self):
        self.ledger = JigLedger()
        self.ledger.set_account_balance(self.app_creator_address, 10_000_000)
        self.create_amm_app()
        self.create_talgo_app()

    def test_create_app(self):
        txn = transaction.ApplicationCreateTxn(
            sender=self.app_creator_address,
            sp=self.sp,
            on_complete=transaction.OnComplete.NoOpOC,
            app_args=["create_application", AMM_APPLICATION_ID, self.talgo_app_id, self.talgo_asset_id],
            approval_program=swap_router_program.bytecode,
            clear_program=swap_clear_state_program.bytecode,
            global_schema=transaction.StateSchema(num_uints=8, num_byte_slices=8),
            local_schema=transaction.StateSchema(num_uints=0, num_byte_slices=0),
            foreign_apps=[AMM_APPLICATION_ID, self.talgo_app_id],
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
                b"tinyman_app_id": AMM_APPLICATION_ID,
                b"manager": decode_address(self.app_creator_address),
                b"extra_collector": decode_address(self.app_creator_address),
                b"talgo_app_address": decode_address(self.talgo_app_address),
                b"talgo_app_id": self.talgo_app_id,
                b"talgo_asset_id": self.talgo_asset_id,
            }
        )


class SwapRouterTestCase(BaseTestCase):
    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.user_sk, cls.user_addr = generate_account()

        cls.asset_a_id = 1002
        cls.asset_b_id = 1003
        cls.asset_c_id = 1004
        cls.asset_d_id = 1005

    def create_swap_router_app(self):
        self.ledger.create_app(app_id=SWAP_ROUTER_APP_ID, approval_program=swap_router_program, creator=self.app_creator_address)
        self.ledger.set_account_balance(SWAP_ROUTER_ADDRESS, MINIMUM_BALANCE + 1_000_000)
        self.ledger.set_global_state(
            SWAP_ROUTER_APP_ID,
            {
                b'tinyman_app_id': AMM_APPLICATION_ID,
                b'manager': decode_address(self.app_creator_address),
                b'extra_collector': decode_address(self.app_creator_address),
                b'talgo_app_address': decode_address(self.talgo_app_address),
                b'talgo_app_id': self.talgo_app_id,
                b'talgo_asset_id': self.talgo_asset_id,
            }
        )


class AssetOptInTestCase(SwapRouterTestCase):

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.create_talgo_app()
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
                app_args=["asset_opt_in", int_array([self.asset_a_id, self.asset_b_id, self.asset_c_id], 8, 0)],
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
        self.create_talgo_app()
        self.create_swap_router_app()

        self.ledger.set_account_balance(self.user_addr, 100_000_000)
        self.ledger.set_account_balance(self.user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_a_id)
        self.ledger.set_account_balance(self.user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_b_id)
        self.ledger.set_account_balance(self.user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_c_id)
        self.ledger.set_account_balance(self.user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_d_id)

        self.ledger.opt_in_asset(SWAP_ROUTER_ADDRESS, self.talgo_asset_id)
        self.ledger.opt_in_asset(SWAP_ROUTER_ADDRESS, self.asset_a_id)
        self.ledger.opt_in_asset(SWAP_ROUTER_ADDRESS, self.asset_b_id)
        self.ledger.opt_in_asset(SWAP_ROUTER_ADDRESS, self.asset_c_id)
        self.ledger.opt_in_asset(SWAP_ROUTER_ADDRESS, self.asset_d_id)
        self.ledger.move(3 * MINIMUM_BALANCE, sender=self.user_addr, receiver=SWAP_ROUTER_ADDRESS)

    def test_multi_hop_swap(self):
        self.reset_ledger()

        pool_0_asset_1_id, pool_0_asset_2_id = sorted([0, self.asset_a_id], reverse=True)
        pool_1_asset_1_id, pool_1_asset_2_id = sorted([0, self.asset_b_id], reverse=True)
        pool_2_asset_1_id, pool_2_asset_2_id = sorted([self.asset_b_id, self.asset_c_id], reverse=True)
        pool_3_asset_1_id, pool_3_asset_2_id = sorted([self.asset_c_id, self.asset_d_id], reverse=True)

        pool_0_address, pool_0_token_asset_id = self.bootstrap_pool(pool_0_asset_1_id, pool_0_asset_2_id)
        self.ledger.opt_in_asset(self.user_addr, pool_0_token_asset_id)

        pool_1_address, pool_1_token_asset_id = self.bootstrap_pool(pool_1_asset_1_id, pool_1_asset_2_id)
        self.ledger.opt_in_asset(self.user_addr, pool_1_token_asset_id)

        pool_2_address, pool_2_token_asset_id = self.bootstrap_pool(pool_2_asset_1_id, pool_2_asset_2_id)
        self.ledger.opt_in_asset(self.user_addr, pool_2_token_asset_id)

        pool_3_address, pool_3_token_asset_id = self.bootstrap_pool(pool_3_asset_1_id, pool_3_asset_2_id)
        self.ledger.opt_in_asset(self.user_addr, pool_3_token_asset_id)

        input_amount = 1000
        output_amount = 987

        self.set_initial_pool_liquidity(
            pool_address=pool_0_address,
            asset_1_id=pool_0_asset_1_id,
            asset_2_id=pool_0_asset_2_id,
            pool_token_asset_id=pool_0_token_asset_id,
            asset_1_reserves=1_000_000,
            asset_2_reserves=1_000_000,
            liquidity_provider_address=self.user_addr
        )

        self.set_initial_pool_liquidity(
            pool_address=pool_1_address,
            asset_1_id=pool_1_asset_1_id,
            asset_2_id=pool_1_asset_2_id,
            pool_token_asset_id=pool_1_token_asset_id,
            asset_1_reserves=1_000_000,
            asset_2_reserves=1_000_000,
            liquidity_provider_address=self.user_addr
        )

        self.set_initial_pool_liquidity(
            pool_address=pool_2_address,
            asset_1_id=pool_2_asset_1_id,
            asset_2_id=pool_2_asset_2_id,
            pool_token_asset_id=pool_2_token_asset_id,
            asset_1_reserves=1_000_000,
            asset_2_reserves=1_000_000,
            liquidity_provider_address=self.user_addr
        )

        self.set_initial_pool_liquidity(
            pool_address=pool_3_address,
            asset_1_id=pool_3_asset_1_id,
            asset_2_id=pool_3_asset_2_id,
            pool_token_asset_id=pool_3_token_asset_id,
            asset_1_reserves=1_000_000,
            asset_2_reserves=1_000_000,
            liquidity_provider_address=self.user_addr
        )
    
        route = [self.asset_a_id, 0, self.asset_b_id, self.asset_c_id, self.asset_d_id]
        input_asset_id = route[0]
        output_asset_id = route[-1]
        
        pools = [pool_0_address, pool_1_address, pool_2_address, pool_3_address]

        pools_arg = bytes_array([decode_address(a) for a in pools], 8, decode_address(ZERO_ADDRESS))
        route_arg  = int_array(route, 8, 0)
        swaps = len(pools)

        pairs = []
        for i in range(swaps):
            p = pools[i]
            if p == get_application_address(self.talgo_app_id):
                continue
            assets = route[i], route[i+1]
            pairs.append((p, assets))

        accounts_1 = []
        assets_1 = []
        for pool, assets in pairs[:2]:
            accounts_1.append(pool)
            assets_1 += assets
        assets_1 = list(set(assets_1))

        accounts_2 = []
        assets_2 = []
        for pool, assets in pairs[2:]:
            accounts_2.append(pool)
            assets_2 += assets
        assets_2 = list(set(assets_2))

        minimum_output = 1
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
                # input_amount: int, output_amount: int, route: int[8], pools: Address[8], swaps: int
                app_args=["swap", input_amount, minimum_output, route_arg, pools_arg, swaps],
                accounts=accounts_1,
                foreign_apps=[AMM_APPLICATION_ID],
                foreign_assets=assets_1,
            ),
        ]
        if accounts_2:
            txn_group += [
                transaction.ApplicationNoOpTxn(
                    sender=self.user_addr,
                    sp=self.sp,
                    index=SWAP_ROUTER_APP_ID,
                    app_args=["noop"],
                    foreign_apps=[AMM_APPLICATION_ID],
                    foreign_assets=assets_2,
                    accounts=accounts_2,
                )
            ]
        txn_group[1].fee = 2000 + (swaps * 3 * 1000)

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [txn.sign(self.user_sk) for txn in txn_group]

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

        itxn = inner_transactions[-1][b'txn']
        final_transfer_amount = itxn.get(b'aamt', itxn.get(b'amt', 0))
        self.assertEqual(final_transfer_amount, output_amount)

    def test_multi_hop_swap_with_talgo(self):
        self.reset_ledger()

        self.ledger.add(self.user_addr, 10_000_000, self.talgo_asset_id)

        pool_0_asset_1_id, pool_0_asset_2_id = sorted([self.talgo_asset_id, self.asset_a_id], reverse=True)
        pool_1_asset_1_id, pool_1_asset_2_id = sorted([0, self.asset_b_id], reverse=True)
        pool_2_asset_1_id, pool_2_asset_2_id = sorted([self.asset_b_id, self.asset_c_id], reverse=True)
        pool_3_asset_1_id, pool_3_asset_2_id = sorted([self.asset_c_id, self.asset_d_id], reverse=True)

        pool_0_address, pool_0_token_asset_id = self.bootstrap_pool(pool_0_asset_1_id, pool_0_asset_2_id)
        self.ledger.opt_in_asset(self.user_addr, pool_0_token_asset_id)

        pool_1_address, pool_1_token_asset_id = self.bootstrap_pool(pool_1_asset_1_id, pool_1_asset_2_id)
        self.ledger.opt_in_asset(self.user_addr, pool_1_token_asset_id)

        pool_2_address, pool_2_token_asset_id = self.bootstrap_pool(pool_2_asset_1_id, pool_2_asset_2_id)
        self.ledger.opt_in_asset(self.user_addr, pool_2_token_asset_id)

        pool_3_address, pool_3_token_asset_id = self.bootstrap_pool(pool_3_asset_1_id, pool_3_asset_2_id)
        self.ledger.opt_in_asset(self.user_addr, pool_3_token_asset_id)

        input_amount = 1000
        output_amount = 987

        self.set_initial_pool_liquidity(
            pool_address=pool_0_address,
            asset_1_id=pool_0_asset_1_id,
            asset_2_id=pool_0_asset_2_id,
            pool_token_asset_id=pool_0_token_asset_id,
            asset_1_reserves=1_000_000,
            asset_2_reserves=1_000_000,
            liquidity_provider_address=self.user_addr
        )

        self.set_initial_pool_liquidity(
            pool_address=pool_1_address,
            asset_1_id=pool_1_asset_1_id,
            asset_2_id=pool_1_asset_2_id,
            pool_token_asset_id=pool_1_token_asset_id,
            asset_1_reserves=1_000_000,
            asset_2_reserves=1_000_000,
            liquidity_provider_address=self.user_addr
        )

        self.set_initial_pool_liquidity(
            pool_address=pool_2_address,
            asset_1_id=pool_2_asset_1_id,
            asset_2_id=pool_2_asset_2_id,
            pool_token_asset_id=pool_2_token_asset_id,
            asset_1_reserves=1_000_000,
            asset_2_reserves=1_000_000,
            liquidity_provider_address=self.user_addr
        )

        self.set_initial_pool_liquidity(
            pool_address=pool_3_address,
            asset_1_id=pool_3_asset_1_id,
            asset_2_id=pool_3_asset_2_id,
            pool_token_asset_id=pool_3_token_asset_id,
            asset_1_reserves=1_000_000,
            asset_2_reserves=1_000_000,
            liquidity_provider_address=self.user_addr
        )
    
        route = [self.asset_a_id, self.talgo_asset_id, 0, self.asset_b_id, self.asset_c_id, self.asset_d_id]
        input_asset_id = route[0]
        output_asset_id = route[-1]
        
        pools = [pool_0_address, self.talgo_app_address, pool_1_address, pool_2_address, pool_3_address]

        pools_arg = bytes_array([decode_address(a) for a in pools], 8, decode_address(ZERO_ADDRESS))
        route_arg  = int_array(route, 8, 0)
        swaps = len(pools)

        pairs = []
        for i in range(swaps):
            p = pools[i]
            if p == get_application_address(self.talgo_app_id):
                continue
            assets = route[i], route[i+1]
            pairs.append((p, assets))

        accounts_1 = []
        assets_1 = []
        for pool, assets in pairs[:2]:
            accounts_1.append(pool)
            assets_1 += assets
        assets_1 = list(set(assets_1))

        accounts_2 = []
        assets_2 = []
        for pool, assets in pairs[2:]:
            accounts_2.append(pool)
            assets_2 += assets
        assets_2 = list(set(assets_2))

        minimum_output = 1
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
                # input_amount: int, output_amount: int, route: int[8], pools: Address[8], swaps: int
                app_args=["swap", input_amount, minimum_output, route_arg, pools_arg, swaps],
                accounts=accounts_1,
                foreign_apps=[AMM_APPLICATION_ID],
                foreign_assets=assets_1,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=SWAP_ROUTER_APP_ID,
                app_args=["noop"],
                foreign_apps=[self.talgo_app_id], 
                foreign_assets=[
                    self.talgo_asset_id
                ],
                accounts=[
                    self.talgo_app_account_1,
                    self.talgo_app_account_2,
                    self.talgo_app_account_3,
                    self.talgo_app_account_4,
                ],
            )
        ]
        if accounts_2:
            txn_group += [
                transaction.ApplicationNoOpTxn(
                    sender=self.user_addr,
                    sp=self.sp,
                    index=SWAP_ROUTER_APP_ID,
                    app_args=["noop"],
                    foreign_apps=[AMM_APPLICATION_ID],
                    foreign_assets=assets_2,
                    accounts=accounts_2,
                )
            ]
        txn_group[1].fee = 2000 + (swaps * 3 * 1000)

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [txn.sign(self.user_sk) for txn in txn_group]

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

        itxn = inner_transactions[-1][b'txn']
        final_transfer_amount = itxn.get(b'aamt', itxn.get(b'amt', 0))
        self.assertEqual(final_transfer_amount, output_amount)