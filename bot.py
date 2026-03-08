import discord
from discord.ext import commands, tasks
import os
from dotenv import load_dotenv
from database import Database
import datetime
import asyncio
import time
import logging

# ロギングの設定: ターミナルとファイル (bot.log) の両方に出力する
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('discord_bot')

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
    logger.error("DISCORD_BOT_TOKENが.envファイルに設定されていません。")
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
    """timedeltaオブジェクトをフォーマットする。24時間以上なら「X日Y時間Z分W秒」とする"""
    total_seconds = int(td.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    if days > 0:
        return f"{days}日{hours}時間{minutes}分{seconds}秒"
    else:
        return f"{hours}時間{minutes}分{seconds}秒"

# --------------------------------------------------------------------------------
# イベントハンドラ
# --------------------------------------------------------------------------------

@bot.event

async def on_ready():

    """ボットが起動したときに呼び出されるイベント"""

    logger.info(f'{bot.user.name} としてログインしました。')

    logger.info('------')

    # スラッシュコマンドを同期

    try:

        synced = await bot.tree.sync()

        logger.info(f"{len(synced)}個のコマンドを同期しました。")

    except Exception as e:

        logger.error(f"コマンドの同期に失敗しました: {e}")

    

    # リマインダーチェックのループを開始 (既に動作中ならスキップして再接続エラーを防止)
    if not check_reminders.is_running():
        check_reminders.start()

# ------ 全てのスラッシュコマンドの実行ログを出力するグローバル設定 ------
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    command_name = interaction.command.name if interaction.command else '不明なコマンド'
    logger.error(f"{interaction.user.name} が /{command_name} を実行中にエラーが発生しました: {error}")

@bot.event
async def on_interaction(interaction: discord.Interaction):
    # スラッシュコマンドが実行された時だけログを出す
    if interaction.type == discord.InteractionType.application_command:
        # コマンドの引数（オプション）を取得して整形
        opts_list = []
        full_command_name = interaction.data.get('name', 'unknown')
        
        if 'options' in interaction.data:
            for opt in interaction.data['options']:
                # type 1 はサブコマンド（例: `/arena status` の `status` 部分）
                if opt.get('type') == 1:
                    full_command_name += f" {opt['name']}"
                    if 'options' in opt:
                        for sub_opt in opt['options']:
                            opts_list.append(f"{sub_opt['name']}={sub_opt.get('value', '')}")
                else:
                    opts_list.append(f"{opt['name']}={opt.get('value', '')}")
        
        options_str = ""
        if opts_list:
            options_str = f" 引数: [{', '.join(opts_list)}]"
            
        logger.info(f"🖥️ 実行ログ: {interaction.user.name} が コマンド `/{full_command_name}` を実行しました。{options_str}")



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
                            f"本日のデイリータスクが開始されます。終了まで: **{format_timedelta(time_left)}**\n\n"
                            f"💡 **Tips:**\n"
                            f"・タスク内容をメモに登録したい場合は `/{game_target} about_task` を使用してください。\n"
                            f"・タイマーの残り時間はずれている可能性があります。正しい時間に修正したい場合は `/{game_target} set_daily_timer` を使用してください。"
                        )
                    except discord.Forbidden:
                        logger.error(f"ユーザー {after.name} ({after.id}) にDMを送信できませんでした。")

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
                            f"今週の **{game_name}** ウィークリータスクが開始されます。終了まで: **{format_timedelta(time_left)}**\n\n"
                            f"💡 **Tips:**\n"
                            f"・タスク内容をメモに登録したい場合は `/{game_target} about_task` を使用してください。\n"
                            f"・タイマーの残り時間はずれている可能性があります。正しい時間に修正したい場合は `/{game_target} set_weekly_timer` を使用してください。"
                        )
                    except discord.Forbidden:
                        logger.error(f"ユーザー {after.name} ({after.id}) にDMを送信できませんでした。")

    # EFTの起動処理
    await process_game_launch("EFT", "eft", EFT_APP_ID)
    
    # ARENAの起動処理
    await process_game_launch("ARENA", "arena", ARENA_APP_ID)



# --------------------------------------------------------------------------------

# バックグラウンドタスク

# --------------------------------------------------------------------------------



@tasks.loop(seconds=5)
async def check_reminders():
    """5秒ごとに実行され、リマインダーを送信するタスク"""
    try:
        pending = db.get_pending_reminders()
        now = datetime.datetime.now(datetime.timezone.utc)

        for reminder in pending:
            user = bot.get_user(reminder['user_id'])
            if not user:
                try:
                    user = await bot.fetch_user(reminder['user_id'])
                except discord.NotFound:
                    continue

            game_name = "EFT" if reminder['game_target'] == 'eft' else "ARENA"
            task_type_jp = "デイリー" if reminder['task_type'] == 'daily' else "ウィークリー"
            time_left = reminder['deadline'] - now
            
            try:
                await user.send(
                    f"🔔 **{game_name} リマインダー** 🔔\n"
                    f"{task_type_jp}タスクの終了まで残り **{format_timedelta(time_left)}** です！"
                )
                db.mark_reminder_sent(reminder['user_id'], reminder['game_target'], reminder['task_type'])
                print(f"{user.name} に {game_name} の {reminder['task_type']} リマインダーを送信しました。")
            except discord.Forbidden:
                print(f"ユーザー {user.name} ({user.id}) にリマインダーDMを送信できませんでした。")
    except Exception as e:
        print(f"リマインダーループでエラーが発生しました: {e}")



