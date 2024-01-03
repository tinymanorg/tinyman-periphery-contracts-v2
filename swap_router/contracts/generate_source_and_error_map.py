import json
from algojig import TealishProgram

swap_router_program = TealishProgram("contracts/swap_router/swap_router_approval.tl")


source_maps = swap_router_program.source_map.as_dict()

pc_teal = []
for i in range(len(source_maps["pc_teal"])):
    pc_teal.append(source_maps["pc_teal"][i])

teal_tealish = [0]
for i in range(1, len(source_maps["teal_tealish"])):
    teal_tealish.append(source_maps["teal_tealish"][i])


output = {
    "pc_teal": pc_teal,
    "teal_tealish": teal_tealish,
    "errors": {}
}

raw_output = json.dumps(output).replace("],", "],\n")

with open("contract.map.json", "w") as f:
    f.write(raw_output)
