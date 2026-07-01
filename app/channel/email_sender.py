# -*- coding: utf-8 -*-
"""邮件发送（对外交付渠道）。标准 smtplib，支持 SSL(465) / STARTTLS(587)。

发件配置按用户隔离：调用方传入该用户自己的 SMTP 配置（见 core.memory.settings）。
"""
from __future__ import annotations

import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr


def send_email(smtp: dict, to_email: str, subject: str, body: str) -> None:
    """用给定的 SMTP 配置发一封纯文本邮件。

    smtp: {host, port, user, password, from_name}
    """
    host = smtp["host"]
    port = int(smtp.get("port") or 465)
    user = smtp["user"]
    password = smtp["password"]
    from_name = smtp.get("from_name") or user

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(from_name, "utf-8")), user))
    msg["To"] = to_email

    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
        server.starttls()
    try:
        server.login(user, password)
        server.sendmail(user, [to_email], msg.as_string())
    finally:
        server.quit()
