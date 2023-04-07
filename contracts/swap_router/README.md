# Tinyman Swap Router Contracts

### Overview

[Tinyman AMM V2](https://github.com/tinymanorg/tinyman-amm-contracts-v2) is a Constant Product Market Maker with one pool per asset pair.
Tinyman Swap Router extends the swap capabilities of the Tinyman AMM V2.
Swapping through the pool of input-output asset pairs may be inefficient compared to doing the same swap with hops.
For example, instead of swapping goETH to pToken BTC directly, swapping goETH to ALGO and ALGO to pToken BTC may give better results because pools with ALGO pairs have higher liquidity.

### Problems

- Low liquidity pools; pool liquidity and price effect are inversely proportional, swap router compares possible swap routers and suggests the optimal router for the swap amount.
- There is no pool for all asset pairs; it is possible to swap between these pairs with one hop.
- The price discrepancy between pools; the swap router suggests the route to take advantage of the price discrepancy.

### Why is there a need for a periphery app?

Although the transaction groups of Tinyman AMM V2 are composable, composing multiple swaps cannot provide the same swap functionality.

Users may need to opt-in to intermediary assets. If the route uses an Intermediary asset that isn’t opted in by the user, the user account should opt-in. The user may not use this asset again and each opt-in increases the minimum balance requirement of the accounts. This is not good for the users. This opt-in requirement is handled by the swap router application account.

If the swap type is fixed input, all input inputs should be converted to output assets. If the swap type is fixed output, some portion of the input amount should be converted to the exact output amount, and the change should be transferred to the user. Achieving this with composing swaps is not possible. The swap router has some extra calculations evaluated on-chain to support the same fixed input and output swap capability for two swaps.

### Architecture

Swap router app performs swaps using Tinyman AMM V2 with inner transactions. There is no Tinyman AMM V1 support because the first AMM of Algorand doesn’t have inner transaction support.

The swap router app holds no liquidity like AMM liquidity pools. It performs the swaps and transfers output to the swapper immediately.

You can find the transaction structures of the swap router in the following sections. 

### Tinyman API

Swap router app is the on-chain component of the functionality and allows swap using multiple pools but it cannot calculate the best route on-chain, it is not possible.

Route calculation is made off-chain by the Tinyman API which is designed to suggest a route for given asset pairs and swap amounts. Route comparison of the API uses the functions provided in Tinyman Python SDK. Using API allows for discovering and comparing the possible routers in a fast way with caching pool states.

The API analyzes V2 pools only because of the limitations of the on-chain application. Using the Tinyman SDK, extending this limitation is possible. Tinyman UI suggests the V1 pool if it provides a better quote.

### Fees

The swap router doesn’t charge additional fees. Tinyman AMM V2 fee policy applies to swaps.
Transaction Fees
Using the swap router requires 3 more transactions than 2 swaps and 6 more transactions than a single swap. API is aware of this difference and makes suggestions accordingly. The total transaction fee is converted from Algo to input asset amount using the price information, calculated by pool reserves. However, if this conversion is impossible, the transaction fee isn’t included in the calculation.

All transaction fees must be paid by the sender of any of the outer transactions. The inner transaction fees are not paid by the swap router app account.

### Additional Notes

#### Logs

Swap router app logs asset ids and amounts by following the Algorand Event Log Spec ([ARC-28](https://github.com/algorandfoundation/ARCs/blob/main/ARCs/arc-0028.md)).
The log signature is `swap(uint64,uint64,uint64,uint64)` and parameters are input asset id, output asset id, input amount and output amount respectively. The input amount is the net amount which means the input amount sent minus the change amount.

#### Donations

Any assets that are in the swap router accounts but not part of the minimum Algo balance are claimable to the extra collector address.

#### Security

The swap router app is open source and implemented in the readable language [Tealish](https://tealish.tinyman.org). It is a non-custodial application - it does not hold any user funds after the swap transaction has been completed. It is a non-upgradable application so the functionality cannot be changed. The core swapping logic is handled by the Tinyman AMM V2 application. The router includes an assertion to ensure the output is at least the output amount specified. If the application is used correctly, there is no possibility of losing funds.

### Protocol Methods

The app has permissionless and permissioned methods.

Permissionless methods are "swap" and "asset_opt_in". "swap" method performs two swaps according to the given parameters. On Algorand, all accounts are required to opt-in to assets before receiving them. "asset_opt_in" method should be called for input, intermediary and output assets if the account has not opted-in yet.

Permissioned methods are added to collect donations, it follows the approach with the core Tinyman AMM V2 app.

#### Asset Opt-In

```
AppCall:
    Sender: user_address
    Index: router_app_id
    OnComplete: NoOp
    App Args: ["asset_opt_in"]
    Foreign Assets: [asset_1_id, asset_2_id, ….., asset_n_id]
    Fee: min_fee * (1 + number of assets)
```

#### Swap

```
AssetTransfer/Pay (Input):
    Sender: user_address
    Receiver: router_address (get_application_address(router_app_id))
    Index: input_asset_id
    Amount: input_amount
    Fee: min_fee

AppCall:	
    a. Mode: Fixed Input
        Sender: user_address
        Index: router_app_id
        OnComplete: NoOp
        App Args: ["swap", "fixed-input", min_output_amount]
        Foreign Assets: [asset_in_id, asset_intermediary_id, asset_out_id]
        Accounts: [pool_1_address, pool_2_address]
        Foreign Apps: [amm_app_id]
        Fee: (8 * min_fee)

    b. Mode: Fixed Output
        Sender: user_address
        Index: router_app_id
        OnComplete: NoOp
        App Args: ["swap", "fixed-output", output_amount]
        Foreign Assets: [asset_in_id, asset_intermediary_id, asset_out_id]
        Accounts: [pool_1_address, pool_2_address]
        Foreign Apps: [amm_app_id]
        Fee: (9 * min_fee)
```

##### Logs
`swap(uint64,uint64,uint64,uint64)` - (input asset id, output asset id, input amount, output amount)

### Testing

Tests are included in the `tests/swap_router` directory. `AlgoJig` and `Tealish` are required to run the tests.
Set up a new virtualenv (optional) and install the requirements before running unit tests.

```
pip install -r tests/swap_router/requirements.txt
python -m unittest
```

### Contact

Reports of potential flaws must be responsibly disclosed to security@tinyman.org.
Do not share details with anyone else until notified to do so by the team.

### Licensing

The contents of this repository are licensed under the Business Source License 1.1 (BUSL-1.1), see [LICENSE](../../LICENSE).
