from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class ProductSearch(StatesGroup):
    waiting_for_query = State()
    choosing_product = State()
    viewing_result = State()


class Onboarding(StatesGroup):
    choosing_skin_type = State()
