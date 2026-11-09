import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

const template = readFileSync(
  join(process.cwd(), "templates", "kiosk", "signin.html"),
  "utf8"
);

function templateFunction(name, globals = {}) {
  const functionPattern = new RegExp(
    `    function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n    \\}`
  );
  const match = template.match(functionPattern);
  expect(match, `Expected ${name} to exist in the kiosk template`).not.toBeNull();
  return new Function(
    ...Object.keys(globals),
    `${match[0]}; return ${name};`
  )(...Object.values(globals));
}

describe("kiosk sign-in session handling", () => {
  it("recognizes only the kiosk's explicit locked response", () => {
    const kioskResponseError = templateFunction("kioskResponseError");

    const lockedError = kioskResponseError(
      { status: 403 },
      { error: "Kiosk is locked." }
    );
    expect(lockedError.code).toBe("kiosk_locked");

    const csrfError = kioskResponseError({ status: 403 }, { error: "Forbidden" });
    expect(csrfError.code).toBeUndefined();
    const serverError = kioskResponseError(
      { status: 503 },
      { error: "Custom server response" }
    );
    expect(serverError.message).toBe("Server error 503");

    const reload = vi.fn();
    const returnToUnlockIfLocked = templateFunction("returnToUnlockIfLocked", {
      window: { location: { reload } },
    });
    expect(returnToUnlockIfLocked(lockedError)).toBe(true);
    expect(returnToUnlockIfLocked(csrfError)).toBe(false);
    expect(reload).toHaveBeenCalledOnce();
  });

  it("reloads into the unlock view when a ping reports an expired cookie", () => {
    const reload = vi.fn();
    const checkUnlockStatus = templateFunction("checkUnlockStatus", {
      window: { location: { reload } },
    });

    expect(checkUnlockStatus({ unlocked: false })).toBe(false);
    expect(reload).toHaveBeenCalledOnce();
    expect(checkUnlockStatus({ unlocked: true })).toBe(true);
    expect(reload).toHaveBeenCalledOnce();
  });

  it("keeps network failures separate from locked-session responses", () => {
    const isNetworkError = templateFunction("isNetworkError");
    expect(isNetworkError(new Error("Failed to fetch"))).toBe(true);
    expect(isNetworkError(new Error("Server error 503"))).toBe(true);
    expect(isNetworkError(new Error("Kiosk is locked."))).toBe(false);
    expect(isNetworkError(new Error("Forbidden"))).toBe(false);

    expect(template).toContain("setInterval(function () {");
    expect(template).toContain("}, 30000);");
    expect(template).toContain("}).catch(showServiceDown);");
  });

  it("explains that member sign-in requires an exact full name", () => {
    expect(template).toContain("No exact member match found.");
    expect(template).toContain(
      "enter the member's first and last name exactly, or tap their RFID card."
    );
  });
});
