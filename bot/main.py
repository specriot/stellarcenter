# =============================================================================
# LoyalCorp P1 Bot — Main Application
# =============================================================================
# Press-1 IVR Campaign Bot
# - Single global SIP trunk (pjsip.conf)
# - Admin approval required
# - Per-1k lines pricing
# - USA only (+1)
# =============================================================================

import logging
import asyncio
import io
import os
import re
from datetime import datetime
from typing import Optional, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

from config import (
    TELEGRAM_BOT_TOKEN, ADMIN_TELEGRAM_IDS,
    COMPANY_NAME, SUPPORT_USERNAME,
    ASTERISK_SOUNDS_DIR, COUNTRY_CODE,
    DEFAULT_CALLER_ID, WEBHOOK_HOST, WEBHOOK_PORT,
    MIN_TOPUP_AMOUNT, DEFAULT_PRICE_PER_1K
)
from database import db
from oxapay_handler import oxapay
from webhook_server import WebhookServer
from trunk_handler import handle_trunk_callback, handle_trunk_input

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

webhook_srv = WebhookServer(db, host=WEBHOOK_HOST, port=WEBHOOK_PORT)


# =============================================================================
# Helpers
# =============================================================================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_TELEGRAM_IDS


async def check_access(update: Update) -> bool:
    """Returns True if user is approved and not banned."""
    user = update.effective_user
    if is_admin(user.id):
        return True
    approved = await db.is_user_approved(user.id)
    if not approved:
        u = await db.get_user(user.id)
        if u and u.get('is_banned'):
            msg = "🚫 Your account has been banned. Contact support."
        else:
            msg = (
                f"⏳ <b>Access Pending</b>\n\n"
                f"Your account is awaiting admin approval.\n"
                f"Please contact {SUPPORT_USERNAME} for access."
            )
        if update.message:
            await update.message.reply_text(msg, parse_mode='HTML')
        elif update.callback_query:
            await update.callback_query.answer("Access denied.", show_alert=True)
        return False
    return True


def mini_bar(pct: float, width: int = 10) -> str:
    filled = int(pct / 100 * width)
    return chr(9608) * filled + chr(9617) * (width - filled)


def parse_numbers(text: str) -> List[str]:
    """Extract and normalize phone numbers from any format.

    Handles:
      - CSV rows with id/email/phone:  1721007,user@x.com,6467850166
      - pipe-delimited:                id|email|phone
      - plain list:                    one number per line
      - formatted:                     (646) 785-0166 / 646-785-0166 / +16467850166

    Rules:
      - 10-digit number  → prepend '1' (US)
      - 11-digit starting with '1' → keep as-is
      - anything else (7/8/9 digit IDs, etc.) → skip
      - duplicates are skipped silently
    """
    result = []
    seen   = set()

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        # Split comma or pipe delimited rows
        parts = re.split(r'[|,]', line)
        for part in parts:
            part = part.strip()

            # Skip if it looks like an email (contains @)
            if '@' in part:
                continue

            # Strip all non-digit characters
            digits = re.sub(r'[^\d]', '', part)

            # Must be exactly 10 or 11 digits to be a valid US phone
            if len(digits) == 10:
                # Plain 10-digit US number → add country code
                digits = COUNTRY_CODE + digits
            elif len(digits) == 11 and digits.startswith('1'):
                # Already has US country code
                pass
            else:
                # Not a valid US phone number (IDs, short numbers, etc.)
                continue

            if digits not in seen:
                seen.add(digits)
                result.append(digits)

    return result


def get_asterisk_sounds() -> List[str]:
    """List .wav/.ulaw/.gsm files in Asterisk sounds dir."""
    sounds = []
    try:
        for f in sorted(os.listdir(ASTERISK_SOUNDS_DIR)):
            if f.endswith(('.wav', '.ulaw', '.gsm', '.alaw', '.g722')):
                sounds.append(f)
    except Exception:
        pass
    return sounds[:40]  # max 40 listed


# =============================================================================
# /start — Main Menu
# =============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await db.get_or_create_user(
        user.id, user.username, user.first_name, user.last_name
    )

    if is_admin(user.id):
        await show_dashboard(update, context, user.id)
        return

    # Check access
    u = await db.get_user(user.id)
    if u and u.get('is_banned'):
        await update.message.reply_text("🚫 Your account has been banned.")
        return

    if u and not u.get('is_active'):
        # Notify admins of new registration
        name = user.first_name or user.username or str(user.id)
        for admin_id in ADMIN_TELEGRAM_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"🆕 <b>New User Registration</b>\n\n"
                        f"👤 Name: <b>{name}</b>\n"
                        f"🆔 ID: <code>{user.id}</code>\n"
                        f"📛 Username: @{user.username or 'N/A'}\n\n"
                        f"Use /approve {user.id} to grant access."
                    ),
                    parse_mode='HTML'
                )
            except Exception:
                pass
        await update.message.reply_text(
            f"👋 Welcome to <b>{COMPANY_NAME}</b>!\n\n"
            f"⏳ Your account is pending approval.\n"
            f"Contact {SUPPORT_USERNAME} for access.",
            parse_mode='HTML'
        )
        return

    await show_dashboard(update, context, user.id)


