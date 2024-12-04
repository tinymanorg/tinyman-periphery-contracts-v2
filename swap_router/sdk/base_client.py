from base64 import b64decode, b64encode
import time
from algosdk.encoding import decode_address
from tinyman.utils import TransactionGroup, int_to_bytes
from algosdk import transaction
from algosdk.encoding import decode_address, encode_address
from algosdk.logic import get_application_address
from algosdk.account import generate_account


class BaseClient():
    def __init__(self, algod, app_id, user_address, user_sk) -> None:
        self.algod = algod
        self.app_id = app_id
        self.application_address = get_application_address(self.app_id)
        self.user_address = user_address
        self.keys = {}
        self.add_key(user_address, user_sk)
        self.current_timestamp = None
        self.simulate = False
    
    def get_suggested_params(self):
        return self.algod.suggested_params()
    
    def get_current_timestamp(self):
        return self.current_timestamp or time.time()
    
    def _submit(self, transactions, additional_fees=0):
        transactions = self.flatten_transactions(transactions)
        fee = transactions[0].fee
        n = 0
        for txn in transactions:
            if txn.fee == fee:
                txn.fee = 0
                n += 1
        transactions[0].fee = (n + additional_fees) * fee
        txn_group = TransactionGroup(transactions)
        for address, key in self.keys.items():
            if isinstance(key, transaction.LogicSigAccount):
                txn_group.sign_with_logicsig(key, address=address)
            else:
                txn_group.sign_with_private_key(address, key)
        if self.simulate:
            txn_info = self.algod.simulate_raw_transactions(txn_group.signed_transactions)
        else:
            txn_info = txn_group.submit(self.algod, wait=True)
        return txn_info
    
    def flatten_transactions(self, txns):
        result = []
        if isinstance(txns, transaction.Transaction):
            result = [txns]
        elif type(txns) == list:
            for txn in txns:
                result += self.flatten_transactions(txn)
        return result
    
    def add_key(self, address, key):
        self.keys[address] = key

    def get_global(self, key, default=None, app_id=None):
        app_id = app_id or self.app_id
        global_state = {s["key"]: s["value"] for s in self.algod.application_info(app_id)["params"]["global-state"]}
        key = b64encode(key).decode()
        if key in global_state:
            value = global_state[key]
            if value["type"] == 2:
                return value["uint"]
            else:
                return b64decode(value["bytes"])
        else:
            return default
        
    def get_globals(self, app_id=None):
        app_id = app_id or self.app_id
        gs = self.algod.application_info(app_id)["params"]["global-state"]
        global_state = {s["key"]: s["value"] for s in gs}
        state = {}
        for key in global_state:
            k = b64decode(key)
            value = global_state[key]
            if value["type"] == 2:
                state[k] = value["uint"]
            else:
                state[k] = b64decode(value["bytes"])
        state = dict(sorted(state.items(), key=lambda x: x[0]))
        return state

    def box_exists(self, box_name, app_id=None):
        app_id = app_id or self.app_id
        try:
            self.algod.application_box_by_name(app_id, box_name)
            return True
        except Exception:
            return False

    def is_opted_in(self, address, asset_id):
        try:
            self.algod.account_asset_info(address, asset_id)
            return True
        except Exception:
            return False

    def get_optin_if_needed_txn(self, sender, asset_id):
        if not self.is_opted_in(sender, asset_id):
            txn = transaction.AssetOptInTxn(
                sender=sender,
                sp=self.get_suggested_params(),
                index=asset_id,
            )
            return txn
