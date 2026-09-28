"""Tests for the email HTML normalizer used by the messaging compose pages.

These cover the four problems that made the old Quill composer frustrating:

* pasted Word bullets arrived as indented paragraphs instead of real lists
* pasted tables arrived without borders / collapse so they rendered as mush
* blank ``<p><br></p>`` spacers added phantom vertical space
* the plain-text alternative ran list items and table cells together
"""

from django.test import SimpleTestCase

from programs.utils.email_html import (
    html_to_text,
    normalize_email_html,
    wrap_email_document,
)


class EmailHtmlSecurityTests(SimpleTestCase):
    """Editor output is untrusted: a crafted POST bypasses the browser."""

    def test_script_tags_are_removed(self):
        out = normalize_email_html("<p>hi</p><script>alert(1)</script>")
        self.assertNotIn("script", out)
        self.assertIn("hi", out)

    def test_event_handler_attributes_are_removed(self):
        out = normalize_email_html('<p onclick="steal()" style="color:red">hi</p>')
        self.assertNotIn("onclick", out)
        self.assertIn("color:red", out)

    def test_javascript_urls_are_stripped(self):
        out = normalize_email_html('<a href="javascript:alert(1)">click</a>')
        self.assertNotIn("javascript:", out)

    def test_image_onerror_is_removed(self):
        out = normalize_email_html('<img src="https://x/y.png" onerror="alert(1)">')
        self.assertNotIn("onerror", out)
        self.assertIn("src=", out)


class EmailHtmlWordCleanupTests(SimpleTestCase):
    """Microsoft Word puts a lot of non-HTML on the clipboard."""

    def test_office_xml_islands_are_stripped(self):
        out = normalize_email_html("<p>hi<o:p></o:p></p><w:sdt><w:sdtPr/></w:sdt>")
        self.assertNotIn("o:p", out)
        self.assertNotIn("w:sdt", out)
        self.assertIn("hi", out)

    def test_conditional_comments_are_stripped(self):
        out = normalize_email_html(
            "<p>real</p><!--[if mso]><table><tr><td>ghost</td></tr></table><![endif]-->"
        )
        self.assertIn("real", out)
        self.assertNotIn("ghost", out)

    def test_mso_list_marker_property_is_removed(self):
        out = normalize_email_html('<p style="mso-list:l0 level1 lfo1">Item</p>')
        self.assertNotIn("mso-list", out)
        self.assertIn("Item", out)

    def test_mso_classes_are_stripped(self):
        out = normalize_email_html('<p class="MsoNormal">Body</p>')
        self.assertNotIn("MsoNormal", out)
        self.assertNotIn("class=", out)


