from __future__ import annotations

import asyncio
import html
import logging
import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from telegram import BotCommand, ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database import ERPDatabase, ERPError, PartialSaveError


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("telegram_erp")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
TIMEZONE_NAME = os.getenv("APP_TIMEZONE", "Asia/Tashkent").strip()
ADMIN_USER_IDS = {
    int(value.strip())
    for value in os.getenv("TELEGRAM_ADMIN_USER_IDS", "").split(",")
    if value.strip()
}
database: ERPDatabase | None = None

BUTTON_STOCK = "📦 Ombor"
BUTTON_SALE = "🛒 Savdo"
BUTTON_ADD_MATERIAL = "➕ Material"
BUTTON_ADJUST_STOCK = "📉 Qoldiqni tuzatish"
BUTTON_CUSTOMERS = "👥 Mijozlar"
BUTTON_ADD_CUSTOMER = "➕ Mijoz"
BUTTON_DEBTS = "💳 Qarzlar"
BUTTON_PAY_DEBT = "💸 Qarz to‘lash"
BUTTON_CASH = "💵 Kassa"
BUTTON_ADD_CASH = "➕ Kassa amali"
BUTTON_RECIPES = "📋 Retseptlar"
BUTTON_PRODUCTION = "🏭 Ishlab chiqarish"
BUTTON_BRANCH = "🏢 Filial"
BUTTON_HELP = "ℹ️ Yordam"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BUTTON_STOCK, BUTTON_SALE],
        [BUTTON_ADD_MATERIAL, BUTTON_ADJUST_STOCK],
        [BUTTON_CUSTOMERS, BUTTON_ADD_CUSTOMER],
        [BUTTON_DEBTS, BUTTON_PAY_DEBT],
        [BUTTON_CASH, BUTTON_ADD_CASH],
        [BUTTON_RECIPES, BUTTON_PRODUCTION],
        [BUTTON_BRANCH, BUTTON_HELP],
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Menyudan bo‘limni tanlang",
)


def get_database() -> ERPDatabase:
    global database
    if database is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("Supabase sozlamalari topilmadi.")
        database = ERPDatabase(SUPABASE_URL, SUPABASE_KEY)
    return database


def local_today() -> date:
    return datetime.now(ZoneInfo(TIMEZONE_NAME)).date()


def parse_number(
    raw: str,
    label: str,
    *,
    allow_zero: bool = True,
    decimal_places: int = 3,
) -> Decimal:
    try:
        value = Decimal(raw.strip().replace(",", "."))
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"{label} raqam bo‘lishi kerak.") from exc
    if not value.is_finite():
        raise ValueError(f"{label} to‘g‘ri raqam bo‘lishi kerak.")
    quantizer = Decimal(1).scaleb(-decimal_places)
    if value != value.quantize(quantizer):
        raise ValueError(f"{label} ko‘pi bilan {decimal_places} xona kasr bo‘lishi mumkin.")
    if value < 0 or (not allow_zero and value == 0):
        condition = "noldan katta" if not allow_zero else "manfiy bo‘lmagan"
        raise ValueError(f"{label} {condition} bo‘lishi kerak.")
    return value


def money(value: Any) -> str:
    return f"{Decimal(str(value or 0)):,.2f}".replace(",", " ") + " so‘m"


def quantity(value: Any) -> str:
    return f"{Decimal(str(value or 0)).normalize():f}"


def split_fields(raw: str, expected: int) -> list[str]:
    fields = [part.strip() for part in raw.split("|")]
    if len(fields) != expected or any(not part for part in fields):
        raise ValueError("Maydonlarni `|` belgisi bilan ajrating. /yordam formatini ko‘ring.")
    return fields


def command_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    return " ".join(context.args).strip()


async def run_db(call: Any) -> Any:
    return await asyncio.to_thread(call)


