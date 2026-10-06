import { describe, it, expect, afterEach } from "vitest";
import { initEditor, wordListHtmlToHtml, readImageFile, imageFromClipboard } from "../email-editor.js";

const WORD_LIST_HTML = [
  "<p class='MsoNormal'>Bring tools</p>",
  "<p class='MsoListParagraph' style='mso-list:l0 level1 lfo1'>",
  "<span style='mso-list:Ignore'>\u00b7<span>&nbsp;</span></span>",
  "Closed-toe shoes",
  "</p>",
  "<p class='MsoListParagraph' style='mso-list:l0 level1 lfo1'>",
  "<span style='mso-list:Ignore'>\u00b7<span>&nbsp;</span></span>",
  "Safety glasses</p>",
].join("");

const GOOGLE_DOCS_HTML = [
  "<ul>",
  "<li>Item alpha</li>",
  "<li>Item beta</li>",
  "</ul>",
].join("");

const GOOGLE_DOCS_LINEBREAKS = "<p>Line one<br>Line two<br>Line three</p>";

let activeEditors = [];

function mountEditor(initialHtml) {
  document.body.innerHTML =
    '<form><textarea id="b" data-email-editor data-email-editor-placeholder="P">' +
    "</textarea></form>";
  const ta = document.getElementById("b");
  ta.value = initialHtml;
  const editor = initEditor(ta);
  activeEditors.push(editor);
  return { editor, ta };
}

afterEach(() => {
  while (activeEditors.length) {
    activeEditors.pop().destroy();
  }
  document.body.innerHTML = "";
});

describe("email editor bootstrap", () => {
  it("replaces the textarea with an editor container", () => {
    const { ta } = mountEditor("");
    expect(ta.classList.contains("d-none")).toBe(true);
    expect(document.querySelector(".email-editor")).toBeTruthy();
    expect(document.querySelector(".email-editor .tiptap")).toBeTruthy();
  });

  it("seeds from the textarea's existing value", () => {
    const { editor } = mountEditor("<p>Already written</p>");
    expect(editor.getHTML()).toContain("Already written");
  });

  it("syncing writes the generated HTML back on update", () => {
    const { editor, ta } = mountEditor("");
    editor.chain().focus().insertContent("<p>Hello <strong>world</strong></p>").run();
    expect(ta.value).toContain("<strong>world</strong>");
  });
});

describe("Word paste fixtures", () => {
  it("keeps list text sensible through the schema", () => {
    const { editor } = mountEditor(WORD_LIST_HTML);
    const html = editor.getHTML();
    expect(html).toContain("Bring tools");
    expect(html).toContain("Closed-toe shoes");
    expect(html).toContain("Safety glasses");
  });

  it("parses real <ul>/<li> lists into list nodes", () => {
    const { editor } = mountEditor(GOOGLE_DOCS_HTML);
    const html = editor.getHTML();
    expect(html).toMatch(/<ul>/);
    expect(html).toContain("Item alpha");
    expect(html).toContain("Item beta");
  });

  it("keeps Google Docs line breaks readable", () => {
    const { editor } = mountEditor(GOOGLE_DOCS_LINEBREAKS);
    const html = editor.getHTML();
    expect(html).toContain("<br>");
    expect(html).not.toContain("Line oneLine two");
  });
});

describe("wordListHtmlToHtml", () => {
  it("converts Word list paragraphs into a real <ul>", () => {
    const out = wordListHtmlToHtml(WORD_LIST_HTML);
    expect(out).toMatch(/<ul>/);
    expect(out).toMatch(/<li>Closed-toe shoes<\/li>/);
    expect(out).toMatch(/<li>Safety glasses<\/li>/);
    expect(out.toLowerCase()).not.toContain("mso-list");
    expect(out).not.toContain("mso-list:Ignore");
  });

  it("leaves non-list pastes untouched", () => {
    const plain = "<p>Just a paragraph</p>";
    expect(wordListHtmlToHtml(plain)).toBe(plain);
  });

  it("keeps leftmost paragraph prose intact", () => {
    const out = wordListHtmlToHtml("<p>Lead-in</p>" + WORD_LIST_HTML);
    expect(out).toContain("<p>Lead-in</p>");
    expect(out).toMatch(/<li>/);
  });

  it("nests by Word level", () => {
    const nested =
      "<p style='mso-list:l0 level1 lfo1'>Top</p>" +
      "<p style='mso-list:l0 level2 lfo1'>Sub</p>" +
      "<p style='mso-list:l0 level1 lfo1'>Top two</p>";
    const out = wordListHtmlToHtml(nested);
    expect(out).toMatch(/<li>Top<ul><li>Sub<\/li><\/ul><\/li>/);
    expect(out).toContain("Top two");
  });
});

describe("image paste", () => {
  // 1x1 transparent PNG.
  const PNG_BYTES = Uint8Array.from(
    atob(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    ),
    (c) => c.charCodeAt(0)
  );

  it("reads an image file into a data URL", async () => {
    const file = new File([PNG_BYTES], "photo.png", { type: "image/png" });
    const src = await readImageFile(file);
    expect(src.startsWith("data:image/png;base64,")).toBe(true);
  });

  it("finds the first image in a clipboard payload", () => {
    const image = new File([PNG_BYTES], "a.png", { type: "image/png" });
    const doc = new File([PNG_BYTES], "b.pdf", { type: "application/pdf" });
    const found = imageFromClipboard({ files: [doc, image] });
    expect(found).toBe(image);
  });

  it("ignores a clipboard with no image files", () => {
    const doc = new File([PNG_BYTES], "b.pdf", { type: "application/pdf" });
    expect(imageFromClipboard({ files: [doc] })).toBe(null);
    expect(imageFromClipboard(null)).toBe(null);
    expect(imageFromClipboard({})).toBe(null);
  });

  it("inserts a pasted image as a data URL that reaches the textarea", async () => {
    const { editor, ta } = mountEditor("");
    const file = new File([PNG_BYTES], "photo.png", { type: "image/png" });
    const src = await readImageFile(file);
    editor.chain().focus().setImage({ src, alt: "Photo" }).run();
    expect(ta.value).toContain("<img");
    expect(ta.value).toContain("data:image/png;base64,");
    expect(ta.value).toContain('alt="Photo"');
  });
});