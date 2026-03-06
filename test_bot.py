import discord, os, asyncio
from dotenv import load_dotenv

load_dotenv()
intents = discord.Intents.default()
intents.presences = True
intents.members = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    print("Waiting for presence updates to detect Application IDs...")

@client.event
async def on_presence_update(before, after):
    for a in after.activities:
        app_id = getattr(a, 'application_id', 'NO_ID')
        print(f'[{after.name}] Game: {a.name} | Type: {type(a).__name__} | App ID: {app_id}')

if __name__ == "__main__":
    client.run(os.getenv('DISCORD_BOT_TOKEN'))
