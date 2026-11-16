import html
from html.parser import HTMLParser

import nh3
from django.utils.html import format_html, format_html_join, strip_tags

ALLOWED_NOTIFICATION_TAGS = {
    "p",
    "br",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "ul",
    "ol",
    "li",
}


def sanitize_notification_html(message):
    return nh3.clean(
        message,
        tags=ALLOWED_NOTIFICATION_TAGS,
        attributes={},
        url_schemes=set(),
    )


class _NotificationHTMLBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ALLOWED_NOTIFICATION_TAGS:
            self.parts.append(format_html("<{}>", tag))

    def handle_endtag(self, tag):
        if tag in ALLOWED_NOTIFICATION_TAGS and tag != "br":
            self.parts.append(format_html("</{}>", tag))

    def handle_data(self, data):
        self.parts.append(format_html("{}", data))


def notification_message_for_display(notification):
    if not notification.message_is_html:
        return notification.message
    parser = _NotificationHTMLBuilder()
    parser.feed(sanitize_notification_html(notification.message))
    parser.close()
    return format_html_join("", "{}", ((part,) for part in parser.parts))


def notification_message_text(notification):
    if not notification.message_is_html:
        return notification.message
    return html.unescape(strip_tags(sanitize_notification_html(notification.message)))


def notification_html_has_text(message):
    text = html.unescape(strip_tags(sanitize_notification_html(message)))
    return bool(text.replace("\xa0", " ").strip())