@check_reminders.before_loop

async def before_check_reminders():

    """ループが開始される前にボットが準備完了するまで待機する"""

    await bot.wait_until_ready()



# --------------------------------------------------------------------------------
# UI コンポーネント
# --------------------------------------------------------------------------------

class TaskSelectView(discord.ui.View):
    def __init__(self, user_id, game_target, task_type, tasks):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.game_target = game_target
        self.task_type = task_type
        
        options = []
        for t in tasks:
            if not t['completed']:
                label = t['description']
                if len(label) > 100:
                    label = label[:97] + "..."
                options.append(discord.SelectOption(label=label, value=str(t['task_index'])))
        
        options.append(discord.SelectOption(label="すべてのタスクを一括完了する", value="all"))
        
        self.select = discord.ui.Select(placeholder="完了したタスクを選択...", options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)
        
    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("あなたはこのメニューを操作できません。", ephemeral=True)
            return
            
        selected = self.select.values[0]
        if selected == "all":
            # すべてのサブタスクを完了済みとしてマークする
            all_tasks = db.get_user_tasks(self.user_id, self.game_target, self.task_type)
            for t in all_tasks:
                if not t['completed']:
                    db.complete_individual_task(self.user_id, self.game_target, self.task_type, t['task_index'])

            next_start = db.complete_task(self.user_id, self.game_target, self.task_type)
            if next_start:
                now = datetime.datetime.now(datetime.timezone.utc)
                if now < next_start:
                    time_until = next_start - now
                    msg = f"{self.game_target.upper()} {self.task_type}タスク完了お疲れ様です！\n次のタスクは **{format_timedelta(time_until)}** 後に開始可能です。"
                else:
                    msg = f"{self.game_target.upper()} {self.task_type}タスク完了お疲れ様です！\n次にゲームを起動すると新しいタスクが開始されます。"
            else:
                 msg = "現在アクティブなタスクが記録されていません。"
            await interaction.response.edit_message(content=msg, view=None)
        else:
            task_index = int(selected)
            db.complete_individual_task(self.user_id, self.game_target, self.task_type, task_index)
            
            all_tasks = db.get_user_tasks(self.user_id, self.game_target, self.task_type)
            remaining = [t for t in all_tasks if not t['completed']]
            
            if not remaining:
                next_start = db.complete_task(self.user_id, self.game_target, self.task_type)
                now = datetime.datetime.now(datetime.timezone.utc)
                if next_start and now < next_start:
                    time_until = next_start - now
                    msg = f"すべてのタスクを完了しました！お疲れ様です！\n次のタスクは **{format_timedelta(time_until)}** 後に開始可能です。"
                else:
                    msg = f"すべてのタスクを完了しました！お疲れ様です！\n次にゲームを起動すると新しいタスクが開始されます。"
            else:
                desc = next(t['description'] for t in all_tasks if t['task_index'] == task_index)
                msg = f"✅ タスク「{desc}」を完了しました！\n残り **{len(remaining)}** 個のタスクが進行中です。"
            await interaction.response.edit_message(content=msg, view=None)

class UndoTaskSelectView(discord.ui.View):
    def __init__(self, user_id, game_target, task_type, tasks):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.game_target = game_target
        self.task_type = task_type
        
        options = []
        for t in tasks:
            if t['completed']:
                label = t['description']
                if len(label) > 100:
                    label = label[:97] + "..."
                options.append(discord.SelectOption(label=label, value=str(t['task_index'])))
        
        options.append(discord.SelectOption(label="すべてのタスクの完了を未完了に戻す", value="all"))
        
        self.select = discord.ui.Select(placeholder="未完了に戻すタスクを選択...", options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)
        
    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("あなたはこのメニューを操作できません。", ephemeral=True)
            return
            
        selected = self.select.values[0]
        if selected == "all":
            # すべてのサブタスクを未完了に戻す
            for t in db.get_user_tasks(self.user_id, self.game_target, self.task_type):
                 db.undo_individual_task(self.user_id, self.game_target, self.task_type, t['task_index'])
            
            # メイン処理のundoを呼ぶ
            result = db.undo_task(self.user_id, self.game_target, self.task_type)
            if result:
                now = datetime.datetime.now(datetime.timezone.utc)
                if now < result:
                    time_left = result - now
                    await interaction.response.edit_message(content=f"{self.game_target.upper()}の{self.task_type}タスクをすべて未完了に戻しました。\n残り時間: **{format_timedelta(time_left)}**", view=None)
                else:
                    await interaction.response.edit_message(content="完了状態を取り消しましたが、既に期限切れです。\n次にゲームを起動すると新しいタスクが開始されます。", view=None)
            else:
                await interaction.response.edit_message(content="取り消すタスクの記録がありません。", view=None)
        else:
            task_index = int(selected)
            db.undo_individual_task(self.user_id, self.game_target, self.task_type, task_index)
            # 大元のフラグも未完了に戻す
            result = db.undo_task(self.user_id, self.game_target, self.task_type)
            
            all_tasks = db.get_user_tasks(self.user_id, self.game_target, self.task_type)
            completed_tasks = [t for t in all_tasks if t['completed']]
            desc = next(t['description'] for t in all_tasks if t['task_index'] == task_index)
            
            msg = f"❌ タスク「{desc}」を未完了に戻しました！"
            await interaction.response.edit_message(content=msg, view=None)

