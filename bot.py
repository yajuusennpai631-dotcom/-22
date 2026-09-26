import os
import json
import re
import discord
from discord.ext import commands
from discord import app_commands
import urllib.parse
import urllib.request
from bs4 import BeautifulSoup
from openai import OpenAI
from collections import defaultdict
import datetime

# 1. GroqのAPIキーを設定（Railwayの環境変数から読み込み）
client = OpenAI(
    api_key=os.environ["GROQ_API_KEY"],
    base_url="https://api.groq.com/openai/v1"
)

# カスタムBotクラスを作成して、自動同期（setup_hook）を実装
class AssistantBot(commands.Bot):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def setup_hook(self):
        print("🤖 スラッシュコマンドを同期中...")
        try:
            synced = await self.tree.sync()
            print(f"✅ スラッシュコマンドの自動同期が完了しました！（同期数: {len(synced)}個）")
        except Exception as e:
            print(f"❌ コマンドの自動同期中にエラーが発生しました: {e}")

# Discordボットの準備
intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True
intents.dm_reactions = True
intents.members = True  
intents.moderation = True 

bot = AssistantBot(command_prefix="/", intents=intents)

# -------------------- データの保存と読み込み機能 --------------------
# オーナー判定はユーザー名ではなくDiscordのユーザーID（数字）で行います。
# ユーザー名は変更可能なため、IDでの判定の方が安全です。
OWNER_IDS = {
    1500522641776447661,  # surippa
    1408802712426119173,  # MakuMaku_ra
}

# Railwayでボリュームをマウントしたパスを指定すると、そこにデータが永続化されます。
# 環境変数 DATA_DIR が未設定の場合はカレントディレクトリに保存します（永続化されません）。
DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)

ALLOWED_USERS_FILE = os.path.join(DATA_DIR, "allowed_users.json")
REACTION_ROLES_FILE = os.path.join(DATA_DIR, "reaction_roles.json")
GUILD_SETTINGS_FILE = os.path.join(DATA_DIR, "guild_settings.json")

