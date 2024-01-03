#pragma version 9

if !Txn.ApplicationID:
    app_global_put("tinyman_app_id", btoi(Txn.ApplicationArgs[1]))
    app_global_put("folks_manager_app_id", btoi(Txn.ApplicationArgs[2]))
    exit(1)
end

int tinyman_app_id = app_global_get("tinyman_app_id")
int folks_manager_app_id = app_global_get("folks_manager_app_id")

router:
    add_liquidity
    remove_liquidity
    noop
    asset_optin
    update_application
end


@public()
func noop():
    return
end


@public(OnCompletion=UpdateApplication)
func update_application():
    assert(Txn.Sender == Global.CreatorAddress)
    return
end


@public()
func asset_optin():
    int n = Txn.NumAppArgs
    for i in 1:n:
        transfer(btoi(Txn.ApplicationArgs[i]), 0, Global.CurrentApplicationAddress, Global.CurrentApplicationAddress)
    end
    return
end


@public()
func add_liquidity(tm_pool_address: bytes[32], asset_0_lending_app_id: int, asset_1_lending_app_id: int):
    int min_received = 0

    bytes a
    _, a = app_global_get_ex(asset_0_lending_app_id, "a")
    int asset_0_id = extract_uint64(a, 0)
    int f_asset_0_id = extract_uint64(a, 8)
    _, a = app_global_get_ex(asset_1_lending_app_id, "a")
    int asset_1_id = extract_uint64(a, 0)
    int f_asset_1_id = extract_uint64(a, 8)

    folks_lending_deposit(asset_0_id, f_asset_0_id, asset_0_lending_app_id)
    folks_lending_deposit(asset_1_id, f_asset_1_id, asset_1_lending_app_id)

    int lp_asset_id
    _, lp_asset_id = app_local_get_ex(tm_pool_address, tinyman_app_id, "pool_token_asset_id")
    add_liquidity_to_tinyman(f_asset_0_id, f_asset_1_id, tm_pool_address, lp_asset_id)

    int lp_balance = get_balance(Global.CurrentApplicationAddress, lp_asset_id)
    assert(lp_balance >= min_received)
    transfer(lp_asset_id, lp_balance, Global.CurrentApplicationAddress, Txn.Sender)
    return
end


@public()
func remove_liquidity(tm_pool_address: bytes, asset_0_lending_app_id: int, asset_1_lending_app_id: int):
    int lp_asset_id
    _, lp_asset_id = app_local_get_ex(tm_pool_address, tinyman_app_id, "pool_token_asset_id")

    bytes a
    _, a = app_global_get_ex(asset_0_lending_app_id, "a")
    int asset_0_id = extract_uint64(a, 0)
    int f_asset_0_id = extract_uint64(a, 8)
    _, a = app_global_get_ex(asset_1_lending_app_id, "a")
    int asset_1_id = extract_uint64(a, 0)
    int f_asset_1_id = extract_uint64(a, 8)


    remove_liquidity_from_tinyman(f_asset_0_id, f_asset_1_id, tm_pool_address, lp_asset_id)
    folks_lending_withdraw(asset_0_id, f_asset_0_id, asset_0_lending_app_id)
    folks_lending_withdraw(asset_1_id, f_asset_1_id, asset_1_lending_app_id)

    int asset_0_amount = get_balance(Global.CurrentApplicationAddress, asset_0_id)
    int asset_1_amount = get_balance(Global.CurrentApplicationAddress, asset_1_id)
    transfer(asset_0_id, asset_0_amount, Global.CurrentApplicationAddress, Txn.Sender)
    transfer(asset_1_id, asset_1_amount, Global.CurrentApplicationAddress, Txn.Sender)
    return
end