# --------------------------------------------------------------------------------
# スラッシュコマンド (EFT)
# --------------------------------------------------------------------------------

class EFTGroup(discord.app_commands.Group):
    def __init__(self):
        super().__init__(name="eft", description="Escape from Tarkov 本編のタスク管理コマンド")

    @discord.app_commands.command(name="status", description="EFTのデイリーおよびウィークリータスクの残り時間を確認します。")
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_data = db.get_user(interaction.user.id)
        now = datetime.datetime.now(datetime.timezone.utc)
        
        response_lines = ["**EFTタスク状況**\n"]
        
        # デイリーステータス
        if user_data and user_data.get('eft_daily_deadline'):
            deadline = datetime.datetime.fromisoformat(user_data['eft_daily_deadline'])
            if now < deadline:
                time_left = deadline - now
                if user_data.get('eft_daily_completed'):
                    response_lines.append(f"✅ **デイリー**: 完了済み！ 次のタスクは **{format_timedelta(time_left)}** 後に開始可能")
                else:
                    response_lines.append(f"⏳ **デイリー** 残り時間: **{format_timedelta(time_left)}**")
                    rem_sec = user_data.get('eft_daily_reminder_seconds', 0)
                    rem_sent = user_data.get('eft_daily_reminder_sent', 0)
                    if rem_sec > 0 and not rem_sent:
                        rem_once = "今回限り" if user_data.get('eft_daily_reminder_once') else "いつでも"
                        response_lines.append(f"  ⏰ 終了 **{format_timedelta(datetime.timedelta(seconds=rem_sec))}前** にお知らせします ({rem_once})")
                    
                tasks = db.get_user_tasks(interaction.user.id, 'eft', 'daily')
                for t in tasks:
                    mark = "✅" if t['completed'] else "🔲"
                    response_lines.append(f"  {mark} {t['description']}")
            else:
                response_lines.append("❌ **デイリー**: 現在アクティブなタスクなし (ゲーム起動で開始)")
        else:
            response_lines.append("❌ **デイリー**: タスク記録なし (ゲーム起動で開始)")
            
        # ウィークリーステータス
        if user_data and user_data.get('eft_weekly_deadline'):
            deadline = datetime.datetime.fromisoformat(user_data['eft_weekly_deadline'])
            if now < deadline:
                time_left = deadline - now
                if user_data.get('eft_weekly_completed'):
                    response_lines.append(f"✅ **ウィークリー**: 完了済み！ 次のタスクは **{format_timedelta(time_left)}** 後に開始可能")
                else:
                    response_lines.append(f"⏳ **ウィークリー** 残り時間: **{format_timedelta(time_left)}**")
                    rem_sec = user_data.get('eft_weekly_reminder_seconds', 0)
                    rem_sent = user_data.get('eft_weekly_reminder_sent', 0)
                    if rem_sec > 0 and not rem_sent:
                        rem_once = "今回限り" if user_data.get('eft_weekly_reminder_once') else "いつでも"
                        response_lines.append(f"  ⏰ 終了 **{format_timedelta(datetime.timedelta(seconds=rem_sec))}前** にお知らせします ({rem_once})")
                    
                tasks = db.get_user_tasks(interaction.user.id, 'eft', 'weekly')
                for t in tasks:
                    mark = "✅" if t['completed'] else "🔲"
                    response_lines.append(f"  {mark} {t['description']}")
            else:
                response_lines.append("❌ **ウィークリー**: 現在アクティブなタスクなし")
        else:
            response_lines.append("❌ **ウィークリー**: タスク記録なし")

        await interaction.followup.send("\n".join(response_lines), ephemeral=True)

    @discord.app_commands.command(name="about_task", description="EFTの個別のタスク内容を登録します。")
    @discord.app_commands.describe(task_type="デイリーかウィークリーか選択", task1="タスク1", task2="タスク2", task3="タスク3(デイリー用)", task4="タスク4(デイリー用)")
    @discord.app_commands.choices(task_type=[
        discord.app_commands.Choice(name="デイリー", value="daily"),
        discord.app_commands.Choice(name="ウィークリー", value="weekly")
    ])
    async def about_task(self, interaction: discord.Interaction, task_type: str, task1: str = None, task2: str = None, task3: str = None, task4: str = None):
        await interaction.response.defer(ephemeral=True)
        if task_type == "weekly" and (task3 or task4):
            await interaction.followup.send("ウィークリータスクは2つまでしか登録できません。(task3, task4は無視されます)", ephemeral=True)
            return
            
        tasks_dict = {1: task1, 2: task2}
        if task_type == "daily":
            tasks_dict[3] = task3
            tasks_dict[4] = task4
            
        db.set_user_tasks(interaction.user.id, "eft", task_type, tasks_dict)
        await interaction.followup.send(f"EFTの{task_type}タスク内容を登録しました！ `/eft status` で確認できます。", ephemeral=True)

    @discord.app_commands.command(name="done_daily", description="EFTのデイリータスクの完了を報告します。")
    async def done_daily(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        tasks = db.get_user_tasks(interaction.user.id, 'eft', 'daily')
        incomplete = [t for t in tasks if not t['completed']]
        
        if incomplete:
            view = TaskSelectView(interaction.user.id, 'eft', 'daily', tasks)
            await interaction.followup.send("完了した項目を選んでください:", view=view, ephemeral=True)
            return
            
        next_start_time = db.complete_task(interaction.user.id, 'eft', 'daily')
        if next_start_time:
            now = datetime.datetime.now(datetime.timezone.utc)
            if now < next_start_time:
                time_until = next_start_time - now
                await interaction.followup.send(
                    f"EFTデイリータスク完了お疲れ様です！\n次のタスクは **{format_timedelta(time_until)}** 後に開始可能です。", ephemeral=True)
            else:
                await interaction.followup.send("EFTデイリータスク完了お疲れ様です！\n次にEFTを起動すると新しいタスクが開始されます。", ephemeral=True)
        else:
            await interaction.followup.send("現在アクティブなEFTデイリータスクが記録されていません。", ephemeral=True)

    @discord.app_commands.command(name="done_weekly", description="EFTのウィークリータスクの完了を報告します。")
    async def done_weekly(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        tasks = db.get_user_tasks(interaction.user.id, 'eft', 'weekly')
        incomplete = [t for t in tasks if not t['completed']]
        
        if incomplete:
            view = TaskSelectView(interaction.user.id, 'eft', 'weekly', tasks)
            await interaction.followup.send("完了した項目を選んでください:", view=view, ephemeral=True)
            return

        next_start_time = db.complete_task(interaction.user.id, 'eft', 'weekly')
        if next_start_time:
            now = datetime.datetime.now(datetime.timezone.utc)
            if now < next_start_time:
                time_until = next_start_time - now
                await interaction.followup.send(
                    f"EFTウィークリータスク完了お疲れ様です！\n次のタスクは **{format_timedelta(time_until)}** 後に開始可能です。", ephemeral=True)
            else:
                await interaction.followup.send("EFTウィークリータスク完了お疲れ様です！\n次にEFTを起動すると新しいタスクが開始されます。", ephemeral=True)
        else:
            await interaction.followup.send("現在アクティブなEFTウィークリータスクが記録されていません。", ephemeral=True)

    @discord.app_commands.command(name="undone_daily", description="EFTのデイリータスクの完了状態を取り消し、進行中に戻します。")
    async def undone_daily(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        tasks = db.get_user_tasks(interaction.user.id, 'eft', 'daily')
        completed = [t for t in tasks if t['completed']]
        
        if completed:
            view = UndoTaskSelectView(interaction.user.id, 'eft', 'daily', tasks)
            await interaction.followup.send("未完了に戻す項目を選んでください:", view=view, ephemeral=True)
            return
            
        result = db.undo_task(interaction.user.id, 'eft', 'daily')
        if result:
            now = datetime.datetime.now(datetime.timezone.utc)
            if now < result:
                time_left = result - now
                await interaction.followup.send(f"EFTデイリータスクを未完了に戻しました。\n残り時間: **{format_timedelta(time_left)}**", ephemeral=True)
            else:
                await interaction.followup.send("完了状態を取り消しましたが、既に期限切れです。\n次にEFTを起動すると新しいタスクが開始されます。", ephemeral=True)
        else:
            await interaction.followup.send("取り消すEFTデイリータスクの記録がありません。", ephemeral=True)

    @discord.app_commands.command(name="undone_weekly", description="EFTのウィークリータスクの完了状態を取り消し、進行中に戻します。")
    async def undone_weekly(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        tasks = db.get_user_tasks(interaction.user.id, 'eft', 'weekly')
        completed = [t for t in tasks if t['completed']]
        
        if completed:
            view = UndoTaskSelectView(interaction.user.id, 'eft', 'weekly', tasks)
            await interaction.followup.send("未完了に戻す項目を選んでください:", view=view, ephemeral=True)
            return
            
        result = db.undo_task(interaction.user.id, 'eft', 'weekly')
        if result:
            now = datetime.datetime.now(datetime.timezone.utc)
            if now < result:
                time_left = result - now
                await interaction.followup.send(f"EFTウィークリータスクを未完了に戻しました。\n残り時間: **{format_timedelta(time_left)}**", ephemeral=True)
            else:
                await interaction.followup.send("完了状態を取り消しましたが、既に期限切れです。\n次にEFTを起動すると新しいタスクが開始されます。", ephemeral=True)
        else:
            await interaction.followup.send("取り消すEFTウィークリータスクの記録がありません。", ephemeral=True)

    @discord.app_commands.command(name="set_daily_timer", description="EFTデイリータスクの残り時間を手動で設定し、タイマーを開始します。")
    @discord.app_commands.describe(hours="終了までの残り時間（0〜24時間）", minutes="終了までの残り時間（0〜59分）", seconds="終了までの残り時間（0〜59秒）")
    async def set_daily_timer(self, interaction: discord.Interaction, hours: int, minutes: int = 0, seconds: int = 0):
        await interaction.response.defer(ephemeral=True)
        if not (0 <= hours <= 24) or not (0 <= minutes <= 59) or not (0 <= seconds <= 59) or (hours == 0 and minutes == 0 and seconds == 0):
            await interaction.followup.send("有効な期間を指定してください（最大24時間0分0秒まで）。", ephemeral=True)
            return
        
        now = datetime.datetime.now(datetime.timezone.utc)
        deadline = now + datetime.timedelta(hours=hours, minutes=minutes, seconds=seconds)
        db.set_manual_deadline(interaction.user.id, 'eft', 'daily', deadline)
        
        # JSTで期限を表示
        jst_deadline = deadline.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
        await interaction.followup.send(f"EFTデイリータスクを手動で開始しました！\n終了まで: **{hours}時間{minutes}分{seconds}秒** ({jst_deadline.strftime('%m/%d %H:%M:%S')} まで)", ephemeral=True)

    @discord.app_commands.command(name="set_weekly_timer", description="EFTウィークリータスクの残り時間を手動で設定し、タイマーを開始します。")
    @discord.app_commands.describe(days="終了までの残り日数（0〜7日）", hours="さらに加算する残り時間（0〜23時間）", minutes="さらに加算する残り時間（0〜59分）", seconds="さらに加算する残り時間（0〜59秒）")
    async def set_weekly_timer(self, interaction: discord.Interaction, days: int, hours: int, minutes: int = 0, seconds: int = 0):
        await interaction.response.defer(ephemeral=True)
        if not (0 <= days <= 7) or not (0 <= hours <= 23) or not (0 <= minutes <= 59) or not (0 <= seconds <= 59) or (days == 0 and hours == 0 and minutes == 0 and seconds == 0):
            await interaction.followup.send("有効な期間を指定してください（最大7日0時間0分0秒まで）。", ephemeral=True)
            return
            
        now = datetime.datetime.now(datetime.timezone.utc)
        deadline = now + datetime.timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        db.set_manual_deadline(interaction.user.id, 'eft', 'weekly', deadline)
        
        jst_deadline = deadline.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
        await interaction.followup.send(f"EFTウィークリータスクを手動で開始しました！\n終了まで: **{days}日{hours}時間{minutes}分{seconds}秒** ({jst_deadline.strftime('%m/%d %H:%M:%S')} まで)", ephemeral=True)

    @discord.app_commands.command(name="reminder", description="EFTタスク終了前のリマインダー通知の時間を設定、または解除します。")
    @discord.app_commands.describe(task_type="タスクの種類", hours="何時間前にお知らせするか", minutes="何分前にお知らせするか", seconds="何秒前にお知らせするか", once="今回限りの設定にするか")
    @discord.app_commands.choices(task_type=[
        discord.app_commands.Choice(name="デイリー", value="daily"),
        discord.app_commands.Choice(name="ウィークリー", value="weekly")
    ])
    @discord.app_commands.choices(once=[
        discord.app_commands.Choice(name="今回限り", value=1),
        discord.app_commands.Choice(name="いつでも（毎回適用）", value=0)
    ])
    async def reminder(self, interaction: discord.Interaction, task_type: str, hours: int = 0, minutes: int = 0, seconds: int = 0, once: int = 0):
        await interaction.response.defer(ephemeral=True)
        total_seconds = hours * 3600 + minutes * 60 + seconds
        
        if total_seconds == 0:
            db.set_reminder(interaction.user.id, 'eft', task_type, 0, False)
            await interaction.followup.send(f"EFTの{task_type}タスクのリマインダーを**解除**しました。", ephemeral=True)
            return

        # Check if the reminder is already in the past
        user_data = db.get_user(interaction.user.id)
        if user_data:
            deadline_str = user_data.get(f'eft_{task_type}_deadline')
            if deadline_str:
                deadline = datetime.datetime.fromisoformat(deadline_str)
                now = datetime.datetime.now(datetime.timezone.utc)
                if deadline > now:
                    time_left = (deadline - now).total_seconds()
                    if total_seconds >= time_left:
                        await interaction.followup.send("⚠️ エラー: 指定したリマインダー時間は、タスクの残り時間よりも長いため、すぐに発火してしまいます。もっと短い時間を指定してください。", ephemeral=True)
                        return

        is_once = bool(once)
        db.set_reminder(interaction.user.id, 'eft', task_type, total_seconds, is_once)
        
        rem_once_str = "今回限り" if is_once else "いつでも（毎回適用）"
        time_str = format_timedelta(datetime.timedelta(seconds=total_seconds))
        await interaction.followup.send(f"EFTの{task_type}タスクのリマインダーを 終了 **{time_str}前** に設定しました。（{rem_once_str}）", ephemeral=True)

# --------------------------------------------------------------------------------
# スラッシュコマンド (ARENA)
# --------------------------------------------------------------------------------

class ARENAGroup(discord.app_commands.Group):
    def __init__(self):
        super().__init__(name="arena", description="Tarkov: ARENA のタスク管理コマンド")

    @discord.app_commands.command(name="status", description="ARENAのデイリーおよびウィークリータスクの残り時間を確認します。")
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        t0 = time.perf_counter()
        # Discord側がコマンドを受理した時刻との差（ネットワーク遅延）を計算
        net_delay = (datetime.datetime.now(datetime.timezone.utc) - interaction.created_at).total_seconds()
        
        user_data = db.get_user(interaction.user.id)
        t1 = time.perf_counter()
        now = datetime.datetime.now(datetime.timezone.utc)
        
        response_lines = ["**ARENAタスク状況**\n"]
        
        # デイリーステータス
        if user_data and user_data.get('arena_daily_deadline'):
            deadline = datetime.datetime.fromisoformat(user_data['arena_daily_deadline'])
            if now < deadline:
                time_left = deadline - now
                if user_data.get('arena_daily_completed'):
                    response_lines.append(f"✅ **デイリー**: 完了済み！ 次のタスクは **{format_timedelta(time_left)}** 後に開始可能")
                else:
                    response_lines.append(f"⏳ **デイリー** 残り時間: **{format_timedelta(time_left)}**")
                    rem_sec = user_data.get('arena_daily_reminder_seconds', 0)
                    rem_sent = user_data.get('arena_daily_reminder_sent', 0)
                    if rem_sec > 0 and not rem_sent:
                        rem_once = "今回限り" if user_data.get('arena_daily_reminder_once') else "いつでも"
                        response_lines.append(f"  ⏰ 終了 **{format_timedelta(datetime.timedelta(seconds=rem_sec))}前** にお知らせします ({rem_once})")
                    
                tasks = db.get_user_tasks(interaction.user.id, 'arena', 'daily')
                for t in tasks:
                    mark = "✅" if t['completed'] else "🔲"
                    response_lines.append(f"  {mark} {t['description']}")
            else:
                response_lines.append("❌ **デイリー**: 現在アクティブなタスクなし (ゲーム起動で開始)")
        else:
            response_lines.append("❌ **デイリー**: タスク記録なし (ゲーム起動で開始)")

        # ウィークリーステータス
        if user_data and user_data.get('arena_weekly_deadline'):
            deadline = datetime.datetime.fromisoformat(user_data['arena_weekly_deadline'])
            if now < deadline:
                time_left = deadline - now
                if user_data.get('arena_weekly_completed'):
                    response_lines.append(f"✅ **ウィークリー**: 完了済み！ 次のタスクは **{format_timedelta(time_left)}** 後に開始可能")
                else:
                    response_lines.append(f"⏳ **ウィークリー** 残り時間: **{format_timedelta(time_left)}**")
                    rem_sec = user_data.get('arena_weekly_reminder_seconds', 0)
                    rem_sent = user_data.get('arena_weekly_reminder_sent', 0)
                    if rem_sec > 0 and not rem_sent:
                        rem_once = "今回限り" if user_data.get('arena_weekly_reminder_once') else "いつでも"
                        response_lines.append(f"  ⏰ 終了 **{format_timedelta(datetime.timedelta(seconds=rem_sec))}前** にお知らせします ({rem_once})")
                    
                tasks = db.get_user_tasks(interaction.user.id, 'arena', 'weekly')
                for t in tasks:
                    mark = "✅" if t['completed'] else "🔲"
                    response_lines.append(f"  {mark} {t['description']}")
            else:
                response_lines.append("❌ **ウィークリー**: 現在アクティブなタスクなし")
        else:
            response_lines.append("❌ **ウィークリー**: タスク記録なし")

        t2 = time.perf_counter()
        await interaction.followup.send("\n".join(response_lines), ephemeral=True)
        t3 = time.perf_counter()
        
        print(f"\n[性能テスト] /arena status 実行時間プロファイル: ({interaction.user.name} が実行)")
        print(f"  - Discord->Bot ネットワーク遅延: {net_delay:.4f}秒")
        print(f"  - 最初のDB読込 (get_user): {t1 - t0:.4f}秒")
        print(f"  - 文字列構築とタスクDB読込: {t2 - t1:.4f}秒")
        print(f"  - Discordへ返信送信 (send_message): {t3 - t2:.4f}秒")
        print(f"  - Bot内での合計処理時間 (t0->t3): {t3 - t0:.4f}秒\n")

    @discord.app_commands.command(name="about_task", description="ARENAの個別のタスク内容を登録します。")
    @discord.app_commands.describe(task_type="デイリーかウィークリーか選択", task1="タスク1", task2="タスク2(デイリー用)", task3="タスク3(デイリー用)")
    @discord.app_commands.choices(task_type=[
        discord.app_commands.Choice(name="デイリー", value="daily"),
        discord.app_commands.Choice(name="ウィークリー", value="weekly")
    ])
    async def about_task(self, interaction: discord.Interaction, task_type: str, task1: str = None, task2: str = None, task3: str = None):
        await interaction.response.defer(ephemeral=True)
        if task_type == "weekly" and task3:
            await interaction.followup.send("ウィークリータスクは2つまでしか登録できません。(task3は無視されます)", ephemeral=True)
            return
        tasks_dict = {1: task1}
        if task_type == "daily":
            tasks_dict[2] = task2
            tasks_dict[3] = task3
            
        db.set_user_tasks(interaction.user.id, "arena", task_type, tasks_dict)
        await interaction.followup.send(f"ARENAの{task_type}タスク内容を登録しました！ `/arena status` で確認できます。", ephemeral=True)

    @discord.app_commands.command(name="done_daily", description="ARENAのデイリータスクの完了を報告します。")
    async def done_daily(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        tasks = db.get_user_tasks(interaction.user.id, 'arena', 'daily')
        incomplete = [t for t in tasks if not t['completed']]
        
        if incomplete:
            view = TaskSelectView(interaction.user.id, 'arena', 'daily', tasks)
            await interaction.followup.send("完了した項目を選んでください:", view=view, ephemeral=True)
            return

        next_start_time = db.complete_task(interaction.user.id, 'arena', 'daily')
        if next_start_time:
            now = datetime.datetime.now(datetime.timezone.utc)
            if now < next_start_time:
                time_until = next_start_time - now
                await interaction.followup.send(
                    f"ARENAデイリータスク完了お疲れ様です！\n次のタスクは **{format_timedelta(time_until)}** 後に開始可能です。", ephemeral=True)
            else:
                await interaction.followup.send("ARENAデイリータスク完了お疲れ様です！\n次にARENAを起動すると新しいタスクが開始されます。", ephemeral=True)
        else:
            await interaction.followup.send("現在アクティブなARENAデイリータスクが記録されていません。", ephemeral=True)

    @discord.app_commands.command(name="done_weekly", description="ARENAのウィークリータスクの完了を報告します。")
    async def done_weekly(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        tasks = db.get_user_tasks(interaction.user.id, 'arena', 'weekly')
        incomplete = [t for t in tasks if not t['completed']]
        
        if incomplete:
            view = TaskSelectView(interaction.user.id, 'arena', 'weekly', tasks)
            await interaction.followup.send("完了した項目を選んでください:", view=view, ephemeral=True)
            return
            
        next_start_time = db.complete_task(interaction.user.id, 'arena', 'weekly')
        if next_start_time:
            now = datetime.datetime.now(datetime.timezone.utc)
            if now < next_start_time:
                time_until = next_start_time - now
                await interaction.followup.send(
                    f"ARENAウィークリータスク完了お疲れ様です！\n次のタスクは **{format_timedelta(time_until)}** 後に開始可能です。", ephemeral=True)
            else:
                await interaction.followup.send("ARENAウィークリータスク完了お疲れ様です！\n次にARENAを起動すると新しいタスクが開始されます。", ephemeral=True)
        else:
            await interaction.followup.send("現在アクティブなARENAウィークリータスクが記録されていません。", ephemeral=True)

    @discord.app_commands.command(name="undone_daily", description="ARENAのデイリータスクの完了状態を取り消し、進行中に戻します。")
    async def undone_daily(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        tasks = db.get_user_tasks(interaction.user.id, 'arena', 'daily')
        completed = [t for t in tasks if t['completed']]
        
        if completed:
            view = UndoTaskSelectView(interaction.user.id, 'arena', 'daily', tasks)
            await interaction.followup.send("未完了に戻す項目を選んでください:", view=view, ephemeral=True)
            return

        result = db.undo_task(interaction.user.id, 'arena', 'daily')
        if result:
            now = datetime.datetime.now(datetime.timezone.utc)
            if now < result:
                time_left = result - now
                await interaction.followup.send(f"ARENAデイリータスクを未完了に戻しました。\n残り時間: **{format_timedelta(time_left)}**", ephemeral=True)
            else:
                await interaction.followup.send("完了状態を取り消しましたが、既に期限切れです。\n次にARENAを起動すると新しいタスクが開始されます。", ephemeral=True)
        else:
            await interaction.followup.send("取り消すARENAデイリータスクの記録がありません。", ephemeral=True)

    @discord.app_commands.command(name="undone_weekly", description="ARENAのウィークリータスクの完了状態を取り消し、進行中に戻します。")
    async def undone_weekly(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        tasks = db.get_user_tasks(interaction.user.id, 'arena', 'weekly')
        completed = [t for t in tasks if t['completed']]
        
        if completed:
            view = UndoTaskSelectView(interaction.user.id, 'arena', 'weekly', tasks)
            await interaction.followup.send("未完了に戻す項目を選んでください:", view=view, ephemeral=True)
            return
            
        result = db.undo_task(interaction.user.id, 'arena', 'weekly')
        if result:
            now = datetime.datetime.now(datetime.timezone.utc)
            if now < result:
                time_left = result - now
                await interaction.followup.send(f"ARENAウィークリータスクを未完了に戻しました。\n残り時間: **{format_timedelta(time_left)}**", ephemeral=True)
            else:
                await interaction.followup.send("完了状態を取り消しましたが、既に期限切れです。\n次にARENAを起動すると新しいタスクが開始されます。", ephemeral=True)
        else:
            await interaction.followup.send("取り消すARENAウィークリータスクの記録がありません。", ephemeral=True)

    @discord.app_commands.command(name="set_daily_timer", description="ARENAデイリータスクの残り時間を手動で設定し、タイマーを開始します。")
    @discord.app_commands.describe(hours="終了までの残り時間（0〜24時間）", minutes="終了までの残り時間（0〜59分）", seconds="終了までの残り時間（0〜59秒）")
    async def set_daily_timer(self, interaction: discord.Interaction, hours: int, minutes: int = 0, seconds: int = 0):
        await interaction.response.defer(ephemeral=True)
        if not (0 <= hours <= 24) or not (0 <= minutes <= 59) or not (0 <= seconds <= 59) or (hours == 0 and minutes == 0 and seconds == 0):
            await interaction.followup.send("有効な期間を指定してください（最大24時間0分0秒まで）。", ephemeral=True)
            return
        
        now = datetime.datetime.now(datetime.timezone.utc)
        deadline = now + datetime.timedelta(hours=hours, minutes=minutes, seconds=seconds)
        db.set_manual_deadline(interaction.user.id, 'arena', 'daily', deadline)
        
        jst_deadline = deadline.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
        await interaction.followup.send(f"ARENAデイリータスクを手動で開始しました！\n終了まで: **{hours}時間{minutes}分{seconds}秒** ({jst_deadline.strftime('%m/%d %H:%M:%S')} まで)", ephemeral=True)

    @discord.app_commands.command(name="set_weekly_timer", description="ARENAウィークリータスクの残り時間を手動で設定し、タイマーを開始します。")
    @discord.app_commands.describe(days="終了までの残り日数（0〜7日）", hours="さらに加算する残り時間（0〜23時間）", minutes="さらに加算する残り時間（0〜59分）", seconds="さらに加算する残り時間（0〜59秒）")
    async def set_weekly_timer(self, interaction: discord.Interaction, days: int, hours: int, minutes: int = 0, seconds: int = 0):
        await interaction.response.defer(ephemeral=True)
        if not (0 <= days <= 7) or not (0 <= hours <= 23) or not (0 <= minutes <= 59) or not (0 <= seconds <= 59) or (days == 0 and hours == 0 and minutes == 0 and seconds == 0):
            await interaction.followup.send("有効な期間を指定してください（最大7日0時間0分0秒まで）。", ephemeral=True)
            return
            
        now = datetime.datetime.now(datetime.timezone.utc)
        deadline = now + datetime.timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        db.set_manual_deadline(interaction.user.id, 'arena', 'weekly', deadline)
        
        jst_deadline = deadline.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
        await interaction.followup.send(f"ARENAウィークリータスクを手動で開始しました！\n終了まで: **{days}日{hours}時間{minutes}分{seconds}秒** ({jst_deadline.strftime('%m/%d %H:%M:%S')} まで)", ephemeral=True)

    @discord.app_commands.command(name="reminder", description="ARENAタスク終了前のリマインダー通知の時間を設定、または解除します。")
    @discord.app_commands.describe(task_type="タスクの種類", hours="何時間前にお知らせするか", minutes="何分前にお知らせするか", seconds="何秒前にお知らせするか", once="今回限りの設定にするか")
    @discord.app_commands.choices(task_type=[
        discord.app_commands.Choice(name="デイリー", value="daily"),
        discord.app_commands.Choice(name="ウィークリー", value="weekly")
    ])
    @discord.app_commands.choices(once=[
        discord.app_commands.Choice(name="今回限り", value=1),
        discord.app_commands.Choice(name="いつでも（毎回適用）", value=0)
    ])
    async def reminder(self, interaction: discord.Interaction, task_type: str, hours: int = 0, minutes: int = 0, seconds: int = 0, once: int = 0):
        await interaction.response.defer(ephemeral=True)
        total_seconds = hours * 3600 + minutes * 60 + seconds
        
        if total_seconds == 0:
            db.set_reminder(interaction.user.id, 'arena', task_type, 0, False)
            await interaction.followup.send(f"ARENAの{task_type}タスクのリマインダーを**解除**しました。", ephemeral=True)
            return

        # Check if the reminder is already in the past
        user_data = db.get_user(interaction.user.id)
        if user_data:
            deadline_str = user_data.get(f'arena_{task_type}_deadline')
            if deadline_str:
                deadline = datetime.datetime.fromisoformat(deadline_str)
                now = datetime.datetime.now(datetime.timezone.utc)
                if deadline > now:
                    time_left = (deadline - now).total_seconds()
                    if total_seconds >= time_left:
                        await interaction.followup.send("⚠️ エラー: 指定したリマインダー時間は、タスクの残り時間よりも長いため、すぐに発火してしまいます。もっと短い時間を指定してください。", ephemeral=True)
                        return
            
        is_once = bool(once)
        db.set_reminder(interaction.user.id, 'arena', task_type, total_seconds, is_once)
        
        rem_once_str = "今回限り" if is_once else "いつでも（毎回適用）"
        time_str = format_timedelta(datetime.timedelta(seconds=total_seconds))
        await interaction.followup.send(f"ARENAの{task_type}タスクのリマインダーを 終了 **{time_str}前** に設定しました。（{rem_once_str}）", ephemeral=True)

# Botの非同期セットアップフック
async def setup_hook():
    eft_commands = EFTGroup()
    bot.tree.add_command(eft_commands)

    arena_commands = ARENAGroup()
    bot.tree.add_command(arena_commands)

    @bot.tree.command(name="help", description="このBotの使い方とコマンド一覧を表示します。")
    async def help_command(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        help_text = (
            "**EFT/ARENA タスクリマインダーBot の使い方**\n\n"
            "1. **自動検知**: Escape from Tarkov または Tarkov: ARENA を起動すると、自動的にデイリー/ウィークリータイマーが開始され、DMに通知が届きます。\n"
            "2. **タスクの登録**: `/eft about_task` や `/arena about_task` で、現在のタスク内容をメモできます。\n"
            "3. **手動タイマー調整**: 実際の残り時間とずれている場合は、`/eft set_daily_timer` 等で時間を直接指定してタイマーを開始できます。\n"
            "4. **完了報告**: `/eft done_daily` などのコマンドを実行し、完了したタスクを選ぶと、次回ゲーム起動時までタイマーが停止します。\n"
            "5. **状態確認**: `/eft status` 等で現在のタイマーと登録したタスクの進捗が確認できます。\n\n"
            "※コマンドは `/eft [コマンド名]` または `/arena [コマンド名]` のようにグループ化されています。"
        )
        await interaction.followup.send(help_text, ephemeral=True)

bot.setup_hook = setup_hook

# --------------------------------------------------------------------------------
# メイン処理
# --------------------------------------------------------------------------------

if __name__ == "__main__":
    # ボットを起動
    bot.run(BOT_TOKEN)