class EmailHtmlListRebuildTests(SimpleTestCase):
    """Word encodes bullets as styled paragraphs, not as lists."""

    def test_word_bullets_become_a_real_unordered_list(self):
        html = (
            '<p class="MsoListParagraphCxSpFirst" style="mso-list:l0 level1 lfo1">'
            '<span style="mso-list:Ignore">·<span>&nbsp;</span></span>First item</p>'
            '<p class="MsoListParagraph" style="mso-list:l0 level1 lfo1">Second item</p>'
        )
        out = normalize_email_html(html)
        self.assertIn("<ul", out)
        self.assertIn("<li", out)
        self.assertNotIn("mso-list", out)
        self.assertNotIn("MsoListParagraph", out)
        self.assertIn("First item", out)
        self.assertIn("Second item", out)
        self.assertEqual(out.count("<li"), 2)

    def test_word_numbers_become_a_real_ordered_list(self):
        html = (
            '<p style="mso-list:l0 level1 lfo1">'
            '<span style="mso-list:Ignore">1.<span>&nbsp;</span></span>Alpha</p>'
            '<p style="mso-list:l0 level1 lfo1">'
            '<span style="mso-list:Ignore">2.<span>&nbsp;</span></span>Beta</p>'
        )
        out = normalize_email_html(html)
        self.assertIn("<ol", out)
        self.assertNotIn("<ul", out)
        self.assertEqual(out.count("<li"), 2)
        self.assertIn("Alpha", out)
        self.assertIn("Beta", out)

    def test_nested_word_lists_nest_by_level(self):
        html = (
            '<p style="mso-list:l0 level1 lfo1">Top</p>'
            '<p style="mso-list:l0 level2 lfo1">Sub</p>'
            '<p style="mso-list:l0 level1 lfo1">Top two</p>'
        )
        out = normalize_email_html(html)
        self.assertIn("<ul", out)
        # A level-2 item must live inside a nested <ul> within its <li>.
        self.assertIn("<li>Top<ul><li>Sub</li></ul></li>", out.replace("\n", ""))
        self.assertIn("Top two", out)

    def test_existing_semantic_lists_survive_untouched(self):
        html = '<ul><li style="list-style-type:disc">Bullet</li></ul>'
        out = normalize_email_html(html)
        self.assertIn("<ul", out)
        self.assertIn("Bullet", out)
        self.assertEqual(out.count("<li"), 1)

    def test_list_paragraphs_do_not_swallow_following_prose(self):
        html = (
            '<p style="mso-list:l0 level1 lfo1">Bullet</p>' "<p>Trailing paragraph.</p>"
        )
        out = normalize_email_html(html)
        self.assertIn("Trailing paragraph.", out)
        # The prose paragraph must come after the rebuilt list, not inside it.
        self.assertLess(out.index("</ul>"), out.index("Trailing paragraph."))


class EmailHtmlListItemFlatteningTests(SimpleTestCase):
    """The editor serializes <li><p>Item</p></li>; emails want <li>Item</li>."""

    def test_single_block_item_is_unwrapped(self):
        out = normalize_email_html("<ul><li><p>Alpha</p></li><li><p>Beta</p></li></ul>")
        self.assertIn("<li>Alpha</li>", out)
        self.assertIn("<li>Beta</li>", out)
        self.assertNotIn("<li><p>", out)

    def test_nested_list_child_blocks_are_flattened_but_lists_kept(self):
        out = normalize_email_html(
            "<ul><li><p>Top</p><ul><li><p>Nested</p></li></ul></li></ul>"
        )
        self.assertIn("<li>Top<ul><li>Nested</li></ul></li>", out.replace("\n", ""))

    def test_formatting_within_item_survives(self):
        out = normalize_email_html(
            "<ul><li><p>Boldly <strong>go</strong> <em>there</em></p></li></ul>"
        )
        self.assertIn("<li>Boldly <strong>go</strong> <em>there</em></li>", out)

    def test_adjacent_block_items_get_a_space_between_them(self):
        out = normalize_email_html("<ul><li><p>First</p><p>Second</p></li></ul>")
        self.assertIn("<li>First Second</li>", out)

    def test_items_with_embedded_breaks_are_kept(self):
        out = normalize_email_html("<ul><li><p>Line one<br>Line two</p></li></ul>")
        self.assertIn("<li>Line one<br>Line two</li>", out)

    def test_ordered_list_items_flatten_too(self):
        out = normalize_email_html("<ol><li><p>One</p></li><li><p>Two</p></li></ol>")
        self.assertIn("<li>One</li>", out)
        self.assertIn("<ol", out)


class EmailHtmlSpacerRemovalTests(SimpleTestCase):
    """Blank paragraphs are what make the email taller than the editor."""

    def test_paragraph_containing_only_br_is_removed(self):
        out = normalize_email_html("<p><br></p><p>real</p>")
        self.assertEqual(out.count("<p>"), 1)
        self.assertIn("real", out)

    def test_nbsp_only_paragraph_is_removed(self):
        out = normalize_email_html("<p>&nbsp;</p><p>real</p>")
        self.assertNotIn("&nbsp;</p>", out)
        self.assertIn("real", out)

    def test_whitespace_only_paragraph_is_removed(self):
        out = normalize_email_html("<p>   </p><p>real</p>")
        self.assertNotIn(">   <", out)
        self.assertIn("real", out)

    def test_consecutive_spacers_all_removed(self):
        out = normalize_email_html("<p><br></p><p>&nbsp;</p><p><br></p><p>real</p>")
        self.assertEqual(out.count("<p"), 1)

    def test_paragraph_containing_br_and_text_is_kept(self):
        out = normalize_email_html("<p>real<br>second line</p>")
        self.assertIn("second line", out)
        self.assertIn("<br", out)


