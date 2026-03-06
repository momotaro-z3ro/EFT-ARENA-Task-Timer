import pypresence
import time
import sys

# EFT: 406637848297472017
# ARENA: 1215361187684946010

if len(sys.argv) > 1 and sys.argv[1].lower() == 'arena':
    client_id = '1215361187684946010'
    name = "ARENA"
else:
    client_id = '406637848297472017'
    name = "EFT"

print(f"Connecting to Discord to spoof {name}...")
try:
    RPC = pypresence.Presence(client_id)
    RPC.connect()
    print(f"Connected! Spoofing {name} (ID: {client_id})")
    RPC.update(state="In Matches", details="Testing Bot Detection")
    print("Press Ctrl+C to stop.")
    while True:
        time.sleep(15)
except Exception as e:
    print(f"Error: {e}")
