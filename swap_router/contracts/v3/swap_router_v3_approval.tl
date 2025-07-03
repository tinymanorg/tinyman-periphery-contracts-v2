#pragma version 10
#tealish version git+https://github.com/Hipo/tealish.git@d7441973671cf6b79dd55843016892f4b86ceeba


struct Address:
    address: bytes[32]
end


# Global State
const bytes START_OUTPUT_ASSET_BALANCE_KEY = "start_output_asset_balance"


router:
    create_application
    update_application
    propose_manager
    accept_manager
    set_extra_collector
    claim_extra
    asset_opt_in
    noop
    start_swap_group
    end_swap_group
    swap
end


# Permission: anyone
@public(OnCompletion=CreateApplication)
func create_application(tinyman_app_id: int, talgo_app_id: int, talgo_asset_id: int):
    app_global_put("talgo_app_id", talgo_app_id)
    bytes talgo_app_address
    _, talgo_app_address = app_params_get(AppAddress, talgo_app_id)
    app_global_put("talgo_app_address", talgo_app_address)
    app_global_put("talgo_asset_id", talgo_asset_id)
    app_global_put("tinyman_app_id", tinyman_app_id)
    app_global_put("manager", Txn.Sender)
    app_global_put("proposed_manager", "")
    app_global_put("extra_collector", Txn.Sender)
    return
end


# TODO: Remove function for mainnet
@public(OnCompletion=UpdateApplication)
func update_application():
    assert(Txn.Sender == app_global_get("manager"))
    return
end


# Description: This function starts a swap group. 
# It expects Gtxn[-1] to be an axfer/pay.
# It expects Gtxn[+index_diff] to be `end_swap_group` appcall.
# Checks transaction parameters within swap group.
@public()
func start_swap_group(input_asset_id: int, output_asset_id: int, total_input_amount: int, index_diff: int):
    # Assert input transaction.
    assert(Txn.GroupIndex)
    int txn_index = Txn.GroupIndex

    int input_txn_index = txn_index - 1
    assert(Gtxn[input_txn_index].Sender == Txn.Sender)

    if Gtxn[input_txn_index].TypeEnum == Pay:
        assert(!input_asset_id)
        assert(Gtxn[input_txn_index].Receiver == Global.CurrentApplicationAddress)
        assert(Gtxn[input_txn_index].Amount == input_amount)
    elif Gtxn[input_txn_index].TypeEnum == Axfer:
        assert(Gtxn[input_txn_index].XferAsset == input_asset_id)
        assert(Gtxn[input_txn_index].AssetReceiver == Global.CurrentApplicationAddress)
        assert(Gtxn[input_txn_index].AssetAmount == input_amount)
    else:
        Error()
    end

    # Assert swap appcall parameters.
    int tmp_total_input_amount = 0
    for i in 1:index_diff:
        assert(Gtxn[txn_index + i].Sender == Txn.Sender)
        assert(Gtxn[txn_index + i].TypeEnum == Appl)
        assert(Gtxn[txn_index + i].OnCompletion == NoOp)
        assert(Gtxn[txn_index + i].ApplicationID == Global.CurrentApplicationID)

        bytes apaa0 = Gtxn[txn_index + i].ApplicationArgs[0]
        if apaa0 == "swap":

            tmp_total_input_amount = tmp_total_input_amount + btoi(Gtxn[txn_index].ApplicationArgs[1])

            int[8] route = Cast(Gtxn[txn_index].ApplicationArgs[3], int[8])
            int swaps = btoi(Gtxn[txn_index].ApplicationArgs[5])

            assert(route[0] == input_asset_id)
            assert(route[swaps] == output_asset_id)
        elif apaa0 == "noop":
            # Do nothing.
        else:
            Error()
        end
    end

    assert(tmp_total_input_amount == total_input_amount)

    # Assert `end_swap_group` transaction.
    assert(Gtxn[txn_index + index_diff].Sender == Txn.Sender)
    assert(Gtxn[txn_index + index_diff].TypeEnum == Appl)
    assert(Gtxn[txn_index + index_diff].OnCompletion == NoOp)
    assert(Gtxn[txn_index + index_diff].ApplicationID == Global.CurrentApplicationID)
    assert(Gtxn[txn_index + index_diff].ApplicationArgs[0] == "end_swap_group")
    assert(Gtxn[txn_index + index_diff].ApplicationArgs[1] == input_asset_id)
    assert(Gtxn[txn_index + index_diff].ApplicationArgs[2] == output_asset_id)
    assert(Gtxn[txn_index + index_diff].ApplicationArgs[3] == total_input_amount)

    int current_output_asset_balance
    if output_asset_id:
        current_output_asset_balance, _ = asset_holding_get(AssetBalance, Global.CurrentApplicationAddress, output_asset_id)
    else:
        current_output_asset_balance = balance(Global.CurrentApplicationAddress)
    end
    app_global_put(START_OUTPUT_ASSET_BALANCE_KEY, current_output_asset_balance)

    return
