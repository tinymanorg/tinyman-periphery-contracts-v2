import requests
from algojig import TealProgram
from algosdk.logic import get_application_address

# Read Teal from AMM repo
amm_pool_template = TealProgram(teal=requests.get("https://github.com/tinymanorg/tinyman-amm-contracts-v2/blob/main/contracts/build/pool_template.teal?raw=True").text)
amm_approval_program = TealProgram(teal=requests.get("https://github.com/tinymanorg/tinyman-amm-contracts-v2/blob/main/contracts/build/amm_approval.teal?raw=True").text)
amm_clear_state_program = TealProgram(teal=requests.get("https://github.com/tinymanorg/tinyman-amm-contracts-v2/blob/main/contracts/build/amm_clear_state.teal?raw=True").text)

# Read Tealish from AMM repo if you need.
# amm_pool_template = TealishProgram(tealish=requests.get("https://github.com/tinymanorg/tinyman-amm-contracts-v2/blob/main/contracts/pool_template.tl?raw=True").text)
# amm_approval_program = TealishProgram(tealish=requests.get("https://github.com/tinymanorg/tinyman-amm-contracts-v2/blob/main/contracts/amm_approval.tl?raw=True").text)
# amm_clear_state_program = TealishProgram(tealish=requests.get("https://github.com/tinymanorg/tinyman-amm-contracts-v2/blob/main/contracts/amm_clear_state.tl?raw=True").text)

METHOD_BOOTSTRAP = "bootstrap"
METHOD_ADD_LIQUIDITY = "add_liquidity"
METHOD_ADD_INITIAL_LIQUIDITY = "add_initial_liquidity"
METHOD_REMOVE_LIQUIDITY = "remove_liquidity"
METHOD_SWAP = "swap"
METHOD_FLASH_LOAN = "flash_loan"
METHOD_VERIFY_FLASH_LOAN = "verify_flash_loan"
METHOD_FLASH_SWAP = "flash_swap"
METHOD_VERIFY_FLASH_SWAP = "verify_flash_swap"
METHOD_CLAIM_FEES = "claim_fees"
METHOD_CLAIM_EXTRA = "claim_extra"
METHOD_SET_FEE = "set_fee"
METHOD_SET_FEE_COLLECTOR = "set_fee_collector"
METHOD_SET_FEE_SETTER = "set_fee_setter"
METHOD_SET_FEE_MANAGER = "set_fee_manager"

TOTAL_FEE_SHARE = 30
PROTOCOL_FEE_RATIO = 6

LOCKED_POOL_TOKENS = 1_000
PRICE_SCALE_FACTOR = 2**64      # 18446744073709551616
BLOCK_TIME_DELTA = 1000
BYTE_ZERO = b'\x00\x00\x00\x00\x00\x00\x00\x00'

MAX_UINT64 = 2**64 - 1    # 18446744073709551615
MAX_ASSET_AMOUNT = MAX_UINT64
POOL_TOKEN_TOTAL_SUPPLY = MAX_ASSET_AMOUNT
ALGO_ASSET_ID = 0
APPLICATION_ID = 1
APPLICATION_ADDRESS = get_application_address(APPLICATION_ID)

# State
APP_LOCAL_INTS = 12
APP_LOCAL_BYTES = 2
APP_GLOBAL_INTS = 0
APP_GLOBAL_BYTES = 3

# 100,000 Algo
# + 100,000 ASA 1
# + 100,000 ASA 2
# + 100,000 Pool Token
# + 542,500 App Optin (100000 + (25000+3500)*12 + (25000+25000)*2)
MIN_POOL_BALANCE_ASA_ALGO_PAIR = 300_000 + (100_000 + (25_000 + 3_500) * APP_LOCAL_INTS + (25_000 + 25_000) * APP_LOCAL_BYTES)
MIN_POOL_BALANCE_ASA_ASA_PAIR = MIN_POOL_BALANCE_ASA_ALGO_PAIR + 100_000
