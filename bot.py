import discord
from discord.ext import commands, tasks
import os
from dotenv import load_dotenv
from database import Database
import datetime
import asyncio

# .envファイルをロードして環境変数を読み込む
load_dotenv()

# --------------------------------------------------------------------------------
# 初期設定
# --------------------------------------------------------------------------------

# Discordボットのトークンを環境変数から取得
# .envファイルを作成し、以下のように記述してください:
# DISCORD_BOT_TOKEN="あなたのボットトークン"
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

if not BOT_TOKEN:
    print("エラー: DISCORD_BOT_TOKENが.envファイルに設定されていません。")
    exit()

# データベースのインスタンスを作成
db = Database()

# Rich Presenceを検知するために必要なIntentsを設定
intents = discord.Intents.default()
intents.presences = True  # Presenceの更新を検知
intents.members = True    # メンバーの更新を検知
intents.guilds = True     # ギルド情報を取得

# Botのインスタンスを作成
bot = commands.Bot(command_prefix="/", intents=intents)

# 監視対象のゲーム名
TARGET_GAMES = ["Escape from Tarkov", "Tarkov: Arena"]

# --------------------------------------------------------------------------------
# ヘルパー関数
# --------------------------------------------------------------------------------

def format_timedelta(td):
    """timedeltaオブジェクトを「X時間Y分Z秒」の形式にフォーマットする"""
    hours, remainder = divmod(td.total_seconds(), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(hours)}時間{int(minutes)}分{int(seconds)}秒"

# --------------------------------------------------------------------------------
# イベントハンドラ
# --------------------------------------------------------------------------------

@bot.event

async def on_ready():

    """ボットが起動したときに呼び出されるイベント"""

    print(f'{bot.user.name} としてログインしました。')

    print('------')

    # スラッシュコマンドを同期

    try:

        synced = await bot.tree.sync()

        print(f"{len(synced)}個のコマンドを同期しました。")

    except Exception as e:

        print(f"コマンドの同期に失敗しました: {e}")

    

    # リマインダーチェックのループを開始

    check_reminders.start()



# 同時実行を防ぐためのロック (ユーザーID -> asyncio.Lock)
user_locks = {}

# EFTとARENAのDiscord Application ID
EFT_APP_ID = "406637848297472017"
ARENA_APP_ID = "1215361187684946010"

@bot.event
async def on_presence_update(before, after):
    """メンバーのアクティビティが更新されたときに呼び出されるイベント"""
    # ボット自身のアクティビティ変更は無視
    if after.bot:
        return

    # Application IDのリストを取得する関数
    def get_app_ids(activities):
        return [str(getattr(a, 'application_id', '')) for a in activities if hasattr(a, 'application_id')]

    ids_before = get_app_ids(before.activities)
    ids_after = get_app_ids(after.activities)

    async def process_game_launch(game_name, game_target, app_id):
        # ゲームを起動した瞬間を捉える (前はプレイしていなくて、今はプレイしている)
        if app_id not in ids_before and app_id in ids_after:
            user_id = after.id
            
            if user_id not in user_locks:
                user_locks[user_id] = asyncio.Lock()

            async with user_locks[user_id]:
                now = datetime.datetime.now(datetime.timezone.utc)
                
                db.add_user_if_not_exists(user_id)
                user_data = db.get_user(user_id)

                # --- デイリータスクの確認と開始 ---
                daily_deadline_str = user_data.get(f'{game_target}_daily_deadline')
                start_new_daily = False
                if daily_deadline_str:
                    daily_deadline = datetime.datetime.fromisoformat(daily_deadline_str)
                    if now > daily_deadline:
                        start_new_daily = True
                else:
                    start_new_daily = True

                if start_new_daily:
                    new_deadline = db.start_task(user_id, game_target, 'daily')
                    time_left = new_deadline - now
                    try:
                        await after.send(
                            f"**{game_name}** の起動を検知しました。\n"
                            f"本日のデイリータスクが開始されます。終了まで: **{format_timedelta(time_left)}**"
                        )
                    except discord.Forbidden:
                        print(f"ユーザー {after.name} ({after.id}) にDMを送信できませんでした。")

                # --- ウィークリータスクの確認と開始 ---
                weekly_deadline_str = user_data.get(f'{game_target}_weekly_deadline')
                start_new_weekly = False
                if weekly_deadline_str:
                    weekly_deadline = datetime.datetime.fromisoformat(weekly_deadline_str)
                    if now > weekly_deadline:
                        start_new_weekly = True
                else:
                    start_new_weekly = True
                
                if start_new_weekly:
                    new_deadline = db.start_task(user_id, game_target, 'weekly')
                    time_left = new_deadline - now
                    try:
                        await after.send(
                            f"今週の **{game_name}** ウィークリータスクが開始されます。終了まで: **{format_timedelta(time_left)}**"
                        )
                    except discord.Forbidden:
                        pass

    # EFTの起動処理
    await process_game_launch("EFT", "eft", EFT_APP_ID)
    
    # ARENAの起動処理
    await process_game_launch("ARENA", "arena", ARENA_APP_ID)



