import os
import io
import json
import logging
import PIL.Image
import asyncio
import nest_asyncio
import textwrap
from pyrogram import Client, filters, idle
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Poll,
    CallbackQuery,
    ForceReply
)
from pyrogram.enums import ParseMode, PollType
import google.generativeai as genai

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========= Configuration Storage =========
AUTH_USERS_FILE = "authorized_users.json"

class Config:
    def __init__(self):
        self.TARGET_CHANNEL = -1002464876558
        self.PREFIX = "[POLL]"
        self.EXPLANATION_TEXT = "Join t.me/link" # Manual explanation for all polls

        self.GOOGLE_API_KEYS = [
            "AISyDRbtjmthmHGbswyq12ZPGt4odEdErU6Q0",
            "AISyCrhbN2hWfiXUfeEYiASumL_H0hUcz4aI8 ="
        ]
        self.api_key_index = 0
        
        # Load users from file or set default
        self.AUTHORIZED_USERS = set()
        self.load_authorized_users()

    def load_authorized_users(self):
        if os.path.exists(AUTH_USERS_FILE):
            try:
                with open(AUTH_USERS_FILE, "r") as f:
                    users_list = json.load(f)
                    self.AUTHORIZED_USERS = set(users_list)
            except Exception as e:
                logger.error(f"Error loading authorized users: {e}")
                self.AUTHORIZED_USERS = {7145991193}
        else:
            self.AUTHORIZED_USERS = {7145991193}
            self.save_authorized_users()

    def save_authorized_users(self):
        try:
            with open(AUTH_USERS_FILE, "w") as f:
                json.dump(list(self.AUTHORIZED_USERS), f)
        except Exception as e:
            logger.error(f"Error saving authorized users: {e}")

    def get_next_api_key(self):
        if not self.GOOGLE_API_KEYS or "YOUR_NEW_SECURE_API_KEY" in self.GOOGLE_API_KEYS[0]:
            raise ValueError("Google API keys are not configured properly.")
        key = self.GOOGLE_API_KEYS[self.api_key_index]
        self.api_key_index = (self.api_key_index + 1) % len(self.GOOGLE_API_KEYS)
        logger.info(f"Using API key ending with ...{key[-4:]}")
        return key

config = Config()

# ========= Bot Setup =========
API_ID = 26400657
API_HASH = "8c20ddfa6c36b3fb15cabc735c180f738"
BOT_TOKEN = "943255622:AAHJLWZdmDQce4NzZ51FdFqXoVVKez4ZrLk"
MODEL_NAME = "gemini-2.5-flash"

app = Client("mcq_quiz_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Global Storage
created_polls = {}
poll_edit_requests = {} 
user_cooldowns = {} # Tracks cooldown status per user
model_for_session = None

async def is_authorized(user_id):
    return user_id in config.AUTHORIZED_USERS

# ========= Admin Command Handler =========
@app.on_message(filters.command(["01869293233"]))
async def manage_authorized_users(client: Client, message: Message):
    if message.from_user.id not in config.AUTHORIZED_USERS:
         return await message.reply_text("🚫 Unauthorized.")

    if len(message.command) < 2:
        user_list = ", ".join(f"`{uid}`" for uid in config.AUTHORIZED_USERS)
        return await message.reply_text(
            f"🔐 Authorized Users:\n{user_list}\n\n"
            "**Usage:**\n"
            "`/01869293233 add <user_id>`\n"
            "`/01869293233 remove <user_id>`"
        )

    action = message.command[1].lower()
    if len(message.command) < 3:
        return await message.reply_text("❌ User ID required.")

    try:
        user_id_to_manage = int(message.command[2])
    except ValueError:
        return await message.reply_text("❌ User ID must be a number.")

    if action == "add":
        config.AUTHORIZED_USERS.add(user_id_to_manage)
        config.save_authorized_users()  
        await message.reply_text(f"✅ User `{user_id_to_manage}` added.")
    elif action == "remove":
        if user_id_to_manage in config.AUTHORIZED_USERS and len(config.AUTHORIZED_USERS) == 1:
            return await message.reply_text("❌ Cannot remove the sole admin.")
        config.AUTHORIZED_USERS.discard(user_id_to_manage)
        config.save_authorized_users()  
        await message.reply_text(f"✅ User `{user_id_to_manage}` removed.")
    else:
        await message.reply_text("❌ Invalid action. Use 'add' or 'remove'.")

# ========= Settings Handlers =========
async def get_settings_markup_and_text():
    text = (f"⚙️ **Settings:**\n\n"
            f"• Channel ID: `{config.TARGET_CHANNEL}`\n"
            f"• Prefix: `{config.PREFIX}`\n"
            f"• Explanation: `{config.EXPLANATION_TEXT}`")
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Set Channel", callback_data="set_channel")],
        [InlineKeyboardButton("🏷️ Set Prefix", callback_data="set_prefix")],
        [InlineKeyboardButton("📝 Set Explanation", callback_data="set_explanation")],
        [InlineKeyboardButton("❌ Close", callback_data="close_settings")]])
    return text, markup

