"""Менеджер пула реферальных кодов с автомиксом.

Каждый код используется случайное число раз (min..max), потом ротация на следующий.
После каждой успешной реги новый код этого акка добавляется в пул.
Файл referrals.json — персистентный стейт между запусками.

Формат referrals.json:
{
  "codes": [
    {"code": "V38EYQ9", "uses": 0, "max_uses": 7, "is_base": true},
    {"code": "ABC1234", "uses": 3, "max_uses": 6, "is_base": false}
  ],
  "active_idx": 0
}
"""

from __future__ import annotations
import json
import random
import logging
from dataclasses import dataclass, asdict, field
from pathlib import Path

log = logging.getLogger("grail-bot.referrals")


@dataclass
class RefCode:
    code: str
    uses: int = 0
    max_uses: int = 0  # генерится при добавлении в пул
    is_base: bool = False  # стартовый код пользователя — не лимитировать


class ReferralManager:
    def __init__(
        self,
        state_file: Path,
        base_code: str | None,
        min_uses: int = 5,
        max_uses: int = 10,
    ) -> None:
        self.state_file = state_file
        self.min_uses = min_uses
        self.max_uses = max_uses
        self.codes: list[RefCode] = []
        self.active_idx = 0
        self._load()
        if base_code and not any(c.code == base_code for c in self.codes):
            self.codes.insert(0, RefCode(
                code=base_code,
                uses=0,
                max_uses=random.randint(min_uses, max_uses),
                is_base=True,
            ))
            log.info("added base referral code: %s", base_code)
            self._save()

    def _load(self) -> None:
        if not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.codes = [RefCode(**c) for c in data.get("codes", [])]
            self.active_idx = int(data.get("active_idx", 0))
        except Exception as e:
            log.warning("could not load %s: %s", self.state_file, e)

    def _save(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps({
                "codes": [asdict(c) for c in self.codes],
                "active_idx": self.active_idx,
            }, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("could not save %s: %s", self.state_file, e)

    def next_code(self) -> str | None:
        """Возвращает следующий referral code для использования."""
        if not self.codes:
            return None
        # Проверяем что активный код ещё не исчерпан
        for _ in range(len(self.codes)):
            active = self.codes[self.active_idx]
            if active.is_base or active.uses < active.max_uses:
                return active.code
            # Ротация
            self.active_idx = (self.active_idx + 1) % len(self.codes)
        # Все исчерпаны — fallback на базовый или первый
        for c in self.codes:
            if c.is_base:
                return c.code
        return self.codes[0].code

    def mark_used(self, code: str) -> None:
        """Инкрементирует счётчик использования. Ротирует если достигнут лимит."""
        for c in self.codes:
            if c.code == code:
                c.uses += 1
                log.info("ref %s used %d/%d", code, c.uses, c.max_uses)
                if not c.is_base and c.uses >= c.max_uses:
                    log.info("ref %s exhausted, rotating", code)
                    self.active_idx = (self.active_idx + 1) % len(self.codes)
                break
        self._save()

    def add_new(self, code: str) -> None:
        """Добавляет новый код (от зарегистрированного акка) в пул."""
        if not code:
            return
        if any(c.code == code for c in self.codes):
            return  # уже есть
        new = RefCode(
            code=code,
            uses=0,
            max_uses=random.randint(self.min_uses, self.max_uses),
            is_base=False,
        )
        self.codes.append(new)
        log.info("added new referral code to pool: %s (max_uses=%d, total codes=%d)",
                 code, new.max_uses, len(self.codes))
        self._save()

    def stats(self) -> str:
        active = self.codes[self.active_idx] if self.codes else None
        return (
            f"pool={len(self.codes)} codes, "
            f"active={active.code if active else 'none'} "
            f"({active.uses}/{active.max_uses if active else 0})"
        )