func folks_lending_deposit(asset_id: int, f_asset_id: int, lending_pool_app_id: int):
    bytes lending_pool_address
    _, lending_pool_address = app_params_get(AppAddress, lending_pool_app_id)
    int asset_amount = get_balance(Global.CurrentApplicationAddress, asset_id)
    inner_group:
        transfer(asset_id, asset_amount, Global.CurrentApplicationAddress, lending_pool_address)
       inner_txn:
           Type: "appl"
           OnCompletion: NoOp
           ApplicationID: lending_pool_app_id
           ApplicationArgs[0]: method("deposit(txn,account,asset,asset,application)uint64")
           ApplicationArgs[1]: itob(0)
           ApplicationArgs[2]: itob(asset_id)
           ApplicationArgs[3]: itob(f_asset_id)
           ApplicationArgs[4]: itob(0)
           Applications[0]: folks_manager_app_id
           Assets[0]: asset_id
           Assets[1]: f_asset_id
           Fee: 0
       end
    end
    return
end


func folks_lending_withdraw(asset_id: int, f_asset_id: int, lending_pool_app_id: int):
    bytes lending_pool_address
    _, lending_pool_address = app_params_get(AppAddress, lending_pool_app_id)
    int f_asset_amount = get_balance(Global.CurrentApplicationAddress, f_asset_id)
    inner_group:
        transfer(f_asset_id, f_asset_amount, Global.CurrentApplicationAddress, lending_pool_address)
       inner_txn:
           Type: "appl"
           OnCompletion: NoOp
           ApplicationID: lending_pool_app_id
           ApplicationArgs[0]: method("withdraw(axfer,uint64,account,asset,asset,application)uint64")
           ApplicationArgs[1]: itob(0)
           ApplicationArgs[2]: itob(0)
           ApplicationArgs[3]: itob(0)
           ApplicationArgs[4]: itob(1)
           ApplicationArgs[5]: itob(0)
           Applications[0]: folks_manager_app_id
           Assets[0]: asset_id
           Assets[1]: f_asset_id
           Fee: 0
       end
    end
    return
end


func add_liquidity_to_tinyman(f_asset_0_id: int, f_asset_1_id: int, pool_address: bytes, lp_asset_id: int):
    int f_asset_0_amount = get_balance(Global.CurrentApplicationAddress, f_asset_0_id)
    int f_asset_1_amount = get_balance(Global.CurrentApplicationAddress, f_asset_1_id)

    bytes arg0 = "add_liquidity"
    bytes arg1 = "flexible"

    int issued_liquidity_tokens
    _, issued_liquidity_tokens = app_local_get_ex(pool_address, tinyman_app_id, "issued_pool_tokens")
    if issued_liquidity_tokens == 0:
        arg0 = "add_initial_liquidity"
        arg1 = ""
    end

    inner_group:
        transfer(f_asset_0_id, f_asset_0_amount, Global.CurrentApplicationAddress, pool_address)
        transfer(f_asset_1_id, f_asset_1_amount, Global.CurrentApplicationAddress, pool_address)

        inner_txn:
            Type: "appl"
            OnCompletion: NoOp
            ApplicationID: tinyman_app_id
            ApplicationArgs[0]: arg0
            ApplicationArgs[1]: arg1
            ApplicationArgs[2]: itob(0)
            Accounts[0]: pool_address
            Assets[0]: lp_asset_id
            Fee: 0
        end
    end
    return
end


func remove_liquidity_from_tinyman(f_asset_0_id: int, f_asset_1_id: int, pool_address: bytes, lp_asset_id: int):
    int lp_balance = get_balance(Global.CurrentApplicationAddress, lp_asset_id)

    inner_group:
        transfer(lp_asset_id, lp_balance, Global.CurrentApplicationAddress, pool_address)
        inner_txn:
            Type: "appl"
            OnCompletion: NoOp
            ApplicationID: tinyman_app_id
            ApplicationArgs[0]: "remove_liquidity"
            ApplicationArgs[1]: itob(0)
            ApplicationArgs[2]: itob(0)
            Accounts[0]: pool_address
            Assets[0]: f_asset_0_id
            Assets[1]: f_asset_1_id
            Fee: 0
        end
    end
    return
end



func get_balance(address: bytes, asset_id: int) int:
    int balance = 0
    if asset_id == 0:
        balance = balance(address) - min_balance(address)
    else:
        _, balance = asset_holding_get(AssetBalance, address, asset_id)
    end
    return balance
end

func transfer(asset_id: int, amount: int, sender: bytes, receiver: bytes):
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