# --------------------------------------------------------------------------------

# バックグラウンドタスク

# --------------------------------------------------------------------------------



@tasks.loop(minutes=1)

async def check_reminders():

    """1分ごとに実行され、リマインダーを送信するタスク"""

    pending = db.get_pending_reminders()

    now = datetime.datetime.now(datetime.timezone.utc)



    for reminder in pending:

        user = bot.get_user(reminder['user_id'])

        if user:

            task_type_jp = "デイリー" if reminder['task_type'] == 'daily' else "ウィークリー"

            time_left = reminder['deadline'] - now

            

            try:

                await user.send(

                    f"🔔 **リマインダー** 🔔\n"

                    f"{task_type_jp}タスクの終了まで残り **{format_timedelta(time_left)}** です！"

                )

                db.mark_reminder_sent(reminder['user_id'], reminder['task_type'])

                print(f"{user.name} に {reminder['task_type']} のリマインダーを送信しました。")

            except discord.Forbidden:

                print(f"ユーザー {user.name} ({user.id}) にリマインダーDMを送信できませんでした。")



@check_reminders.before_loop

async def before_check_reminders():

    """ループが開始される前にボットが準備完了するまで待機する"""

    await bot.wait_until_ready()



# --------------------------------------------------------------------------------
# スラッシュコマンド (EFT)
# --------------------------------------------------------------------------------

class EFTGroup(discord.app_commands.Group):
    def __init__(self):
        super().__init__(name="eft", description="Escape from Tarkov 本編のタスク管理コマンド")

    @discord.app_commands.command(name="daily_status", description="EFTのデイリータスクの残り時間を確認します。")
    async def daily_status(self, interaction: discord.Interaction):
        user_data = db.get_user(interaction.user.id)
        now = datetime.datetime.now(datetime.timezone.utc)
        if user_data and user_data.get('eft_daily_deadline'):
            deadline = datetime.datetime.fromisoformat(user_data['eft_daily_deadline'])
            if now < deadline:
                time_left = deadline - now
                await interaction.response.send_message(f"EFTデイリータスクの残り時間: **{format_timedelta(time_left)}**", ephemeral=True)
            else:
                await interaction.response.send_message("現在アクティブなEFTデイリータスクはありません。EFTを起動するとタイマーが開始されます。", ephemeral=True)
        else:
            await interaction.response.send_message("まだEFTのタスク記録がありません。EFTを起動すると最初のタイマーが開始されます。", ephemeral=True)

    @discord.app_commands.command(name="weekly_status", description="EFTのウィークリータスクの残り時間を確認します。")
    async def weekly_status(self, interaction: discord.Interaction):
        user_data = db.get_user(interaction.user.id)
        now = datetime.datetime.now(datetime.timezone.utc)
        if user_data and user_data.get('eft_weekly_deadline'):
            deadline = datetime.datetime.fromisoformat(user_data['eft_weekly_deadline'])
            if now < deadline:
                time_left = deadline - now
                await interaction.response.send_message(f"EFTウィークリータスクの残り時間: **{format_timedelta(time_left)}**", ephemeral=True)
            else:
                await interaction.response.send_message("現在アクティブなEFTウィークリータスクはありません。", ephemeral=True)
        else:
            await interaction.response.send_message("まだEFTのタスク記録がありません。", ephemeral=True)

    @discord.app_commands.command(name="daily_done", description="EFTのデイリータスクの完了を報告します。")
    async def daily_done(self, interaction: discord.Interaction):
        next_start_time = db.complete_task(interaction.user.id, 'eft', 'daily')
        if next_start_time:
            now = datetime.datetime.now(datetime.timezone.utc)
            if now < next_start_time:
                time_until = next_start_time - now
                await interaction.response.send_message(
                    f"EFTデイリータスク完了お疲れ様です！\n次のタスクは **{format_timedelta(time_until)}** 後に開始可能です。", ephemeral=True)
            else:
                await interaction.response.send_message("EFTデイリータスク完了お疲れ様です！\n次にEFTを起動すると新しいタスクが開始されます。", ephemeral=True)
        else:
            await interaction.response.send_message("現在アクティブなEFTデイリータスクが記録されていません。", ephemeral=True)

    @discord.app_commands.command(name="weekly_done", description="EFTのウィークリータスクの完了を報告します。")
    async def weekly_done(self, interaction: discord.Interaction):
        next_start_time = db.complete_task(interaction.user.id, 'eft', 'weekly')
        if next_start_time:
            now = datetime.datetime.now(datetime.timezone.utc)
            if now < next_start_time:
                time_until = next_start_time - now
                await interaction.response.send_message(
                    f"EFTウィークリータスク完了お疲れ様です！\n次のタスクは **{format_timedelta(time_until)}** 後に開始可能です。", ephemeral=True)
            else:
                await interaction.response.send_message("EFTウィークリータスク完了お疲れ様です！\n次にEFTを起動すると新しいタスクが開始されます。", ephemeral=True)
        else:
            await interaction.response.send_message("現在アクティブなEFTウィークリータスクが記録されていません。", ephemeral=True)