async def show_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    u = await db.get_user(user_id)
    stats = await db.get_user_stats(user_id)
    credits = float(u.get('credits', 0)) if u else 0.0
    name = (u.get('first_name') or u.get('username') or 'User') if u else 'User'

    if credits > 100:
        cr_status = "🟢 Excellent"
    elif credits > 20:
        cr_status = "🟡 Good"
    elif credits > 5:
        cr_status = "🟠 Low"
    else:
        cr_status = "🔴 Critical"

    text = (
        f"🏢 <b>{COMPANY_NAME}</b>\n\n"
        f"👤 {name}\n"
        f"💰 Balance: <b>${credits:.2f}</b> ({cr_status})\n\n"
        f"📊 Stats:\n"
        f"  📋 Leads: {stats.get('lead_count', 0)}\n"
        f"  📞 Campaigns: {stats.get('campaign_count', 0)}\n"
        f"  ✅ Press-1 Hits: {stats.get('total_p1', 0)}\n"
    )

    keyboard = [
        [
            InlineKeyboardButton("🚀 New Campaign", callback_data="menu_campaign"),
            InlineKeyboardButton("📊 My Campaigns", callback_data="menu_campaigns"),
        ],
        [
            InlineKeyboardButton("📋 My Leads",   callback_data="menu_leads"),
            InlineKeyboardButton("💰 Balance",     callback_data="menu_balance"),
        ],
        [
            InlineKeyboardButton("💳 Buy Credits", callback_data="menu_buy"),
            InlineKeyboardButton("📞 Set P1 Group", callback_data="menu_p1group"),
        ],
    ]
    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("🛡️ Admin Panel", callback_data="admin_panel")])

    markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text(text, parse_mode='HTML', reply_markup=markup)
    elif update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, parse_mode='HTML', reply_markup=markup)
        except Exception:
            await update.callback_query.message.reply_text(text, parse_mode='HTML', reply_markup=markup)


# =============================================================================
# Admin Commands
# =============================================================================

async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /approve <telegram_id>")
        return
    try:
        target = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID.")
        return
    result = await db.approve_user(target)
    if result:
        await update.message.reply_text(f"✅ User {target} approved.")
        try:
            await context.bot.send_message(
                chat_id=target,
                text=(
                    f"✅ <b>Access Granted!</b>\n\n"
                    f"Welcome to <b>{COMPANY_NAME}</b>!\n"
                    f"Use /start to begin."
                ),
                parse_mode='HTML'
            )
        except Exception:
            pass
    else:
        await update.message.reply_text(f"❌ User {target} not found.")


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /ban <telegram_id> [reason]")
        return
    try:
        target = int(context.args[0])
        reason = ' '.join(context.args[1:]) if len(context.args) > 1 else ""
    except ValueError:
        await update.message.reply_text("❌ Invalid ID.")
        return
    await db.ban_user(target, reason)
    await update.message.reply_text(f"🚫 User {target} banned. Reason: {reason or 'N/A'}")


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /unban <telegram_id>")
        return
    try:
        target = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID.")
        return
    await db.unban_user(target)
    await update.message.reply_text(f"✅ User {target} unbanned.")


