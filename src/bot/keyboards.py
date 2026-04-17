from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def receipt_confirmation_keyboard(expense_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💼 Company", callback_data=f"tag_company:{expense_id}"),
            InlineKeyboardButton("👤 Personal", callback_data=f"tag_personal:{expense_id}"),
        ],
        [
            InlineKeyboardButton("Edit", callback_data=f"edit:{expense_id}"),
            InlineKeyboardButton("Discard", callback_data=f"discard:{expense_id}"),
        ],
    ])


def delete_confirmation_keyboard(expense_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Yes, delete", callback_data=f"delete_yes:{expense_id}"),
            InlineKeyboardButton("Cancel", callback_data=f"delete_no:{expense_id}"),
        ]
    ])


def duplicate_keyboard(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Save anyway", callback_data=f"dup_save:{key}"),
            InlineKeyboardButton("Discard", callback_data=f"dup_discard:{key}"),
        ]
    ])


def nuke_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Yes, delete everything", callback_data="nuke_yes:1"),
            InlineKeyboardButton("Cancel", callback_data="nuke_no:1"),
        ]
    ])


def report_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("This week", callback_data="report:this_week"),
            InlineKeyboardButton("Previous week", callback_data="report:prev_week"),
        ],
        [
            InlineKeyboardButton("This month", callback_data="report:this_month"),
            InlineKeyboardButton("Previous month", callback_data="report:prev_month"),
        ],
        [
            InlineKeyboardButton("Last 7 days", callback_data="report:last_7"),
            InlineKeyboardButton("Last 30 days", callback_data="report:last_30"),
        ],
        [
            InlineKeyboardButton("All time", callback_data="report:all_time"),
        ],
    ])