@app.on_message(filters.command(["settings"]))
async def settings_cmd(client: Client, message: Message):
    if not await is_authorized(message.from_user.id): return await message.reply_text("🚫 Unauthorized.")
    settings_text, settings_markup = await get_settings_markup_and_text()
    await message.reply_text(settings_text, reply_markup=settings_markup)

@app.on_callback_query(filters.regex(r"^(set_channel|set_prefix|set_explanation|close_settings)$"))
async def handle_settings_callbacks(client: Client, callback: CallbackQuery):
    if not await is_authorized(callback.from_user.id): return await callback.answer("🚫 Unauthorized.", show_alert=True)

    action = callback.data

    if action in ["set_channel", "set_prefix", "set_explanation"]:
        prompts = {
            "set_channel": "📢 Send new Channel ID (e.g., -100123456789):",
            "set_prefix": "🏷️ Send new Prefix (e.g., [QUIZ]):",
            "set_explanation": "📝 Send new Explanation text:"
        }
        await callback.message.edit_text(prompts[action])
        await callback.answer()

    elif action == "close_settings":
        await callback.message.delete()
        await callback.answer("Settings closed.")

@app.on_message(filters.private & ~filters.command(["start", "help", "mcq", "send", "settings", "01869293233"]))
async def handle_text_input(client: Client, message: Message):
    if not await is_authorized(message.from_user.id): return
    if not message.reply_to_message or not message.reply_to_message.from_user.is_self: return

    # --- Handle Poll Edits (Question or Options) ---
    if message.reply_to_message.id in poll_edit_requests:
        req_data = poll_edit_requests.pop(message.reply_to_message.id)
        original_poll_id = req_data["poll_id"]
        
        if original_poll_id not in created_polls:
            return await message.reply("❌ Poll not found (might be deleted).")
        
        poll_data = created_polls[original_poll_id]
        new_text = message.text.strip()
        
        status_msg = await message.reply("🔄 Updating poll...")
        
        if req_data["type"] == "question":
            full_question_text = f"{config.PREFIX}\n\n{new_text}"
            poll_data["question"] = full_question_text
            poll_data["original_block"]["question"] = new_text
        
        elif req_data["type"] == "option":
            idx = req_data["opt_idx"]
            label = poll_data["labels"][idx]
            poll_data["options"][idx] = new_text
            poll_data["original_block"]["options"][label] = new_text
            
        try:
            try:
                await client.delete_messages(message.chat.id, original_poll_id)
            except:
                pass 

            new_poll_msg = await client.send_poll(
                chat_id=message.chat.id,
                question=poll_data["question"],
                options=poll_data["options"],
                correct_option_id=poll_data["correct_option_id"],
                type=PollType.QUIZ,
                explanation=poll_data["explanation"],
                is_anonymous=True
            )
            
            del created_polls[original_poll_id]
            created_polls[new_poll_msg.id] = poll_data
            
            markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🗑️ Delete", callback_data=f"del:{new_poll_msg.id}"),
                    InlineKeyboardButton("✏️ Edit", callback_data=f"ed:{new_poll_msg.id}")
                ]
            ])
            await new_poll_msg.edit_reply_markup(reply_markup=markup)
            
            await status_msg.edit_text("✅ Poll updated successfully!")
            await message.reply_to_message.delete()
            
        except Exception as e:
            logger.error(f"Error updating poll: {e}")
            await status_msg.edit_text(f"❌ Failed to update: {e}")
            
        return

    # --- Handle Settings Input ---
    reply_text = message.reply_to_message.text
    original_message = message.reply_to_message

    if "Channel ID" in reply_text:
        try:
            config.TARGET_CHANNEL = int(message.text.strip())
            await message.reply(f"✅ Channel ID updated: `{config.TARGET_CHANNEL}`")
        except ValueError:
            await message.reply("❌ Invalid Channel ID!")
    elif "Prefix" in reply_text:
        config.PREFIX = message.text.strip()
        await message.reply(f"✅ Prefix updated: `{config.PREFIX}`")
    elif "Explanation text" in reply_text:
        config.EXPLANATION_TEXT = message.text.strip()
        await message.reply(f"✅ Explanation updated: `{config.EXPLANATION_TEXT}`")

    await original_message.delete()