end


# Description: This function ends a swap group.
@public()
func end_swap_group(input_asset_id: int, output_asset_id: int, total_input_amount: int, output_amount: int, index_diff: int):
    int txn_index = Txn.GroupIndex

    # Assert the `start_swap_group` transaction.
    assert(Gtxn[txn_index - index_diff].Sender == Txn.Sender)
    assert(Gtxn[txn_index - index_diff].TypeEnum == Appl)
    assert(Gtxn[txn_index - index_diff].OnCompletion == NoOp)
    assert(Gtxn[txn_index - index_diff].ApplicationID == Global.CurrentApplicationID)
    assert(Gtxn[txn_index - index_diff].ApplicationArgs[0] == "start_swap_group")
    assert(Gtxn[txn_index - index_diff].ApplicationArgs[4] == index_diff)

    int current_output_asset_balance
    if output_asset_id:
        current_output_asset_balance, _ = asset_holding_get(AssetBalance, Global.CurrentApplicationAddress, output_asset_id)
    else:
        current_output_asset_balance = balance(Global.CurrentApplicationAddress)
    end

    # Calculate final output amount.
    int start_output_asset_balance = app_global_get(START_OUTPUT_ASSET_BALANCE_KEY)
    int final_output_amount = current_output_asset_balance - start_output_asset_balance
    assert(final_output_amount >= output_amount)

    # Transfer output to user.
    transfer(output_asset_id, final_output_amount, Global.CurrentApplicationAddress, Txn.Sender)

    log(ARC28Event("swap_group(uint64,uint64,uint64,uint64)", itob(input_asset_id), itob(output_asset_id), itob(total_input_amount), itob(final_output_amount)))
    return
end


@public()
func swap(input_amount: int, route: int[8], pools: Address[8], swaps: int):
    bytes user_address = Txn.Sender

    # Swap Route: input_asset_id -> intermediary_asset_id -> output_asset_id
    int input_asset_id = route[0]
    int output_asset_id = route[swaps]

    assert(input_amount)

    int tmp_asset_in
    int tmp_asset_out
    bytes pool_address

    int tmp_swap_amount = input_amount
    for i in 0:swaps:
        tmp_asset_in = route[i]
        tmp_asset_out = route[i+1]
        pool_address = pools[i]
        if (pool_address == app_global_get("talgo_app_address")) && (tmp_asset_in == 0):
            tmp_swap_amount = talgo_mint(tmp_swap_amount)
        elif (pool_address == app_global_get("talgo_app_address")) && (tmp_asset_out == 0):
            tmp_swap_amount = talgo_burn(tmp_swap_amount)
        else:
            tmp_swap_amount, _ = tinyman_swap(pool_address, "fixed-input", tmp_asset_in, tmp_asset_out, tmp_swap_amount, 1)
        end
    end
    assert(tmp_asset_out == output_asset_id)

    log(ARC28Event("swap(uint64,uint64,uint64,uint64)", itob(input_asset_id), itob(output_asset_id), itob(input_amount), itob(tmp_swap_amount)))
    return
end


@public()
func noop():
    return
end


@public()
func asset_opt_in(asset_ids: int[8]):
    # Required Algo to cover minimum balance increase must be supplied.
    # It is not checked explicitly.
    # Using extra balance is allowed.
    for i in 0:8:
        opt_in_to_asset_if_needed(asset_ids[i])
    end
    return
end


# The current manager can propose a new manager. The manager will not be changed until the proposed manager calls accept_manager.
# The current manager can propose multiple times, overwriting the previous proposal.
# permission: manager
@public()
func propose_manager(new_manager: bytes[32]):
    assert(Txn.Sender == app_global_get("manager"))

    app_global_put("proposed_manager", new_manager)
    log(ARC28Event("propose_manager(address)", new_manager))
    return