async def guard(update: Update) -> bool:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return False
    if chat.type != "private":
        await message.reply_text(
            "Maxfiylik uchun bot buyruqlarini faqat shaxsiy chatda ishlating."
        )
        return False
    if user.id not in ADMIN_USER_IDS:
        await message.reply_text(
            "Sizda boshqaruv ruxsati yo‘q. Telegram ID’ingizni /id buyrug‘i bilan ko‘ring."
        )
        return False
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None:
        return
    if user is not None and user.id not in ADMIN_USER_IDS:
        await message.reply_text(
            "ERP botiga xush kelibsiz. Bu bot faqat ruxsat berilgan foydalanuvchilar uchun.\n"
            "Telegram ID’ingizni ko‘rish: /id",
            reply_markup=MAIN_KEYBOARD,
        )
        return
    await message.reply_text(
        "ERP botiga xush kelibsiz.\n"
        "Boshlash uchun “Filial” tugmasidan filialni tanlang, so‘ng kerakli bo‘limni oching.\n"
        "/yordam — buyruqlar va kiritish formatlari.",
        reply_markup=MAIN_KEYBOARD,
    )


async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return
    if chat.type != "private":
        await message.reply_text("Telegram ID’ingizni shaxsiy chatda so‘rang.")
        return
    await message.reply_text(f"Telegram ID’ingiz: {user.id}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not await guard(update):
        return
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        "<b>Ombor</b>\n"
        "/ombor — tanlangan filial materiallari\n"
        "/material_qoshish nomi | birlik | boshlang‘ich qoldiq | tannarx\n"
        "/qoldiq nomi | miqdor o‘zgarishi (+/-)\n\n"
        "<b>Savdo va mijozlar</b>\n"
        "/sotuv nomi | miqdor | dona narxi | to‘landi | mijoz ID yoki - | naqd/karta\n"
        "/mijozlar — mijozlar ro‘yxati\n"
        "/mijoz_qoshish ism | telefon yoki - | manzil yoki - | papka yoki -\n"
        "/qarzlar — qarzdorlar ro‘yxati\n"
        "/qarz_tolash mijoz ID | summa | naqd/karta\n\n"
        "<b>Kassa</b>\n"
        "/kassa — bugungi kassa\n"
        "/kassa_amal kirim/chiqim | sabab | summa | naqd/karta\n\n"
        "<b>Retsept va ishlab chiqarish</b>\n"
        "/retseptlar — retseptlar ro‘yxati\n"
        "/ishlab_chiqarish retsept ID | miqdor | bonus ha/yo‘q\n\n"
        "/filial — filial tanlash. Menyu tugmalaridan ham foydalanishingiz mumkin.",
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD,
    )


async def choose_branch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    context.user_data.pop("pending_action", None)
    message = update.effective_message
    if message is None:
        return
    raw = command_text(context)
    if raw:
        try:
            branch_id = int(raw)
        except ValueError:
            await message.reply_text("Filial ID butun son bo‘lishi kerak.")
            return
        context.user_data["branch_id"] = branch_id
        await message.reply_text(
            f"{branch_id}-filial tanlandi.",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    try:
        branch_ids = await run_db(get_database().list_branches)
    except Exception as exc:
        await reply_error(message, exc)
        return
    if not branch_ids:
        await message.reply_text(
            "Jadvallardan filial ID’lari topilmadi. "
            "Filial ID’sini bilsangiz, /filial 1 ko‘rinishida yuboring.",
            reply_markup=MAIN_KEYBOARD,
        )
        return
    rows = [
        [f"Filial ID: {branch_id}" for branch_id in branch_ids[index : index + 2]]
        for index in range(0, len(branch_ids), 2)
    ]
    rows.append([BUTTON_HELP])
    await message.reply_text(
        "Ishlaydigan filialni tanlang:",
        reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True),
    )