# ========= MCQ Command =========
@app.on_message(filters.command(["mcq"]))
async def mcq_cmd(client: Client, message: Message):
    global created_polls, model_for_session, user_cooldowns
    user_id = message.from_user.id
    
    if not await is_authorized(user_id): 
        return await message.reply_text("🚫 Unauthorized.")
    if not message.reply_to_message or not message.reply_to_message.photo: 
        return await message.reply_text("❌ Reply to an image with /mcq.")

    # Check cooldown
    if user_cooldowns.get(user_id, False):
        return await message.reply_text("⏳ Please wait for the current cooldown to finish before sending another request.")

    # Lock user
    user_cooldowns[user_id] = True
    status_msg = await message.reply("🔍 Analyzing image...")

    try:
        api_key = config.get_next_api_key()
        genai.configure(api_key=api_key)
        model_for_session = genai.GenerativeModel(MODEL_NAME)

        img_data = await client.download_media(message.reply_to_message, in_memory=True)
        img = PIL.Image.open(io.BytesIO(img_data.getbuffer()))

        prompt = textwrap.dedent("""
            # Output format (keep strictly to this structure):
            প্রশ্ন: [Question text without question numbers, include source if any]
            ক) [Option A]
            খ) [Option B]
            গ) [Option C]
            ঘ) [Option D]
            ঙ) [Option E if exists]
            সঠিক উত্তর: [Correct Option Letter]
            
            # Rules:
            1. Copy exactly. Do not translate.
            2. Use Unicode for math symbols (e.g., ², ₂).
            3. Extract all available questions from the image.
        """)

        response = await asyncio.to_thread(
            model_for_session.generate_content,
            [prompt, img],
        )

        extracted_text = response.text
        question_blocks = []
        current_block = {}
        OPTION_LABELS = ["ক", "খ", "গ", "ঘ", "ঙ"]
        for line in extracted_text.split('\n'):
            line = line.strip()
            if not line: continue
            if line.startswith("প্রশ্ন:"):
                if current_block: question_blocks.append(current_block)
                current_block = {"question": line.replace("প্রশ্ন:", "").strip(), "options": {}, "correct": ""}
            elif len(line) > 1 and line[0] in OPTION_LABELS and line[1] in [')', '.']:
                current_block["options"][line[0]] = line[2:].strip()
            elif line.startswith("সঠিক উত্তর:"):
                current_block["correct"] = line.replace("সঠিক উত্তর:", "").strip()
        if current_block: question_blocks.append(current_block)

        created_polls.clear()

        for block in question_blocks:
            try:
                found_labels = sorted(block.get("options", {}).keys())
                if not found_labels or block['correct'] not in found_labels:
                    logger.warning(f"Skipping poll: {block}")
                    continue

                correct_option_id = found_labels.index(block['correct'])
                options_list = [block['options'][label] for label in found_labels]
                question_text = f"{config.PREFIX}\n\n{block['question']}"
                
                final_explanation = config.EXPLANATION_TEXT

                sent_poll_msg = await client.send_poll(
                    chat_id=message.chat.id,
                    question=question_text,
                    options=options_list,
                    correct_option_id=correct_option_id,
                    type=PollType.QUIZ,
                    explanation=final_explanation,
                    is_anonymous=True
                )

                poll_data = {
                    "question": question_text,
                    "options": options_list,
                    "correct_option_id": correct_option_id,
                    "explanation": final_explanation,
                    "original_block": block,
                    "labels": found_labels
                }
                created_polls[sent_poll_msg.id] = poll_data

                markup = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🗑️ Delete", callback_data=f"del:{sent_poll_msg.id}"),
                        InlineKeyboardButton("✏️ Edit", callback_data=f"ed:{sent_poll_msg.id}")
                    ]
                ])
                await sent_poll_msg.edit_reply_markup(reply_markup=markup)
                await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Poll creation error: {e}")
                continue

        # Start 10 seconds countdown
        for i in range(10, 0, -1):
            await status_msg.edit_text(f"✅ {len(created_polls)} polls created!\n\n⏳ Next request cooldown: {i}s...")
            await asyncio.sleep(1)

        await status_msg.edit_text(f"✅ {len(created_polls)} polls created!\n\n📢 Send /send to forward them.\n✅ Ready for the next image!")
    
    except Exception as e:
        logger.error(f"MCQ command error: {e}", exc_info=True)
        await status_msg.edit_text("❌ A critical error occurred! Check logs.")
    finally:
        # Unlock user regardless of success or failure
        user_cooldowns[user_id] = False

