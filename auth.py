import os

def _load_users() -> dict:
    users = {}
    for i in range(1, 11):
        suffix = "" if i == 1 else f"_{i}"
        u = os.environ.get(f"APP_USERNAME{suffix}", "")
        p = os.environ.get(f"APP_PASSWORD{suffix}", "")
        if u and p:
            users[u.strip().lower()] = p
    return users

_USERS = _load_users()


def check_credentials(username: str, password: str) -> bool:
    return bool(_USERS) and _USERS.get((username or "").strip().lower()) == password