async def require_branch(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int | None:
    branch_id = context.user_data.get("branch_id")
    if branch_id is not None:
        return int(branch_id)
    await update.effective_message.reply_text(
        "Avval “Filial” tugmasi yoki /filial buyrug‘i bilan filial tanlang.",
        reply_markup=MAIN_KEYBOARD,
    )
    return None


async def show_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    message = update.effective_message
    if message is None:
        return
    branch_id = await require_branch(update, context)
    if branch_id is None:
        return
    try:
        rows = await run_db(lambda: get_database().list_materials(branch_id))
        if not rows:
            await message.reply_text(
                f"{branch_id}-filialda material topilmadi.",
                reply_markup=MAIN_KEYBOARD,
            )
            return
        lines = [f"<b>📦 {branch_id}-filial ombori</b>"]
        for row in rows[:50]:
            lines.append(
                f"• {html.escape(str(row['name']))}: "
                f"{quantity(row['stock'])} {html.escape(str(row['unit']))} "
                f"· tannarx {money(row['cost'])}"
            )
        if len(rows) > 50:
            lines.append(f"50 ta yozuv ko‘rsatildi (jami {len(rows)}).")
        await message.reply_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=MAIN_KEYBOARD
        )
    except Exception as exc:
        await reply_error(message, exc)


PROMPTS = {
    "material": (
        "Yangi material ma’lumotlarini yuboring:\n"
        "<code>nomi | birlik | boshlang‘ich qoldiq | tannarx</code>\n"
        "Masalan: Sement | kg | 100 | 1200"
    ),
    "adjust": (
        "Qoldiq o‘zgarishini yuboring:\n"
        "<code>material nomi | miqdor (+/-)</code>\n"
        "Masalan: Sement | -5"
    ),
    "sale": (
        "Savdo ma’lumotlarini yuboring:\n"
        "<code>material nomi | miqdor | dona narxi | to‘landi | mijoz ID yoki - | naqd/karta</code>\n"
        "Masalan: Sement | 2 | 18000 | 36000 | - | naqd"
    ),
    "customer": (
        "Mijoz ma’lumotlarini yuboring:\n"
        "<code>ism | telefon yoki - | manzil yoki - | papka yoki -</code>"
    ),
    "debt_payment": (
        "Qarz to‘lovini yuboring:\n"
        "<code>mijoz ID | summa | naqd/karta</code>"
    ),
    "cash": (
        "Kassa amalini yuboring:\n"
        "<code>kirim/chiqim | sabab | summa | naqd/karta</code>"
    ),
    "production": (
        "Ishlab chiqarishni yuboring:\n"
        "<code>retsept ID | miqdor | bonus ha/yo‘q</code>\n"
        "Miqdor — retsept bo‘yicha ishlab chiqariladigan partiyalar soni."
    ),
}

BRANCH_ACTIONS = {"material", "adjust", "sale", "cash", "production"}


async def begin_action(
    update: Update, context: ContextTypes.DEFAULT_TYPE, action: str
) -> None:
    if not await guard(update):
        return
    context.user_data.pop("pending_action", None)
    if action in BRANCH_ACTIONS and await require_branch(update, context) is None:
        return
    context.user_data["pending_action"] = action
    await update.effective_message.reply_text(
        PROMPTS[action],
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD,
    )