# ========= Edit and Delete Poll Handler =========
@app.on_callback_query(filters.regex(r"^(del|ed|edq|edo|eol|eso|esel):"))
async def handle_poll_actions(client: Client, callback: CallbackQuery):
    if not await is_authorized(callback.from_user.id):
        return await callback.answer("🚫 Unauthorized.", show_alert=True)

    data_parts = callback.data.split(":", 1)
    action = data_parts[0]
    msg_id_str = data_parts[1]
    
    if ":" in msg_id_str and action in ["esel", "eso"]:
        message_id = int(msg_id_str.split(":")[0])
    else:
        message_id = int(msg_id_str)

    if message_id not in created_polls:
        return await callback.answer("❌ Poll no longer available.", show_alert=True)

    if action == "del":
        del created_polls[message_id]
        await callback.message.delete()
        await callback.answer("🗑️ Poll deleted.", show_alert=True)

    elif action == "ed":
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 Edit Question", callback_data=f"edq:{message_id}")],
            [InlineKeyboardButton("✅ Edit Correct Ans", callback_data=f"edo:{message_id}")],
            [InlineKeyboardButton("🔘 Edit Options", callback_data=f"eol:{message_id}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"esel:{message_id}:cancel")]
        ])
        await callback.message.edit_reply_markup(reply_markup=markup)
        await callback.answer("Select what to edit.")

    elif action == "edq":
        prompt_msg = await client.send_message(
            chat_id=callback.message.chat.id,
            text="✏️ Reply with the new question text:",
            reply_markup=ForceReply(selective=True)
        )
        poll_edit_requests[prompt_msg.id] = {"poll_id": message_id, "type": "question"}
        
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑️ Delete", callback_data=f"del:{message_id}"),
            InlineKeyboardButton("✏️ Edit", callback_data=f"ed:{message_id}")
        ]])
        await callback.message.edit_reply_markup(reply_markup=markup)
        await callback.answer()

    elif action == "edo":
        poll_data = created_polls[message_id]
        buttons = []
        for i, option_text in enumerate(poll_data["options"]):
            prefix = "✅ " if i == poll_data["correct_option_id"] else ""
            buttons.append(
                InlineKeyboardButton(
                    f"{prefix}{option_text[:25]}",
                    callback_data=f"esel:{message_id}:{i}"
                )
            )
        buttons.append(InlineKeyboardButton("🔙 Back", callback_data=f"ed:{message_id}"))
        edit_markup = InlineKeyboardMarkup([buttons[i:i+1] for i in range(len(buttons))])
        await callback.message.edit_reply_markup(reply_markup=edit_markup)
        await callback.answer("✏️ Select new correct answer.")
        
    elif action == "eol":
        poll_data = created_polls[message_id]
        buttons = []
        for i, option_text in enumerate(poll_data["options"]):
            buttons.append([
                InlineKeyboardButton(
                    f"✏️ {option_text[:30]}",
                    callback_data=f"eso:{message_id}:{i}"
                )
            ])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data=f"ed:{message_id}")])
        await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))
        await callback.answer("Select an option to edit.")

    elif action == "eso":
        _, original_msg_id_str, opt_index_str = callback.data.split(":")
        original_message_id = int(original_msg_id_str)
        opt_index = int(opt_index_str)
        
        prompt_msg = await client.send_message(
            chat_id=callback.message.chat.id,
            text=f"✏️ Reply with new text for Option {opt_index + 1}:",
            reply_markup=ForceReply(selective=True)
        )
        poll_edit_requests[prompt_msg.id] = {"poll_id": original_message_id, "type": "option", "opt_idx": opt_index}
        
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑️ Delete", callback_data=f"del:{original_message_id}"),
            InlineKeyboardButton("✏️ Edit", callback_data=f"ed:{original_message_id}")
        ]])
        await callback.message.edit_reply_markup(reply_markup=markup)
        await callback.answer()
        
    elif action == "esel":
        _, original_msg_id_str, new_index_str = callback.data.split(":")
        original_message_id = int(original_msg_id_str)
        
        if new_index_str == 'cancel':
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ Delete", callback_data=f"del:{original_message_id}"),
                InlineKeyboardButton("✏️ Edit", callback_data=f"ed:{original_message_id}")
            ]])
            await callback.message.edit_reply_markup(reply_markup=markup)
            return await callback.answer("✏️ Edit cancelled.")

        new_correct_index = int(new_index_str)
        poll_data = created_polls[original_message_id]

        if new_correct_index == poll_data["correct_option_id"]:
            return await callback.answer("⚠️ Already the correct answer.", show_alert=True)

        await callback.answer("🔄 Updating poll...", show_alert=False)
        original_block = poll_data["original_block"]
        labels = poll_data["labels"]

        await callback.message.delete()
        del created_polls[original_message_id]

        new_poll_msg = await client.send_poll(
            chat_id=callback.message.chat.id,
            question=poll_data["question"],
            options=poll_data["options"],
            correct_option_id=new_correct_index,
            type=PollType.QUIZ,
            explanation=poll_data["explanation"],
            is_anonymous=True
        )

        poll_data["correct_option_id"] = new_correct_index
        created_polls[new_poll_msg.id] = poll_data

        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑️ Delete", callback_data=f"del:{new_poll_msg.id}"),
            InlineKeyboardButton("✏️ Edit", callback_data=f"ed:{new_poll_msg.id}")
        ]])
        await new_poll_msg.edit_reply_markup(reply_markup=markup)