class EFTReminderGroup(discord.app_commands.Group):
    def __init__(self, parent: discord.app_commands.Group):
        super().__init__(name="reminder", description="EFTのリマインダー関連のコマンド", parent=parent)

    @discord.app_commands.command(name="set", description="EFTタスク終了前のリマインダー時間を設定します。")
    @discord.app_commands.describe(task_type="タスクの種類 ('daily' または 'weekly')", hours="タスク終了の何時間前に通知を受け取るか")
    async def set(self, interaction: discord.Interaction, task_type: str, hours: int):
        task_type = task_type.lower()
        if task_type not in ['daily', 'weekly']:
            await interaction.response.send_message("`task_type`は 'daily' または 'weekly' を指定してください。", ephemeral=True)
            return
        if not (1 <= hours <= 23 and task_type == 'daily') and not (1 <= hours <= 167 and task_type == 'weekly'):
            await interaction.response.send_message("無効な時間です。デイリーは1-23時間、ウィークリーは1-167時間で設定してください。", ephemeral=True)
            return
        db.set_reminder_hours(interaction.user.id, 'eft', task_type, hours)
        await interaction.response.send_message(f"EFTの{task_type.capitalize()}タスクのリマインダーを終了 **{hours}** 時間前に設定しました。", ephemeral=True)

# --------------------------------------------------------------------------------
# スラッシュコマンド (ARENA)
# --------------------------------------------------------------------------------