async def process_form(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
    raw: str,
) -> None:
    if not await guard(update):
        return
    message = update.effective_message
    if message is None:
        return
    try:
        branch_id = None
        if action in BRANCH_ACTIONS:
            branch_id = await require_branch(update, context)
            if branch_id is None:
                return
        today = local_today()

        if action == "material":
            name, unit, stock_raw, cost_raw = split_fields(raw, 4)
            stock = parse_number(stock_raw, "Boshlang‘ich qoldiq")
            cost = parse_number(cost_raw, "Tannarx", decimal_places=2)
            row = await run_db(
                lambda: get_database().add_material(
                    name, unit, stock, cost, branch_id
                )
            )
            response = (
                f"Material qo‘shildi: {row['name']} — "
                f"{quantity(row['stock'])} {row['unit']}."
            )

        elif action == "adjust":
            name, delta_raw = split_fields(raw, 2)
            delta = parse_number(delta_raw, "Miqdor o‘zgarishi")
            if delta == 0:
                raise ValueError("Miqdor o‘zgarishi 0 bo‘lishi mumkin emas.")
            row = await run_db(
                lambda: get_database().adjust_material_stock(
                    name, delta, branch_id
                )
            )
            response = (
                f"{row['name']} qoldig‘i yangilandi: "
                f"{quantity(row['stock'])} {row['unit']}."
            )

        elif action == "sale":
            name, qty_raw, price_raw, paid_raw, customer_raw, method = split_fields(
                raw, 6
            )
            qty = parse_number(qty_raw, "Miqdor", allow_zero=False)
            price = parse_number(price_raw, "Dona narxi", decimal_places=2)
            paid = parse_number(paid_raw, "To‘langan summa", decimal_places=2)
            method = method.casefold()
            if method not in {"naqd", "karta"}:
                raise ValueError("To‘lov turi “naqd” yoki “karta” bo‘lishi kerak.")
            customer_id = None
            if customer_raw != "-":
                try:
                    customer_id = int(customer_raw)
                except ValueError as exc:
                    raise ValueError("Mijoz ID butun son yoki “-” bo‘lishi kerak.") from exc
            result = await run_db(
                lambda: get_database().record_sale(
                    name, qty, price, paid, customer_id, method, branch_id, today
                )
            )
            response = (
                f"✅ Savdo saqlandi: {result['name']}\n"
                f"Jami: {money(result['total'])}\n"
                f"To‘landi: {money(result['paid'])}\n"
                f"Qarz: {money(result['debt'])}\n"
                f"Qoldiq: {quantity(result['stock'])} {result['unit']}"
            )

        elif action == "customer":
            name, phone_raw, address_raw, folder_raw = split_fields(raw, 4)
            row = await run_db(
                lambda: get_database().add_customer(
                    name,
                    None if phone_raw == "-" else phone_raw,
                    None if address_raw == "-" else address_raw,
                    None if folder_raw == "-" else folder_raw,
                )
            )
            response = f"Mijoz qo‘shildi: {row['name']} (ID: {row['id']})."

        elif action == "debt_payment":
            customer_raw, amount_raw, method = split_fields(raw, 3)
            try:
                customer_id = int(customer_raw)
            except ValueError as exc:
                raise ValueError("Mijoz ID butun son bo‘lishi kerak.") from exc
            amount = parse_number(
                amount_raw, "To‘lov summasi", allow_zero=False, decimal_places=2
            )
            method = method.casefold()
            if method not in {"naqd", "karta"}:
                raise ValueError("To‘lov turi “naqd” yoki “karta” bo‘lishi kerak.")
            result = await run_db(
                lambda: get_database().record_debt_payment(
                    customer_id, amount, method, branch_id, today
                )
            )
            response = (
                f"Qarz to‘lovi saqlandi: {result['customer']}\n"
                f"To‘landi: {money(result['paid'])}\n"
                f"Qolgan qarz: {money(result['remaining_debt'])}"
            )

        elif action == "cash":
            kind, reason, amount_raw, method = split_fields(raw, 4)
            kind = kind.casefold()
            method = method.casefold()
            amount = parse_number(
                amount_raw, "Kassa summasi", allow_zero=False, decimal_places=2
            )
            if kind not in {"kirim", "chiqim"}:
                raise ValueError("Kassa turi “kirim” yoki “chiqim” bo‘lishi kerak.")
            if method not in {"naqd", "karta"}:
                raise ValueError("To‘lov turi “naqd” yoki “karta” bo‘lishi kerak.")
            await run_db(
                lambda: get_database().add_cash_transaction(
                    kind, reason, amount, method, branch_id, today
                )
            )
            response = f"Kassa amali saqlandi: {kind}, {money(amount)}."

        elif action == "production":
            recipe_raw, qty_raw, bonus_raw = split_fields(raw, 3)
            try:
                recipe_id = int(recipe_raw)
            except ValueError as exc:
                raise ValueError("Retsept ID butun son bo‘lishi kerak.") from exc
            qty = parse_number(qty_raw, "Ishlab chiqarish miqdori", allow_zero=False)
            bonus_text = bonus_raw.casefold().replace("‘", "'")
            if bonus_text not in {"ha", "yo'q", "yoq"}:
                raise ValueError("Bonus uchun “ha” yoki “yo‘q” deb yozing.")
            has_bonus = bonus_text == "ha"
            result = await run_db(
                lambda: get_database().produce(
                    recipe_id, qty, has_bonus, branch_id, today
                )
            )
            materials = "\n".join(
                f"• {item['name']}: −{quantity(item['used'])} {item['unit']} "
                f"(qoldi {quantity(item['remaining'])})"
                for item in result["materials"]
            )
            response = (
                f"Ishlab chiqarish saqlandi: {result['recipe']}\n"
                f"Miqdor: {quantity(result['quantity'])}\n"
                f"Bonus: {quantity(result['total_bonus'])}\n"
                f"Xomashyo sarfi:\n{materials}"
            )
        else:
            raise ValueError("Noma’lum amal. /yordam buyrug‘ini ko‘ring.")

        context.user_data.pop("pending_action", None)
        await message.reply_text(response, reply_markup=MAIN_KEYBOARD)
    except (ValueError, ERPError) as exc:
        await message.reply_text(
            html.escape(str(exc)), parse_mode="HTML", reply_markup=MAIN_KEYBOARD
        )
    except Exception as exc:
        context.user_data.pop("pending_action", None)
        await reply_error(message, exc)