# ========= Send & Basics Handlers =========
@app.on_message(filters.command(["send"]))
async def send_to_channel(client: Client, message: Message):
    if not await is_authorized(message.from_user.id): return await message.reply_text("🚫 Unauthorized.")
    if not created_polls: return await message.reply_text("❌ No polls created.")

    status_msg = await message.reply(f"📢 Sending to `{config.TARGET_CHANNEL}`...")
    success_count = 0
    total_polls = len(created_polls)
    
    for poll_data in list(created_polls.values()):
        try:
            await client.send_poll(
                chat_id=config.TARGET_CHANNEL,
                question=poll_data["question"],
                options=poll_data["options"],
                correct_option_id=poll_data["correct_option_id"],
                type=PollType.QUIZ,
                explanation=poll_data["explanation"],
                is_anonymous=True)
            success_count += 1
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"Error sending to channel: {e}")
            await message.reply(f"Error sending '{poll_data['question'][:30]}...': {e}")
            continue
            
    await status_msg.edit_text(f"✅ {success_count}/{total_polls} polls sent to channel.")
    created_polls.clear()

@app.on_message(filters.command(["start", "help"]))
async def start_help_command(client: Client, message: Message):
    if not await is_authorized(message.from_user.id): return await message.reply_text("🚫 Unauthorized.")
    help_text = textwrap.dedent("""
        **🤖 MCQ Quiz Bot**

        1. Send an image of MCQs.
        2. Reply to the image with `/mcq`.
        3. Use Edit/Delete under each poll to modify Question, Options, or Correct Ans.
        4. Use `/send` to post all pending polls to the channel.
        5. Use `/settings` to configure Channel ID, Prefix, or Manual Explanation.
    """)
    await message.reply_text(help_text)

# ========= Start Bot =========
async def main():
    await app.start()
    user = await app.get_me()
    logger.info(f"🤖 @{user.username} is active!")
    await idle()
    await app.stop()

if __name__ == "__main__":
    nest_asyncio.apply()
    asyncio.run(main())