class EmailHtmlTableTests(SimpleTestCase):
    def test_pasted_table_gets_borders_and_collapse(self):
        html = (
            "<table><tr><td>A</td><td>B</td></tr>"
            "<tr><td>C</td><td>D</td></tr></table>"
        )
        out = normalize_email_html(html)
        self.assertIn("border-collapse", out)
        self.assertIn("border", out)
        self.assertIn("A", out)
        self.assertIn("D", out)

    def test_table_without_explicit_width_becomes_full_width(self):
        out = normalize_email_html("<table><tr><td>A</td></tr></table>")
        self.assertIn("width:100%", out.replace(" ", ""))

    def test_nowrap_attribute_is_dropped_so_cells_can_wrap(self):
        html = "<table><tr><td nowrap>A very long cell value here</td></tr></table>"
        out = normalize_email_html(html)
        self.assertNotIn("nowrap", out)

    def test_user_chosen_border_color_is_preserved(self):
        html = (
            '<table style="border:2px solid #ff0000">'
            '<tr><td style="border:2px solid #ff0000">A</td></tr></table>'
        )
        out = normalize_email_html(html)
        self.assertIn("#ff0000", out)

    def test_colspan_and_rowspan_survive(self):
        html = (
            '<table><tr><td colspan="2">Wide</td></tr>'
            '<tr><td rowspan="2">Tall</td><td>x</td></tr></table>'
        )
        out = normalize_email_html(html)
        self.assertIn("colspan", out)
        self.assertIn("rowspan", out)


class EmailHtmlFormattingPreservationTests(SimpleTestCase):
    """Normalization must not throw away formatting the author wanted."""

    def test_inline_formatting_is_preserved(self):
        html = (
            "<p><strong>bold</strong> <em>italic</em> "
            '<span style="text-decoration:underline">under</span></p>'
        )
        out = normalize_email_html(html)
        self.assertIn("<strong>", out)
        self.assertIn("<em>", out)
        self.assertIn("text-decoration:underline", out)

    def test_headings_and_colors_survive(self):
        html = '<h2 style="color:#123456">Title</h2><p style="color:#654321">Body</p>'
        out = normalize_email_html(html)
        self.assertIn("<h2", out)
        self.assertIn("#123456", out)
        self.assertIn("#654321", out)

    def test_google_docs_markup_survives(self):
        html = (
            '<ul><li dir="ltr" style="list-style-type:disc">'
            '<p dir="ltr" style="line-height:1.38;margin-top:0pt">'
            '<span style="font-size:11pt">Bullet</span></p></li></ul>'
        )
        out = normalize_email_html(html)
        self.assertIn("Bullet", out)
        self.assertIn("font-size:11pt", out)
        self.assertIn("<ul", out)

    def test_links_keep_href_and_text(self):
        out = normalize_email_html('<a href="https://example.com/x">Click</a>')
        self.assertIn("https://example.com/x", out)
        self.assertIn("Click", out)


