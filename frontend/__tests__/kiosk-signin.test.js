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
  it("offers varied personalized greetings with the appropriate sign-out hours", () => {
    const pools = {
      MSG_STUDENT_IN: [12, []],
      MSG_STUDENT_OUT: [10, ["session", "week"]],
      MSG_MENTOR_IN: [12, []],
      MSG_MENTOR_OUT: [9, ["week"]],
      MSG_VISITOR_IN: [8, []],
      MSG_VISITOR_OUT: [8, []],
    };
    for (const [pool, [minimum, hours]] of Object.entries(pools)) {
      const match = template.match(new RegExp(`var ${pool} = (\\[[\\s\\S]*?\\]);`));
      expect(match).not.toBeNull();
      const messages = new Function(`return ${match[1]}`)();
      expect(new Set(messages).size).toBeGreaterThanOrEqual(minimum);
      for (const message of messages) {
        expect(message).toContain("{name}");
        for (const hour of hours) expect(message).toContain(`{${hour}}`);
        const buildMessage = templateFunction("buildMessage", { pickMessage: () => message });
        const result = buildMessage(messages, "Alex", { session: 1.5, week: 2.5 });
        expect(result).toContain("Alex");
        expect(result).not.toMatch(/\{(?:name|session|week)\}/);
      }
    }
  });

  it("shows weekly hours in every mentor sign-out message", async () => {
    const poolText = template.match(/var MSG_MENTOR_OUT = (\[[\s\S]*?\]);/);
    expect(poolText).not.toBeNull();
    const messages = new Function(`return ${poolText[1]}`)();
    for (const message of messages) {
      expect(message).toMatch(/volunteer/i);
      expect(message).not.toMatch(/you logged/i);
      const input = { value: "Test Mentor", focus: vi.fn() };
      const button = {};
      const showToast = vi.fn();
      const buildMessage = templateFunction("buildMessage", {
        pickMessage: () => message,
      });
      const memberSignIn = templateFunction("memberSignIn", {
        document: {
          getElementById: (id) => id === "memberInput" ? input : button,
        },
        showToast,
        showFlash: vi.fn(),
        showOverlay: vi.fn(),
        hideOverlay: vi.fn(),
        lookupStudent: () => Promise.resolve({
          students: [{ id: 1, type: "mentor", name: "Test" }],
        }),
        recordTap: () => Promise.resolve({
          event_type: "OUT", person_type: "mentor", week_hours: 2.5,
        }),
        buildMessage,
        MSG_MENTOR_OUT: messages,
      });
      memberSignIn();
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(showToast).toHaveBeenCalledWith(
        expect.stringContaining("2.5 hours this week"), true
      );
    }
  });

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