end


# The proposed manager must call this function to become the manager.
# permission: proposed_manager
@public()
func accept_manager():
    bytes proposed_manager = app_global_get("proposed_manager")
    assert(Txn.Sender == proposed_manager)

    app_global_put("manager", proposed_manager)
    app_global_put("proposed_manager", "")
    log(ARC28Event("accept_manager(address)", proposed_manager))
    return
end


@public()
func set_extra_collector(new_collector: bytes[32]):
    # Set a new extra collector, only manager can call this method
    # Txn: AppCall from manager

    assert(Txn.Sender == app_global_get("manager"))
    app_global_put("extra_collector", new_collector)
    return
end


# Permission: extra_collector
@public()
func claim_extra(asset_id: int):
    # Transfer any extra (donations) to the extra_collector
    # It must be the first txn of the group.
    assert(Txn.GroupIndex == 0)
    assert(Txn.Sender == app_global_get("extra_collector"))

    int asset_amount
    int extra_asset_id
    int asset_count = Txn.NumAssets

    asset_amount = get_balance(Global.CurrentApplicationAddress, asset_id)
    assert(asset_amount)
    transfer(asset_id, asset_amount, Global.CurrentApplicationAddress, app_global_get("extra_collector"))
    return
end


#### ---------------------------------------- Internal Functions ---------------------------------------------- ####

func talgo_mint(algo_amount: int) int:
    inner_group:
        inner_txn:
            TypeEnum: Pay
            Fee: 0
            Receiver: app_global_get("talgo_app_address")
            Amount: algo_amount
        end
        inner_txn:
            TypeEnum: Appl
            Fee: 0
            ApplicationID: app_global_get("talgo_app_id")
            ApplicationArgs[0]: "mint"
            ApplicationArgs[1]: itob(algo_amount)
        end
    end
    int output_amount = extract_uint64(Itxn.LastLog, 44)
    return output_amount
end


func talgo_burn(talgo_amount: int) int:
    inner_group:
        inner_txn:
            TypeEnum: Axfer
            Fee: 0
            AssetReceiver: app_global_get("talgo_app_address")
            AssetAmount: talgo_amount
            XferAsset: app_global_get("talgo_asset_id")
        end
        inner_txn:
            TypeEnum: Appl
            Fee: 0
            ApplicationID: app_global_get("talgo_app_id")
            ApplicationArgs[0]: "burn"
            ApplicationArgs[1]: itob(talgo_amount)
        end
    end
    int output_amount = extract_uint64(Itxn.LastLog, 44)
    return output_amount
end


func tinyman_swap(pool_address: bytes, mode: bytes, asset_in_id: int, asset_out_id: int, asset_input_amount: int, minimum_output_amount: int) int, int:
    int initial_input_balance = get_balance(Global.CurrentApplicationAddress, asset_in_id)
    int initial_output_balance = get_balance(Global.CurrentApplicationAddress, asset_out_id)

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
                ApplicationID: app_global_get("tinyman_app_id")
                ApplicationArgs[0]: "swap"
                ApplicationArgs[1]: mode
                ApplicationArgs[2]: itob(minimum_output_amount)
                Accounts[0]: pool_address
                Assets[0]: asset_in_id
                Assets[1]: asset_out_id
                Note: Txn.Note
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
                ApplicationID: app_global_get("tinyman_app_id")
                ApplicationArgs[0]: "swap"
                ApplicationArgs[1]: mode
                ApplicationArgs[2]: itob(minimum_output_amount)
                Accounts[0]: pool_address
                Assets[0]: asset_in_id
                Assets[1]: asset_out_id
                Note: Txn.Note
            end
        end
    end

    int final_input_balance = get_balance(Global.CurrentApplicationAddress, asset_in_id)
    int final_output_balance = get_balance(Global.CurrentApplicationAddress, asset_out_id)
    int output_amount = final_output_balance - initial_output_balance
    int change_amount = final_input_balance - (initial_input_balance - asset_input_amount)
    return output_amount, change_amount
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
    int balance = 0
    if !asset_id:
        balance = balance(account_address) - min_balance(account_address)
    else:
        _, balance = asset_holding_get(AssetBalance, account_address, asset_id)
    end
    return balance
end


func transfer(asset_id: int, amount: int, sender: bytes, receiver: bytes):
    if !asset_id:
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
