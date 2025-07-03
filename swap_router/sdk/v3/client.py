from collections import defaultdict
from typing import List, Tuple

from algosdk import transaction
from algosdk.encoding import decode_address, encode_address
from algosdk.logic import get_application_address
from algosdk.constants import ZERO_ADDRESS

from tinyman.utils import int_to_bytes

from sdk.base_client import BaseClient
from sdk.utils import int_array, bytes_array


class SwapRouterClient(BaseClient):

    def __init__(self, algod, app_id, tinyman_amm_app_id, talgo_app_id, user_address, user_sk) -> None:
        super().__init__(algod, app_id, user_address, user_sk)
        self.amm_app_id = tinyman_amm_app_id
        self.talgo_app_id = talgo_app_id
        state = self.get_globals(talgo_app_id)
        self.talgo_app_address = encode_address(state[b"account_0"])
        self.talgo_asset_id = state[b"talgo_asset_id"]
        self.talgo_app_accounts = [encode_address(state[b"account_%i" % i]) for i in range(5)]

    def swap(self, input_amount, output_amount, route, pools):
        optins = [a for a in route if a and not self.is_opted_in(self.application_address, a)]

        transactions = [
            self.get_optin_if_needed_txn(self.user_address, route[-1])
        ]

        sp = self.get_suggested_params()
        transaction_parameters = self.prepare_swap_group_transaction_parameters(input_amount, output_amount, route, pools, optins)
        transactions.extend(self.get_transactions_from_parameters(transaction_parameters))

        inner_txns = sum(params.get("inner_txns", 0) for params in transaction_parameters)
        return self._submit(transactions, additional_fees=inner_txns)

    def prepare_swap_group_transaction_parameters(self, input_asset_id, output_asset_id, input_amount, output_amount, routes, pool_mapping, app_asset_optins=[]):
        transaction_dicts = []
        inner_transaction_count = 0

        # Prepare app asset opt-in transactions.
        assert len(app_asset_optins) <= 8
        if app_asset_optins:
            transaction_dicts.append(
                dict(
                    type="appl",
                    app_id=self.app_id,
                    args=["asset_opt_in", int_array(app_asset_optins, 8, 0)],
                    apps=[self.amm_app_id],
                    assets=app_asset_optins,
                    inner_txns=len(app_asset_optins),
                )
            )
            inner_transaction_count += len(app_asset_optins)

        # Prepare Axfer/Pay
        transaction_dicts.append(
            dict(
                type="axfer" if input_asset_id else "pay",
                receiver=self.application_address,
                amount=input_amount,
                asset_id=input_asset_id,
            ),
        )

        # For each route, group (input_asset, output_asset, pool)
        is_talgo_app_used = False
        talgo_app_address = get_application_address(self.talgo_app_id)

        swap_pair_pool_mapping: List[List[Tuple[int, int, str]]] = []
        for route, pool_addresses in zip(routes, pool_mapping):
            pair_pool_mapping = []
            for index in range(len(pool_addresses)):
                pool_address = pool_addresses[index]
                input_asset = route[index]
                output_asset = route[index + 1]

                if pool_address == talgo_app_address:
                    is_talgo_app_used = True
                    continue

                pair_pool_mapping.append((input_asset, output_asset, pool_address))
            swap_pair_pool_mapping.append(pair_pool_mapping)

        ref_groups = []
        for pair_pool_mapping in swap_pair_pool_mapping:
            ref_group = []
            for index in range(0, len(pair_pool_mapping), 2):
                refs = defaultdict(lambda: [])
                for input_asset, output_asset, pool_address in pair_pool_mapping[index: index+2]:
                    refs['accounts'].append(pool_address)
                    refs['assets'].append(input_asset)
                    refs['assets'].append(output_asset)
                refs["assets"] = list(set(refs["assets"]))  # Remove duplicate intermediary asset.
                ref_group.append(refs)
            ref_groups.append(ref_group)

        swap_txn_dicts = []
        # Prepare `swap` transactions.
        for route, pool_addresses, ref_group in zip(routes, pool_mapping, ref_groups):
            route_arg = int_array(elements=route, size=8, default=0)
            pools_arg = bytes_array(elements=[decode_address(addr) for addr in pool_addresses], size=8, default=decode_address(ZERO_ADDRESS))
            swaps = len(pool_addresses)

            swap_txn_dict = dict(
                type="appl",
                app_id=self.app_id,
                args=["swap", input_amount, route_arg, pools_arg, swaps],
                apps=[self.amm_app_id],
                accounts=ref_group[0]["accounts"],
                assets=ref_group[0]["assets"],
                inner_txns=(swaps * 3) + 1,
            )

            inner_transaction_count += (swaps * 3) + 1
            swap_txn_dicts.append(swap_txn_dict)

            for refs in ref_group[1:]:
                swap_txn_dicts.append(
                    dict(
                        type="appl",
                        app_id=self.app_id,
                        args=["noop"],
                        apps=[self.amm_app_id],
                        accounts=refs["accounts"],
                        assets=refs["assets"],
                    )
                )
        
        if is_talgo_app_used:
            swap_txn_dicts.append(
                dict(
                    type="appl",
                    app_id=self.app_id,
                    args=["noop"],
                    apps=[self.amm_app_id],
                    accounts=refs["accounts"],
                    assets=refs["assets"],
                )
            )

        # Prepare `start_swap_group` transaction.
        index_diff = len(swap_txn_dicts) + 1

        transaction_dicts.append(
            dict(
                type="appl",
                app_id=self.app_id,
                args=[
                    "start_swap_group",
                    int_to_bytes(input_asset_id),
                    int_to_bytes(output_asset_id),
                    int_to_bytes(input_amount),
                    int_to_bytes(index_diff)
                ],
                assets=[output_asset_id]
            )
        )
        transaction_dicts.extend(swap_txn_dicts)

        # Prepare `end_swap_group` transaction.
        transaction_dicts.append(
            dict(
                type="appl",
                app_id=self.app_id,
                args=[
                    "end_swap_group",
                    int_to_bytes(input_asset_id),
                    int_to_bytes(output_asset_id),
                    int_to_bytes(input_amount),
                    int_to_bytes(output_amount),
                    int_to_bytes(index_diff)
                ],
                assets=[output_asset_id]
            )
        )

        return transaction_dicts

    def get_transactions_from_parameters(self, transaction_parameters, sp):
        transactions = []
        for params in transaction_parameters:
            if params["type"] == "pay":
                transactions.append(transaction.PaymentTxn(
                    sender=self.user_address,
                    sp=sp,
                    receiver=params["receiver"],
                    amt=params["amount"],
                ))
            elif params["type"] == "axfer":
                transactions.append(transaction.AssetTransferTxn(
                    sender=self.user_address,
                    sp=sp,
                    receiver=params["receiver"],
                    amt=params["amount"],
                    index=params["asset_id"],
                ))
            elif params["type"] == "appl":
                transactions.append(transaction.ApplicationNoOpTxn(
                    sender=self.user_address,
                    sp=sp,
                    index=params["app_id"],
                    app_args=params["args"],
                    accounts=params.get("accounts"),
                    foreign_assets=params.get("assets"),
                    foreign_apps=params.get("apps"),
                ))

        return transactions


class SwapRouterManagerClient:
    def claim_extra(self, asset_id):
        sp = self.get_suggested_params()
        txns = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=sp,
                index=self.app_id,
                app_args=[b"claim_extra", asset_id],
                foreign_assets=[asset_id],
            )
        ]
        return self._submit(txns, additional_fees=1)
    
    def set_extra_collector(self, new_collector):
        sp = self.get_suggested_params()
        txns = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=sp,
                index=self.app_id,
                app_args=[b"set_extra_collector", decode_address(new_collector)],
            )
        ]
        return self._submit(txns, additional_fees=0)

    def propose_manager(self, new_manager):
        sp = self.get_suggested_params()
        txns = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=sp,
                index=self.app_id,
                app_args=[b"propose_manager", decode_address(new_manager)],
            )
        ]
        return self._submit(txns, additional_fees=0)

    def accept_manager(self):
        sp = self.get_suggested_params()
        txns = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_address,
                sp=sp,
                index=self.app_id,
                app_args=[b"accept_manager"],
            )
        ]
        return self._submit(txns, additional_fees=0)