async def addbal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /addbal <telegram_id> <amount>")
        return
    try:
        target = int(context.args[0])
        amount = float(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID or amount.")
        return
    new_bal = await db.add_credits(target, amount)
    await update.message.reply_text(f"✅ +${amount:.2f} added to {target}. New balance: ${new_bal:.2f}")
    try:
        await context.bot.send_message(
            chat_id=target,
            text=f"💰 <b>${amount:.2f} credits added</b> to your account by admin.\nNew balance: <b>${new_bal:.2f}</b>",
            parse_mode='HTML'
        )
    except Exception:
        pass


async def removebal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /removebal <telegram_id> <amount>")
        return
    try:
        target = int(context.args[0])
        amount = float(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID or amount.")
        return
    new_bal = await db.remove_credits(target, amount)
    await update.message.reply_text(f"✅ -${amount:.2f} removed from {target}. New balance: ${new_bal:.2f}")


# =============================================================================
# Menu Callbacks
# =============================================================================

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not await check_access(update):
        return

    data = query.data

    # ---- Main Menu ----
    if data == "menu_main":
        await show_dashboard(update, context, user.id)

    # ---- Balance ----
    elif data == "menu_balance":
        u = await db.get_user(user.id)
        stats = await db.get_user_stats(user.id)
        credits = float(u.get('credits', 0)) if u else 0.0
        price_1k = await db.get_price_per_1k()
        text = (
            f"💰 <b>Account Balance</b>\n\n"
            f"Balance: <b>${credits:.4f}</b>\n"
            f"Total Spent: ${float(u.get('total_spent', 0)):.2f}\n\n"
            f"📊 Usage Stats:\n"
            f"  Campaigns: {stats.get('campaign_count', 0)}\n"
            f"  Press-1 Hits: {stats.get('total_p1', 0)}\n\n"
            f"💡 Pricing: <b>${price_1k:.2f} per 1,000 numbers</b>"
        )
        keyboard = [
            [InlineKeyboardButton("💳 Buy Credits", callback_data="menu_buy")],
            [InlineKeyboardButton("🔙 Back", callback_data="menu_main")]
        ]
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

    # ---- Buy Credits ----
    elif data == "menu_buy":
        price_1k = await db.get_price_per_1k()
        text = (
            f"💳 <b>Buy Credits</b>\n\n"
            f"Current rate: <b>${price_1k:.2f} per 1,000 numbers</b>\n\n"
            f"Enter the amount you want to top up (minimum ${MIN_TOPUP_AMOUNT:.0f} USDT).\n\n"
            f"Send the amount as a message (e.g. <code>50</code>)"
        )
        context.user_data['awaiting_topup'] = True
        await query.edit_message_text(text, parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_balance")]]))

    # ---- My Campaigns ----
    elif data == "menu_campaigns":
        u = await db.get_or_create_user(user.id)
        campaigns = await db.get_user_campaigns(u['id'])
        if not campaigns:
            await query.edit_message_text(
                "📂 <b>No campaigns yet.</b>\n\nUse 🚀 New Campaign to start.",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚀 New Campaign", callback_data="menu_campaign")],
                    [InlineKeyboardButton("🔙 Back", callback_data="menu_main")]
                ])
            )
            return
        text = "📊 <b>My Campaigns</b>\n\n"
        keyboard = []
        status_emoji = {'running': '🟢', 'paused': '🟡', 'completed': '✅', 'draft': '⚪', 'failed': '❌'}
        for c in campaigns:
            emoji = status_emoji.get(c.get('status', ''), '⚪')
            text += f"{emoji} <b>{c['name']}</b> — {c.get('completed', 0)}/{c.get('total_numbers', 0)}\n"
            row = [InlineKeyboardButton(f"📊 {c['name'][:20]}", callback_data=f"camp_detail_{c['id']}")]
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_main")])
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

    # ---- My Leads ----
    elif data == "menu_leads":
        u = await db.get_or_create_user(user.id)
        leads = await db.get_user_leads(u['id'])
        text = "📋 <b>My Lead Lists</b>\n\n"
        keyboard = []
        if leads:
            for l in leads:
                text += f"📁 <b>{l['list_name']}</b> — {l['total_numbers']} numbers\n"
                keyboard.append([
                    InlineKeyboardButton(f"🗑 {l['list_name'][:20]}", callback_data=f"lead_del_{l['id']}")
                ])
        else:
            text += "No lead lists yet.\n\n"
        text += "\n<b>To add leads:</b> Send a .txt or .csv file with phone numbers, or paste numbers as text."
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_main")])
        context.user_data['awaiting_leads'] = True
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

    # ---- P1 Group ----
    elif data == "menu_p1group":
        await query.edit_message_text(
            "📞 <b>Set P1 Notification Group</b>\n\n"
            "1. Add this bot to your Telegram group\n"
            "2. Give it Admin rights\n"
            "3. Type /setp1group in that group\n\n"
            "Press-1 hits will be forwarded there.",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_main")]])
        )

    # ---- New Campaign (Step 1: Name) ----
    elif data == "menu_campaign":
        u = await db.get_user(user.id)
        credits = float(u.get('credits', 0)) if u else 0.0
        if credits <= 0 and not is_admin(user.id):
            await query.edit_message_text(
                "❌ <b>Insufficient Credits</b>\n\nBuy credits to start a campaign.",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Buy Credits", callback_data="menu_buy")],
                    [InlineKeyboardButton("🔙 Back", callback_data="menu_main")]
                ])
            )
            return
        context.user_data['new_camp'] = {}
        context.user_data['camp_step'] = 'name'
        await query.edit_message_text(
            "🚀 <b>New Campaign — Step 1/5</b>\n\nEnter campaign name:",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="menu_main")]])
        )

    # ---- Admin Panel ----
    elif data == "admin_panel":
        if not is_admin(user.id):
            return
        await show_admin_panel(query)

    # ---- Admin: Pending users ----
    elif data == "admin_pending":
        if not is_admin(user.id):
            return
        pending = await db.get_pending_users()
        if not pending:
            await query.edit_message_text(
                "✅ No pending users.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin", callback_data="admin_panel")]])
            )
            return
        text = f"⏳ <b>Pending Users ({len(pending)})</b>\n\n"
        keyboard = []
        for u2 in pending[:10]:
            name = u2.get('first_name') or u2.get('username') or str(u2['telegram_id'])
            text += f"👤 {name} — <code>{u2['telegram_id']}</code>\n"
            keyboard.append([
                InlineKeyboardButton(f"✅ Approve {name[:12]}", callback_data=f"admin_approve_{u2['telegram_id']}"),
                InlineKeyboardButton(f"🚫 Ban",                  callback_data=f"admin_ban_{u2['telegram_id']}")
            ])
        keyboard.append([InlineKeyboardButton("🔙 Admin", callback_data="admin_panel")])
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("admin_approve_"):
        if not is_admin(user.id):
            return
        target = int(data.replace("admin_approve_", ""))
        await db.approve_user(target)
        try:
            await context.bot.send_message(
                chat_id=target,
                text=f"✅ <b>Access Granted!</b>\n\nWelcome to <b>{COMPANY_NAME}</b>! Use /start to begin.",
                parse_mode='HTML'
            )
        except Exception:
            pass
        await query.answer("✅ Approved!", show_alert=False)
        await show_admin_panel(query)

    elif data.startswith("admin_ban_"):
        if not is_admin(user.id):
            return
        target = int(data.replace("admin_ban_", ""))
        await db.ban_user(target)
        await query.answer("🚫 Banned.", show_alert=False)
        await show_admin_panel(query)

    # ---- Admin: All Users ----
    elif data == "admin_users":
        if not is_admin(user.id):
            return
        all_users = await db.get_all_users()
        text = f"👥 <b>All Users ({len(all_users)})</b>\n\n"
        keyboard = []
        for u2 in all_users[:15]:
            name = u2.get('first_name') or u2.get('username') or str(u2['telegram_id'])
            status = "🚫" if u2.get('is_banned') else ("✅" if u2.get('is_active') else "⏳")
            credits = float(u2.get('credits', 0))
            text += f"{status} <b>{name}</b> — ${credits:.2f} | {u2.get('total_calls',0)} calls\n"
        keyboard.append([InlineKeyboardButton("🔙 Admin", callback_data="admin_panel")])
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

    # ---- Admin: Payments ----
    elif data == "admin_payments":
        if not is_admin(user.id):
            return
        pending_payments = await db.get_pending_payments()
        if not pending_payments:
            await query.edit_message_text(
                "✅ No pending payments.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin", callback_data="admin_panel")]])
            )
            return
        text = f"💳 <b>Pending Payments ({len(pending_payments)})</b>\n\n"
        keyboard = []
        for p in pending_payments[:10]:
            name = p.get('first_name') or p.get('username') or str(p.get('telegram_id'))
            text += f"⏳ {name} — ${p['credits']:.2f}\n   <code>{p['track_id'][:20]}</code>\n"
            keyboard.append([InlineKeyboardButton(
                f"✅ Confirm ${p['credits']:.0f} ({name[:10]})",
                callback_data=f"admin_confirm_{p['track_id']}"
            )])
        keyboard.append([InlineKeyboardButton("🔙 Admin", callback_data="admin_panel")])
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("admin_confirm_"):
        if not is_admin(user.id):
            return
        track_id = data.replace("admin_confirm_", "")
        payment = await db.get_payment_by_track_id(track_id)
        result = await db.confirm_payment(track_id)
        if result and payment:
            await query.answer("✅ Confirmed!", show_alert=False)
            try:
                await context.bot.send_message(
                    chat_id=payment['telegram_id'],
                    text=f"✅ <b>Payment Confirmed!</b>\n\n💰 <b>${payment['credits']:.2f}</b> credits added.",
                    parse_mode='HTML'
                )
            except Exception:
                pass
        else:
            await query.answer("❌ Already confirmed.", show_alert=True)
        await show_admin_panel(query)

    # ---- Admin: Set Price ----
    elif data == "admin_set_price":
        if not is_admin(user.id):
            return
        price = await db.get_price_per_1k()
        context.user_data['admin_set_price'] = True
        await query.edit_message_text(
            f"💲 <b>Set Price Per 1,000 Numbers</b>\n\n"
            f"Current price: <b>${price:.2f}</b>\n\n"
            f"Send the new price (e.g. <code>7.50</code>):",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]])
        )

    # ---- Campaign Detail ----
    elif data.startswith("camp_detail_"):
        camp_id = int(data.replace("camp_detail_", ""))
        await show_campaign_detail(query, camp_id)

    elif data.startswith("camp_start_"):
        camp_id = int(data.replace("camp_start_", ""))
        await db.start_campaign(camp_id)
        await query.answer("▶️ Campaign started!", show_alert=False)
        await show_campaign_detail(query, camp_id)

    elif data.startswith("camp_pause_"):
        camp_id = int(data.replace("camp_pause_", ""))
        await db.pause_campaign(camp_id)
        await query.answer("⏸️ Paused.", show_alert=False)
        await show_campaign_detail(query, camp_id)

    elif data.startswith("camp_resume_"):
        camp_id = int(data.replace("camp_resume_", ""))
        await db.start_campaign(camp_id)
        await query.answer("▶️ Resumed.", show_alert=False)
        await show_campaign_detail(query, camp_id)

    elif data.startswith("camp_reset_"):
        camp_id = int(data.replace("camp_reset_", ""))
        await db.reset_campaign(camp_id)
        await query.answer("🔄 Reset.", show_alert=False)
        await show_campaign_detail(query, camp_id)

    elif data.startswith("camp_delete_"):
        camp_id = int(data.replace("camp_delete_", ""))
        u2 = await db.get_or_create_user(user.id)
        await db.delete_campaign(camp_id, u2['id'])
        await query.edit_message_text(
            "❌ Campaign deleted.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 My Campaigns", callback_data="menu_campaigns")]])
        )

    elif data.startswith("camp_p1_"):
        camp_id = int(data.replace("camp_p1_", ""))
        rows = await db.get_p1_results(camp_id)
        if not rows:
            await query.answer("No press-1 results yet.", show_alert=True)
            return
        lines = [r['phone_number'] for r in rows]
        txt_bytes = "\n".join(lines).encode('utf-8')
        await query.message.reply_document(
            document=io.BytesIO(txt_bytes),
            filename=f"p1_results_{camp_id}.txt",
            caption=f"✅ Press-1 Results — {len(rows)} numbers"
        )

    elif data.startswith("camp_csv_"):
        camp_id = int(data.replace("camp_csv_", ""))
        rows = await db.get_full_report(camp_id)
        if not rows:
            await query.answer("No records yet.", show_alert=True)
            return
        header = "Phone,Status,Press1,Duration,Cost,Started,Ended\n"
        lines = []
        for r in rows:
            s = r['started_at'].strftime('%Y-%m-%d %H:%M:%S') if r['started_at'] else ''
            e = r['ended_at'].strftime('%Y-%m-%d %H:%M:%S') if r['ended_at'] else ''
            lines.append(f"{r['phone_number']},{r['status'] or ''},{r['dtmf_pressed'] or 0},{r['duration'] or 0},{r['cost'] or 0},{s},{e}")
        csv_bytes = (header + "\n".join(lines)).encode('utf-8')
        await query.message.reply_document(
            document=io.BytesIO(csv_bytes),
            filename=f"report_{camp_id}.csv",
            caption=f"📥 Full Report — {len(rows)} calls"
        )

    elif data.startswith("camp_cps_"):
        camp_id = int(data.replace("camp_cps_", ""))
        keyboard = [
            [InlineKeyboardButton(str(n), callback_data=f"cps_set_{camp_id}_{n}") for n in [1, 3, 5, 10]],
            [InlineKeyboardButton(str(n), callback_data=f"cps_set_{camp_id}_{n}") for n in [15, 20, 30, 50]],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"camp_detail_{camp_id}")]
        ]
        await query.edit_message_text(
            "⚡ <b>Change CPS</b>\n\nSelect concurrent call count:",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("cps_set_"):
        parts = data.replace("cps_set_", "").rsplit("_", 1)
        camp_id, new_cps = int(parts[0]), int(parts[1])
        async with db.pool.acquire() as conn:
            await conn.execute("UPDATE campaigns SET cps=$1 WHERE id=$2", new_cps, camp_id)
        await query.answer(f"✅ CPS → {new_cps}", show_alert=False)
        await show_campaign_detail(query, camp_id)

    # ---- Voice selection during campaign setup ----
    elif data.startswith("voice_pick_"):
        sound = data.replace("voice_pick_", "")
        # Remove extension — Asterisk Playback adds it automatically
        sound = sound.rsplit('.', 1)[0] if '.' in sound else sound
        camp = context.user_data.get('new_camp', {})
        if context.user_data.get('picking_outro'):
            camp['outro_file'] = sound
            context.user_data['picking_outro'] = False
            context.user_data['new_camp'] = camp
            context.user_data['camp_step'] = 'confirm'
            await show_campaign_confirm(query, context, user)
        else:
            camp['voice_file'] = sound
            context.user_data['new_camp'] = camp
            context.user_data['camp_step'] = 'outro'
            context.user_data['picking_outro'] = True
            sounds = get_asterisk_sounds()
            keyboard = [[InlineKeyboardButton(s, callback_data=f"voice_pick_{s}")] for s in sounds[:20]]
            keyboard.append([InlineKeyboardButton("⏭ Skip (no outro)", callback_data="outro_skip")])
            await query.edit_message_text(
                "🎵 <b>Step 4/5 — Select Outro Sound</b>\n\n"
                "(Plays after person presses 1)\n\nChoose from Asterisk sounds:",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    elif data == "outro_skip":
        camp = context.user_data.get('new_camp', {})
        camp['outro_file'] = ''
        context.user_data['new_camp'] = camp
        context.user_data['camp_step'] = 'confirm'
        await show_campaign_confirm(query, context, user.id)

    elif data == "camp_confirm_yes":
        await create_and_launch_campaign(query, context, user)

    elif data == "camp_confirm_no":
        context.user_data.clear()
        await show_dashboard(update, context, user.id)

    # ---- Lead delete ----
    elif data.startswith("lead_del_"):
        lead_id = int(data.replace("lead_del_", ""))
        u2 = await db.get_or_create_user(user.id)
        await db.delete_lead(lead_id, u2['id'])
        await query.answer("✅ Lead list deleted.", show_alert=False)
        # Refresh leads menu
        context.user_data['awaiting_leads'] = True
        leads = await db.get_user_leads(u2['id'])
        text = "📋 <b>My Lead Lists</b>\n\n"
        keyboard = []
        if leads:
            for l in leads:
                text += f"📁 <b>{l['list_name']}</b> — {l['total_numbers']} numbers\n"
                keyboard.append([InlineKeyboardButton(f"🗑 {l['list_name'][:20]}", callback_data=f"lead_del_{l['id']}")])
        else:
            text += "No lead lists.\n"
        text += "\nSend a .txt/.csv file or paste numbers to add a new list."
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_main")])
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

    # ---- Payment check ----
    elif data.startswith("pay_check_"):
        track_id = data.replace("pay_check_", "")
        result = await oxapay.check_payment_status(track_id)
        status = (result.get('status_text') or result.get('status', '')).lower() if result else ''
        if status in ('paid', 'complete', 'completed', 'confirmed', 'success'):
            payment = await db.get_payment_by_track_id(track_id)
            confirmed = await db.confirm_payment(track_id)
            if confirmed and payment:
                await query.edit_message_text(
                    f"✅ <b>Payment Confirmed!</b>\n\n💰 ${payment['credits']:.2f} credits added.",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]])
                )
            else:
                await query.answer("Already confirmed or not found.", show_alert=True)
        elif status in ('failed', 'expired', 'canceled'):
            await query.edit_message_text(
                f"❌ Payment {status}. Please create a new payment.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Buy Again", callback_data="menu_buy")],
                    [InlineKeyboardButton("🔙 Back", callback_data="menu_main")]
                ])
            )
        else:
            await query.answer("⏳ Payment still pending. Please wait.", show_alert=True)