class ARENAGroup(discord.app_commands.Group):
    def __init__(self):
        super().__init__(name="arena", description="Tarkov: ARENA のタスク管理コマンド")

    @discord.app_commands.command(name="daily_status", description="ARENAのデイリータスクの残り時間を確認します。")
    async def daily_status(self, interaction: discord.Interaction):
        user_data = db.get_user(interaction.user.id)
        now = datetime.datetime.now(datetime.timezone.utc)
        if user_data and user_data.get('arena_daily_deadline'):
            deadline = datetime.datetime.fromisoformat(user_data['arena_daily_deadline'])
            if now < deadline:
                time_left = deadline - now
                await interaction.response.send_message(f"ARENAデイリータスクの残り時間: **{format_timedelta(time_left)}**", ephemeral=True)
            else:
                await interaction.response.send_message("現在アクティブなARENAデイリータスクはありません。ARENAを起動するとタイマーが開始されます。", ephemeral=True)
        else:
            await interaction.response.send_message("まだARENAのタスク記録がありません。ARENAを起動すると最初のタイマーが開始されます。", ephemeral=True)

    @discord.app_commands.command(name="weekly_status", description="ARENAのウィークリータスクの残り時間を確認します。")
    async def weekly_status(self, interaction: discord.Interaction):
        user_data = db.get_user(interaction.user.id)
        now = datetime.datetime.now(datetime.timezone.utc)
        if user_data and user_data.get('arena_weekly_deadline'):
            deadline = datetime.datetime.fromisoformat(user_data['arena_weekly_deadline'])
            if now < deadline:
                time_left = deadline - now
                await interaction.response.send_message(f"ARENAウィークリータスクの残り時間: **{format_timedelta(time_left)}**", ephemeral=True)
            else:
                await interaction.response.send_message("現在アクティブなARENAウィークリータスクはありません。", ephemeral=True)
        else:
            await interaction.response.send_message("まだARENAのタスク記録がありません。", ephemeral=True)

    @discord.app_commands.command(name="daily_done", description="ARENAのデイリータスクの完了を報告します。")
    async def daily_done(self, interaction: discord.Interaction):
        next_start_time = db.complete_task(interaction.user.id, 'arena', 'daily')
        if next_start_time:
            now = datetime.datetime.now(datetime.timezone.utc)
            if now < next_start_time:
                time_until = next_start_time - now
                await interaction.response.send_message(
                    f"ARENAデイリータスク完了お疲れ様です！\n次のタスクは **{format_timedelta(time_until)}** 後に開始可能です。", ephemeral=True)
            else:
                await interaction.response.send_message("ARENAデイリータスク完了お疲れ様です！\n次にARENAを起動すると新しいタスクが開始されます。", ephemeral=True)
        else:
            await interaction.response.send_message("現在アクティブなARENAデイリータスクが記録されていません。", ephemeral=True)

    @discord.app_commands.command(name="weekly_done", description="ARENAのウィークリータスクの完了を報告します。")
    async def weekly_done(self, interaction: discord.Interaction):
        next_start_time = db.complete_task(interaction.user.id, 'arena', 'weekly')
        if next_start_time:
            now = datetime.datetime.now(datetime.timezone.utc)
            if now < next_start_time:
                time_until = next_start_time - now
                await interaction.response.send_message(
                    f"ARENAウィークリータスク完了お疲れ様です！\n次のタスクは **{format_timedelta(time_until)}** 後に開始可能です。", ephemeral=True)
            else:
                await interaction.response.send_message("ARENAウィークリータスク完了お疲れ様です！\n次にARENAを起動すると新しいタスクが開始されます。", ephemeral=True)
        else:
            await interaction.response.send_message("現在アクティブなARENAウィークリータスクが記録されていません。", ephemeral=True)

class ARENAReminderGroup(discord.app_commands.Group):
    def __init__(self, parent: discord.app_commands.Group):
        super().__init__(name="reminder", description="ARENAのリマインダー関連のコマンド", parent=parent)

    @discord.app_commands.command(name="set", description="ARENAタスク終了前のリマインダー時間を設定します。")
    @discord.app_commands.describe(task_type="タスクの種類 ('daily' または 'weekly')", hours="タスク終了の何時間前に通知を受け取るか")
    async def set(self, interaction: discord.Interaction, task_type: str, hours: int):
        task_type = task_type.lower()
        if task_type not in ['daily', 'weekly']:
            await interaction.response.send_message("`task_type`は 'daily' または 'weekly' を指定してください。", ephemeral=True)
            return
        if not (1 <= hours <= 23 and task_type == 'daily') and not (1 <= hours <= 167 and task_type == 'weekly'):
            await interaction.response.send_message("無効な時間です。デイリーは1-23時間、ウィークリーは1-167時間で設定してください。", ephemeral=True)
            return
        db.set_reminder_hours(interaction.user.id, 'arena', task_type, hours)
        await interaction.response.send_message(f"ARENAの{task_type.capitalize()}タスクのリマインダーを終了 **{hours}** 時間前に設定しました。", ephemeral=True)

# Botの非同期セットアップフック
async def setup_hook():
    eft_commands = EFTGroup()
    EFTReminderGroup(parent=eft_commands)
    bot.tree.add_command(eft_commands)

    arena_commands = ARENAGroup()
    ARENAReminderGroup(parent=arena_commands)
    bot.tree.add_command(arena_commands)

bot.setup_hook = setup_hook

# --------------------------------------------------------------------------------
# メイン処理
# --------------------------------------------------------------------------------

if __name__ == "__main__":
    # ボットを起動
    bot.run(BOT_TOKEN)
