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
                eft_daily_reminder_hours INTEGER DEFAULT 3,
                eft_weekly_reminder_hours INTEGER DEFAULT 24,
                eft_daily_reminder_sent BOOLEAN DEFAULT 0,
                eft_weekly_reminder_sent BOOLEAN DEFAULT 0,
                eft_daily_completed BOOLEAN DEFAULT 0,
                eft_weekly_completed BOOLEAN DEFAULT 0,
                arena_daily_deadline TIMESTAMP,
                arena_weekly_deadline TIMESTAMP,
                arena_daily_reminder_hours INTEGER DEFAULT 3,
                arena_weekly_reminder_hours INTEGER DEFAULT 24,
                arena_daily_reminder_sent BOOLEAN DEFAULT 0,
                arena_weekly_reminder_sent BOOLEAN DEFAULT 0,
                arena_daily_completed BOOLEAN DEFAULT 0,
                arena_weekly_completed BOOLEAN DEFAULT 0
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
        now = datetime.datetime.now(datetime.timezone.utc)
        
        deadline_col = f"{game_target}_{task_type}_deadline"
        sent_flag_col = f"{game_target}_{task_type}_reminder_sent"
        completed_col = f"{game_target}_{task_type}_completed"

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

    def set_reminder_hours(self, user_id, game_target, task_type, hours):
        """
        タスクのリマインダー時間を設定する。
        """
        self.add_user_if_not_exists(user_id)
        column_name = f'{game_target}_{task_type}_reminder_hours'
        sent_flag_name = f'{game_target}_{task_type}_reminder_sent'
        
        self.cursor.execute(
            f"UPDATE users SET {column_name} = ?, {sent_flag_name} = 0 WHERE user_id = ?",
            (hours, user_id)
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
                hours_col = f"{game}_{task}_reminder_hours"
                sent_col = f"{game}_{task}_reminder_sent"
                completed_col = f"{game}_{task}_completed"

                query = f"""
                    SELECT user_id, {deadline_col}, {hours_col} FROM users
                    WHERE {deadline_col} IS NOT NULL AND {sent_col} = 0 AND {completed_col} = 0
                """
                self.cursor.execute(query)
                reminders = self.cursor.fetchall()

                for user_id, deadline_str, hours in reminders:
                    deadline = datetime.datetime.fromisoformat(deadline_str)
                    reminder_time = deadline - datetime.timedelta(hours=hours)
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
        deadline_col = f"{game_target}_{task_type}_deadline"
        sent_flag_col = f"{game_target}_{task_type}_reminder_sent"
        completed_col = f"{game_target}_{task_type}_completed"

        self.cursor.execute(
            f"UPDATE users SET {deadline_col} = ?, {sent_flag_col} = 0, {completed_col} = 0 WHERE user_id = ?",
            (new_deadline, user_id)
        )
        self.conn.commit()

        return new_deadline


if __name__ == '__main__':
    # テスト用コード (省略)
    pass