async def begin_or_process(
    update: Update, context: ContextTypes.DEFAULT_TYPE, action: str
) -> None:
    raw = command_text(context)
    if raw:
        await process_form(update, context, action, raw)
    else:
        await begin_action(update, context, action)


async def show_customers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    message = update.effective_message
    if message is None:
        return
    try:
        rows = await run_db(get_database().list_customers)
        if not rows:
            await message.reply_text("Mijozlar ro‘yxati bo‘sh.", reply_markup=MAIN_KEYBOARD)
            return
        lines = ["<b>👥 Mijozlar</b>"]
        for row in rows[:50]:
            phone = f" · {html.escape(str(row['phone']))}" if row.get("phone") else ""
            lines.append(
                f"• ID <code>{row['id']}</code>: {html.escape(str(row['name']))}{phone}"
            )
        if len(rows) > 50:
            lines.append(f"50 ta mijoz ko‘rsatildi (jami {len(rows)}).")
        await message.reply_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=MAIN_KEYBOARD
        )
    except Exception as exc:
        await reply_error(message, exc)


async def show_debts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    message = update.effective_message
    if message is None:
        return
    try:
        rows = await run_db(get_database().list_debts)
        if not rows:
            await message.reply_text(
                "Hozircha ochiq qarzlar yo‘q.", reply_markup=MAIN_KEYBOARD
            )
            return
        lines = ["<b>💳 Mijozlar qarzi</b>"]
        lines.extend(
            f"• ID <code>{row['customer_id']}</code> — "
            f"{html.escape(str(row['name']))}: {money(row['total_debt'])}"
            for row in rows[:50]
        )
        await message.reply_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=MAIN_KEYBOARD
        )
    except Exception as exc:
        await reply_error(message, exc)


async def show_cash(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    message = update.effective_message
    if message is None:
        return
    branch_id = await require_branch(update, context)
    if branch_id is None:
        return
    try:
        result = await run_db(
            lambda: get_database().daily_cash(branch_id, local_today())
        )
        rows = result["rows"]
        lines = [
            f"<b>💵 {branch_id}-filial kassa · {local_today().isoformat()}</b>",
            f"Kirim: {money(result['income'])}",
            f"Chiqim: {money(result['expenses'])}",
            f"Qoldiq: {money(result['income'] - result['expenses'])}",
        ]
        for row in rows[:20]:
            lines.append(
                f"• {row['type']} · {html.escape(str(row['reason']))} · "
                f"{money(row['amount'])} ({html.escape(str(row['method']))})"
            )
        if len(rows) > 20:
            lines.append(f"20 ta yozuv ko‘rsatildi (jami {len(rows)}).")
        await message.reply_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=MAIN_KEYBOARD
        )
    except Exception as exc:
        await reply_error(message, exc)


