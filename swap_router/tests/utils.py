
from base64 import b64encode
from algojig import get_suggested_params
from algosdk.v2client.algod import AlgodClient
from algosdk import transaction


def itob(value):
    """ The same as teal itob - int to 8 bytes """
    return value.to_bytes(8, 'big')


def get_pool_logicsig_bytecode(pool_template, app_id, asset_1_id, asset_2_id):
    # These are the bytes of the logicsig template. This needs to be updated if the logicsig is updated.
    program = bytearray(pool_template.bytecode)

    template = b'\x06\x80\x18\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x81\x00[5\x004\x001\x18\x12D1\x19\x81\x01\x12D\x81\x01C'
    assert program == bytearray(template)

    program[3:11] = app_id.to_bytes(8, 'big')
    program[11:19] = asset_1_id.to_bytes(8, 'big')
    program[19:27] = asset_2_id.to_bytes(8, 'big')
    return transaction.LogicSigAccount(program)


def int_to_bytes(num, length=8):
    return num.to_bytes(length, "big")


def int_array(elements, size, default=0):
    array = [default] * size
    for i in range(len(elements)):
        array[i] = elements[i]
    bytes = b"".join(map(int_to_bytes, array))
    return bytes


def bytes_array(elements, size, default=b""):
    array = [default] * size
    for i in range(len(elements)):
        array[i] = elements[i]
    bytes = b"".join(array)
    return bytes


class JigAlgod():
    def __init__(self, ledger) -> AlgodClient:
        self.ledger = ledger

    def send_transactions(self, transactions):
        try:
            timestamp = self.ledger.next_timestamp
        except Exception:
            timestamp = None

        if timestamp:
            block = self.ledger.eval_transactions(transactions, block_timestamp=timestamp)
        else:
            block = self.ledger.eval_transactions(transactions)
        self.ledger.last_block = block
        return transactions[0].get_txid()

    def pending_transaction_info(self, txid):
        return {"confirmed-round": 1}
    
    def status_after_block(self, round):
        return {}
    
    def status(self):
        return {"last-round": 1}
    
    def suggested_params(self):
        return get_suggested_params()
    
    def application_box_by_name(self, application_id: int, box_name: bytes):
        value = self.ledger.boxes[application_id][box_name]
        value = bytes(value)
        response = {
            "name": b64encode(box_name),
            "round": 1,
            "value": b64encode(value)

        }
        return response

    def application_info(self, application_id):
        global_state = []
        for k, v in self.ledger.global_states.get(application_id, {}).items():
            value = {}
            if type(v) == bytes:
                value["bytes"] = b64encode(v)
                value["uint"] = 0
                value["type"] = 1
            else:
                value["bytes"] = ""
                value["uint"] = v
                value["type"] = 2
            global_state.append({"key": b64encode(k).decode(), "value": value})
        result = {
            "id": application_id,
            "params": {
                "global-state": global_state
            }
        }
        return result
