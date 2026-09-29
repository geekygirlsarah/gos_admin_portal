import { Editor } from "@tiptap/core";
import { DOMParser as PmDOMParser } from "@tiptap/pm/model";
import StarterKit from "@tiptap/starter-kit";
import Link from "@tiptap/extension-link";
import Placeholder from "@tiptap/extension-placeholder";
import Underline from "@tiptap/extension-underline";
import TextAlign from "@tiptap/extension-text-align";
import { TableKit } from "@tiptap/extension-table";
import ListKeymap from "@tiptap/extension-list-keymap";
import Image from "@tiptap/extension-image";

const TOOLBAR = [
  { icon: "bi-type-bold", title: "Bold", run: (e) => e.chain().focus().toggleBold().run(), isActive: (e) => e.isActive("bold") },
  { icon: "bi-type-italic", title: "Italic", run: (e) => e.chain().focus().toggleItalic().run(), isActive: (e) => e.isActive("italic") },
  { icon: "bi-type-underline", title: "Underline", run: (e) => e.chain().focus().toggleUnderline().run(), isActive: (e) => e.isActive("underline") },
  { icon: "bi-type-strikethrough", title: "Strikethrough", run: (e) => e.chain().focus().toggleStrike().run(), isActive: (e) => e.isActive("strike") },
  { type: "separator" },
  { icon: "bi-list-ul", title: "Bulleted list", run: (e) => e.chain().focus().toggleBulletList().run(), isActive: (e) => e.isActive("bulletList") },
  { icon: "bi-list-ol", title: "Numbered list", run: (e) => e.chain().focus().toggleOrderedList().run(), isActive: (e) => e.isActive("orderedList") },
  { icon: "bi-quote", title: "Blockquote", run: (e) => e.chain().focus().toggleBlockquote().run(), isActive: (e) => e.isActive("blockquote") },
  { type: "separator" },
  { icon: "bi-table", title: "Insert table", run: (e) => e.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run(), isActive: (e) => e.isActive("table") },
  { icon: "bi-table-column", title: "Delete table", run: (e) => e.chain().focus().deleteTable().run() },
  { type: "separator" },
  { icon: "bi-image", title: "Insert image", run: (e) => pickImageFile(e) },
  { icon: "bi-link-45deg", title: "Add link", run: (e) => promptForLink(e) },
  { icon: "bi-link-45deg", title: "Remove link", run: (e) => e.chain().focus().extendMarkRange("link").unsetLink().run() },
  { type: "separator" },
  { icon: "bi-text-left", title: "Align left", run: (e) => e.chain().focus().setTextAlign("left").run(), isActive: (e) => e.isActive({ textAlign: "left" }) },
  { icon: "bi-text-center", title: "Align center", run: (e) => e.chain().focus().setTextAlign("center").run(), isActive: (e) => e.isActive({ textAlign: "center" }) },
  { icon: "bi-text-right", title: "Align right", run: (e) => e.chain().focus().setTextAlign("right").run(), isActive: (e) => e.isActive({ textAlign: "right" }) },
  { type: "separator" },
  { icon: "bi-arrow-counterclockwise", title: "Undo", run: (e) => e.chain().focus().undo().run() },
  { icon: "bi-arrow-clockwise", title: "Redo", run: (e) => e.chain().focus().redo().run() },
];

function promptForLink(editor) {
  const previous = editor.getAttributes("link").href || "";
  const url = window.prompt("Link URL (https://...)", previous);
  if (url === null) {
    return;
  }
  if (url === "") {
    editor.chain().focus().extendMarkRange("link").unsetLink().run();
    return;
  }
  editor.chain().focus().extendMarkRange("link").setLink({ href: url }).run();
}

// ---------------------------------------------------------------------------
// Images
//
// Tiptap's Image extension only models the node; it does not read the
// clipboard for you. Pasted or picked images become `data:` URLs in the
// editor, and the server-side normalizer re-encodes them to a bounded JPEG
// (see EMAIL_MAX_EMBEDDED_IMAGES in programs/utils/email_html.py) before
// they leave the compose page.
// ---------------------------------------------------------------------------

function readImageFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function imageFromClipboard(clipboardData) {
  if (!clipboardData) {
    return null;
  }
  const files = Array.from(clipboardData.files || []);
  return files.find((file) => file.type && file.type.startsWith("image/")) || null;
}

function pickImageFile(editor) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "image/*";
  input.addEventListener("change", () => {
    const file = input.files && input.files[0];
    if (file) {
      readImageFile(file).then((src) => {
        editor.chain().focus().setImage({ src }).run();
      });
    }
  });
  input.click();
}

// ---------------------------------------------------------------------------
// Word paste normalization
//
// Word (and Google Docs copying Word) encodes list items as *flat* paragraphs
// tagged with an `mso-list` inline style plus a `mso-list:Ignore` marker span:
//
//   <p style='mso-list:l0 level1 lfo1'>
//     <span style='mso-list:Ignore'>·<span>&nbsp;</span></span>Item text</p>
//
// ProseMirror strips those styles on parse, so without intervention the paste
// degenerates into plain paragraphs bearing the stray "·" glyph. This detect +
// rewrite step converts the paragraphs into a real <ul>/<ol> *before* the
// strip happens, matching what the server-side normalizer does for stored HTML.
// ---------------------------------------------------------------------------

const MSO_LIST_RE = /mso-list\s*:/i;
const MSO_IGNORE_RE = /mso-list\s*:\s*ignore/i;
const MSO_LEVEL_RE = /level\s*(\d+)/i;

function getListLevel(el) {
  if (el.tagName.toLowerCase() !== "p") {
    return null;
  }
  const style = (el.getAttribute("style") || "").toLowerCase();
  if (!MSO_LIST_RE.test(style)) {
    return null;
  }
  const match = style.match(MSO_LEVEL_RE);
  return match ? parseInt(match[1], 10) : 1;
}

function removeMarkerSpan(p) {
  const first = p.firstElementChild;
  if (first && first.tagName.toLowerCase() === "span" && MSO_IGNORE_RE.test((first.getAttribute("style") || "").toLowerCase())) {
    first.remove();
  }
}

function buildNestedList(run) {
  const base = Math.min(...run.map(getListLevel));
  const root = document.createElement("ul");
  const stack = [{ level: base, list: root }];
  let lastItem = null;
  for (const p of run) {
    const level = getListLevel(p);
    while (stack.length > 1 && level <= stack[stack.length - 1].level) {
      stack.pop();
    }
    if (level > stack[stack.length - 1].level) {
      const nested = document.createElement("ul");
      lastItem.appendChild(nested);
      stack.push({ level, list: nested });
    }
    removeMarkerSpan(p);
    const li = document.createElement("li");
    while (p.firstChild) {
      li.appendChild(p.firstChild);
    }
    stack[stack.length - 1].list.appendChild(li);
    lastItem = li;
  }
  return root;
}

function wordListHtmlToHtml(html) {
  if (!html || !MSO_LIST_RE.test(html)) {
    return html;
  }
  const doc = new DOMParser().parseFromString(`<div id="gos-paste-root">${html}</div>`, "text/html");
  const container = doc.getElementById("gos-paste-root");
  if (!container) {
    return html;
  }

  const paragraphs = Array.from(container.querySelectorAll("p"));
  const runs = [];
  let run = [];
  for (const p of paragraphs) {
    if (getListLevel(p) !== null) {
      run.push(p);
    } else if (run.length) {
      runs.push(run);
      run = [];
    }
  }
  if (run.length) {
    runs.push(run);
  }
  if (!runs.length) {
    return html;
  }

  for (const current of runs) {
    const built = buildNestedList(current);
    current[0].parentNode.replaceChild(built, current[0]);
    for (const p of current.slice(1)) {
      p.remove();
    }
  }
  return container.innerHTML;
}