async def show_recipes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    message = update.effective_message
    if message is None:
        return
    try:
        rows = await run_db(get_database().list_recipes)
        if not rows:
            await message.reply_text("Retseptlar topilmadi.", reply_markup=MAIN_KEYBOARD)
            return
        lines = ["<b>📋 Retseptlar</b>"]
        for recipe in rows[:30]:
            ingredients = recipe.get("ingredients") or []
            if isinstance(ingredients, str):
                import json

                ingredients = json.loads(ingredients)
            parts = ", ".join(
                f"{item.get('name', '?')} {quantity(item.get('qty', 0))} "
                f"{item.get('unit', '')}"
                for item in ingredients[:8]
            )
            lines.append(
                f"• ID <code>{recipe['id']}</code> — "
                f"{html.escape(str(recipe['name']))}; "
                f"og‘irligi {quantity(recipe['total_weight'])}; "
                f"bonus {quantity(recipe['bonus_amount'])}"
                + (f"\n  Tarkibi: {html.escape(parts)}" if parts else "")
            )
        await message.reply_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=MAIN_KEYBOARD
        )
    except Exception as exc:
        await reply_error(message, exc)


async def show_production(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    message = update.effective_message
    if message is None:
        return
    branch_id = await require_branch(update, context)
    if branch_id is None:
        return
    try:
        rows = await run_db(
            lambda: get_database().list_production(branch_id, local_today())
        )
        lines = [f"<b>🏭 {branch_id}-filial ishlab chiqarishi</b>"]
        if rows:
            lines.extend(
                f"• {row['date']} — {html.escape(str(row['recipe_name']))}; "
                f"miqdor {quantity(row['quantity'])}; "
                f"bonus {money(row['total_bonus'])}"
                for row in rows[:15]
            )
        else:
            lines.append("Ishlab chiqarish yozuvlari topilmadi.")
        context.user_data["pending_action"] = "production"
        lines.append("\nYangi ishlab chiqarish uchun:")
        lines.append(PROMPTS["production"])
        await message.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=MAIN_KEYBOARD,
        )
    except Exception as exc:
        await reply_error(message, exc)


async def menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    message = update.effective_message
    if message is None or message.text is None:
        return
    text = message.text.strip()

    if text.startswith("Filial ID:"):
        try:
            branch_id = int(text.split(":", 1)[1].strip())
        except ValueError:
            await message.reply_text("Filial ID noto‘g‘ri.")
            return
        context.user_data["branch_id"] = branch_id
        context.user_data.pop("pending_action", None)
        await message.reply_text(
            f"{branch_id}-filial tanlandi.", reply_markup=MAIN_KEYBOARD
        )
        return

    if text == BUTTON_BRANCH:
        await choose_branch(update, context)
        return

    actions = {
        BUTTON_STOCK: show_stock,
        BUTTON_CUSTOMERS: show_customers,
        BUTTON_DEBTS: show_debts,
        BUTTON_CASH: show_cash,
        BUTTON_RECIPES: show_recipes,
        BUTTON_PRODUCTION: show_production,
        BUTTON_HELP: help_command,
    }
    if text in actions:
        context.user_data.pop("pending_action", None)
        await actions[text](update, context)
        return

    action_buttons = {
        BUTTON_SALE: "sale",
        BUTTON_ADD_MATERIAL: "material",
        BUTTON_ADJUST_STOCK: "adjust",
        BUTTON_ADD_CUSTOMER: "customer",
        BUTTON_PAY_DEBT: "debt_payment",
        BUTTON_ADD_CASH: "cash",
    }
    if text in action_buttons:
        await begin_action(update, context, action_buttons[text])
        return

    pending_action = context.user_data.get("pending_action")
    if pending_action:
        await process_form(update, context, pending_action, text)
        return

    await message.reply_text(
        "Menyudagi tugmani bosing yoki /yordam buyrug‘ini yuboring.",
        reply_markup=MAIN_KEYBOARD,
    )