async def show_admin_panel(query):
    text = f"🛡️ <b>Admin Panel — {COMPANY_NAME}</b>\n\n"
    keyboard = [
        [
            InlineKeyboardButton("⏳ Pending Users",  callback_data="admin_pending"),
            InlineKeyboardButton("👥 All Users",       callback_data="admin_users"),
        ],
        [
            InlineKeyboardButton("💳 Payments",       callback_data="admin_payments"),
            InlineKeyboardButton("💲 Set Price/1k",   callback_data="admin_set_price"),
        ],
        [InlineKeyboardButton("🔌 SIP Trunk",  callback_data="admin_trunk")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="menu_main")]
    ]
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))


async def show_campaign_detail(query, camp_id: int):
    stats = await db.get_campaign_stats(camp_id)
    if not stats:
        await query.edit_message_text("❌ Campaign not found.")
        return

    total     = stats.get('total_numbers', 0)
    completed = stats.get('completed', 0)
    answered  = stats.get('answered', 0)
    pressed   = stats.get('pressed_one', 0)
    failed    = stats.get('failed', 0)
    cost      = float(stats.get('actual_cost', 0))
    status    = stats.get('status', '')

    progress    = (completed / total * 100) if total > 0 else 0
    answer_rate = (answered / completed * 100) if completed > 0 else 0
    press_rate  = (pressed / total * 100) if total > 0 else 0
    fail_rate   = (failed / completed * 100) if completed > 0 else 0

    text = (
        f"📊 <b>{stats.get('name', 'Campaign')}</b>\n\n"
        f"Status: {status.upper()} | Cost: ${cost:.4f}\n\n"
        f"<b>Progress</b>  {mini_bar(progress)} {progress:.0f}%  ({completed}/{total})\n"
        f"<b>Answered</b>  {mini_bar(answer_rate)} {answer_rate:.0f}%  ({answered})\n"
        f"<b>Press-1</b>   {mini_bar(press_rate)} {press_rate:.0f}%  ({pressed})\n"
        f"<b>Failed</b>    {mini_bar(fail_rate)} {fail_rate:.0f}%  ({failed})\n"
    )

    keyboard = []
    if status == 'draft':
        keyboard.append([InlineKeyboardButton("▶️ Start", callback_data=f"camp_start_{camp_id}")])
    elif status == 'running':
        keyboard.append([
            InlineKeyboardButton("⏸️ Pause", callback_data=f"camp_pause_{camp_id}"),
            InlineKeyboardButton("⚡ CPS",   callback_data=f"camp_cps_{camp_id}"),
        ])
    elif status == 'paused':
        keyboard.append([
            InlineKeyboardButton("▶️ Resume", callback_data=f"camp_resume_{camp_id}"),
            InlineKeyboardButton("🔄 Reset",  callback_data=f"camp_reset_{camp_id}"),
        ])
    elif status == 'completed':
        keyboard.append([InlineKeyboardButton("🔄 Reset", callback_data=f"camp_reset_{camp_id}")])

    if pressed > 0:
        keyboard.append([InlineKeyboardButton(f"✅ P1 Results ({pressed})", callback_data=f"camp_p1_{camp_id}")])
    keyboard.append([InlineKeyboardButton("📥 CSV Report", callback_data=f"camp_csv_{camp_id}")])
    keyboard.append([
        InlineKeyboardButton("🔄 Refresh", callback_data=f"camp_detail_{camp_id}"),
        InlineKeyboardButton("🗑 Delete",  callback_data=f"camp_delete_{camp_id}"),
    ])
    keyboard.append([InlineKeyboardButton("🔙 Campaigns", callback_data="menu_campaigns")])

    await query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))


