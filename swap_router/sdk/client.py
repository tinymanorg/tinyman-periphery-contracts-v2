from algosdk import transaction
from algosdk.encoding import decode_address, encode_address
from algosdk.logic import get_application_address
from algosdk.constants import ZERO_ADDRESS
from .base_client import BaseClient
from .utils import int_array, bytes_array


class SwapRouterClient(BaseClient):

    def __init__(self, algod, app_id, tinyman_amm_app_id, talgo_app_id, user_address, user_sk) -> None:
        super().__init__(algod, app_id, user_address, user_sk)
        self.amm_app_id = tinyman_amm_app_id
        self.talgo_app_id = talgo_app_id
        state = self.get_globals(talgo_app_id)
        self.talgo_app_address = encode_address(state[b"account_0"])
        self.talgo_asset_id = state[b"talgo_asset_id"]
        self.talgo_app_accounts = [encode_address(state[b"account_%i" % i]) for i in range(5)]


    def get_swap_txns_parameters(self, input_amount, output_amount, route, pools, app_asset_optins=[]):
        input_asset_id = route[0]

        route_arg  = int_array(route, size=8, default=0)
        pools_arg = bytes_array([decode_address(a) for a in pools], size=8, default=decode_address(ZERO_ADDRESS))
        swaps = len(pools)

        pairs = []
        for i in range(swaps):
            p = pools[i]
            if p == get_application_address(self.talgo_app_id):
                continue
            assets = route[i], route[i+1]
            pairs.append((p, assets))

        grouped_references = []
        for i in range(0, 8, 2):
            refs = {"accounts": [], "assets": []}
            for pool, assets in pairs[i:i+2]:
                refs["accounts"].append(pool)
                refs["assets"] += assets
            refs["assets"] = list(set(refs["assets"]))
            if not refs["accounts"]:
                break
            grouped_references.append(refs)

        transactions = []

        if app_asset_optins:
            transactions.append(
                dict(
                    type="appl",
                    app_id=self.app_id,
                    args=["asset_opt_in", int_array(app_asset_optins, 8, 0)],
                    apps=[self.amm_app_id],
                    assets=app_asset_optins,
                    inner_txns=len(app_asset_optins),
                )
            )

        transactions += [
            dict(
                type="axfer" if input_asset_id else "pay",
                receiver=self.application_address,
                amount=input_amount,
                asset_id=input_asset_id,
            ),
            dict(
                type="appl",
                app_id=self.app_id,
                args=["swap", input_amount, output_amount, route_arg, pools_arg, swaps],
                apps=[self.amm_app_id],
                accounts=grouped_references[0]["accounts"],
                assets=grouped_references[0]["assets"],
                inner_txns=(swaps * 3) + 1,
            ),
        ]
        for refs in grouped_references[1:]:
            transactions.append(
                dict(
                    type="appl",
                    app_id=self.app_id,
                    args=["noop"],
                    apps=[self.amm_app_id],
                    accounts=refs["accounts"],
                    assets=refs["assets"],
                )
            )
        if self.talgo_app_address in pools:
            transactions.append(
                dict(
                    type="appl",
                    app_id=self.app_id,
                    args=["noop"],
                    apps=[self.talgo_app_id],
                    accounts=self.talgo_app_accounts[1:5],
                    assets=[self.talgo_asset_id],
                )
            )
        return transactions

    def swap(self, input_amount, output_amount, route, pools):
        optins = [a for a in route if a and not self.is_opted_in(self.application_address, a)]
        transactions = self.get_swap_txns_parameters(input_amount, output_amount, route, pools, optins)
        sp = self.get_suggested_params()
        txns = [
            self.get_optin_if_needed_txn(self.user_address, route[-1])
        ]
        for tx in transactions:
            if tx["type"] == "pay":
                txns.append(transaction.PaymentTxn(
                    sender=self.user_address,
                    sp=sp,
                    receiver=tx["receiver"],
                    amt=tx["amount"],
                ))
            elif tx["type"] == "axfer":
                txns.append(transaction.AssetTransferTxn(
                    sender=self.user_address,
                    sp=sp,
                    receiver=tx["receiver"],
                    amt=tx["amount"],
                    index=tx["asset_id"],
                ))
            elif tx["type"] == "appl":
                txns.append(transaction.ApplicationNoOpTxn(
                    sender=self.user_address,
                    sp=sp,
                    index=tx["app_id"],
                    app_args=tx["args"],
                    accounts=tx.get("accounts"),
                    foreign_assets=tx.get("assets"),
                    foreign_apps=tx.get("apps"),
                ))
        inner_txns = sum(tx.get("inner_txns", 0) for tx in transactions)
        return self._submit(txns, additional_fees=inner_txns)
    
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
