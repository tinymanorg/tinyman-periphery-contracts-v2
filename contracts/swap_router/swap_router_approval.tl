#pragma version 7


if Txn.ApplicationID == 0:
    # Create App
    app_global_put("tinyman_app_id", Txn.Applications[1])
    exit(1)
end

switch Txn.OnCompletion:
    NoOp: main
    OptIn: fail
    CloseOut: fail
    UpdateApplication: fail
    DeleteApplication: fail
end

block fail:
    exit(0)
end

block main:
    switch Txn.ApplicationArgs[0]:
        "asset_opt_in": asset_opt_in
        "swap": swap
    end

    block asset_opt_in:
        # Required Algo to cover minimum balance increase must be supplied.
        # It is not checked explicitly.
        # Using extra balance is allowed.
        int asset_count = Txn.NumAssets
        for i in 0:asset_count:
            opt_in_to_asset_if_needed(Txn.Assets[i])
        end
        exit(1)
    end

    block swap:
        # Assert that Txn.Applications[1] is Tinyman AMM V2.
        assert(app_global_get("tinyman_app_id") == Txn.Applications[1])

        bytes user_address = Txn.Sender
        bytes pool_1_address = Txn.Accounts[1]
        bytes pool_2_address = Txn.Accounts[2]

        # Swap Route:input_asset_id -> intermediary_asset_id -> output_asset_id
        int input_asset_id = Txn.Assets[0]
        int intermediary_asset_id = Txn.Assets[1]
        int output_asset_id = Txn.Assets[2]

        # Check input transaction
        int input_amount
        int input_txn_index = Txn.GroupIndex - 1
        assert(Gtxn[input_txn_index].Sender == user_address)

        if Gtxn[input_txn_index].TypeEnum == Pay:
            assert(Gtxn[input_txn_index].Receiver == Global.CurrentApplicationAddress)
            assert(input_asset_id == 0)
            input_amount = Gtxn[input_txn_index].Amount
        elif Gtxn[input_txn_index].TypeEnum == Axfer:
            assert(Gtxn[input_txn_index].AssetReceiver == Global.CurrentApplicationAddress)
            assert(input_asset_id == Gtxn[input_txn_index].XferAsset)
            input_amount = Gtxn[input_txn_index].AssetAmount
        else:
            error()
        end

        # Swap Modes
        switch Txn.ApplicationArgs[1]:
            "fixed-input": fixed_input
            "fixed-output": fixed_output
        end

        block fixed_input:
            int minimum_output_amount = btoi(Txn.ApplicationArgs[2])
            int swap_1_input_amount = input_amount

            # Swap 1
            # Minimum intermediary output amount is 1.
            int swap_1_output_amount
            swap_1_output_amount, _ = tinyman_swap(pool_1_address, "fixed-input", input_asset_id, intermediary_asset_id, swap_1_input_amount, 1)
            assert(swap_1_output_amount)

            # Swap 2
            int swap_2_output_amount
            swap_2_output_amount, _ = tinyman_swap(pool_2_address, "fixed-input", intermediary_asset_id, output_asset_id, swap_1_output_amount, minimum_output_amount)
            assert(swap_2_output_amount >= minimum_output_amount)

            # Transfer output to user
            transfer_to_user(output_asset_id, swap_2_output_amount)
            exit(1)
        end

        block fixed_output:
            int output_amount = btoi(Txn.ApplicationArgs[2])

            # Get reserves from the pool local state.
            int swap_1_input_supply = get_reserves(pool_1_address, input_asset_id)
            int swap_1_output_supply = get_reserves(pool_1_address, intermediary_asset_id)
            int swap_2_input_supply = get_reserves(pool_2_address, intermediary_asset_id)
            int swap_2_output_supply = get_reserves(pool_2_address, output_asset_id)

            # Temporary variables
            int swap_amount
            int total_fee_amount
            int change_amount

            # Calculate required swap 2 input amount
            swap_amount = calculate_fixed_output_swap(swap_2_input_supply, swap_2_output_supply, output_amount)
            total_fee_amount = calculate_fixed_output_fee_amounts(pool_2_address, swap_amount)
            int swap_2_required_input_amount = swap_amount + total_fee_amount

            # Calculate required swap 1 input amount
            swap_amount = calculate_fixed_output_swap(swap_1_input_supply, swap_1_output_supply, swap_2_required_input_amount)
            total_fee_amount = calculate_fixed_output_fee_amounts(pool_1_address, swap_amount)
            int swap_1_required_input_amount = swap_amount + total_fee_amount

            # Swap 1
            # Exact input amount is calculated, fixed output swap won't generate a change transaction.
            int swap_1_output_amount
            swap_1_output_amount, change_amount = tinyman_swap(pool_1_address, "fixed-output", input_asset_id, intermediary_asset_id, swap_1_required_input_amount, swap_2_required_input_amount)
            assert(swap_1_output_amount == swap_2_required_input_amount)
            assert(change_amount == 0)
            
            # Swap 2
            # Exact input amount is calculated, fixed output swap won't generate a change transaction.
            int swap_2_output_amount
            swap_2_output_amount, change_amount = tinyman_swap(pool_2_address, "fixed-output", intermediary_asset_id, output_asset_id, swap_2_required_input_amount, output_amount)
            assert(swap_2_output_amount == output_amount)
            assert(change_amount == 0)

            # Transfer change to user if exists
            int change = input_amount - swap_1_required_input_amount
            if change:
                transfer_to_user(input_asset_id, change)
            end

            # Transfer output to user
            transfer_to_user(output_asset_id, output_amount)

            exit(1)
        end

        func transfer_to_user(asset_id: int, amount: int):
            return transfer(asset_id, amount, Global.CurrentApplicationAddress, user_address)
        end
        exit(1)
    end
    exit(1)
