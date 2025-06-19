
import logging
import time
import random
import string
import os
from datetime import datetime, timedelta
from pymongo import MongoClient
from flask import Flask, request
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes
import requests
import threading
import asyncio
from dotenv import load_dotenv

# === Load environment variables ===
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
SHORTNER_API = os.getenv("SHORTNER_API")
FLASK_URL = os.getenv("FLASK_URL")
LIKE_API_URL = os.getenv("LIKE_API_URL")
PLAYER_INFO_API = os.getenv("PLAYER_INFO_API")
HOW_TO_VERIFY_URL = os.getenv("HOW_TO_VERIFY_URL")
VIP_ACCESS_URL = os.getenv("VIP_ACCESS_URL")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.isdigit()]

client = MongoClient(MONGO_URI)
db = client['likebot']
users = db['verifications']
profiles = db['users']

# === Flask App ===
flask_app = Flask(__name__)

@flask_app.route("/verify/<code>")
def verify(code):
    user = users.find_one({"code": code})
    if user and not user.get("verified"):
        users.update_one({"code": code}, {"$set": {"verified": True, "verified_at": datetime.utcnow()}})
        return "✅ Verification successful. Bot will now process your like."
    return "❌ Link expired or already used."

async def like_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    try:
        args = update.message.text.split()
        uid = args[2]
    except:
        await update.message.reply_text("❌ Format galat hai. Use: /like ind <uid>")
        return

    try:
        url = PLAYER_INFO_API.format(uid=uid)
        resp = requests.get(url, timeout=5)
        info = resp.json()
        player_name = info.get("PlayerNickname") or f"Player-{uid[-4:]}"
        region = info.get("region") or "?"
    except Exception as e:
        print(f"[ERROR] PLAYER_INFO_API failed: {e}")
        player_name = f"Player-{uid[-4:]}"
        region = "?"

    code = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
    short_link = requests.get(
        f"https://shortner.in/api?api={SHORTNER_API}&url={FLASK_URL}/verify/{code}"
    ).json().get("shortenedUrl", f"{FLASK_URL}/verify/{code}")

    users.insert_one({
        "user_id": update.message.from_user.id,
        "uid": uid,
        "code": code,
        "verified": False,
        "region": region,
        "expires_at": datetime.utcnow() + timedelta(minutes=10),
        "chat_id": update.effective_chat.id,
        "message_id": update.message.message_id
    })

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ VERIFY & SEND LIKE ✅", url=short_link)],
        [InlineKeyboardButton("❓ How to Verify ❓", url=HOW_TO_VERIFY_URL)],
        [InlineKeyboardButton("😇 PURCHASE VIP & NO VERIFY", url=VIP_ACCESS_URL)]
    ])

    msg = f"""✅ Like request process !

👤 Name: {player_name}
🆔 UID: {uid}
🌍 Region: {region.upper()}

⚠️ Verify within 10 minutes"""
    await update.message.reply_text(msg, reply_markup=keyboard, parse_mode='Markdown')

async def givevip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🚫 You are not authorized to use this command.")
        return
    try:
        target_id = int(context.args[0])
    except:
        await update.message.reply_text("❌ Use: /givevip <user_id>")
        return

    profiles.update_one({"user_id": target_id}, {"$set": {"is_vip": True}}, upsert=True)
    await update.message.reply_text(f"✅ VIP access granted to user `{target_id}`", parse_mode='Markdown')

async def process_verified_likes(app: Application):
    while True:
        pending = users.find({"verified": True, "processed": {"$ne": True}})
        for user in pending:
            uid = user['uid']
            region = user.get('region', '?')
            user_id = user['user_id']
            profile = profiles.find_one({"user_id": user_id}) or {}
            is_vip = profile.get("is_vip", False)
            last_used = profile.get("last_used")

            if not is_vip and last_used:
                elapsed = datetime.utcnow() - last_used
                if elapsed < timedelta(hours=24):
                    result = "❌ You have reached your daily request limit. ❤️‍🩹 Please wait for reset or contact @dg_gaming_1m ✓ to upgrade your vip package!."
                    await app.bot.send_message(
                        chat_id=user['chat_id'],
                        reply_to_message_id=user['message_id'],
                        text=result,
                        parse_mode='Markdown'
                    )
                    users.update_one({"_id": user['_id']}, {"$set": {"processed": True}})
                    continue

            try:
                api_resp = requests.get(LIKE_API_URL.format(uid=uid), timeout=10).json()
                player_name = api_resp.get("PlayerNickname", f"Player-{uid[-4:]}")
                before = api_resp.get("LikesbeforeCommand", 0)
                after = api_resp.get("LikesafterCommand", 0)
                given_likes = api_resp.get("LikesGivenByAPI", 0)

                if given_likes == 0:
                    result = f"💔 UID {uid} ({player_name}) has already received max likes for today 😢. Try again tomorrow!"
                else:
                    result = f"""✅ Like Process Completed!

👤 Name: {player_name}
🆔 UID: {uid}
🌍 Region: {region.upper()}
🤡 Before: {before}
🗿 After: {after}
🎉 Given: {given_likes}✓

❤️‍🩹 Buy vip contact @dg_gaming_1m ✓"""
                    profiles.update_one({"user_id": user_id}, {"$set": {"last_used": datetime.utcnow()}}, upsert=True)

            except Exception as e:
                result = f"""❌ *API Error: Unable to process like*

🆔 UID: `{uid}`
📛 Error: {str(e)}"""

            await app.bot.send_message(
                chat_id=user['chat_id'],
                reply_to_message_id=user['message_id'],
                text=result,
                parse_mode='Markdown'
            )
            users.update_one({"_id": user['_id']}, {"$set": {"processed": True}})
        await asyncio.sleep(5)

def run_bot():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("like", like_command))
    app.add_handler(CommandHandler("givevip", givevip_command))

    threading.Thread(target=flask_app.run, kwargs={"host": "0.0.0.0", "port": 8000}).start()

    async def runner():
        await process_verified_likes(app)

    threading.Thread(target=lambda: asyncio.run(runner())).start()
    app.run_polling()

if __name__ == '__main__':
    run_bot()
