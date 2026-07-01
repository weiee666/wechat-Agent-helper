# -*- coding: utf-8 -*-
"""全局员工通讯录（从 Google Drive 同步），所有用户共用。匹配逻辑同联系人。"""
from __future__ import annotations

import re
import uuid
from collections import Counter
from datetime import datetime

from app.core.memory.store import get_conn


def _pinyin_syllables(s: str) -> list[str]:
    """姓名 → 拼音音节列表（小写）。中文转拼音，英文按空格/点拆词。"""
    s = (s or "").strip()
    if not s:
        return []
    if re.search(r"[一-鿿]", s):
        try:
            from pypinyin import lazy_pinyin
            return [t.lower() for t in lazy_pinyin(s) if t]
        except ImportError:
            return []
    return [t.lower() for t in re.split(r"[\s·]+", s) if t]


def pinyin_letters(s: str) -> Counter:
    """姓名 → 拼音字母多重集（跟顺序/分词/声调都无关）。

    '危博'→weibo→{b,e,i,o,w}；'Bo Wei'→bowei→同；'李文弟'→liwendi 与 'Wendi Li'→wendili 同。
    用字母多重集，'Wendi'(一个词)和'文弟'(两音节)也能对上。
    """
    return Counter("".join(_pinyin_syllables(s)))


def _fuzzy_hit(query_letters: Counter, name: str) -> bool:
    """query 的拼音字母是否被 name 的拼音字母覆盖（支持只说名/同音/换序）。"""
    nl = pinyin_letters(name)
    if not query_letters or not nl:
        return False
    return not (query_letters - nl)  # query ⊆ name（多重集子集）


class Employee:
    __slots__ = ("id", "name", "email", "aliases")

    def __init__(self, id, name, email, aliases):
        self.id = id
        self.name = name
        self.email = email
        self.aliases = aliases

    def __repr__(self):
        return f"Employee({self.name!r}, {self.email!r})"


def _row(r) -> Employee:
    return Employee(r["id"], r["name"], r["email"],
                    [a for a in (r["aliases"] or "").split(",") if a])


class EmployeeDirectory:
    def replace_all(self, entries: list[dict]) -> int:
        """整表替换。entries: [{name, email, aliases:[...]}...]。返回写入条数。"""
        now = datetime.now().isoformat(timespec="seconds")
        conn = get_conn()
        try:
            conn.execute("DELETE FROM employee_directory")
            for e in entries:
                if not (e.get("email") or "").strip():
                    continue
                conn.execute(
                    "INSERT INTO employee_directory(id, name, email, aliases, updated_at) VALUES (?,?,?,?,?)",
                    (uuid.uuid4().hex, e["name"], e["email"], ",".join(e.get("aliases") or []), now),
                )
            conn.commit()
            return conn.execute("SELECT COUNT(*) FROM employee_directory").fetchone()[0]
        finally:
            conn.close()

    def list_all(self) -> list[Employee]:
        conn = get_conn()
        try:
            return [_row(r) for r in conn.execute("SELECT * FROM employee_directory ORDER BY name").fetchall()]
        finally:
            conn.close()

    def count(self) -> int:
        conn = get_conn()
        try:
            return conn.execute("SELECT COUNT(*) FROM employee_directory").fetchone()[0]
        finally:
            conn.close()

    def match(self, name: str) -> Employee | None:
        """按姓名/别名/拼音精准匹配；歧义或无匹配返回 None。"""
        name = (name or "").strip()
        if not name:
            return None
        emps = self.list_all()
        exact = [e for e in emps if e.name == name]
        if len(exact) == 1:
            return exact[0]
        alias_hit = [e for e in emps if name in e.aliases]
        if len(alias_hit) == 1:
            return alias_hit[0]
        contains = [e for e in emps
                    if name in e.name or e.name in name or any(name in a or a in name for a in e.aliases)]
        if len(contains) == 1:
            return contains[0]
        # 拼音字母模糊匹配：中文名↔英文名、同音字、换序、只说名（如 危博/微博→Bo Wei，李文弟/文弟→Wendi Li）
        ql = pinyin_letters(name)
        pin = [e for e in emps if _fuzzy_hit(ql, e.name)]
        if len(pin) == 1:
            return pin[0]
        return None

    def search(self, name: str) -> list[Employee]:
        """返回所有可能匹配的人（供"查通讯录"工具展示，可能多个）。"""
        name = (name or "").strip()
        if not name:
            return []
        emps = self.list_all()
        ql = pinyin_letters(name)
        out, seen = [], set()
        for e in emps:
            hit = (name == e.name or name in e.aliases
                   or name in e.name or e.name in name
                   or any(name in a or a in name for a in e.aliases)
                   or _fuzzy_hit(ql, e.name))
            if hit and e.id not in seen:
                seen.add(e.id)
                out.append(e)
        return out
