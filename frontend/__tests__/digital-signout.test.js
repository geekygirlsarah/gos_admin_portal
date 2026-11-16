import { afterEach, describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const template = readFileSync(
  resolve("templates", "programs", "digital_signout.html"),
  "utf8",
);
const script = template.match(/<script nonce="{{ csp_nonce }}">([\s\S]*?)<\/script>/)[1];
const signatureLink = template.match(/<a class="js-signature"[\s\S]*?<\/a>/)[0];

function renderSignatureLink(id) {
  return signatureLink
    .replace(/{% url '[^']+' [^%]+ %}/, `/signature/${id}/`)
    .replace(/{{ s\.signed_by_name\|default:'parent' }}/, "Guardian");
}

describe("mentor sign-out signatures", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("loads only the selected signature and retains the full-size link", () => {
    document.body.innerHTML = renderSignatureLink(1) + renderSignatureLink(2);
    new Function(script)();
    const [first, second] = document.querySelectorAll(".js-signature");
    const image = first.querySelector("img");
    expect(image.hasAttribute("src")).toBe(false);
    const firstClick = new MouseEvent("click", { bubbles: true, cancelable: true });
    expect(first.dispatchEvent(firstClick)).toBe(false);
    expect(image.getAttribute("src")).toBe("/signature/1/");
    expect(image.hidden).toBe(false);
    expect(first.querySelector("span").hidden).toBe(true);
    expect(second.querySelector("img").hasAttribute("src")).toBe(false);
    const secondClick = new MouseEvent("click", { bubbles: true, cancelable: true });
    expect(first.dispatchEvent(secondClick)).toBe(true);
  });

  it("preserves modified clicks for opening the image directly", () => {
    document.body.innerHTML = renderSignatureLink(1);
    new Function(script)();
    const link = document.querySelector(".js-signature");
    const event = new MouseEvent("click", {
      bubbles: true,
      cancelable: true,
      ctrlKey: true,
    });
    expect(link.dispatchEvent(event)).toBe(true);
    expect(link.querySelector("img").hasAttribute("src")).toBe(false);
  });
});
