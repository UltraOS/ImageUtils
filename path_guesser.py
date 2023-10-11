import os
from typing import Optional, List


_project_root_path = ""


def project_root() -> str:
    return _project_root_path


def set_project_root(path: str) -> None:
    global _project_root_path
    _project_root_path = os.path.abspath(path)


def project_root_relative(*parts: str) -> str:
    return os.path.join(_project_root_path, *parts)


def valid_path_or_none(
    guess: str,
    validity_check: int = os.F_OK
) -> Optional[str]:
    return guess if os.access(guess, validity_check) else None


def valid_path_with_middle_parts_or_none(
    middle_parts: List[str], postfix: str,
    prefix: str = project_root(),
    validity_check: int = os.F_OK
) -> Optional[str]:
    for mp in middle_parts:
        path = os.path.join(prefix, mp, postfix)

        res = valid_path_or_none(path, validity_check)
        if res is not None:
            return res

    return None


def valid_path_with_prefixes_or_none(
    prefixes: List[str], postfix: str, validity_check: int = os.F_OK
) -> Optional[str]:
    for prefix in prefixes:
        path = os.path.join(prefix, postfix)

        res = valid_path_or_none(path, validity_check)
        if res is not None:
            return res

    return None
