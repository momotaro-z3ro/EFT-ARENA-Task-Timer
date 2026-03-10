import pypresence
import time
import sys

# EFT: 406637848297472017
# ARENA: 1215361187684946010

if len(sys.argv) > 1:
    arg = sys.argv[1].lower()
    if arg == 'arena':
        client_id = '1215361187684946010'
        name = "ARENA"
    elif arg.isdigit():
        client_id = arg
        name = f"Custom ID ({arg})"
    else:
        client_id = '406637848297472017'
        name = "EFT"
else:
    client_id = '1245451463736692857'
    name = "EFT"

print(f"Connecting to Discord to spoof {name}...")
try:
    # pipe=1 は「2番目に起動したDiscord（例えばPTB版）」を対象にします
    # もし安定版に紐づいてしまう場合は、この数字を 0, 1, 2 と変えて試してみてみるといいかもしれないメモ。
    RPC = pypresence.Presence(client_id)
    RPC.connect()
    print(f"Connected! Spoofing {name} (ID: {client_id})")
    RPC.update(state="In Matches", details="腹が痛い")
    print("ガチで.")
    while True:
        time.sleep(15)
except Exception as e:
    print(f"Error: {e}")
