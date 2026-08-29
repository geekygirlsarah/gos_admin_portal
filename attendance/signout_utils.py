"""Lock/unlock helpers for digital sign-out stations.

Unlocking is done by a mentor from the program page and stores an HttpOnly
cookie (mirroring the kiosk unlock), which then makes the public sign-out page
usable on the tablet for ``_COOKIE_MAX_AGE`` seconds.
"""

_COOKIE_MAX_AGE = 8 * 60 * 60  # 8 hours in seconds


def _cookie_name(config_id):
    return f"signout_unlocked_{config_id}"


def _is_unlocked(request, config_id):
    return request.COOKIES.get(_cookie_name(config_id)) == "1"


def _set_unlocked(response, config_id):
    response.set_cookie(
        _cookie_name(config_id),
        "1",
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
    )


def _clear_unlocked(response, config_id):
    response.delete_cookie(_cookie_name(config_id))