class HtmlToTextTests(SimpleTestCase):
    """The plain-text alternative used to run everything together."""

    def test_paragraphs_are_separated_by_blank_lines(self):
        text = html_to_text("<p>One</p><p>Two</p>")
        self.assertEqual(text, "One\n\nTwo")

    def test_br_becomes_a_single_newline(self):
        text = html_to_text("<p>One<br>Two</p>")
        self.assertEqual(text, "One\nTwo")

    def test_unordered_list_items_get_bullets(self):
        text = html_to_text("<ul><li>Alpha</li><li>Beta</li></ul>")
        self.assertIn("\u2022 Alpha", text)
        self.assertIn("\u2022 Beta", text)

    def test_ordered_list_items_are_numbered(self):
        text = html_to_text("<ol><li>First</li><li>Second</li></ol>")
        self.assertIn("1. First", text)
        self.assertIn("2. Second", text)

    def test_nested_lists_are_indented(self):
        text = html_to_text("<ul><li>Top<ul><li>Sub</li></ul></li></ul>")
        self.assertIn("Top", text)
        self.assertIn("Sub", text)
        self.assertRegex(text, r"\n\s+[\u2022-] Sub")

    def test_table_cells_are_tab_separated(self):
        text = html_to_text("<table><tr><td>A</td><td>B</td></tr></table>")
        self.assertIn("A\tB", text)

    def test_table_rows_are_on_separate_lines(self):
        text = html_to_text(
            "<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>"
        )
        self.assertIn("A\tB", text)
        self.assertIn("C\tD", text)
        self.assertIn("\n", text)

    def test_table_cells_containing_paragraphs_stay_on_one_line(self):
        # Word puts a <p> inside every cell; those must not split the row apart.
        text = html_to_text(
            "<table>"
            "<tr><td><p>Time</p></td><td><p>What</p></td></tr>"
            "<tr><td><p>6:00 PM</p></td><td><p>Check in</p></td></tr>"
            "</table>"
        )
        self.assertIn("Time\tWhat", text)
        self.assertIn("6:00 PM\tCheck in", text)
        self.assertNotIn("\t\n", text)

    def test_headings_are_kept(self):
        text = html_to_text("<h2>Title</h2><p>Body</p>")
        self.assertIn("Title", text)
        self.assertIn("Body", text)

    def test_scripts_and_styles_are_excluded(self):
        text = html_to_text(
            "<style>p{color:red}</style><script>alert(1)</script><p>Body</p>"
        )
        self.assertEqual(text, "Body")

    def test_entities_are_decoded(self):
        text = html_to_text("<p>Caf&eacute; &amp; bar</p>")
        self.assertIn("Caf\u00e9", text)
        self.assertIn("&", text)

    def test_whitespace_is_collapsed_and_trimmed(self):
        text = html_to_text("<p>  lots   of\n\nspace  </p>")
        self.assertEqual(text, "lots of space")

    def test_empty_input_returns_empty_string(self):
        self.assertEqual(html_to_text(""), "")
        self.assertEqual(html_to_text("<p><br></p>"), "")


class WrapEmailDocumentTests(SimpleTestCase):
    """Two of the three compose pages emailed a bare HTML fragment."""

    def test_fragment_is_wrapped_in_a_full_document(self):
        out = wrap_email_document("<p>Hi</p>")
        self.assertTrue(out.lstrip().lower().startswith("<!doctype html>"))
        self.assertIn("<html", out)
        self.assertIn("<head>", out)
        self.assertIn("<body", out)
        self.assertIn("Hi", out)

    def test_wrapper_declares_utf8_and_a_viewport(self):
        out = wrap_email_document("<p>Hi</p>").lower()
        self.assertIn("charset", out)
        self.assertIn("utf-8", out)
        self.assertIn('name="viewport"', out)

    def test_wrapper_includes_base_email_styles(self):
        out = wrap_email_document("<p>Hi</p>")
        self.assertIn("<style", out)
        self.assertIn("line-height", out)

    def test_content_is_escaped_from_style_injection(self):
        out = wrap_email_document("<p>Hi</p>")
        # The fragment must land inside <body>, not be able to close the wrapper.
        body_at = out.lower().index("<body")
        self.assertLess(body_at, out.index("Hi"))

    def test_unsafe_fragment_is_sanitized_before_wrapping(self):
        out = wrap_email_document("<p>ok</p><script>alert(1)</script>")
        self.assertNotIn("script", out)
        self.assertIn("ok", out)