function createToolbar(editor) {
  const bar = document.createElement("div");
  bar.className = "btn-toolbar gap-1 flex-wrap";

  const buttons = [];
  for (const item of TOOLBAR) {
    if (item.type === "separator") {
      const sep = document.createElement("div");
      sep.className = "vr mx-1";
      bar.appendChild(sep);
      continue;
    }
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-secondary border-0";
    btn.title = item.title;
    btn.setAttribute("aria-label", item.title);
    btn.innerHTML = `<i class="bi ${item.icon}" aria-hidden="true"></i>`;
    btn.addEventListener("mousedown", (event) => event.preventDefault());
    btn.addEventListener("click", () => {
      item.run(editor);
      editor.commands.focus();
    });
    buttons.push({ btn, isActive: item.isActive });
    bar.appendChild(btn);
  }

  const refresh = () => {
    for (const { btn, isActive } of buttons) {
      const active = isActive && isActive(editor);
      btn.classList.toggle("active", Boolean(active));
    }
  };
  editor.on("transaction", refresh);
  refresh();
  return bar;
}

function initEditor(textarea) {
  const wrap = document.createElement("div");
  wrap.className = "email-editor border rounded";
  textarea.parentNode.insertBefore(wrap, textarea);
  textarea.classList.add("d-none");

  const placeholder = textarea.dataset.emailEditorPlaceholder || "Compose your message...";

  const editor = new Editor({
    element: document.createElement("div"),
    extensions: [
      StarterKit.configure({
        link: false,
        underline: false,
        listKeymap: false,
      }),
      Underline,
      ListKeymap,
      Link.configure({
        openOnClick: false,
        autolink: true,
        linkOnPaste: true,
      }),
      Placeholder.configure({ placeholder }),
      Image.configure({ allowBase64: true }),
      TextAlign.configure({ types: ["paragraph", "heading"] }),
      TableKit.configure({
        table: { resizable: true },
        row: {},
        header: {},
        cell: {},
      }),
    ],
    content: textarea.value || "",
    editorProps: {
      handlePaste(view, event) {
        const clipboard = event.clipboardData;
        const image = imageFromClipboard(clipboard);
        if (image) {
          // Pasted image files never carry text/html, so handle them before
          // the Word-list transform below.
          readImageFile(image).then((src) => {
            view.dispatch(
              view.state.tr.replaceSelection(
                view.state.schema.nodes.image.create({ src })
              )
            );
          });
          return true;
        }
        const html = clipboard && clipboard.getData("text/html");
        if (!html) {
          return false; // Let ProseMirror's default (plain text) handling run.
        }
        const transformed = wordListHtmlToHtml(html);
        if (transformed === html) {
          return false; // No Word lists; default HTML paste handling is right.
        }
        const slice = PmDOMParser.fromSchema(view.state.schema).parseSlice(
          new window.DOMParser().parseFromString(transformed, "text/html").body
        );
        view.dispatch(view.state.tr.replaceSelection(slice));
        return true;
      },
    },
  });

  editor.setEditable(true);
  wrap.appendChild(editor.view.dom);

  const toolbar = createToolbar(editor);
  wrap.insertBefore(toolbar, editor.view.dom);

  // Keep the hidden textarea in sync so the form submits the generated HTML,
  // and mark it touched so client-side "you have unsaved changes" still sees edits.
  const sync = () => {
    textarea.value = editor.getHTML();
    textarea.dataset.touched = "true";
  };
  editor.on("update", sync);

  const form = textarea.form;
  if (form) {
    form.addEventListener("submit", sync);
  }
  return editor;
}

export function init() {
  document
    .querySelectorAll("textarea[data-email-editor]")
    .forEach((text) => initEditor(text));
}

export { initEditor, promptForLink, wordListHtmlToHtml, readImageFile, imageFromClipboard, pickImageFile };

if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
}