def load_allowed_users():
    if os.path.exists(ALLOWED_USERS_FILE):
        try:
            with open(ALLOWED_USERS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_allowed_users():
    try:
        with open(ALLOWED_USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(allowed_users), f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"ユーザー保存エラー: {e}")

def load_reaction_roles():
    if os.path.exists(REACTION_ROLES_FILE):
        try:
            with open(REACTION_ROLES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {int(k): v for k, v in data.items()}
        except Exception:
            return {}
    return {}

def save_reaction_roles():
    try:
        with open(REACTION_ROLES_FILE, "w", encoding="utf-8") as f:
            json.dump(reaction_roles, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"リアクションロール保存エラー: {e}")

def load_guild_settings():
    if os.path.exists(GUILD_SETTINGS_FILE):
        try:
            with open(GUILD_SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_guild_settings():
    try:
        with open(GUILD_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(guild_settings, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"サーバー設定保存エラー: {e}")

allowed_users = load_allowed_users()
reaction_roles = load_reaction_roles()
guild_settings = load_guild_settings()
ai_chat_channels = {}

# スパム検知用の一時メモリ
user_message_timestamps = defaultdict(lambda: defaultdict(list))

# -------------------- セキュリティ制限 --------------------

def is_allowed_user():
    async def predicate(interaction: discord.Interaction) -> bool:
        user_name = interaction.user.name.lower()
        if interaction.user.id in OWNER_IDS or user_name in allowed_users:
            return True
        await interaction.response.send_message("❌ あなたはこのボットを使用する権限がありません。", ephemeral=True)
        return False
    return app_commands.check(predicate)

def is_owner():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id in OWNER_IDS:
            return True
        await interaction.response.send_message("❌ このコマンドはボットオーナー専用です。", ephemeral=True)
        return False
    return app_commands.check(predicate)

# -------------------- ログ送信ヘルパー関数 --------------------

async def send_log(guild: discord.Guild, embed: discord.Embed):
    if not guild:
        return
    guild_id = str(guild.id)
    settings = guild_settings.get(guild_id, {})
    log_channel_id = settings.get("log_channel_id")
    if log_channel_id:
        channel = guild.get_channel(int(log_channel_id))
        if channel:
            try:
                await channel.send(embed=embed)
            except discord.Forbidden:
                pass

# -------------------- ⚙️ サーバー向け：管理＆モデレーションコマンド --------------------

@bot.tree.command(name="give_role", description="【管理者専用】指定したユーザーに特定の役職を付与します")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(administrator=True)
@is_allowed_user()
async def give_role_command(interaction: discord.Interaction, 対象ユーザー: discord.Member, 付与する役職: discord.Role):
    try:
        await 対象ユーザー.add_roles(付与する役職)
        await interaction.response.send_message(f"✅ {対象ユーザー.mention} に、役職「**{付与する役職.name}**」を付与しました。")
        
        embed = discord.Embed(title="🛡️ 役職付与ログ", color=discord.Color.green(), timestamp=discord.utils.utcnow())
        embed.add_field(name="対象ユーザー", value=対象ユーザー.mention, inline=True)
        embed.add_field(name="付与された役職", value=付与する役職.name, inline=True)
        embed.add_field(name="実行者", value=interaction.user.mention, inline=True)
        await send_log(interaction.guild, embed)
    except discord.Forbidden:
        await interaction.response.send_message("❌ ボットの役職順位が低いため、または権限不足により役職を付与できません。", ephemeral=True)

@bot.tree.command(name="ban", description="【管理者専用】指定したユーザーをサーバーから永久追放します")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(administrator=True)
@is_allowed_user()
async def ban_command(interaction: discord.Interaction, 対象ユーザー: discord.Member, 理由: str = "理由なし"):
    try:
        await 対象ユーザー.ban(reason=理由)
        await interaction.response.send_message(f"🔨 {対象ユーザー.name} をサーバーからBANしました。理由: `{理由}`")
    except discord.Forbidden:
        await interaction.response.send_message("❌ 権限が不足しているため、このユーザーをBANできません。", ephemeral=True)

@bot.tree.command(name="timeout", description="【管理者専用】指定したユーザーを指定時間（分）タイムアウトします")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(administrator=True)
@is_allowed_user()
async def timeout_command(interaction: discord.Interaction, 対象ユーザー: discord.Member, 時間分: int, 理由: str = "理由なし"):
    if 時間分 <= 0:
        await interaction.response.send_message("❌ 時間は1分以上で指定してください。", ephemeral=True)
        return
    try:
        until = discord.utils.utcnow() + datetime.timedelta(minutes=時間分)
        await 対象ユーザー.timed_out_until(until, reason=理由)
        await interaction.response.send_message(f"🕒 {対象ユーザー.mention} を **{時間分}分間** タイムアウトしました。理由: `{理由}`")
    except discord.Forbidden:
        await interaction.response.send_message("❌ 権限が不足しているため、このユーザーをタイムアウトできません。", ephemeral=True)

@bot.tree.command(name="kick", description="【管理者専用】指定したユーザーをサーバーからキックします")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(administrator=True)
@is_allowed_user()
async def kick_command(interaction: discord.Interaction, 対象ユーザー: discord.Member, 理由: str = "理由なし"):
    try:
        await 対象ユーザー.kick(reason=理由)
        await interaction.response.send_message(f"👢 {対象ユーザー.name} をサーバーからキックしました。理由: `{理由}`")
    except discord.Forbidden:
        await interaction.response.send_message("❌ 権限が不足しているため、このユーザーをキックできません。", ephemeral=True)

@bot.tree.command(name="purge", description="【管理者専用】このチャンネルのメッセージを最大100件まで一括削除します")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(administrator=True)
@is_allowed_user()
async def purge_command(interaction: discord.Interaction, 件数: int):
    if 件数 < 1 or 件数 > 100:
        await interaction.response.send_message("❌ 削除できるメッセージは 1〜100 件の間のみです。", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    try:
        deleted = await interaction.channel.purge(limit=件数)
        await interaction.followup.send(f"🧹 メッセージを **{len(deleted)}件** 削除しました。")
    except Exception as e:
        await interaction.followup.send(f"❌ 削除中にエラーが発生しました: {e}")

@bot.tree.command(name="lock_channel", description="【管理者専用】チャンネルをロックし、管理者以外見れないようにします")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(administrator=True)
@is_allowed_user()
@app_commands.choices(状態=[
    app_commands.Choice(name="ロックする", value="lock"),
    app_commands.Choice(name="ロック解除する", value="unlock")
])
async def lock_channel_command(interaction: discord.Interaction, 状態: str):
    channel = interaction.channel
    guild = interaction.guild
    overwrite = channel.overwrites_for(guild.default_role)

    if 状態 == "lock":
        overwrite.read_messages = False
        overwrite.send_messages = False
        await channel.set_permissions(guild.default_role, overwrite=overwrite)
        await interaction.response.send_message("🚨 **チャンネルをロックしました。**\n一般メンバーからはアクセスできません。")
    else:
        overwrite.read_messages = None
        overwrite.send_messages = None
        await channel.set_permissions(guild.default_role, overwrite=overwrite)
        await interaction.response.send_message("🔓 **チャンネルロックを解除しました。**")

# -------------------- 🌐 外部向け：どこでも使える（User Install）コマンド --------------------

@bot.tree.command(name="jst_time", description="現在の正確な日本時間(JST)を表示します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@is_allowed_user()
async def jst_time_command(interaction: discord.Interaction):
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_jst = now_utc + datetime.timedelta(hours=9)
    time_str = now_jst.strftime("%Y年%m月%d日 %H時%M分%S秒")
    await interaction.response.send_message(f"🕒 現在の日本時間(JST)は **{time_str}** です。")

@bot.tree.command(name="purge_my_messages", description="このチャンネル内の「あなたのメッセージだけ」を最大100件まで一括削除します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@is_allowed_user()
async def purge_my_messages_command(interaction: discord.Interaction, 件数: int):
    if 件数 < 1 or 件数 > 100:
        await interaction.response.send_message("❌ 削除できるメッセージは 1〜100 件の間のみです。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    target_user = interaction.user
    
    def is_me(m):
        return m.author.id == target_user.id

    try:
        deleted = await interaction.channel.purge(limit=100, check=is_me)
        actual_deleted = deleted[:件数]
        await interaction.followup.send(f"🧹 ご自身のメッセージを **{len(actual_deleted)}件** 削除しました。")
    except Exception as e:
        await interaction.followup.send(f"❌ メッセージの削除に失敗しました。\nエラー: {e}")

@bot.tree.command(name="shutdown", description="【オーナー専用】プログラムを安全にシャットダウンします")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@is_owner()
async def shutdown_command(interaction: discord.Interaction):
    await interaction.response.send_message("🛑 システムをシャットダウンします。")
    print("⚠️ オーナーの指示によりボットをシャットダウンします。")
    await bot.close()

# -------------------- 🎉 汎用・便利コマンド --------------------

@bot.tree.command(name="summarize", description="指定されたWebサイトや動画(YouTube)のURLの内容を要約します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@is_allowed_user()
async def summarize_command(interaction: discord.Interaction, url: str):
    await interaction.response.defer()
    
    if not (url.startswith("http://") or url.startswith("https://")):
        await interaction.followup.send("❌ 有効なURLを入力してください。")
        return

    is_youtube = "youtube.com" in url or "youtu.be" in url

    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            html = response.read()
        
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.string if soup.title else "タイトル不明のページ"
        
        for script in soup(["script", "style"]):
            script.decompose()
        page_text = soup.get_text()
        clean_text = " ".join(page_text.split())[:1200]
        
    except Exception as e:
        await interaction.followup.send(f"❌ サイトの情報を取得できませんでした。\n詳細: {e}")
        return

    system_instruction = (
        "あなたは優秀なAIアシスタントです。提示されたページのデータに基づき、"
        f"{'動画の概要' if is_youtube else '何について書かれているのか'}を丁寧な日本語で、箇条書きを交えて300文字程度に要約してください。"
    )

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"URL: {url}\nページタイトル: {title}\nページ抽出テキスト:\n{clean_text}"}
            ]
        )
        summary = response.choices[0].message.content
        
        embed = discord.Embed(
            title="📰 AI要約" if not is_youtube else "🎥 動画要約",
            description=summary,
            color=discord.Color.dark_green() if not is_youtube else discord.Color.red()
        )
        embed.add_field(name="解析対象URL", value=f"[{title}]({url})", inline=False)
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ AI要約の生成中にエラーが発生しました: {e}")

@bot.tree.command(name="make_schedule", description="今日の予定やタスクから時間割を作成します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@is_allowed_user()
async def make_schedule_command(interaction: discord.Interaction, 予定データ: str):
    await interaction.response.defer()
    
    system_instruction = (
        "あなたは優秀なAIアシスタントです。ユーザーが送ってきた予定、タスク、時間を分析し、"
        "1日の流れが一目でわかるタイムスケジュールをマークダウン形式で分かりやすく出力してください。"
    )

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"予定データ:\n{予定データ}"}
            ]
        )
        schedule_result = response.choices[0].message.content
        
        embed = discord.Embed(
            title="📅 タイムスケジュール",
            description=schedule_result,
            color=discord.Color.blue()
        )
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ スケジュール生成中にエラーが発生しました: {e}")

@bot.tree.command(name="set_log_channel", description="【サーバー専用】各種ログを送信するチャンネルを設定します")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(manage_guild=True)
@is_allowed_user()
async def set_log_channel_command(interaction: discord.Interaction, チャンネル: discord.TextChannel = None):
    guild_id = str(interaction.guild_id)
    if guild_id not in guild_settings:
        guild_settings[guild_id] = {"ng_words": {}, "spam_detection": None, "remove_invite": False, "log_channel_id": None, "auto_replies": {}}
    
    if チャンネル is None:
        guild_settings[guild_id]["log_channel_id"] = None
        save_guild_settings()
        await interaction.response.send_message("✅ ログチャンネルの設定を解除しました。", ephemeral=True)
    else:
        guild_settings[guild_id]["log_channel_id"] = チャンネル.id
        save_guild_settings()
        await interaction.response.send_message(f"✅ ログ送信先チャンネルを {チャンネル.mention} に設定しました！", ephemeral=True)

@bot.tree.command(name="auto_reply", description="【サーバー専用】特定の単語にボットが自動で返信する設定を管理します")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(manage_messages=True)
@is_allowed_user()
@app_commands.choices(操作=[
    app_commands.Choice(name="追加・更新する", value="set"),
    app_commands.Choice(name="削除する", value="remove")
])
async def auto_reply_command(interaction: discord.Interaction, 操作: str, 反応キーワード: str, 返信メッセージ: str = None):
    guild_id = str(interaction.guild_id)
    if guild_id not in guild_settings:
        guild_settings[guild_id] = {"ng_words": {}, "spam_detection": None, "remove_invite": False, "log_channel_id": None, "auto_replies": {}}
    
    if "auto_replies" not in guild_settings[guild_id]:
        guild_settings[guild_id]["auto_replies"] = {}

    if 操作 == "set":
        if not 返信メッセージ:
            await interaction.response.send_message("❌ 返信内容を設定してください。", ephemeral=True)
            return
        guild_settings[guild_id]["auto_replies"][反応キーワード] = 返信メッセージ
        save_guild_settings()
        await interaction.response.send_message(f"✅ 自動返信を設定しました！\n👉 キーワード: `{反応キーワード}`\n👉 返信: `{返信メッセージ}`", ephemeral=True)
    
    elif 操作 == "remove":
        if 反応キーワード in guild_settings[guild_id]["auto_replies"]:
            del guild_settings[guild_id]["auto_replies"][反応キーワード]
            save_guild_settings()
            await interaction.response.send_message(f"✅ キーワード `{反応キーワード}` の自動返信を削除しました。", ephemeral=True)
        else:
            await interaction.response.send_message("❌ そのキーワードは設定されていません。", ephemeral=True)

@bot.tree.command(name="add_ng_words", description="【サーバー専用】NGワードを追加し、処罰方法を設定します")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(manage_messages=True)
@is_allowed_user()
@app_commands.choices(処罰=[
    app_commands.Choice(name="メッセージ削除のみ", value="delete"),
    app_commands.Choice(name="タイムアウト", value="timeout")
])
async def add_ng_words_command(
    interaction: discord.Interaction, 
    ngワード: str, 
    処罰: str, 
    タイムアウト時間分: int = None
):
    guild_id = str(interaction.guild_id)
    if guild_id not in guild_settings:
        guild_settings[guild_id] = {"ng_words": {}, "spam_detection": None, "remove_invite": False, "log_channel_id": None, "auto_replies": {}}
    
    if "ng_words" not in guild_settings[guild_id]:
        guild_settings[guild_id]["ng_words"] = {}

    if 処罰 == "timeout" and (タイムアウト時間分 is None or タイムアウト時間分 <= 0):
        await interaction.response.send_message("❌ タイムアウトを選択した場合は、1分以上の『タイムアウト時間分』を指定してください。", ephemeral=True)
        return

    guild_settings[guild_id]["ng_words"][ngワード] = {
        "action": 処罰,
        "duration": タイムアウト時間分
    }
    save_guild_settings()

    action_text = "メッセージ削除" if 処罰 == "delete" else f"タイムアウト ({タイムアウト時間分}分間) + メッセージ削除"
    await interaction.response.send_message(f"✅ NGワードに **「{ngワード}」** を登録しました。\n検知時の処罰: `{action_text}`", ephemeral=True)

@bot.tree.command(name="remove_ng_words", description="【サーバー専用】登録したNGワードを削除します")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(manage_messages=True)
@is_allowed_user()
async def remove_ng_words_command(interaction: discord.Interaction, 削除するngワード: str):
    guild_id = str(interaction.guild_id)
    if guild_id in guild_settings and "ng_words" in guild_settings[guild_id]:
        if 削除するngワード in guild_settings[guild_id]["ng_words"]:
            del guild_settings[guild_id]["ng_words"][削除するngワード]
            save_guild_settings()
            await interaction.response.send_message(f"✅ NGワード **「{削除するngワード}」** を削除しました。", ephemeral=True)
            return
    await interaction.response.send_message("❌ そのNGワードは登録されていません。", ephemeral=True)

@bot.tree.command(name="spam_detection", description="【サーバー専用】連投スパムを検知して自動でメッセージ削除・タイムアウトさせます")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(manage_messages=True)
@is_allowed_user()
async def spam_detection_command(interaction: discord.Interaction, 秒: int, メッセージ数: int, 有効化: bool = True):
    guild_id = str(interaction.guild_id)
    if guild_id not in guild_settings:
        guild_settings[guild_id] = {"ng_words": {}, "spam_detection": None, "remove_invite": False, "log_channel_id": None, "auto_replies": {}}

    if not 有効化:
        guild_settings[guild_id]["spam_detection"] = None
        save_guild_settings()
        await interaction.response.send_message("✅ スパム検知機能を無効化しました。", ephemeral=True)
        return

    if 秒 <= 0 or メッセージ数 <= 1:
        await interaction.response.send_message("❌ 秒数は1秒以上、メッセージ数は2通以上に設定してください。", ephemeral=True)
        return

    guild_settings[guild_id]["spam_detection"] = {
        "seconds": 秒,
        "count": メッセージ数
    }
    save_guild_settings()
    await interaction.response.send_message(f"✅ スパム検知を設定しました：\n👉 **{秒}秒間** に **{メッセージ数}通以上** 送信したユーザーのメッセージを自動削除＆5分間タイムアウトします。", ephemeral=True)

@bot.tree.command(name="remove_invitation_link", description="【サーバー専用】Discordサーバーへの招待リンクが投稿されたら自動削除します")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(manage_messages=True)
@is_allowed_user()
async def remove_invitation_link_command(interaction: discord.Interaction, 有効にする: bool):
    guild_id = str(interaction.guild_id)
    if guild_id not in guild_settings:
        guild_settings[guild_id] = {"ng_words": {}, "spam_detection": None, "remove_invite": False, "log_channel_id": None, "auto_replies": {}}

    guild_settings[guild_id]["remove_invite"] = 有効にする
    save_guild_settings()

    status = "有効（検知して削除）" if 有効にする else "無効"
    await interaction.response.send_message(f"✅ 招待リンク自動削除機能を **{status}** に設定しました。", ephemeral=True)

@bot.tree.command(name="search", description="指定した文章のGoogle検索用リンクを生成します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@is_allowed_user()
async def search_command(interaction: discord.Interaction, 検索ワード: str):
    encoded_query = urllib.parse.quote_plus(検索ワード)
    search_url = f"https://www.google.com/search?q={encoded_query}"
    
    embed = discord.Embed(
        title=f"🔍 「{検索ワード}」の検索結果リンク",
        description=f"こちらからGoogle検索へ移動できます：\n👉 [**Googleで検索する**]({search_url})",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="add_user", description="【オーナー専用】このBOTを使用できるユーザーを追加します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@is_owner()
async def add_user_command(interaction: discord.Interaction, ユーザー: discord.User):
    target_username = ユーザー.name.lower()
    allowed_users.add(target_username)
    save_allowed_users()
    await interaction.response.send_message(f"✅ {ユーザー.mention} (ユーザー名: `{ユーザー.name}`) を許可リストに追加しました！")

@bot.tree.command(name="sync", description="【オーナー専用】スラッシュコマンドを手動でDiscordに強制同期します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@is_owner()
async def sync_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        await bot.tree.sync()
        await interaction.followup.send("✅ スラッシュコマンドの手動同期が完了しました！")
    except Exception as e:
        await interaction.followup.send(f"❌ 同期中にエラーが発生しました: {e}")

@bot.tree.command(name="ai", description="AIアシスタントに質問します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@is_allowed_user()
async def ai_command(interaction: discord.Interaction, 質問内容: str):
    await interaction.response.defer()
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "あなたは親切なAIアシスタントです。必ず「日本語」で、質問に回答してください。"},
                {"role": "user", "content": 質問内容}
            ]
        )
        await interaction.followup.send(f"🗣️ **質問:** {質問内容}\n\n🤖 **回答:** {response.choices[0].message.content}")
    except Exception as e:
        await interaction.followup.send(f"エラーが発生しました: {e}")

@bot.tree.command(name="setup_reaction_role", description="メッセージに絵文字リアクションで自動ロール付与を設定します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@is_allowed_user()
@app_commands.default_permissions(manage_roles=True)
async def setup_reaction_role_command(interaction: discord.Interaction, メッセージid: str, 絵文字: str, ロール: discord.Role):
    if not interaction.guild:
        await interaction.response.send_message("❌ このコマンドはサーバー内でのみ実行できます。", ephemeral=True)
        return

    try:
        msg_id = int(メッセージid)
    except ValueError:
        await interaction.response.send_message("❌ メッセージIDは正しい数字を入力してください。", ephemeral=True)
        return

    if msg_id not in reaction_roles:
        reaction_roles[msg_id] = {}

    reaction_roles[msg_id][絵文字] = ロール.id
    save_reaction_roles()

    await interaction.response.send_message(
        f"✅ **リアクションロールの設定を完了しました！**\n"
        f"・対象メッセージID: `{msg_id}`\n"
        f"・絵文字: {絵文字}\n"
        f"・付与するロール: {ロール.mention}",
        ephemeral=True
    )

@bot.tree.command(name="help", description="コマンドリストを表示します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@is_allowed_user()
async def help_command(interaction: discord.Interaction):
    help_text = (
        "### 🤖 コマンドリスト\n"
        "📋 **/help**: コマンドリストを表示します。\n"
        "💬 **/ai [質問]**: AIに質問します。\n"
        "🔍 **/search [検索語]**: Googleの検索リンクを作ります。\n"
        "🗣️ **/auto_reply**: 【サーバー管理】自動返信を設定・削除します。\n"
        "📰 **/summarize [URL]**: WebサイトやYouTube動画の内容を要約します。\n"
        "📅 **/make_schedule [予定]**: タスクから時間割を作成します。\n"
        "⚙️ **/set_log_channel**: 【サーバー管理】ログ送信チャンネルを設定します。\n"
        "🚫 **/add_ng_words**: 【サーバー管理】NGワードを設定します。\n"
        "🗑️ **/remove_ng_words**: 【サーバー管理】NGワードを削除します。\n"
        "⚡ **/spam_detection**: 【サーバー管理】スパム自動検知を設定します。\n"
        "🔗 **/remove_invitation_link**: 【サーバー管理】招待リンク削除のON/OFF。\n"
        "👤 **/add_user [ユーザー]**: 【オーナー専用】使用許可ユーザーを追加します。\n"
        "🔄 **/sync**: 【オーナー専用】コマンドを強制同期します。\n"
        "🎭 **/setup_reaction_role**: リアクションロールを設定します。\n"
        "\n**🆕 管理・モデレーション機能**\n"
        "👑 **/give_role [ユーザー] [ロール]**: 【サーバー専用・管理者】役職を付与します。\n"
        "🔨 **/ban [ユーザー] [理由]**: 【サーバー専用・管理者】指定メンバーをBANします。\n"
        "🕒 **/timeout [ユーザー] [分数]**: 【サーバー専用・管理者】一時停止します。\n"
        "👢 **/kick [ユーザー] [理由]**: 【サーバー専用・管理者】キックします。\n"
        "🧹 **/purge [件数]**: 【サーバー専用・管理者】メッセージを一括削除します。\n"
        "🚨 **/lock_channel [状態]**: 【サーバー専用・管理者】チャンネルをロック・解除します。\n"
        "🌐 **/jst_time**: 現在の日本時間（JST）を表示します。\n"
        "🌐 **/purge_my_messages [件数]**: 自分の発言を一括削除します。\n"
        "🛑 **/shutdown**: 【オーナー専用】ボットを安全に停止させます。"
    )
    await interaction.response.send_message(help_text)

# -------------------- サーバー監視システム --------------------

INVITE_REGEX = re.compile(r"(discord\.(gg|io|me|li)|discordapp\.com/invite|discord\.com/invite)/[a-zA-Z0-9\-]+")

async def check_and_moderate_message(message: discord.Message) -> bool:
    if not message.guild or message.author.bot:
        return False

    guild_id = str(message.guild.id)
    settings = guild_settings.get(guild_id, {})
    member = message.author

    if settings.get("remove_invite", False):
        if INVITE_REGEX.search(message.content):
            try:
                await message.delete()
                await message.channel.send(f"⚠️ {member.mention} 招待リンクの投稿は禁止されています。", delete_after=5)
                
                embed = discord.Embed(title="🛡️ 招待リンクの自動削除", color=discord.Color.orange(), timestamp=discord.utils.utcnow())
                embed.add_field(name="対象ユーザー", value=f"{member.mention} ({member.name})", inline=True)
                embed.add_field(name="検知チャンネル", value=message.channel.mention, inline=True)
                embed.add_field(name="削除された内容", value=message.content, inline=False)
                await send_log(message.guild, embed)
                return True
            except discord.Forbidden:
                pass

    ng_words_dict = settings.get("ng_words", {})
    for word, punish in ng_words_dict.items():
        if word in message.content:
            try:
                await message.delete()
                action_text = "メッセージ削除のみ"
                
                if punish["action"] == "timeout":
                    duration_min = punish["duration"]
                    until = discord.utils.utcnow() + datetime.timedelta(minutes=duration_min)
                    await member.timed_out_until(until, reason=f"NGワード「{word}」発言によるペナルティ")
                    await message.channel.send(f"🚨 {member.mention} がNGワードを発言したため、**{duration_min}分間** タイムアウトしました。", delete_after=10)
                    action_text = f"タイムアウト ({duration_min}分) + メッセージ削除"
                else:
                    await message.channel.send(f"⚠️ {member.mention} 不適切な言葉が含まれていたため、メッセージを削除しました。", delete_after=5)
                
                embed = discord.Embed(title="🚨 NGワード検知", color=discord.Color.red(), timestamp=discord.utils.utcnow())
                embed.add_field(name="対象ユーザー", value=f"{member.mention} ({member.name})", inline=True)
                embed.add_field(name="検知ワード", value=f"||{word}||", inline=True)
                embed.add_field(name="適用された処罰", value=action_text, inline=True)
                embed.add_field(name="検知チャンネル", value=message.channel.mention, inline=True)
                embed.add_field(name="発言内容", value=message.content, inline=False)
                await send_log(message.guild, embed)
                return True
            except discord.Forbidden:
                pass

    spam_set = settings.get("spam_detection")
    if spam_set:
        now = discord.utils.utcnow().timestamp()
        timestamps = user_message_timestamps[guild_id][member.id]
        
        cutoff = now - spam_set["seconds"]
        user_message_timestamps[guild_id][member.id] = [t for t in timestamps if t > cutoff]
        user_message_timestamps[guild_id][member.id].append(now)
        
        if len(user_message_timestamps[guild_id][member.id]) >= spam_set["count"]:
            try:
                await message.delete()
                until = discord.utils.utcnow() + datetime.timedelta(minutes=5)
                await member.timed_out_until(until, reason="連投スパム検知")
                await message.channel.send(f"⚡ {member.mention} スパム連投が検知されたため、5分間タイムアウトしました。", delete_after=10)
                
                embed = discord.Embed(title="⚡ 連投スパム検知", color=discord.Color.red(), timestamp=discord.utils.utcnow())
                embed.add_field(name="対象ユーザー", value=f"{member.mention} ({member.name})", inline=True)
                embed.add_field(name="検知設定", value=f"{spam_set['seconds']}秒内に{spam_set['count']}通以上", inline=True)
                embed.add_field(name="適用された処罰", value="5分間タイムアウト + メッセージ削除", inline=True)
                embed.add_field(name="検知チャンネル", value=message.channel.mention, inline=True)
                await send_log(message.guild, embed)
                
                user_message_timestamps[guild_id][member.id] = []
                return True
            except discord.Forbidden:
                pass

    return False

# -------------------- Discord イベント処理群 --------------------

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')

@bot.event
async def on_message_edit(before, after):
    if before.author.bot:
        return
    if before.content != after.content:
        embed = discord.Embed(title="📝 メッセージ編集", color=discord.Color.blue(), timestamp=discord.utils.utcnow())
        embed.add_field(name="作成者", value=f"{before.author.mention} ({before.author.name})", inline=True)
        embed.add_field(name="チャンネル", value=before.channel.mention, inline=True)
        embed.add_field(name="編集前", value=before.content or "(画像や埋め込みなど)", inline=False)
        embed.add_field(name="編集後", value=after.content or "(画像や埋め込みなど)", inline=False)
        await send_log(before.guild, embed)

    await check_and_moderate_message(after)

@bot.event
async def on_message_delete(message):
    if message.author.bot:
        return
    embed = discord.Embed(title="🗑️ メッセージ削除", color=discord.Color.light_gray(), timestamp=discord.utils.utcnow())
    embed.add_field(name="作成者", value=f"{message.author.mention} ({message.author.name})", inline=True)
    embed.add_field(name="チャンネル", value=message.channel.mention, inline=True)
    embed.add_field(name="削除された内容", value=message.content or "(画像や埋め込みなど)", inline=False)
    await send_log(message.guild, embed)

@bot.event
async def on_member_ban(guild, user):
    reason_text = "指定なし"
    executor_text = "不明"
    try:
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
            if entry.target.id == user.id:
                reason_text = entry.reason or "指定なし"
                executor_text = entry.user.mention
                break
    except discord.Forbidden:
        pass

    embed = discord.Embed(title="🔨 メンバー追放 (BAN)", color=discord.Color.dark_red(), timestamp=discord.utils.utcnow())
    embed.add_field(name="対象者", value=f"{user.mention} ({user.name})", inline=True)
    embed.add_field(name="実行者", value=executor_text, inline=True)
    embed.add_field(name="理由", value=reason_text, inline=False)
    await send_log(guild, embed)

@bot.event
async def on_member_remove(member):
    guild = member.guild
    is_kick = False
    executor_text = "不明"
    reason_text = "指定なし"
    try:
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.kick):
            if entry.target.id == member.id and (discord.utils.utcnow() - entry.created_at).total_seconds() < 5:
                is_kick = True
                executor_text = entry.user.mention
                reason_text = entry.reason or "指定なし"
                break
    except discord.Forbidden:
        pass

    if is_kick:
        embed = discord.Embed(title="👢 メンバーキック", color=discord.Color.red(), timestamp=discord.utils.utcnow())
        embed.add_field(name="対象者", value=f"{member.mention} ({member.name})", inline=True)
        embed.add_field(name="実行者", value=executor_text, inline=True)
        embed.add_field(name="理由", value=reason_text, inline=False)
        await send_log(guild, embed)
    else:
        embed = discord.Embed(title="🚪 メンバー脱退", color=discord.Color.dark_grey(), timestamp=discord.utils.utcnow())
        embed.add_field(name="ユーザー", value=f"{member.mention} ({member.name})", inline=False)
        await send_log(guild, embed)

@bot.event
async def on_member_update(before, after):
    guild = after.guild
    if before.timed_out_until != after.timed_out_until:
        if after.timed_out_until is not None:
            executor_text = "不明"
            reason_text = "指定なし"
            try:
                async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.member_update):
                    if entry.target.id == after.id and entry.after.timed_out_until is not None:
                        executor_text = entry.user.mention
                        reason_text = entry.reason or "指定なし"
                        break
            except discord.Forbidden:
                pass

            embed = discord.Embed(title="🕒 手動タイムアウト適用", color=discord.Color.red(), timestamp=discord.utils.utcnow())
            embed.add_field(name="対象者", value=f"{after.mention} ({after.name})", inline=True)
            embed.add_field(name="解除時間", value=discord.utils.format_dt(after.timed_out_until, style="R"), inline=True)
            embed.add_field(name="実行者", value=executor_text, inline=True)
            embed.add_field(name="理由", value=reason_text, inline=False)
            await send_log(guild, embed)

@bot.event
async def on_message(message):
    is_moderated = await check_and_moderate_message(message)
    if is_moderated:
        return

    if message.author == bot.user or message.content.startswith("/"):
        return

    guild_id = str(message.guild.id) if message.guild else None
    if guild_id and guild_id in guild_settings:
        auto_replies = guild_settings[guild_id].get("auto_replies", {})
        for kw, reply in auto_replies.items():
            if kw in message.content:
                try:
                    await message.channel.send(reply)
                    return  
                except Exception:
                    pass

    if message.author.id not in OWNER_IDS and message.author.name.lower() not in allowed_users:
        return

    if ai_chat_channels.get(message.channel.id, False):
        try:
            async with message.channel.typing():
                response = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": "あなたは親切なAIアシスタントです。必ず「日本語」で、ユーザーと日常会話をしてください。"},
                        {"role": "user", "content": message.content}
                    ]
                )
            await message.channel.send(response.choices[0].message.content)
        except Exception:
            pass

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return

    if payload.message_id in reaction_roles:
        emoji_str = str(payload.emoji)
        if emoji_str in reaction_roles[payload.message_id]:
            role_id = reaction_roles[payload.message_id][emoji_str]
            guild = bot.get_guild(payload.guild_id)
            if guild:
                role = guild.get_role(role_id)
                try:
                    member = payload.member or await guild.fetch_member(payload.user_id)
                    if role and member:
                        await member.add_roles(role)
                        try:
                            await member.send(f"✅ **{guild.name}** で役職「**{role.name}**」が自動付与されました！")
                        except discord.Forbidden:
                            pass
                except Exception as e:
                    print(f"ロール付与エラー: {e}")

@bot.event
async def on_raw_reaction_remove(payload):
    if payload.message_id in reaction_roles:
        emoji_str = str(payload.emoji)
        if emoji_str in reaction_roles[payload.message_id]:
            role_id = reaction_roles[payload.message_id][emoji_str]
            guild = bot.get_guild(payload.guild_id)
            if guild:
                role = guild.get_role(role_id)
                try:
                    member = await guild.fetch_member(payload.user_id)
                    if role and member:
                        await member.remove_roles(role)
                        try:
                            await member.send(f"❌ **{guild.name}** で役職「**{role.name}**」が解除されました。")
                        except discord.Forbidden:
                            pass
                except Exception as e:
                    print(f"ロール解除エラー: {e}")

# 3. Discordのトークンを設定（Railwayの環境変数から読み込み）
bot.run(os.environ["DISCORD_BOT_TOKEN"])