async def show_campaign_confirm(query, context, user):
    camp = context.user_data.get('new_camp', {})
    price_1k = await db.get_price_per_1k()
    total = camp.get('lead_total', 0)
    cost = (total / 1000) * price_1k

    text = (
        f"✅ <b>Confirm Campaign</b>\n\n"
        f"📝 Name: <b>{camp.get('name', 'N/A')}</b>\n"
        f"📋 Lead: <b>{camp.get('lead_name', 'N/A')}</b> ({total} numbers)\n"
        f"🎵 Intro: <b>{camp.get('voice_file', 'N/A')}</b>\n"
        f"🎵 Outro: <b>{camp.get('outro_file') or 'None'}</b>\n"
        f"⚡ CPS: <b>{camp.get('cps', 5)}</b>\n\n"
        f"💰 Estimated cost: <b>${cost:.2f}</b> ({total} numbers × ${price_1k:.2f}/1k)\n"
    )
    u = await db.get_user(user.id)
    credits = float(u.get('credits', 0)) if u else 0
    if credits < cost and not is_admin(user.id):
        text += f"\n⚠️ <b>Insufficient credits!</b> You have ${credits:.2f}, need ${cost:.2f}.\n"
        keyboard = [
            [InlineKeyboardButton("💳 Buy Credits", callback_data="menu_buy")],
            [InlineKeyboardButton("❌ Cancel", callback_data="menu_main")]
        ]
    else:
        text += f"\n💳 Your balance: ${credits:.2f}"
        keyboard = [
            [
                InlineKeyboardButton("✅ Launch", callback_data="camp_confirm_yes"),
                InlineKeyboardButton("❌ Cancel", callback_data="camp_confirm_no"),
            ]
        ]
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))