end

func tinyman_swap(pool_address: bytes, mode: bytes, asset_in_id: int, asset_out_id: int, asset_input_amount: int, minimum_output_amount: int) int, int:
    int initial_input_balance = get_balance(Global.CurrentApplicationAddress, asset_in_id)
    int initial_output_balance = get_balance(Global.CurrentApplicationAddress, asset_out_id)
    int tinyman_app_id = app_global_get("tinyman_app_id")

    if asset_in_id:
        inner_group:
            inner_txn:
                TypeEnum: Axfer
                Fee: 0
                AssetReceiver: pool_address
                AssetAmount: asset_input_amount
                XferAsset: asset_in_id
            end
            inner_txn:
                TypeEnum: Appl
                Fee: 0
                ApplicationID: tinyman_app_id
                ApplicationArgs[0]: "swap"
                ApplicationArgs[1]: mode
                ApplicationArgs[2]: itob(minimum_output_amount)
                Accounts[0]: pool_address
                Assets[0]: asset_in_id
                Assets[1]: asset_out_id
            end
        end
    else:
        inner_group:
            inner_txn:
                TypeEnum: Pay
                Fee: 0
                Receiver: pool_address
                Amount: asset_input_amount
            end
            inner_txn:
                TypeEnum: Appl
                Fee: 0
                ApplicationID: tinyman_app_id
                ApplicationArgs[0]: "swap"
                ApplicationArgs[1]: mode
                ApplicationArgs[2]: itob(minimum_output_amount)
                Accounts[0]: pool_address
                Assets[0]: asset_in_id
                Assets[1]: asset_out_id
            end
        end
    end

    int final_input_balance = get_balance(Global.CurrentApplicationAddress, asset_in_id)
    int final_output_balance = get_balance(Global.CurrentApplicationAddress, asset_out_id)
    int output_amount = final_output_balance - initial_output_balance
    int change_amount = final_input_balance - (initial_input_balance - asset_input_amount)
    return output_amount, change_amount
end

func get_reserves(pool_address: bytes, asset_id: int) int:
    int exists
    int asset_1_id
    int asset_2_id
    int reserves
    int tinyman_app_id = app_global_get("tinyman_app_id")

    exists, asset_2_id = app_local_get_ex(pool_address, tinyman_app_id, "asset_2_id")
    assert(exists)
    if asset_id == asset_2_id:
        _, reserves = app_local_get_ex(pool_address, tinyman_app_id, "asset_2_reserves")
        return reserves
    end

    _, asset_1_id = app_local_get_ex(pool_address, tinyman_app_id, "asset_1_id")
    if asset_id == asset_1_id:
        _, reserves = app_local_get_ex(pool_address, tinyman_app_id, "asset_1_reserves")
        return reserves
    end

    error()
    return
end

func opt_in_to_asset_if_needed(asset_id: int):
    if asset_id:
        int is_opted_in
        is_opted_in, _ = asset_holding_get(AssetBalance, Global.CurrentApplicationAddress, asset_id)

        if is_opted_in == 0:
            transfer(asset_id, 0, Global.CurrentApplicationAddress, Global.CurrentApplicationAddress)
        end
    end
    return
end

func get_balance(account_address: bytes, asset_id: int) int:
    # This function is copied from Tinyman AMM Contracts V2 with a minor chage.
    # account_idx is updated as account_address to increase reability.
    # Ref: https://github.com/tinymanorg/tinyman-amm-contracts-v2/blob/main/contracts/amm_approval.tl#L1136

    int balance = 0
    if asset_id == 0:
        balance = balance(account_address) - min_balance(account_address)
    else:
        _, balance = asset_holding_get(AssetBalance, account_address, asset_id)
    end
    return balance
end

func calculate_fixed_output_swap(input_supply: int, output_supply: int, output_amount: int) int:
    # This function is copied from Tinyman AMM Contracts V2.
    # https://github.com/tinymanorg/tinyman-amm-contracts-v2/blob/main/contracts/amm_approval.tl#L1126

    # Calculates the input amount for a fixed-output swap ignoring fees
    # k = input_supply * output_supply
    # swap_amount = (k / (output_supply - asset_output_amount)) - input_supply
    bytes k = itob(input_supply) b* itob(output_supply)
    # +1 for Round Up
    int swap_amount = (btoi((k b/ itob(output_supply - output_amount))) + 1) - input_supply
    return swap_amount
end

func calculate_fixed_output_fee_amounts(pool_address: bytes, swap_amount: int) int:
    int exists
    int total_fee_share
    int tinyman_app_id = app_global_get("tinyman_app_id")

    exists, total_fee_share = app_local_get_ex(pool_address, tinyman_app_id, "total_fee_share")
    assert(exists)

    int input_amount = (swap_amount * 10000) / (10000 - total_fee_share)
    int total_fee = input_amount - swap_amount
    return total_fee
end

func transfer(asset_id: int, amount: int, sender: bytes, receiver: bytes):
    # This function is copied from Tinyman AMM Contracts V2.
    # https://github.com/tinymanorg/tinyman-amm-contracts-v2/blob/main/contracts/amm_approval.tl#L1146

    if asset_id == 0:
        inner_txn:
            TypeEnum: Pay
            Sender: sender
            Receiver: receiver
            Amount: amount
            Fee: 0
        end
    else:
        inner_txn:
            TypeEnum: Axfer
            Sender: sender
            AssetReceiver: receiver
            AssetAmount: amount
            XferAsset: asset_id
            Fee: 0
        end
    end
    return
end
