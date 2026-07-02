# -*- coding: utf-8 -*-
"""Agent 工具：LangChain 原生 @tool。

模型通过 bind_tools(TOOLS) 直接拿到工具 schema（名字/描述/参数由签名+docstring 生成）。
user_id（= from_user_id，用户微信号）用 InjectedToolArg 注入——对模型不可见，由 runner 执行时填入，
用于多租户隔离（谁的联系人/任务/发件箱/记忆）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Annotated

from langchain_core.tools import InjectedToolArg, tool

from app import config
from app.agent import summarizer
from app.agent.llm import get_llm
from app.channel import email_sender
from app.core.memory.bot_users import BotUsersStore
from app.core.memory.contacts import ContactStore
from app.core.memory.directory import EmployeeDirectory
from app.core.memory.recording import RecordingStore
from app.core.memory.settings import UserSettingsStore, infer_smtp
from app.core.memory.tasks import TaskStore

# 注入参数类型别名（对模型不可见）。user_id = from_user_id（用户微信号），稳定的用户主键。
UserId = Annotated[str, InjectedToolArg]

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _today_str() -> str:
    now = datetime.now()
    return f"{now.strftime('%Y-%m-%d')} {_WEEKDAYS[now.weekday()]}"


def _parse_json_array(raw: str) -> list[dict]:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if m:
        raw = m.group(0)
    return json.loads(raw)


def _resolve_recipient(user_id: str, name: str):
    """解析收件邮箱：先个人联系人，再公司通讯录（带拼音模糊匹配）。返回 (email, 显示名)。"""
    c = ContactStore().match(user_id, name)
    if c and c.email:
        return c.email, c.name
    e = EmployeeDirectory().match(name)
    if e and e.email:
        return e.email, e.name
    return "", name


# ── structure_task ──────────────────────────────────────────
@tool
def structure_task(content: str, user_id: UserId = "default") -> str:
    """把用户口述的内容整理成简明的任务记录。当用户**在同一句话里就把要记的内容说全了**
    （如「记一下：明天三点前交报销单」）时调用，立即整理成简明记录。"""
    content = (content or "").strip()
    if not content:
        return "[structure_task] 没有可记录的内容"
    return f"📝 已记录：\n{summarizer.summarize(content)}"


# ── start_recording ─────────────────────────────────────────
@tool
def start_recording(user_id: UserId = "default") -> str:
    """开始一段「多条录制」会话：之后用户会连续发多条语音/文字，全部说完后发『就这些』生成
    整段总结。当用户说「我想记录一下 / 开始记录 / 我要记一段」这类**还没给具体内容、准备连发
    多条**的指令时调用。如果用户当场就把内容说全了，用 structure_task 而不是这个。"""
    RecordingStore().start(user_id)
    return ("🎙️ 开始记录，你尽管说，我先攒着不打断；说完发一句『就这些』我来整理。")


# ── assign_task ─────────────────────────────────────────────
@tool
def assign_task(content: str, user_id: UserId = "default") -> str:
    """把用户要发布/布置/分配的任务拆解成一条条结构化任务（完成人、内容、开始/完成时间），
    完成人自动匹配收件人（先个人联系人、再公司通讯录，**带拼音模糊匹配**，中文名/同音字/只说名
    都能对上英文名的人）后写入任务库。完成人哪怕是语音转写的同音字也照原样传进来。
    **若用户当场直接给了某人的邮箱（如"派给张三，邮箱 zhangsan@x.com"），就用那个邮箱，
    不必先加联系人**——一次性发送也支持。当用户说要给某人发任务、布置任务、分配任务、派活时调用。"""
    content = (content or "").strip()
    if not content:
        return "[assign_task] 没有可发布的任务内容"
    

    prompt = (config.PROMPT_DIR / "decompose_tasks.txt").read_text(encoding="utf-8")
    prompt = prompt.format(today=_today_str(), content=content)
    raw = get_llm().invoke(prompt)
    text = getattr(raw, "content", raw)
    try:
        items = _parse_json_array(str(text))
    except (json.JSONDecodeError, ValueError) as e:
        return f"[assign_task] 任务拆解失败（模型未返回合法 JSON）：{e}"

    tasks = TaskStore()
    lines, unmatched = [], []
    for i, it in enumerate(items, 1):
        assignee = str(it.get("assignee") or "").strip()
        inline_email = str(it.get("email") or "").strip()
        body = str(it.get("content") or "").strip()
        start = str(it.get("start_time") or "").strip()
        end = str(it.get("end_time") or "").strip()
        if not body:
            continue
        # 话里直接给了邮箱 → 一次性用它，不必加联系人；否则查联系人/通讯录
        if "@" in inline_email:
            email, display = inline_email, (assignee or inline_email)
        elif assignee:
            email, display = _resolve_recipient(user_id, assignee)
        else:
            email, display = "", assignee
        tasks.add(user_id, assignee_name=assignee, content=body,
                  start_time=start, end_time=end, assignee_email=email)
        who = display or "（未指定）"
        mark = "✅" if email else ("⚠️" if assignee else "•")
        tag = "" if (email or not assignee) else "(没找到邮箱)"
        when = " ｜ ".join(filter(None, [f"起 {start}" if start else "", f"止 {end}" if end else ""])) or "时间未定"
        lines.append(f"{i}. {mark} {who}{tag}｜{body}\n   {when}")
        if assignee and not email:
            unmatched.append(assignee)

    if not lines:
        return "[assign_task] 没有解析出有效任务"
    out = f"📋 已登记 {len(lines)} 个任务：\n" + "\n".join(lines)
    if unmatched:
        uniq = "、".join(dict.fromkeys(unmatched))
        out += (f"\n\n⚠️ 这些完成人在公司通讯录和你的联系人里都没找到邮箱：{uniq}。"
                f"（可能是同名歧义，或不在名单里——要我把 ta 加为联系人吗？）")
    # 主动提醒：还没设自己的发件邮箱，发任务前需要先设
    if UserSettingsStore().get_smtp(user_id) is None:
        out += ("\n\n💡 你还没设发件邮箱，发任务邮件前先设一下："
                "『把我的发件邮箱设成 xxx@xxx.com，授权码 yyy』。")
    return out


# ── add_contact ─────────────────────────────────────────────
@tool
def add_contact(name: str, email: str = "", aliases: str = "",
                user_id: UserId = "default") -> str:
    """把一个人加入**个人**联系人名单（用于任务完成人匹配和邮件发送）。
    aliases 是别名/昵称，多个用逗号分隔。当用户说把某人加为联系人、添加联系人、
    记一下某人的邮箱、某人也叫什么时调用。"""
    name = (name or "").strip()
    if not name:
        return "[add_contact] 缺少联系人姓名"
    email = (email or "").strip()
    alias_list = [a.strip() for a in re.split(r"[,，、]", aliases or "") if a.strip()]
    _, created = ContactStore().add_or_update(user_id, name, email=email, aliases=alias_list)
    verb = "已新增" if created else "已更新"
    extra = "".join(filter(None, [f"，邮箱 {email}" if email else "",
                                  f"，别名 {'/'.join(alias_list)}" if alias_list else ""]))
    tail = "" if email else "（还没邮箱，发任务前记得补上）"
    return f"👤 {verb}联系人：{name}{extra}{tail}"


# ── set_sender_email ────────────────────────────────────────
@tool
def set_sender_email(email: str, password: str, smtp_host: str = "", smtp_port: str = "",
                     from_name: str = "", user_id: UserId = "default") -> str:
    """设置/更新本用户自己的发件邮箱（用于发任务邮件，按用户隔离，不与他人共用）。
    password 是邮箱授权码/应用专用密码（不是登录密码）。smtp_host/smtp_port 常见邮箱可不填会自动识别。
    当用户说设置发件邮箱、我的发件箱是、我的邮箱授权码是、改发件邮箱时调用。"""
    email = (email or "").strip()
    password = (password or "").strip()
    if not password:
        # 用户给了邮箱但没给授权码 → 回设置引导，教他怎么拿授权码
        guide = (config.PROMPT_DIR / "email_setup_guide.txt").read_text(encoding="utf-8").strip()
        return guide
    if not email:
        return "[set_sender_email] 还需要邮箱地址"
    host = (smtp_host or "").strip()
    port_raw = (smtp_port or "").strip()
    if not host:
        inferred = infer_smtp(email)
        if not inferred:
            return f"未能识别 {email} 的邮箱服务商，请告诉我 SMTP 服务器地址（如 smtp.qq.com）和端口。"
        host, default_port = inferred
    else:
        default_port = 465
    port = int(port_raw) if port_raw.isdigit() else default_port
    UserSettingsStore().set_smtp(user_id, host=host, port=port, user=email,
                                 password=password, from_name=(from_name or "").strip())
    return f"📮 已设置你的发件邮箱：{email}（{host}:{port}）。以后任务邮件都从这个邮箱发出。"


# ── send_tasks_email ────────────────────────────────────────
@tool
def send_tasks_email(user_id: UserId = "default") -> str:
    """把已登记、还没发出的任务，按完成人分别发邮件通知到 ta 的邮箱。
    当用户**确认无误后**说要发送/发出去/发邮件给他们/确认发布时调用。发送前若用户还没确认，
    先把任务念给他确认。"""
    
    smtp = UserSettingsStore().get_smtp(user_id)
    if not smtp:
        return ("✉️ 你还没设置自己的发件邮箱。先告诉我：把我的发件邮箱设成 "
                "xxx@xxx.com，授权码 yyy（每个用户用自己的邮箱发，不共用）。")
    pending = TaskStore().list_by_session(user_id, status="pending")
    if not pending:
        return "没有待发送的任务。"

    by_email: dict[str, list] = {}
    skipped = []
    for t in pending:
        if not t.assignee_email:
            skipped.append(t.assignee_name or "（未指定）")
            continue
        by_email.setdefault(t.assignee_email, []).append(t)

    sent_lines = []
    for email, tlist in by_email.items():
        who = tlist[0].assignee_name or email
        body_lines = [f"{i}. {t.content}"
                      + (f"\n   开始：{t.start_time}" if t.start_time else "")
                      + (f"\n   截止：{t.end_time}" if t.end_time else "")
                      for i, t in enumerate(tlist, 1)]
        body = f"你好 {who}，\n\n以下是分配给你的任务：\n\n" + "\n".join(body_lines) + "\n\n—— 任务助手"
        try:
            email_sender.send_email(smtp, email, subject=f"【任务】给{who}的{len(tlist)}项任务", body=body)
            TaskStore().mark_sent([t.id for t in tlist])
            sent_lines.append(f"✅ {who}（{email}）：{len(tlist)} 项")
        except Exception as e:  # noqa: BLE001
            sent_lines.append(f"❌ {who}：发送失败 {e}")

    out = "✉️ 邮件发送结果：\n" + ("\n".join(sent_lines) if sent_lines else "（无可发送对象）")
    if skipped:
        out += f"\n\n⚠️ 这些完成人没找到邮箱，没发：{'、'.join(dict.fromkeys(skipped))}。"
    return out


# ── lookup_directory ────────────────────────────────────────
@tool
def lookup_directory(name: str, user_id: UserId = "default") -> str:
    """在公司员工通讯录里按姓名查某人的邮箱。**自带拼音模糊匹配**：中文名能查到英文名的人
    （『危博』→Bo Wei）、语音转写的同音字也行（『微博』『维博』都查到 Bo Wei）、只说名也行
    （『文弟』→Wendi Li）、中英文换序也行。遇到像人名的词——哪怕看起来像普通词（如『微博』）——
    也直接把听到的名字原样传进来，由本工具匹配，不要自己改写或当成别的东西。
    当用户问某人邮箱、查某人、某人在不在通讯录、或派任务前想确认能不能匹配到时调用。"""
    name = (name or "").strip()
    if not name:
        return "[lookup_directory] 没说要查谁"
    matches = EmployeeDirectory().search(name)
    if not matches:
        return f"📒 公司通讯录里没找到「{name}」。（可能不在名单、或同名歧义需说全名）"
    if len(matches) == 1:
        m = matches[0]
        return f"📒 {m.name}：{m.email}"
    lines = "\n".join(f"- {m.name}：{m.email}" for m in matches)
    return f"📒 「{name}」匹配到多个人，是哪位？\n{lines}"


# ── call_agent ──────────────────────────────────────────────
@tool
def call_agent(target_name: str, message: str, user_id: UserId = "default") -> str:
    """向另一个用户的 Agent 发一条消息，由对方 Agent 处理后回复你。target_name 可以是
    对方的姓名或 Agent 名字（会在全局 bot 用户表里查找）。当用户说「问危博xxx」/
    「让危博的助手帮我确认xxx」/「跟危博的Agent说xxx」/「问下小博xxx」时调用。

    重要：
    - 对方可能不在线，或没设名字找不到，工具会返回明确原因
    - 两个 Agent 间对话有 5 轮上限，防止无限对话
    - 对方 Agent 的回复以文本形式返回给你，你要综合它的回复给用户最终答案"""
    from app import realtime
    from app.agent import conversation
    from app.agent.runner import VoiceTaskAgent
    from app.core.memory.bot_users import BotUsersStore

    target_name = (target_name or "").strip()
    message = (message or "").strip()
    if not target_name or not message:
        return "[call_agent] target_name 和 message 都不能空"

    # 5 轮硬限制（每 call_agent 一次算一轮）
    round_num = conversation.inc_round()
    if round_num > conversation.MAX_ROUNDS:
        return (f"[call_agent] 已经和其他 Agent 交换了 {conversation.MAX_ROUNDS} 轮消息，"
                f"到达上限，本次调用被阻止。请直接回复用户当前进展。")

    users = BotUsersStore()
    target = users.find_by_name(target_name)
    if target is None:
        return (f"[call_agent] 全局 bot 用户表里没找到「{target_name}」。可能这个人还没开通 bot，"
                f"或还没设置自己的名字。可以让用户先补一下信息。")
    if target.user_id == user_id:
        return "[call_agent] 目标就是你自己，不能自己跟自己对话。"
    if target.status != "running":
        return (f"[call_agent] 「{target.display_name or target.user_id}」的 Agent 当前不在线，"
                f"消息发不出去。可以晚点再试或走别的渠道（比如邮件）。")

    sender = users.get(user_id)
    sender_display = (sender.display_name if sender and sender.display_name else "某个用户")

    # 双方 channel 都推同一条 agent_conversation 事件，两边看板都能看到这一轮
    def _publish_both(payload: dict) -> None:
        realtime.publish(user_id, "agent_conversation", payload)
        realtime.publish(target.user_id, "agent_conversation", payload)

    _publish_both({
        "round": round_num,
        "direction": "→",
        "from": sender_display,
        "from_user_id": user_id,
        "to": target.display_name or target.user_id,
        "to_user_id": target.user_id,
        "text": message,
    })

    # 同步触发目标 Agent 处理
    try:
        agent = VoiceTaskAgent()
        reply = agent.handle_agent_message(
            target_user_id=target.user_id,
            from_user_id=user_id,
            from_display_name=sender_display,
            message=message,
        )
    except Exception as e:  # noqa: BLE001
        return f"[call_agent] 目标 Agent 处理失败：{e}"

    _publish_both({
        "round": round_num,
        "direction": "←",
        "from": target.display_name or target.user_id,
        "from_user_id": target.user_id,
        "to": sender_display,
        "to_user_id": user_id,
        "text": reply,
    })

    return f"「{target.display_name or target.user_id}」的 Agent 回复：\n{reply}"


# ── set_my_name ─────────────────────────────────────────────
@tool
def set_my_name(name: str, user_id: UserId = "default") -> str:
    """设置或修改当前用户自己的姓名（用于让别人的 Agent 通过名字找到你）。
    当用户说「我叫xxx」/「我是xxx」/「记一下我叫xxx」/「设置我的名字为xxx」时调用。
    只用来记'用户自己是谁'，如果是要加别的联系人，用 add_contact。"""
    name = (name or "").strip()
    if not name:
        return "[set_my_name] 没说姓名"
    BotUsersStore().set_display_name(user_id, name)
    return f"👤 记住了，你叫「{name}」。别人的 Agent 说'找{name}'时能找到你。"


# ── set_agent_name ──────────────────────────────────────────
@tool
def set_agent_name(name: str, user_id: UserId = "default") -> str:
    """给用户的 Agent（也就是"我"）起个名字。当用户说「给你起名叫xxx」/
    「你就叫xxx吧」/「以后叫你xxx」时调用。"""
    name = (name or "").strip()
    if not name:
        return "[set_agent_name] 没说名字"
    BotUsersStore().set_agent_name(user_id, name)
    return f"🤖 好的，我叫「{name}」。之后我跟别人的 Agent 沟通会自称这个名字。"


# ── directory_status ────────────────────────────────────────
@tool
def directory_status(user_id: UserId = "default") -> str:
    """查询公司员工通讯录当前有多少人、最后更新时间。
    当用户问通讯录有多少人、名单更新到什么时候时调用。"""
    d = EmployeeDirectory()
    emps = d.list_all()
    if not emps:
        return "📒 公司通讯录目前是空的，请管理员从中台页上传一次。"
    from app.core.memory.store import get_conn
    conn = get_conn()
    try:
        upd = conn.execute("SELECT MAX(updated_at) FROM employee_directory").fetchone()[0]
    finally:
        conn.close()
    return f"📒 公司通讯录共 {len(emps)} 人，最后更新：{upd}。（名单从中台页上传维护）"


# 所有工具（runner 直接 bind_tools(TOOLS)）
TOOLS = [
    structure_task, start_recording, assign_task, add_contact,
    set_sender_email, send_tasks_email, lookup_directory, directory_status,
    set_my_name, set_agent_name, call_agent,
]
BY_NAME = {t.name: t for t in TOOLS}