async def create_and_launch_campaign(query, context, user):
    camp = context.user_data.get('new_camp', {})
    u = await db.get_or_create_user(user.id)
    price_1k = await db.get_price_per_1k()

    campaign = await db.create_campaign(
        user_id=u['id'],
        name=camp.get('name', 'Campaign'),
        lead_id=camp['lead_id'],
        caller_id=camp.get('caller_id') or DEFAULT_CALLER_ID,
        cps=camp.get('cps', 5),
        voice_file=camp.get('voice_file', ''),
        outro_file=camp.get('outro_file', ''),
        cost_per_1k=price_1k
    )

    # Deduct cost upfront
    total = camp.get('lead_total', 0)
    cost = (total / 1000) * price_1k
    if cost > 0 and not is_admin(user.id):
        await db.deduct_credits(u['id'], cost)

    # Start campaign
    await db.start_campaign(campaign['id'])

    context.user_data.clear()
    await query.edit_message_text(
        f"🚀 <b>Campaign Launched!</b>\n\n"
        f"📝 <b>{campaign['name']}</b>\n"
        f"📞 {total} numbers queued\n"
        f"💰 ${cost:.2f} deducted",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 View Campaign", callback_data=f"camp_detail_{campaign['id']}")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
        ])
    )


# =============================================================================
# Message Handler — Campaign creation wizard + lead upload + topup
# =============================================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (update.message.text or '').strip()

    if not await check_access(update):
        return

    camp_step = context.user_data.get('camp_step')

    # ---- Step 1: Campaign Name ----
    if camp_step == 'name':
        if not text:
            await update.message.reply_text("❌ Please send a campaign name.")
            return
        context.user_data['new_camp']['name'] = text
        context.user_data['camp_step'] = 'lead'

        u = await db.get_or_create_user(user.id)
        leads = await db.get_user_leads(u['id'])
        if not leads:
            context.user_data['camp_step'] = 'awaiting_lead_for_camp'
            await update.message.reply_text(
                "📋 <b>Step 2/5 — Upload Lead List</b>\n\n"
                "You have no lead lists yet.\n"
                "Send a .txt or .csv file with phone numbers (one per line).",
                parse_mode='HTML'
            )
            return

        keyboard = [[InlineKeyboardButton(
            f"📁 {l['list_name']} ({l['total_numbers']})",
            callback_data=f"lead_pick_{l['id']}_{l['list_name'][:20]}_{l['total_numbers']}"
        )] for l in leads]
        keyboard.append([InlineKeyboardButton("➕ Upload New List", callback_data="lead_upload_new")])
        await update.message.reply_text(
            "📋 <b>Step 2/5 — Select Lead List</b>",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # ---- Topup amount ----
    if context.user_data.get('awaiting_topup'):
        try:
            amount = float(text)
        except ValueError:
            await update.message.reply_text("❌ Send a valid number (e.g. 50)")
            return
        if amount < MIN_TOPUP_AMOUNT:
            await update.message.reply_text(f"❌ Minimum top-up is ${MIN_TOPUP_AMOUNT:.0f}")
            return
        context.user_data['awaiting_topup'] = False
        u = await db.get_or_create_user(user.id)
        result = await oxapay.create_invoice(amount, f"{COMPANY_NAME} credit top-up")
        if not result.get('success'):
            await update.message.reply_text(f"❌ Payment gateway error: {result.get('error', 'Unknown')}")
            return
        await db.create_payment(
            user_id=u['id'],
            track_id=result['track_id'],
            order_id=result['order_id'],
            amount=amount,
            credits=amount,
            payment_url=result['payment_url']
        )
        await update.message.reply_text(
            f"💳 <b>Payment Invoice Created</b>\n\n"
            f"Amount: <b>${amount:.2f} USDT</b>\n"
            f"Tap Pay Now to complete payment.",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Pay Now", url=result['payment_url'])],
                [InlineKeyboardButton("🔄 Check Status", callback_data=f"pay_check_{result['track_id']}")],
                [InlineKeyboardButton("🔙 Back", callback_data="menu_main")]
            ])
        )
        return

    # ---- CPS input during camp setup ----
    if camp_step == 'cps':
        try:
            cps = int(text)
            if not 1 <= cps <= 50:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Enter a number between 1 and 50.")
            return
        context.user_data['new_camp']['cps'] = cps
        context.user_data['camp_step'] = 'voice'
        sounds = get_asterisk_sounds()
        if not sounds:
            await update.message.reply_text(
                "⚠️ No sound files found in Asterisk sounds directory.\n"
                f"Path: {ASTERISK_SOUNDS_DIR}\n"
                "Please add .wav files to Asterisk sounds folder.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="menu_main")]])
            )
            return
        keyboard = [[InlineKeyboardButton(s, callback_data=f"voice_pick_{s}")] for s in sounds[:20]]
        await update.message.reply_text(
            "🎵 <b>Step 4/5 — Select Intro Sound</b>\n\nChoose from Asterisk sounds:",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # ---- Admin: set price ----
    if context.user_data.get('admin_set_price') and is_admin(user.id):
        try:
            price = float(text)
        except ValueError:
            await update.message.reply_text("❌ Send a valid number (e.g. 7.50)")
            return
        await db.set_price_per_1k(price)
        context.user_data['admin_set_price'] = False
        await update.message.reply_text(f"✅ Price set to <b>${price:.2f} per 1,000 numbers</b>", parse_mode='HTML')
        return

    # ---- Admin: trunk credentials input ----
    if is_admin(user.id) and context.user_data.get('admin_trunk_step') == 'waiting_input':
        handled = await handle_trunk_input(update, context)
        if handled:
            return

    # ---- Lead list text paste ----
    if context.user_data.get('awaiting_leads') or camp_step == 'awaiting_lead_for_camp':
        numbers = parse_numbers(text)
        if not numbers:
            await update.message.reply_text("❌ No valid phone numbers found. Send one per line.")
            return
        await _save_lead_and_continue(update, context, user, numbers, f"List_{datetime.now().strftime('%m%d_%H%M')}")
        return

    await update.message.reply_text("Use /start to access the menu.")


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await check_access(update):
        return

    doc = update.message.document
    if not doc:
        return

    fname = doc.file_name or ''
    if not (fname.endswith('.txt') or fname.endswith('.csv')):
        await update.message.reply_text("❌ Only .txt and .csv files are supported.")
        return

    file = await doc.get_file()
    content = await file.download_as_bytearray()
    text = content.decode('utf-8', errors='ignore')
    numbers = parse_numbers(text)

    if not numbers:
        await update.message.reply_text("❌ No valid phone numbers found in the file.")
        return

    list_name = fname.rsplit('.', 1)[0][:50]
    await _save_lead_and_continue(update, context, user, numbers, list_name)


async def _save_lead_and_continue(update, context, user, numbers: list, list_name: str):
    camp_step = context.user_data.get('camp_step')
    u = await db.get_or_create_user(user.id)
    lead = await db.create_lead(u['id'], list_name)
    new_count = await db.bulk_insert_numbers(lead['id'], numbers)
    total_count = lead.get('total_numbers') or new_count

    # Refresh lead to get updated total
    refreshed = await db.get_lead(lead['id'])
    if refreshed:
        total_count = refreshed['total_numbers']

    if new_count == 0:
        await update.message.reply_text(
            f"⚠️ <b>No new numbers added</b> — all {len(numbers)} numbers already exist in '<b>{list_name}</b>'.\n"
            f"Total in list: <b>{total_count}</b>",
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(
            f"✅ <b>{new_count} new numbers added</b> to '<b>{list_name}</b>'\n"
            f"Total in list: <b>{total_count}</b>",
            parse_mode='HTML'
        )

    if camp_step in ('awaiting_lead_for_camp', 'lead'):
        context.user_data['new_camp']['lead_id']    = lead['id']
        context.user_data['new_camp']['lead_name']  = list_name
        context.user_data['new_camp']['lead_total'] = total_count
        context.user_data['camp_step'] = 'cps'
        await update.message.reply_text(
            "⚡ <b>Step 3/5 — Concurrent Calls (CPS)</b>\n\n"
            "How many calls to run simultaneously? (1–50)\n\nSend a number:",
            parse_mode='HTML'
        )
    else:
        context.user_data['awaiting_leads'] = False


# ---- Lead pick callback (from existing leads) ----
async def handle_lead_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not await check_access(update):
        return

    # Format: lead_pick_{id}_{name}_{total}
    parts = query.data.replace("lead_pick_", "").split("_", 2)
    lead_id = int(parts[0])
    try:
        total = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        total = 0

    lead = await db.get_lead(lead_id)
    if not lead:
        await query.answer("Lead not found.", show_alert=True)
        return

    context.user_data['new_camp']['lead_id']    = lead_id
    context.user_data['new_camp']['lead_name']  = lead['list_name']
    context.user_data['new_camp']['lead_total'] = lead['total_numbers']
    context.user_data['camp_step'] = 'cps'

    await query.edit_message_text(
        f"✅ Lead: <b>{lead['list_name']}</b> ({lead['total_numbers']} numbers)\n\n"
        "⚡ <b>Step 3/5 — Concurrent Calls (CPS)</b>\n\nSend a number (1–50):",
        parse_mode='HTML'
    )


async def handle_lead_upload_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['camp_step'] = 'awaiting_lead_for_camp'
    await query.edit_message_text(
        "📤 <b>Upload Lead List</b>\n\nSend a .txt or .csv file, or paste numbers (one per line).",
        parse_mode='HTML'
    )


# =============================================================================
# /setp1group command
# =============================================================================

async def setp1group_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == 'private':
        await update.message.reply_text("❌ Use this command inside a group.")
        return
    async with db.pool.acquire() as conn:
        await conn.execute("UPDATE users SET p1_group_id=$1 WHERE telegram_id=$2", chat.id, user.id)
    await update.message.reply_text("✅ Press-1 hits will be forwarded to this group!")


async def unsetp1group_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    async with db.pool.acquire() as conn:
        await conn.execute("UPDATE users SET p1_group_id=NULL WHERE telegram_id=$1", user.id)
    await update.message.reply_text("✅ Group forwarding disabled.")


# =============================================================================
# Post Init / Shutdown
# =============================================================================

async def post_init(application):
    await db.connect()
    webhook_srv.bot_app = application
    await webhook_srv.start()
    logger.info("✅ LoyalCorp P1 Bot ready")


async def post_shutdown(application):
    await webhook_srv.stop()
    await db.close()
    logger.info("🔴 Bot stopped")


# =============================================================================
# Main
# =============================================================================

def main():
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start",        start_command))
    app.add_handler(CommandHandler("approve",      approve_command))
    app.add_handler(CommandHandler("ban",          ban_command))
    app.add_handler(CommandHandler("unban",        unban_command))
    app.add_handler(CommandHandler("addbal",       addbal_command))
    app.add_handler(CommandHandler("removebal",    removebal_command))
    app.add_handler(CommandHandler("setp1group",   setp1group_command))
    app.add_handler(CommandHandler("unsetp1group", unsetp1group_command))

    # Callbacks
    app.add_handler(CallbackQueryHandler(handle_lead_pick,       pattern="^lead_pick_"))
    app.add_handler(CallbackQueryHandler(handle_lead_upload_new, pattern="^lead_upload_new$"))
    app.add_handler(CallbackQueryHandler(handle_trunk_callback,  pattern="^admin_trunk"))
    app.add_handler(CallbackQueryHandler(handle_menu,            pattern="^(menu_|admin_|camp_|voice_pick_|outro_|cps_set_|pay_check_|lead_del_)"))

    # Messages
    app.add_handler(MessageHandler(filters.Document.ALL,               handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,    handle_message))

    logger.info(f"🚀 Starting {COMPANY_NAME} Bot...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, timeout=0, poll_interval=1.0)


if __name__ == "__main__":
    main()