async def reply_error(message: Any, exc: Exception) -> None:
    logger.error("ERP operation failed (%s)", type(exc).__name__)
    if isinstance(exc, (ERPError, ValueError)):
        response = str(exc)
    else:
        details = str(exc).casefold()
        if "permission" in details or "not allowed" in details:
            response = "Supabase ushbu jadvalga yozishga ruxsat bermadi."
        elif "column" in details or "relation" in details or "schema cache" in details:
            response = (
                "Mavjud jadval yoki ustunlar mos kelmadi. Supabase’dagi jadval "
                "ustunlarini tekshiring; bot yangi jadval yaratmaydi."
            )
        else:
            response = "Amal bajarilmadi. Supabase ulanishi va jadval ruxsatlarini tekshiring."
    await message.reply_text(
        html.escape(response), parse_mode="HTML", reply_markup=MAIN_KEYBOARD
    )


async def global_error(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    del update
    if context.error is not None:
        logger.error("Unhandled Telegram error (%s)", type(context.error).__name__)


async def set_commands(application: Application) -> None:
    commands = [
        BotCommand("start", "Asosiy menyu"),
        BotCommand("id", "Telegram ID ni ko‘rish"),
        BotCommand("yordam", "Buyruqlar va formatlar"),
        BotCommand("filial", "Filialni tanlash"),
        BotCommand("ombor", "Ombor qoldig‘i"),
        BotCommand("sotuv", "Savdo kiritish"),
        BotCommand("mijozlar", "Mijozlar ro‘yxati"),
        BotCommand("qarzlar", "Qarzlar ro‘yxati"),
        BotCommand("kassa", "Bugungi kassa"),
        BotCommand("retseptlar", "Retseptlar ro‘yxati"),
        BotCommand("ishlab_chiqarish", "Ishlab chiqarish"),
    ]
    await application.bot.set_my_commands(commands)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN Replit Secrets’da sozlanmagan.")
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Supabase sozlamalari Replit Secrets’da topilmadi.")
    try:
        ZoneInfo(TIMEZONE_NAME)
    except Exception as exc:
        raise RuntimeError("APP_TIMEZONE vaqt zonasi noto‘g‘ri.") from exc
    if not ADMIN_USER_IDS:
        raise RuntimeError("TELEGRAM_ADMIN_USER_IDS sozlanmagan.")

    application = (
        Application.builder().token(BOT_TOKEN).post_init(set_commands).build()
    )
    commands = {
        "start": start,
        "id": show_id,
        "myid": show_id,
        "yordam": help_command,
        "help": help_command,
        "filial": choose_branch,
        "ombor": show_stock,
        "materiallar": show_stock,
        "material_qoshish": lambda update, context: begin_or_process(
            update, context, "material"
        ),
        "qoldiq": lambda update, context: begin_or_process(
            update, context, "adjust"
        ),
        "sotuv": lambda update, context: begin_or_process(update, context, "sale"),
        "mijozlar": show_customers,
        "mijoz_qoshish": lambda update, context: begin_or_process(
            update, context, "customer"
        ),
        "qarzlar": show_debts,
        "qarz_tolash": lambda update, context: begin_or_process(
            update, context, "debt_payment"
        ),
        "kassa": show_cash,
        "kassa_amal": lambda update, context: begin_or_process(
            update, context, "cash"
        ),
        "retseptlar": show_recipes,
        "ishlab_chiqarish": lambda update, context: begin_or_process(
            update, context, "production"
        ),
    }
    for name, handler in commands.items():
        application.add_handler(CommandHandler(name, handler))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, menu_text)
    )
    application.add_error_handler(global_error)
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
