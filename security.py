"""密码哈希：bcrypt，成本因子 12。"""

import bcrypt

_COST = 12
_MAX_PASSWORD_BYTES = 72  # bcrypt 只接受前 72 字节


def _normalize(password: str) -> bytes:
    return password.encode("utf-8")[:_MAX_PASSWORD_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_normalize(password), bcrypt.gensalt(_COST)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(_normalize(password), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def password_problems(password: str) -> list[str]:
    """返回密码强度问题列表，空列表表示通过。"""
    problems = []
    if len(password or "") < 8:
        problems.append("密码至少 8 位")
    if len(password or "") > 72:
        problems.append("密码不能超过 72 字节")
    if not any(ch.isalpha() for ch in password or ""):
        problems.append("密码需包含字母")
    if not any(ch.isdigit() for ch in password or ""):
        problems.append("密码需包含数字")
    return problems
