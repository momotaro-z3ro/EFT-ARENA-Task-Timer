import sqlite3
import datetime

class Database:
    """
    ユーザーデータとタスクの期限を管理するためのSQLiteデータベースクラス。
    EFTとARENAを完全に独立して管理します。
    """
    def __init__(self, db_name='eft_bot.db'):
        """
        データベースに接続し、初期設定を呼び出す。
        """
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self.setup_tables()

    def setup_tables(self):
        """
        'users'テーブルが存在しない場合に作成する。
        EFTとARENAのカラムをそれぞれ独立して持つ。
        """
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                eft_daily_deadline TIMESTAMP,
                eft_weekly_deadline TIMESTAMP,
                eft_daily_reminder_seconds INTEGER DEFAULT 10800,
                eft_weekly_reminder_seconds INTEGER DEFAULT 86400,
                eft_daily_reminder_sent BOOLEAN DEFAULT 0,
                eft_weekly_reminder_sent BOOLEAN DEFAULT 0,
                eft_daily_reminder_once BOOLEAN DEFAULT 0,
                eft_weekly_reminder_once BOOLEAN DEFAULT 0,
                eft_daily_completed BOOLEAN DEFAULT 0,
                eft_weekly_completed BOOLEAN DEFAULT 0,
                arena_daily_deadline TIMESTAMP,
                arena_weekly_deadline TIMESTAMP,
                arena_daily_reminder_seconds INTEGER DEFAULT 10800,
                arena_weekly_reminder_seconds INTEGER DEFAULT 86400,
                arena_daily_reminder_sent BOOLEAN DEFAULT 0,
                arena_weekly_reminder_sent BOOLEAN DEFAULT 0,
                arena_daily_reminder_once BOOLEAN DEFAULT 0,
                arena_weekly_reminder_once BOOLEAN DEFAULT 0,
                arena_daily_completed BOOLEAN DEFAULT 0,
                arena_weekly_completed BOOLEAN DEFAULT 0
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                game_target TEXT,
                task_type TEXT,
                task_index INTEGER,
                description TEXT,
                completed BOOLEAN DEFAULT 0,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')
        self.conn.commit()

    def get_user(self, user_id):
        """
        ユーザーIDに基づいてユーザー情報を取得する。
        存在しない場合はNoneを返す。
        """
        self.cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = self.cursor.fetchone()
        if row:
            # カラム名と値を辞書として返す
            keys = [desc[0] for desc in self.cursor.description]
            return dict(zip(keys, row))
        return None

    def add_user_if_not_exists(self, user_id):
        """
        ユーザーが存在しない場合にデータベースに追加する。
        """
        if not self.get_user(user_id):
            self.cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
            self.conn.commit()

    def start_task(self, user_id, game_target, task_type):
        """
        指定されたゲーム（'eft'か'arena'）とタスクタイプ（'daily'か'weekly'）のカウントダウンを開始（更新）する。
        """
        self.add_user_if_not_exists(user_id)
        self.reset_user_tasks(user_id, game_target, task_type)
        now = datetime.datetime.now(datetime.timezone.utc)
        
        user_data = self.get_user(user_id)
        
        deadline_col = f"{game_target}_{task_type}_deadline"
        sent_flag_col = f"{game_target}_{task_type}_reminder_sent"
        completed_col = f"{game_target}_{task_type}_completed"
        seconds_col = f"{game_target}_{task_type}_reminder_seconds"
        once_col = f"{game_target}_{task_type}_reminder_once"

        if task_type == 'daily':
            if game_target == 'eft':
                deadline = now + datetime.timedelta(hours=22)
            else:
                deadline = now + datetime.timedelta(hours=24)
        elif task_type == 'weekly':
            if game_target == 'eft':
                deadline = now + datetime.timedelta(days=6, hours=22)
            else:
                deadline = now + datetime.timedelta(days=7)
        else:
            return None

        # 今回限りのリマインダーならリセットする
        if user_data and user_data.get(once_col):
            self.cursor.execute(
                f"UPDATE users SET {seconds_col} = 0, {once_col} = 0 WHERE user_id = ?",
                (user_id,)
            )

        self.cursor.execute(
            f"UPDATE users SET {deadline_col} = ?, {sent_flag_col} = 0, {completed_col} = 0 WHERE user_id = ?",
            (deadline, user_id)
        )
        
        self.conn.commit()
        return deadline

    def complete_task(self, user_id, game_target, task_type):
        """
        タスクを完了としてマークし、次のタスク開始可能時刻までの時間を計算する。
        """
        user_data = self.get_user(user_id)
        if not user_data:
            return None

        deadline_str = user_data.get(f'{game_target}_{task_type}_deadline')
        if not deadline_str:
            return None
        
        # 完了フラグを立てる
        completed_col = f"{game_target}_{task_type}_completed"
        self.cursor.execute(f"UPDATE users SET {completed_col} = 1 WHERE user_id = ?", (user_id,))
        self.conn.commit()

        # SQLiteのタイムスタンプ文字列をdatetimeオブジェクトに変換
        deadline = datetime.datetime.fromisoformat(deadline_str)
        return deadline

    def set_reminder(self, user_id, game_target, task_type, seconds: int, once: bool):
        """
        タスクのリマインダー時間を設定する。0秒の場合は無効化を意味する。
        """
        self.add_user_if_not_exists(user_id)
        seconds_col = f'{game_target}_{task_type}_reminder_seconds'
        once_col = f'{game_target}_{task_type}_reminder_once'
        sent_flag_col = f'{game_target}_{task_type}_reminder_sent'
        
        self.cursor.execute(
            f"UPDATE users SET {seconds_col} = ?, {once_col} = ?, {sent_flag_col} = 0 WHERE user_id = ?",
            (seconds, 1 if once else 0, user_id)
        )
        self.conn.commit()

    def get_pending_reminders(self):
        """
        通知されるべき保留中のリマインダーをすべて取得する。
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        pending = []

        for game in ['eft', 'arena']:
            for task in ['daily', 'weekly']:
                deadline_col = f"{game}_{task}_deadline"
                seconds_col = f"{game}_{task}_reminder_seconds"
                sent_col = f"{game}_{task}_reminder_sent"
                completed_col = f"{game}_{task}_completed"

                query = f"""
                    SELECT user_id, {deadline_col}, {seconds_col} FROM users
                    WHERE {deadline_col} IS NOT NULL AND {sent_col} = 0 AND {completed_col} = 0 AND {seconds_col} > 0
                """
                self.cursor.execute(query)
                reminders = self.cursor.fetchall()

                for user_id, deadline_str, seconds in reminders:
                    deadline = datetime.datetime.fromisoformat(deadline_str)
                    reminder_time = deadline - datetime.timedelta(seconds=seconds)
                    if now >= reminder_time:
                        pending.append({
                            'user_id': user_id,
                            'game_target': game,
                            'task_type': task,
                            'deadline': deadline
                        })
        
        return pending

    def mark_reminder_sent(self, user_id, game_target, task_type):
        """
        指定されたタスクのリマインダーを送信済みとしてマークする。
        """
        sent_flag_name = f'{game_target}_{task_type}_reminder_sent'
        self.cursor.execute(f"UPDATE users SET {sent_flag_name} = 1 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def undo_task(self, user_id, game_target, task_type):
        """
        完了したタスクを未完了状態に戻す。
        """
        user_data = self.get_user(user_id)
        if not user_data:
            return None

        deadline_str = user_data.get(f'{game_target}_{task_type}_deadline')
        if not deadline_str:
            return None

        completed_col = f'{game_target}_{task_type}_completed'
        self.cursor.execute(f"UPDATE users SET {completed_col} = 0 WHERE user_id = ?", (user_id,))
        self.conn.commit()

        return datetime.datetime.fromisoformat(deadline_str)

    def set_manual_deadline(self, user_id, game_target, task_type, new_deadline):
        """
        タスクの終了期限を手動で設定し、完了・通知状態をリセットする。
        """
        self.add_user_if_not_exists(user_id)
        self.reset_user_tasks(user_id, game_target, task_type)
        user_data = self.get_user(user_id)
        
        deadline_col = f"{game_target}_{task_type}_deadline"
        sent_flag_col = f"{game_target}_{task_type}_reminder_sent"
        completed_col = f"{game_target}_{task_type}_completed"
        seconds_col = f"{game_target}_{task_type}_reminder_seconds"
        once_col = f"{game_target}_{task_type}_reminder_once"

        # 今回限りのリマインダーならリセットする
        if user_data and user_data.get(once_col):
            self.cursor.execute(
                f"UPDATE users SET {seconds_col} = 0, {once_col} = 0 WHERE user_id = ?",
                (user_id,)
            )

        self.cursor.execute(
            f"UPDATE users SET {deadline_col} = ?, {sent_flag_col} = 0, {completed_col} = 0 WHERE user_id = ?",
            (new_deadline, user_id)
        )
        self.conn.commit()

        return new_deadline

    # --------------------------------------------------------------------------------
    # 個別タスク管理
    # --------------------------------------------------------------------------------

    def set_user_tasks(self, user_id, game_target, task_type, tasks_dict: dict):
        """
        ユーザーの個別タスク（説明文）を保存する。既存のものを上書き・削除する。
        tasks_dict: {1: "task desc", 2: "another desc"} (Noneや空文字ならスキップされる想定)
        """
        self.add_user_if_not_exists(user_id)
        # 既存タスクを削除
        self.cursor.execute(
            "DELETE FROM user_tasks WHERE user_id = ? AND game_target = ? AND task_type = ?",
            (user_id, game_target, task_type)
        )
        
        # 新しいタスクを挿入
        for index, desc in tasks_dict.items():
            if desc and str(desc).lower() != "none" and str(desc).strip() != "":
                self.cursor.execute(
                    "INSERT INTO user_tasks (user_id, game_target, task_type, task_index, description, completed) VALUES (?, ?, ?, ?, ?, 0)",
                    (user_id, game_target, task_type, index, str(desc).strip())
                )
        self.conn.commit()

    def get_user_tasks(self, user_id, game_target, task_type):
        """
        登録された個別タスクの一覧を取得する。
        """
        self.cursor.execute(
            "SELECT task_index, description, completed FROM user_tasks WHERE user_id = ? AND game_target = ? AND task_type = ? ORDER BY task_index ASC",
            (user_id, game_target, task_type)
        )
        rows = self.cursor.fetchall()
        tasks = []
        for index, desc, completed in rows:
            tasks.append({
                'task_index': index,
                'description': desc,
                'completed': bool(completed)
            })
        return tasks

    def complete_individual_task(self, user_id, game_target, task_type, task_index):
        """
        個別タスクを完了としてマークする。
        """
        self.cursor.execute(
            "UPDATE user_tasks SET completed = 1 WHERE user_id = ? AND game_target = ? AND task_type = ? AND task_index = ?",
            (user_id, game_target, task_type, task_index)
        )
        self.conn.commit()

    def undo_individual_task(self, user_id, game_target, task_type, task_index):
        """
        個別タスクを未完了に戻す。
        """
        self.cursor.execute(
            "UPDATE user_tasks SET completed = 0 WHERE user_id = ? AND game_target = ? AND task_type = ? AND task_index = ?",
            (user_id, game_target, task_type, task_index)
        )
        self.conn.commit()

    def reset_user_tasks(self, user_id, game_target, task_type):
        """
        新しいタイマーサイクルの開始に伴い、個別タスクを削除する。
        """
        self.cursor.execute(
            "DELETE FROM user_tasks WHERE user_id = ? AND game_target = ? AND task_type = ?",
            (user_id, game_target, task_type)
        )
        self.conn.commit()

if __name__ == '__main__':
    # テスト用コード (省略)
    pass
